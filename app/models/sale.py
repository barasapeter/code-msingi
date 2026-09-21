from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Sale(Base):
    """A completed paid purchase or successful free download."""

    __tablename__ = "sales"
    __table_args__ = (UniqueConstraint("payment_id", name="uq_sales_payment_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("ebooks.id"), index=True)
    seller_email: Mapped[str] = mapped_column(String(320), index=True)
    amount: Mapped[float] = mapped_column(Float, default=0)
    sale_type: Mapped[str] = mapped_column(String(30))
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("mpesa_payments.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
