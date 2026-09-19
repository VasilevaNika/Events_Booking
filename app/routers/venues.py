from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional
from app.database import get_db
from app.services.venue_service import VenueService
from app.schemas.venue import VenueCreate, VenueUpdate

router = APIRouter(prefix="/api/venues", tags=["venues"])


@router.get("")
def get_venues(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    db: Session = Depends(get_db),
):
    return VenueService(db).get_all(page=page, page_size=page_size, search=search)


@router.get("/{venue_id}")
def get_venue(venue_id: int, db: Session = Depends(get_db)):
    return VenueService(db).get_by_id(venue_id)


@router.post("", status_code=201)
def create_venue(data: VenueCreate, db: Session = Depends(get_db)):
    return VenueService(db).create(data)


@router.put("/{venue_id}")
def update_venue(venue_id: int, data: VenueUpdate, db: Session = Depends(get_db)):
    return VenueService(db).update(venue_id, data)


@router.delete("/{venue_id}", status_code=204)
def delete_venue(venue_id: int, db: Session = Depends(get_db)):
    VenueService(db).delete(venue_id)
