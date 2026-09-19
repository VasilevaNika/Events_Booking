from sqlalchemy.orm import Session
from typing import List, Optional
from app.models.venue import Venue
from app.schemas.venue import VenueCreate, VenueUpdate


class VenueRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_all(self, skip: int = 0, limit: int = 20, search: Optional[str] = None) -> List[Venue]:
        query = self.db.query(Venue)
        if search:
            query = query.filter(Venue.name.ilike(f"%{search}%"))
        return query.offset(skip).limit(limit).all()

    def count(self, search: Optional[str] = None) -> int:
        query = self.db.query(Venue)
        if search:
            query = query.filter(Venue.name.ilike(f"%{search}%"))
        return query.count()

    def get_by_id(self, venue_id: int) -> Optional[Venue]:
        return self.db.query(Venue).filter(Venue.id == venue_id).first()

    def create(self, data: VenueCreate) -> Venue:
        venue = Venue(**data.model_dump())
        self.db.add(venue)
        self.db.commit()
        self.db.refresh(venue)
        return venue

    def update(self, venue_id: int, data: VenueUpdate) -> Optional[Venue]:
        venue = self.get_by_id(venue_id)
        if not venue:
            return None
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(venue, key, value)
        self.db.commit()
        self.db.refresh(venue)
        return venue

    def delete(self, venue_id: int) -> bool:
        venue = self.get_by_id(venue_id)
        if not venue:
            return False
        self.db.delete(venue)
        self.db.commit()
        return True
