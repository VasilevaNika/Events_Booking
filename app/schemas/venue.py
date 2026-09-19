from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class VenueCreate(BaseModel):
    name: str
    address: str
    capacity: int
    description: Optional[str] = None


class VenueUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    capacity: Optional[int] = None
    description: Optional[str] = None


class VenueResponse(BaseModel):
    id: int
    name: str
    address: str
    capacity: int
    description: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}
