from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class BookingCreate(BaseModel):
    user_id: int
    event_id: int
    seats_count: int = 1


class BookingUpdate(BaseModel):
    status: Optional[str] = None
    seats_count: Optional[int] = None


class BookingResponse(BaseModel):
    id: int
    user_id: int
    event_id: int
    status: str
    seats_count: int
    total_price: float
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
