from pathlib import Path
import hashlib
from uuid import uuid4

import pymupdf
from fastapi import HTTPException, UploadFile, status

BASE_DIR = Path(__file__).resolve().parents[1]
MEDIA_DIR = BASE_DIR / "media"
PDF_DIR = MEDIA_DIR / "ebooks"
THUMBNAIL_DIR = MEDIA_DIR / "thumbnails"
MAX_PDF_SIZE = 50 * 1024 * 1024  # 50 MB
THUMBNAIL_WIDTH = 480
MAX_THUMBNAIL_SCALE = 1.75


async def hash_pdf_upload(upload: UploadFile) -> str:
    """Validate and calculate a SHA-256 fingerprint without saving the PDF."""
    if not upload.filename or Path(upload.filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")
    await upload.seek(0)
    digest = hashlib.sha256()
    size = 0
    first_bytes = b""
    while chunk := await upload.read(1024 * 1024):
        if not first_bytes:
            first_bytes = chunk[:5]
        size += len(chunk)
        if size > MAX_PDF_SIZE:
            raise HTTPException(status_code=413, detail="PDF files may be at most 50 MB.")
        digest.update(chunk)
    await upload.seek(0)
    if first_bytes != b"%PDF-":
        raise HTTPException(status_code=400, detail="The uploaded file is not a valid PDF.")
    return digest.hexdigest()


def remove_saved_upload(pdf_path: str, thumbnail_path: str) -> None:
    """Remove assets created for an upload that could not be stored in the DB."""
    (PDF_DIR / Path(pdf_path).name).unlink(missing_ok=True)
    (THUMBNAIL_DIR / Path(thumbnail_path).name).unlink(missing_ok=True)


async def save_pdf_and_thumbnail(upload: UploadFile) -> tuple[str, str]:
    """Store a PDF and render its first page as a JPEG thumbnail."""
    if not upload.filename or Path(upload.filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    pdf_file = PDF_DIR / f"{token}.pdf"
    thumbnail_file = THUMBNAIL_DIR / f"{token}.jpg"

    try:
        first_bytes = await upload.read(5)
        if first_bytes != b"%PDF-":
            raise HTTPException(status_code=400, detail="The uploaded file is not a valid PDF.")

        size = len(first_bytes)
        with pdf_file.open("wb") as output:
            output.write(first_bytes)
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_PDF_SIZE:
                    raise HTTPException(status_code=413, detail="PDF files may be at most 50 MB.")
                output.write(chunk)

        document = pymupdf.open(pdf_file)
        try:
            if document.page_count == 0:
                raise ValueError("PDF contains no pages")
            first_page = document[0]
            # Render a cover-sized image instead of a full-resolution page. This
            # keeps uploads and responsive listing pages lightweight.
            scale = min(MAX_THUMBNAIL_SCALE, THUMBNAIL_WIDTH / first_page.rect.width)
            pixmap = first_page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            pixmap.save(thumbnail_file)
        finally:
            document.close()
    except HTTPException:
        pdf_file.unlink(missing_ok=True)
        raise
    except Exception as exc:
        pdf_file.unlink(missing_ok=True)
        thumbnail_file.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="We could not read the uploaded PDF.",
        ) from exc
    finally:
        await upload.close()

    # PDFs are private files. Store only the server-side filename; public covers
    # use the media URL because they are intentionally mounted as static assets.
    return (pdf_file.name, f"/media/thumbnails/{thumbnail_file.name}")
