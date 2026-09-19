from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional
from app.database import get_db
from app.services.user_service import UserService
from app.services.booking_service import BookingService
from app.schemas.user import UserCreate, UserUpdate

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("")
def get_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    db: Session = Depends(get_db),
):
    return UserService(db).get_all(page=page, page_size=page_size, search=search)


@router.get("/{user_id}")
def get_user(user_id: int, db: Session = Depends(get_db)):
    return UserService(db).get_by_id(user_id)


@router.get("/{user_id}/stats")
def get_user_stats(user_id: int, db: Session = Depends(get_db)):
    return UserService(db).get_stats(user_id)


@router.get("/{user_id}/bookings")
def get_user_bookings(
    user_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return BookingService(db).get_by_user(user_id, page=page, page_size=page_size)


@router.post("", status_code=201)
def create_user(data: UserCreate, db: Session = Depends(get_db)):
    return UserService(db).create(data)


@router.put("/{user_id}")
def update_user(user_id: int, data: UserUpdate, db: Session = Depends(get_db)):
    return UserService(db).update(user_id, data)


@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: int, db: Session = Depends(get_db)):
    UserService(db).delete(user_id)
