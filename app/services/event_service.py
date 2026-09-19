from sqlalchemy.orm import Session
from typing import Optional
from fastapi import HTTPException
from app.repositories.event_repository import EventRepository
from app.schemas.event import EventCreate, EventUpdate


class EventService:
    def __init__(self, db: Session):
        self.repo = EventRepository(db)

    def get_all(self, page: int = 1, page_size: int = 20,
                search: Optional[str] = None, sort: Optional[str] = None) -> dict:
        skip = (page - 1) * page_size
        items = self.repo.get_all(skip=skip, limit=page_size, search=search, sort=sort)
        total = self.repo.count(search=search)
        return {"items": items, "total": total, "page": page, "page_size": page_size,
                "pages": max(1, (total + page_size - 1) // page_size)}

    def get_by_id(self, event_id: int):
        event = self.repo.get_by_id(event_id)
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")
        return event

    def create(self, data: EventCreate):
        return self.repo.create(data)

    def update(self, event_id: int, data: EventUpdate):
        event = self.repo.update(event_id, data)
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")
        return event

    def delete(self, event_id: int):
        if not self.repo.delete(event_id):
            raise HTTPException(status_code=404, detail="Event not found")

    def get_bookings(self, event_id: int) -> list:
        self.get_by_id(event_id)
        return self.repo.get_event_bookings(event_id)

    def get_category_stats(self) -> list:
        return self.repo.get_category_stats()
