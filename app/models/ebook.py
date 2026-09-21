from datetime import datetime

from sqlalchemy import DateTime, Float, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Ebook(Base):
    __tablename__ = "ebooks"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    author: Mapped[str] = mapped_column(String(255), index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[float] = mapped_column(Float)
    pdf_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    thumbnail_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    uploader_email: Mapped[str | None] = mapped_column(String(320), index=True, nullable=True)
    discount_enabled: Mapped[bool] = mapped_column(default=False)
    discount_amount: Mapped[float] = mapped_column(Float, default=0)
    discount_ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    deleted_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    @property
    def active_discount(self) -> bool:
        return bool(self.discount_enabled and self.discount_amount > 0 and self.discount_amount < self.price and self.discount_ends_at is not None and self.discount_ends_at > datetime.now())

    @property
    def current_price(self) -> float:
        return self.price - self.discount_amount if self.active_discount else self.price
