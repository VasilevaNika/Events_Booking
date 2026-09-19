from sqlalchemy.orm import Session
from typing import Optional
from fastapi import HTTPException
from app.repositories.venue_repository import VenueRepository
from app.schemas.venue import VenueCreate, VenueUpdate


class VenueService:
    def __init__(self, db: Session):
        self.repo = VenueRepository(db)

    def get_all(self, page: int = 1, page_size: int = 20, search: Optional[str] = None) -> dict:
        skip = (page - 1) * page_size
        items = self.repo.get_all(skip=skip, limit=page_size, search=search)
        total = self.repo.count(search=search)
        return {"items": items, "total": total, "page": page, "page_size": page_size,
                "pages": max(1, (total + page_size - 1) // page_size)}

    def get_by_id(self, venue_id: int):
        venue = self.repo.get_by_id(venue_id)
        if not venue:
            raise HTTPException(status_code=404, detail="Venue not found")
        return venue

    def create(self, data: VenueCreate):
        return self.repo.create(data)

    def update(self, venue_id: int, data: VenueUpdate):
        venue = self.repo.update(venue_id, data)
        if not venue:
            raise HTTPException(status_code=404, detail="Venue not found")
        return venue

    def delete(self, venue_id: int):
        if not self.repo.delete(venue_id):
            raise HTTPException(status_code=404, detail="Venue not found")
