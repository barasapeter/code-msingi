from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.ebook import Ebook
from app.schemas.ebook import EbookCreate, EbookRead

router = APIRouter()


@router.get("/", response_model=list[EbookRead])
def list_ebooks(db: Session = Depends(get_db)) -> list[Ebook]:
    return list(db.scalars(select(Ebook).order_by(Ebook.id.desc())))


@router.post("/", response_model=EbookRead, status_code=status.HTTP_201_CREATED)
def create_ebook(payload: EbookCreate, db: Session = Depends(get_db)) -> Ebook:
    ebook = Ebook(**payload.model_dump())
    db.add(ebook)
    db.commit()
    db.refresh(ebook)
    return ebook


@router.get("/{ebook_id}", response_model=EbookRead)
def get_ebook(ebook_id: int, db: Session = Depends(get_db)) -> Ebook:
    ebook = db.get(Ebook, ebook_id)
    if ebook is None:
        raise HTTPException(status_code=404, detail="E-book not found")
    return ebook
