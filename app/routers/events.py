from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional
from app.database import get_db, get_read_db
from app.services.event_service import EventService
from app.schemas.event import EventCreate, EventUpdate

router = APIRouter(prefix="/api/events", tags=["events"])


# Лабораторная №4 — какие чтения уходят на реплику.
#
# REPLICA:  каталог мероприятий и статистика категорий. Это самые частые
#           чтения сервиса, и они переживают отставание в доли секунды:
#           если новое мероприятие появится в списке на мгновение позже, никто
#           не пострадает.
# PRIMARY:  карточка /{event_id}. Её открывают сразу после создания или
#           редактирования мероприятия — здесь нужны read-your-writes.


@router.get("")
def get_events(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    sort: Optional[str] = None,
    db: Session = Depends(get_read_db),
):
    return EventService(db).get_all(page=page, page_size=page_size, search=search, sort=sort)


@router.get("/stats/categories")
def get_category_stats(db: Session = Depends(get_read_db)):
    return EventService(db).get_category_stats()


@router.get("/{event_id}")
def get_event(event_id: int, db: Session = Depends(get_db)):
    return EventService(db).get_by_id(event_id)


@router.get("/{event_id}/bookings")
def get_event_bookings(event_id: int, db: Session = Depends(get_db)):
    return EventService(db).get_bookings(event_id)


@router.post("", status_code=201)
def create_event(data: EventCreate, db: Session = Depends(get_db)):
    return EventService(db).create(data)


@router.put("/{event_id}")
def update_event(event_id: int, data: EventUpdate, db: Session = Depends(get_db)):
    return EventService(db).update(event_id, data)


@router.delete("/{event_id}", status_code=204)
def delete_event(event_id: int, db: Session = Depends(get_db)):
    EventService(db).delete(event_id)
