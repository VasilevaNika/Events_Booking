from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List, Optional
from app.models.user import User
from app.schemas.user import UserCreate, UserUpdate


class UserRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_all(self, skip: int = 0, limit: int = 20, search: Optional[str] = None) -> List[User]:
        query = self.db.query(User)
        if search:
            query = query.filter(
                User.name.ilike(f"%{search}%") | User.email.ilike(f"%{search}%")
            )
        return query.offset(skip).limit(limit).all()

    def count(self, search: Optional[str] = None) -> int:
        query = self.db.query(User)
        if search:
            query = query.filter(
                User.name.ilike(f"%{search}%") | User.email.ilike(f"%{search}%")
            )
        return query.count()

    def get_by_id(self, user_id: int) -> Optional[User]:
        return self.db.query(User).filter(User.id == user_id).first()

    def get_by_email(self, email: str) -> Optional[User]:
        return self.db.query(User).filter(User.email == email).first()

    def create(self, data: UserCreate) -> User:
        user = User(**data.model_dump())
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def update(self, user_id: int, data: UserUpdate) -> Optional[User]:
        user = self.get_by_id(user_id)
        if not user:
            return None
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(user, key, value)
        self.db.commit()
        self.db.refresh(user)
        return user

    def delete(self, user_id: int) -> bool:
        user = self.get_by_id(user_id)
        if not user:
            return False
        self.db.delete(user)
        self.db.commit()
        return True

    def get_user_stats(self, user_id: int) -> Optional[dict]:
        # Aggregation query: booking stats per user
        result = self.db.execute(text("""
            SELECT
                u.id,
                u.name,
                u.email,
                COUNT(b.id)                             AS total_bookings,
                COALESCE(SUM(b.total_price), 0)         AS total_spent,
                COALESCE(AVG(b.total_price), 0)         AS avg_booking_price
            FROM users u
            LEFT JOIN bookings b ON u.id = b.user_id AND b.status != 'cancelled'
            WHERE u.id = :user_id
            GROUP BY u.id, u.name, u.email
        """), {"user_id": user_id}).fetchone()
        return dict(result._mapping) if result else None
