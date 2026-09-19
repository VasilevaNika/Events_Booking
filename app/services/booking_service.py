from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime
from fastapi import HTTPException
from app.repositories.booking_repository import BookingRepository
from app.repositories.event_repository import EventRepository
from app.repositories.user_repository import UserRepository
from app.schemas.booking import BookingCreate, BookingUpdate


class BookingService:
    def __init__(self, db: Session):
        self.repo = BookingRepository(db)
        self.event_repo = EventRepository(db)
        self.user_repo = UserRepository(db)

    def get_all(self, page: int = 1, page_size: int = 20,
                status: Optional[str] = None,
                from_date: Optional[datetime] = None,
                to_date: Optional[datetime] = None,
                sort: Optional[str] = None) -> dict:
        skip = (page - 1) * page_size
        items = self.repo.get_all(skip=skip, limit=page_size, status=status,
                                  from_date=from_date, to_date=to_date, sort=sort)
        total = self.repo.count(status=status, from_date=from_date, to_date=to_date)
        return {"items": items, "total": total, "page": page, "page_size": page_size,
                "pages": max(1, (total + page_size - 1) // page_size)}

    def get_by_id(self, booking_id: int):
        booking = self.repo.get_by_id(booking_id)
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")
        return booking

    def get_by_user(self, user_id: int, page: int = 1, page_size: int = 20) -> list:
        skip = (page - 1) * page_size
        return self.repo.get_by_user_id(user_id, skip=skip, limit=page_size)

    def create(self, data: BookingCreate):
        if not self.user_repo.get_by_id(data.user_id):
            raise HTTPException(status_code=404, detail="User not found")
        event = self.event_repo.get_by_id(data.event_id)
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")
        total_price = round(event["price"] * data.seats_count, 2)
        return self.repo.create(data, total_price)

    def update(self, booking_id: int, data: BookingUpdate):
        booking = self.repo.update(booking_id, data)
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")
        return booking

    def delete(self, booking_id: int):
        if not self.repo.delete(booking_id):
            raise HTTPException(status_code=404, detail="Booking not found")

    def get_revenue_stats(self) -> list:
        return self.repo.get_revenue_by_event()
