from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class EbookCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    author: str = Field(min_length=1, max_length=255)
    description: str | None = None
    details: str | None = None
    price: float = Field(ge=0)


class EbookRead(EbookCreate):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
