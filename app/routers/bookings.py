from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime
from app.database import get_db, get_read_db
from app.services.booking_service import BookingService
from app.schemas.booking import BookingCreate, BookingUpdate

router = APIRouter(prefix="/api/bookings", tags=["bookings"])


# Лабораторная №4 — какие чтения уходят на реплику.
#
# REPLICA:  /stats/revenue — агрегация по 5 млн бронирований. Тяжёлый запрос,
#           который незачем выполнять на сервере, принимающем записи.
#           Отчёт о выручке, отстающий на доли секунды, остаётся верным отчётом.
# PRIMARY:  список, карточка и всё, что пишет. Пользователь смотрит
#           бронирование сразу после оформления — реплика могла его ещё
#           не получить.


@router.get("")
def get_bookings(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    sort: Optional[str] = None,
    db: Session = Depends(get_db),
):
    return BookingService(db).get_all(
        page=page, page_size=page_size,
        status=status, from_date=from_date, to_date=to_date, sort=sort,
    )


@router.get("/stats/revenue")
def get_revenue_stats(db: Session = Depends(get_read_db)):
    return BookingService(db).get_revenue_stats()


@router.get("/{booking_id}")
def get_booking(booking_id: int, db: Session = Depends(get_db)):
    return BookingService(db).get_by_id(booking_id)


@router.post("", status_code=201)
def create_booking(data: BookingCreate, db: Session = Depends(get_db)):
    return BookingService(db).create(data)


@router.put("/{booking_id}")
def update_booking(booking_id: int, data: BookingUpdate, db: Session = Depends(get_db)):
    return BookingService(db).update(booking_id, data)


@router.delete("/{booking_id}", status_code=204)
def delete_booking(booking_id: int, db: Session = Depends(get_db)):
    BookingService(db).delete(booking_id)
