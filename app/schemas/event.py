from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List


class EventCreate(BaseModel):
    title: str
    description: Optional[str] = None
    venue_id: int
    start_time: datetime
    end_time: datetime
    price: float = 0.0
    max_capacity: int
    category_ids: Optional[List[int]] = []


class EventUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    venue_id: Optional[int] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    price: Optional[float] = None
    max_capacity: Optional[int] = None
    category_ids: Optional[List[int]] = None


class EventResponse(BaseModel):
    id: int
    title: str
    description: Optional[str] = None
    venue_id: int
    start_time: datetime
    end_time: datetime
    price: float
    max_capacity: int
    created_at: datetime

    model_config = {"from_attributes": True}
