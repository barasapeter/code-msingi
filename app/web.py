from datetime import datetime
from pathlib import Path
import json

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.ebook import Ebook
from app.models.admin_user import AdminUser
from app.models.payment import MpesaPayment
from app.models.sale import Sale
from app.services.pdf_uploads import MEDIA_DIR, PDF_DIR, hash_pdf_upload, remove_saved_upload, save_pdf_and_thumbnail
from app.services.mpesa import MpesaApiError, initiate_stk_push, normalize_kenyan_phone
from app.services.google_auth import (
    current_admin_email,
    google_email_from_callback,
    google_login_url,
    is_admin_email,
    normalized_email,
)
from app.config import get_settings

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
router = APIRouter(include_in_schema=False)


class MpesaPaymentRequest(BaseModel):
    phone_number: str


def payment_reason(payment: MpesaPayment) -> str:
    """Return Safaricom's saved explanation for an unsuccessful payment."""
    if payment.status != "failed" or not payment.callback_payload:
        return ""
    try:
        return (
            json.loads(payment.callback_payload)
            .get("Body", {})
            .get("stkCallback", {})
            .get("ResultDesc", "")
        )
    except (TypeError, ValueError):
        return ""


def session_payment(request: Request, db: Session, book_id: int) -> MpesaPayment | None:
    """Find the last M-Pesa request for this checkout in this browser session."""
    checkout_request_id = request.session.get("mpesa_checkout_request_id")
    if not isinstance(checkout_request_id, str):
        return None
    return db.scalar(
        select(MpesaPayment).where(
            MpesaPayment.checkout_request_id == checkout_request_id,
            MpesaPayment.book_id == book_id,
        )
    )


def active_book(db: Session, book_id: int) -> Ebook | None:
    return db.scalar(select(Ebook).where(Ebook.id == book_id, Ebook.deleted_at.is_(None)))


def can_manage_book(book: Ebook, admin_email: str) -> bool:
    return admin_email == get_settings().master_admin_email or book.uploader_email == admin_email


def record_paid_sale(db: Session, payment: MpesaPayment) -> bool:
    """Create the sale for a settled M-Pesa payment exactly once.

    Callback delivery can be delayed or legacy payments may predate sales
    recording, so the dashboard also uses this idempotent reconciliation step.
    """
    if payment.status != "paid" or db.scalar(select(Sale.id).where(Sale.payment_id == payment.id)) is not None:
        return False
    book = db.get(Ebook, payment.book_id)
    if book is None:
        return False
    db.add(
        Sale(
            book_id=book.id,
            seller_email=book.uploader_email or get_settings().master_admin_email,
            amount=payment.amount if payment.amount is not None else book.current_price,
            sale_type="mpesa_payment",
            payment_id=payment.id,
        )
    )
    return True


@router.get("/")
def home(request: Request, db: Session = Depends(get_db)):
    books = list(db.scalars(select(Ebook).where(Ebook.deleted_at.is_(None)).order_by(Ebook.id.desc())))
    return templates.TemplateResponse(request, "index.html", {"books": books})

@router.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    return """User-agent: *
Allow: /
"""

@router.get("/books/{book_id}/checkout")
def checkout(book_id: int, request: Request, db: Session = Depends(get_db)):
    book = active_book(db, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    if book.current_price <= 0:
        return RedirectResponse(url=f"/books/{book.id}/download", status_code=303)
    return RedirectResponse(url=f"/books/{book.id}", status_code=303)


@router.post("/books/{book_id}/checkout/mpesa")
def begin_mpesa_checkout(book_id: int, request: Request, db: Session = Depends(get_db)):
    book = active_book(db, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    if book.current_price <= 0:
        return RedirectResponse(url=f"/books/{book.id}/download", status_code=303)
    request.session["mpesa_book_id"] = book.id
    return RedirectResponse(url="/payments/mpesa", status_code=303)


@router.get("/payments/mpesa")
def mpesa_payment_page(request: Request, db: Session = Depends(get_db)):
    book_id = request.session.get("mpesa_book_id")
    book = active_book(db, book_id) if isinstance(book_id, int) else None
    if book is None or book.current_price <= 0:
        raise HTTPException(
            status_code=403, detail="Choose a paid book before opening M-Pesa checkout."
        )
    payment = session_payment(request, db, book.id)
    payment_state = None
    if payment is not None:
        payment_state = {
            "status": payment.status,
            "reason": payment_reason(payment),
            "phone_number": payment.phone_number,
            "download_url": (
                f"/books/{book.id}/download" if payment.status == "paid" else ""
            ),
        }
    return templates.TemplateResponse(
        request, "mpesa_payment.html", {"book": book, "payment_state": payment_state}
    )


@router.post("/payments/mpesa")
async def request_mpesa_payment(
    request: Request, payload: MpesaPaymentRequest, db: Session = Depends(get_db)
):
    book_id = request.session.get("mpesa_book_id")
    book = active_book(db, book_id) if isinstance(book_id, int) else None
    if book is None or book.current_price <= 0:
        raise HTTPException(
            status_code=403, detail="Your M-Pesa checkout session has expired."
        )
    # Snapshot the applicable price so a discount cannot change mid-request.
    amount = book.current_price
    try:
        digits = normalize_kenyan_phone(payload.phone_number)
    except ValueError:
        return JSONResponse(
            {
                "ok": False,
                "message": "Enter a valid number, e.g. 0712 345 678.",
            },
            status_code=422,
        )
    try:
        result = await initiate_stk_push(
            phone=digits,
            amount=amount,
            account_reference=f"BOOK-{book.id}",
            description=f"Ebook {book.id}",
        )
    except MpesaApiError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=502)
    except (RuntimeError, httpx.HTTPError, KeyError):
        return JSONResponse(
            {
                "ok": False,
                "message": "We could not contact M-Pesa. Please try again shortly.",
            },
            status_code=502,
        )

    request.session["mpesa_phone"] = digits
    checkout_request_id = result.get("CheckoutRequestID")
    if not checkout_request_id:
        return JSONResponse(
            {
                "ok": False,
                "message": "M-Pesa did not accept this request. Please try again.",
            },
            status_code=502,
        )
    payment = MpesaPayment(
        book_id=book.id,
        phone_number=digits,
        checkout_request_id=checkout_request_id,
        merchant_request_id=result.get("MerchantRequestID"),
        amount=amount,
    )
    db.add(payment)
    db.commit()
    request.session["mpesa_checkout_request_id"] = checkout_request_id
    message = result.get(
        "CustomerMessage", "Check your phone and enter your M-Pesa PIN."
    )
    return {"ok": True, "message": message, "status": "pending"}


@router.get("/payments/mpesa/status")
async def mpesa_payment_status(
    request: Request, db: Session = Depends(get_db)
) -> dict[str, str]:
    """Return only the payment status belonging to this browser session."""
    book_id = request.session.get("mpesa_book_id")
    if not isinstance(book_id, int):
        return {"status": "none"}
    payment = session_payment(request, db, book_id)
    if payment is None:
        return {"status": "none"}
    return {
        "status": payment.status,
        "reason": payment_reason(payment),
        "download_url": (
            f"/books/{payment.book_id}/download" if payment.status == "paid" else ""
        ),
    }


@router.post("/payments/mpesa/callback", include_in_schema=False)
async def mpesa_callback(
    payload: dict, db: Session = Depends(get_db)
) -> dict[str, int]:
    """Receive Daraja's asynchronous STK result and retain it for fulfilment."""
    callback = payload.get("Body", {}).get("stkCallback", {})
    checkout_request_id = callback.get("CheckoutRequestID")
    if not checkout_request_id:
        return {"ResultCode": 0}
    payment = db.scalar(
        select(MpesaPayment).where(
            MpesaPayment.checkout_request_id == checkout_request_id
        )
    )
    if payment is not None:
        payment.status = "paid" if callback.get("ResultCode") == 0 else "failed"
        payment.callback_payload = json.dumps(payload)
        record_paid_sale(db, payment)
        db.commit()

    return {"ResultCode": 0}


@router.get("/books/{book_id}/download")
def download_book(
    book_id: int, request: Request, db: Session = Depends(get_db)
) -> FileResponse:
    book = active_book(db, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    if book.current_price > 0:
        checkout_request_id = request.session.get("mpesa_checkout_request_id")
        payment = db.scalar(
            select(MpesaPayment).where(
                MpesaPayment.checkout_request_id == checkout_request_id,
                MpesaPayment.book_id == book.id,
                MpesaPayment.status == "paid",
            )
        )
        if payment is None:
            raise HTTPException(
                status_code=403,
                detail="Complete checkout before downloading this book.",
            )
    if not book.pdf_path:
        raise HTTPException(
            status_code=404, detail="This book does not have a downloadable PDF."
        )

    # Only use the stored filename, so a database value cannot escape PDF_DIR.
    pdf_file = PDF_DIR / book.pdf_path.rsplit("/", 1)[-1]
    if not pdf_file.is_file():
        raise HTTPException(status_code=404, detail="The book PDF is unavailable.")
    if book.current_price <= 0:
        db.add(Sale(book_id=book.id, seller_email=book.uploader_email or get_settings().master_admin_email, amount=0, sale_type="free_download"))
        db.commit()
    return FileResponse(
        pdf_file, media_type="application/pdf", filename=f"{book.title}.pdf"
    )


@router.get("/books/{book_id}")
def book_detail(book_id: int, request: Request, db: Session = Depends(get_db)):
    """Canonical, shareable public page for one book."""
    book = active_book(db, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    return templates.TemplateResponse(request, "book_detail.html", {"book": book})


@router.get("/admin/login")
def admin_login(request: Request, db: Session = Depends(get_db)):
    if current_admin_email(request, db):
        return RedirectResponse(url="/admin/books/new", status_code=303)
    return templates.TemplateResponse(request, "admin_login.html")


@router.get("/admin/login/google")
def admin_google_login(request: Request, next_url: str = "/admin/books/new"):
    return RedirectResponse(url=google_login_url(request, next_url), status_code=303)


@router.get("/admin/auth/google/callback", name="google_auth_callback")
async def google_auth_callback(request: Request, db: Session = Depends(get_db)):
    email = await google_email_from_callback(request)
    next_url = request.session.pop("google_oauth_next", "/admin/books/new")
    if not is_admin_email(email, db):
        request.session.pop("admin_email", None)
        return templates.TemplateResponse(request, "admin_login.html", {"error": "This Google account is not authorised to upload materials."}, status_code=403)
    request.session["admin_email"] = email
    return RedirectResponse(url=next_url, status_code=303)


@router.post("/admin/logout")
def admin_logout(request: Request):
    request.session.pop("admin_email", None)
    return RedirectResponse(url="/admin/login", status_code=303)


@router.get("/admin/register")
def admin_registration_page(request: Request, db: Session = Depends(get_db)):
    email = current_admin_email(request, db)
    if email != get_settings().master_admin_email:
        return RedirectResponse(url="/admin/login", status_code=303)
    admins = list(db.scalars(select(AdminUser).order_by(AdminUser.email)))
    return templates.TemplateResponse(request, "admin_register.html", {"admins": admins})


@router.post("/admin/register")
def register_admin(request: Request, email: str = Form(...), db: Session = Depends(get_db)):
    if current_admin_email(request, db) != get_settings().master_admin_email:
        raise HTTPException(status_code=403, detail="Only the master administrator can authorise uploaders.")
    email = normalized_email(email)
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    if email != get_settings().master_admin_email and db.scalar(select(AdminUser).where(AdminUser.email == email)) is None:
        db.add(AdminUser(email=email))
        db.commit()
    return RedirectResponse(url="/admin/register", status_code=303)


@router.get("/admin/books/new")
def new_book_form(request: Request, db: Session = Depends(get_db)):
    admin_email = current_admin_email(request, db)
    if admin_email is None:
        return RedirectResponse(url="/admin/login", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin_new_book.html",
        {"admin_email": admin_email, "is_master_admin": admin_email == get_settings().master_admin_email},
    )


@router.get("/admin/dashboard")
def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    admin_email = current_admin_email(request, db)
    if admin_email is None:
        return RedirectResponse(url="/admin/login", status_code=303)
    # Reconcile completed payments that were paid before sales recording was
    # introduced or whose callback was retried after an interrupted request.
    reconciled = False
    for payment in db.scalars(select(MpesaPayment).where(MpesaPayment.status == "paid")):
        reconciled = record_paid_sale(db, payment) or reconciled
    if reconciled:
        db.commit()

    book_filter = Ebook.deleted_at.is_(None)
    if admin_email != get_settings().master_admin_email:
        book_filter = book_filter & (Ebook.uploader_email == admin_email)
    books = list(db.scalars(select(Ebook).where(book_filter).order_by(Ebook.created_at.desc(), Ebook.id.desc())))
    book_count = db.scalar(select(func.count(Ebook.id)).where(Ebook.uploader_email == admin_email)) or 0
    sale_count, revenue = db.execute(select(func.count(Sale.id), func.coalesce(func.sum(Sale.amount), 0)).where(Sale.seller_email == admin_email)).one()
    sales = list(db.execute(select(Sale, Ebook.title).join(Ebook, Ebook.id == Sale.book_id).where(Sale.seller_email == admin_email).order_by(Sale.created_at.desc(), Sale.id.desc()).limit(50)))
    return templates.TemplateResponse(request, "admin_dashboard.html", {"admin_email": admin_email, "books": books, "book_count": book_count, "sale_count": sale_count, "revenue": revenue, "sales": sales, "is_master_admin": admin_email == get_settings().master_admin_email})


@router.post("/admin/books/{book_id}/delete")
def soft_delete_book(book_id: int, request: Request, db: Session = Depends(get_db)):
    """Remove a book from the storefront while retaining it for recovery."""
    admin_email = current_admin_email(request, db)
    if admin_email is None:
        raise HTTPException(status_code=401, detail="Sign in to delete this book.")
    book = active_book(db, book_id)
    if book is None or not can_manage_book(book, admin_email):
        raise HTTPException(status_code=404, detail="Book not found")
    book.deleted_at = datetime.now()
    book.deleted_by = admin_email
    db.commit()
    return RedirectResponse(url="/admin/dashboard", status_code=303)


@router.get("/admin/trash")
def admin_trash(request: Request, db: Session = Depends(get_db)):
    admin_email = current_admin_email(request, db)
    if admin_email != get_settings().master_admin_email:
        raise HTTPException(status_code=403, detail="Only the master administrator can view the trash.")
    books = list(
        db.scalars(
            select(Ebook)
            .where(Ebook.deleted_at.is_not(None))
            .order_by(Ebook.deleted_at.desc(), Ebook.id.desc())
        )
    )
    return templates.TemplateResponse(request, "admin_trash.html", {"books": books})


@router.post("/admin/trash/{book_id}/restore")
def restore_book(book_id: int, request: Request, db: Session = Depends(get_db)):
    admin_email = current_admin_email(request, db)
    if admin_email != get_settings().master_admin_email:
        raise HTTPException(status_code=403, detail="Only the master administrator can restore books.")
    book = db.scalar(select(Ebook).where(Ebook.id == book_id, Ebook.deleted_at.is_not(None)))
    if book is None:
        raise HTTPException(status_code=404, detail="Deleted book not found")
    book.deleted_at = None
    book.deleted_by = None
    db.commit()
    return RedirectResponse(url="/admin/trash", status_code=303)


@router.post("/admin/trash/{book_id}/delete-permanently")
def permanently_delete_book(book_id: int, request: Request, db: Session = Depends(get_db)):
    admin_email = current_admin_email(request, db)
    if admin_email != get_settings().master_admin_email:
        raise HTTPException(status_code=403, detail="Only the master administrator can permanently delete books.")
    book = db.scalar(select(Ebook).where(Ebook.id == book_id, Ebook.deleted_at.is_not(None)))
    if book is None:
        raise HTTPException(status_code=404, detail="Deleted book not found")
    pdf_path, thumbnail_path = book.pdf_path, book.thumbnail_path
    # A permanent deletion removes records that cannot remain without the book.
    db.execute(delete(Sale).where(Sale.book_id == book.id))
    db.execute(delete(MpesaPayment).where(MpesaPayment.book_id == book.id))
    db.delete(book)
    db.commit()
    if pdf_path and thumbnail_path:
        remove_saved_upload(pdf_path, thumbnail_path)
    elif pdf_path:
        (PDF_DIR / Path(pdf_path).name).unlink(missing_ok=True)
    elif thumbnail_path:
        (MEDIA_DIR / "thumbnails" / Path(thumbnail_path).name).unlink(missing_ok=True)
    return RedirectResponse(url="/admin/trash", status_code=303)


@router.get("/admin/books/{book_id}/pricing")
def edit_book_pricing(book_id: int, request: Request, db: Session = Depends(get_db)):
    admin_email = current_admin_email(request, db)
    book = active_book(db, book_id)
    if admin_email is None:
        return RedirectResponse(url="/admin/login", status_code=303)
    if book is None or not can_manage_book(book, admin_email):
        raise HTTPException(status_code=404, detail="Book not found")
    return templates.TemplateResponse(request, "admin_book_pricing.html", {"book": book})


@router.post("/admin/books/{book_id}/pricing")
def update_book_pricing(book_id: int, request: Request, price: float = Form(...), discount_enabled: bool = Form(False), discount_amount: float = Form(0), discount_ends_at: str = Form(""), db: Session = Depends(get_db)):
    admin_email = current_admin_email(request, db)
    book = active_book(db, book_id)
    if admin_email is None:
        raise HTTPException(status_code=401, detail="Sign in to edit this book.")
    if book is None or not can_manage_book(book, admin_email):
        raise HTTPException(status_code=404, detail="Book not found")
    if price < 0:
        raise HTTPException(status_code=422, detail="Price cannot be negative.")
    ends_at = None
    if discount_enabled:
        try:
            ends_at = datetime.fromisoformat(discount_ends_at)
        except ValueError:
            raise HTTPException(status_code=422, detail="Choose when the discount ends.")
        if discount_amount <= 0 or discount_amount >= price:
            raise HTTPException(status_code=422, detail="Discount must be greater than zero and less than the normal price.")
        if ends_at <= datetime.now():
            raise HTTPException(status_code=422, detail="Discount end time must be in the future.")
    book.price = price
    book.discount_enabled = discount_enabled
    book.discount_amount = discount_amount if discount_enabled else 0
    book.discount_ends_at = ends_at
    db.commit()
    return RedirectResponse(url="/admin/dashboard", status_code=303)


@router.post("/admin/books/new")
async def create_book_from_upload(
    request: Request,
    title: str = Form(""),
    author: str = Form(...),
    description: str = Form(...),
    details: str = Form(...),
    price: float = Form(...),
    pdf: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    admin_email = current_admin_email(request, db)
    if admin_email is None:
        raise HTTPException(status_code=401, detail="Sign in with an authorised Google account to upload materials.")
    derived_title = Path(pdf.filename or "").stem.strip()
    final_title = title.strip() or derived_title
    if not final_title:
        raise HTTPException(
            status_code=422, detail="A book title or PDF filename is required."
        )

    content_hash = await hash_pdf_upload(pdf)
    existing_book = db.scalar(select(Ebook).where(Ebook.content_hash == content_hash))
    if existing_book is not None:
        await pdf.close()
        raise HTTPException(
            status_code=409,
            detail=f"This PDF is already in the library as '{existing_book.title}'.",
        )

    pdf_path, thumbnail_path = await save_pdf_and_thumbnail(pdf)
    book = Ebook(
        title=final_title[:255],
        author=author.strip(),
        description=description.strip(),
        details=details.strip(),
        price=price,
        pdf_path=pdf_path,
        thumbnail_path=thumbnail_path,
        content_hash=content_hash,
        uploader_email=admin_email,
    )
    db.add(book)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        remove_saved_upload(pdf_path, thumbnail_path)
        raise HTTPException(status_code=409, detail="This PDF was uploaded by another user moments ago.")
    return templates.TemplateResponse(
        request,
        "admin_new_book.html",
        {"created": book, "admin_email": admin_email, "is_master_admin": admin_email == get_settings().master_admin_email},
    )
