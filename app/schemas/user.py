from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class UserCreate(BaseModel):
    email: str
    name: str
    phone: Optional[str] = None


class UserUpdate(BaseModel):
    email: Optional[str] = None
    name: Optional[str] = None
    phone: Optional[str] = None


class UserResponse(BaseModel):
    id: int
    email: str
    name: str
    phone: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}
