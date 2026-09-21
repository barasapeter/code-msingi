from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.ebook import Ebook
from app.services.pdf_uploads import MEDIA_DIR, PDF_DIR, save_pdf_and_thumbnail
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
router = APIRouter(include_in_schema=False)


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


@router.get("/books/{book_id}/download")
def download_free_book(book_id: int, db: Session = Depends(get_db)) -> FileResponse:
    book = db.get(Ebook, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    if book.price > 0:
        raise HTTPException(status_code=403, detail="Complete checkout before downloading this book.")
    if not book.pdf_path:
        raise HTTPException(status_code=404, detail="This book does not have a downloadable PDF.")

    # Only use the stored filename, so a database value cannot escape PDF_DIR.
    pdf_file = PDF_DIR / book.pdf_path.rsplit("/", 1)[-1]
    if not pdf_file.is_file():
        raise HTTPException(status_code=404, detail="The book PDF is unavailable.")
    return FileResponse(pdf_file, media_type="application/pdf", filename=f"{book.title}.pdf")


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
        raise HTTPException(status_code=422, detail="A book title or PDF filename is required.")

    pdf_path, thumbnail_path = await save_pdf_and_thumbnail(pdf)
    book = Ebook(
        title=final_title[:255], author=author.strip(), description=description.strip(),
        details=details.strip(), price=price, pdf_path=pdf_path, thumbnail_path=thumbnail_path,
    )
    db.add(book)
    db.commit()
    return templates.TemplateResponse(request, "admin_new_book.html", {"created": book})
