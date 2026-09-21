from pathlib import Path
import json

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.ebook import Ebook
from app.models.admin_user import AdminUser
from app.models.payment import MpesaPayment
from app.services.pdf_uploads import MEDIA_DIR, PDF_DIR, save_pdf_and_thumbnail
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


@router.get("/")
def home(request: Request, db: Session = Depends(get_db)):
    books = list(db.scalars(select(Ebook).order_by(Ebook.id.desc())))
    return templates.TemplateResponse(request, "index.html", {"books": books})


@router.get("/books/{book_id}/checkout")
def checkout(book_id: int, request: Request, db: Session = Depends(get_db)):
    book = db.get(Ebook, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    if book.price <= 0:
        return RedirectResponse(url=f"/books/{book.id}/download", status_code=303)
    return templates.TemplateResponse(request, "checkout.html", {"book": book})


@router.post("/books/{book_id}/checkout/mpesa")
def begin_mpesa_checkout(book_id: int, request: Request, db: Session = Depends(get_db)):
    book = db.get(Ebook, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    if book.price <= 0:
        return RedirectResponse(url=f"/books/{book.id}/download", status_code=303)
    request.session["mpesa_book_id"] = book.id
    return RedirectResponse(url="/payments/mpesa", status_code=303)


@router.get("/payments/mpesa")
def mpesa_payment_page(request: Request, db: Session = Depends(get_db)):
    book_id = request.session.get("mpesa_book_id")
    book = db.get(Ebook, book_id) if isinstance(book_id, int) else None
    if book is None or book.price <= 0:
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
    book = db.get(Ebook, book_id) if isinstance(book_id, int) else None
    if book is None or book.price <= 0:
        raise HTTPException(
            status_code=403, detail="Your M-Pesa checkout session has expired."
        )
    try:
        digits = normalize_kenyan_phone(payload.phone_number)
    except ValueError:
        return JSONResponse(
            {
                "ok": False,
                "message": "Enter a valid Kenyan M-Pesa number, e.g. 0712 345 678.",
            },
            status_code=422,
        )
    try:
        result = await initiate_stk_push(
            phone=digits,
            amount=book.price,
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
        db.commit()

    return {"ResultCode": 0}


@router.get("/books/{book_id}/download")
def download_book(
    book_id: int, request: Request, db: Session = Depends(get_db)
) -> FileResponse:
    book = db.get(Ebook, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    if book.price > 0:
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
    return FileResponse(
        pdf_file, media_type="application/pdf", filename=f"{book.title}.pdf"
    )


@router.get("/books/{book_id}")
def book_detail(book_id: int, request: Request, db: Session = Depends(get_db)):
    """Canonical, shareable public page for one book."""
    book = db.get(Ebook, book_id)
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

    pdf_path, thumbnail_path = await save_pdf_and_thumbnail(pdf)
    book = Ebook(
        title=final_title[:255],
        author=author.strip(),
        description=description.strip(),
        details=details.strip(),
        price=price,
        pdf_path=pdf_path,
        thumbnail_path=thumbnail_path,
    )
    db.add(book)
    db.commit()
    return templates.TemplateResponse(
        request,
        "admin_new_book.html",
        {"created": book, "admin_email": admin_email, "is_master_admin": admin_email == get_settings().master_admin_email},
    )
