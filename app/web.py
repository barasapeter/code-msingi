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
from app.models.payment import MpesaPayment
from app.services.pdf_uploads import MEDIA_DIR, PDF_DIR, save_pdf_and_thumbnail
from app.services.mpesa import MpesaApiError, initiate_stk_push, normalize_kenyan_phone

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


@router.get("/admin/books/new")
def new_book_form(request: Request):
    return templates.TemplateResponse(request, "admin_new_book.html")


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
    return templates.TemplateResponse(request, "admin_new_book.html", {"created": book})
