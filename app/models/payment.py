from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MpesaPayment(Base):
    __tablename__ = "mpesa_payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("ebooks.id"), index=True)
    phone_number: Mapped[str] = mapped_column(String(12))
    checkout_request_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    merchant_request_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    amount: Mapped[float | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    callback_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
