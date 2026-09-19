from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List, Optional
from datetime import datetime
from app.models.booking import Booking
from app.schemas.booking import BookingCreate, BookingUpdate


class BookingRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_all(
        self,
        skip: int = 0,
        limit: int = 20,
        status: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        sort: Optional[str] = None,
    ) -> List[dict]:
        conditions = ["1=1"]
        params: dict = {"limit": limit, "skip": skip}

        if status:
            conditions.append("b.status = :status")
            params["status"] = status
        if from_date:
            conditions.append("b.created_at >= :from_date")
            params["from_date"] = from_date
        if to_date:
            conditions.append("b.created_at <= :to_date")
            params["to_date"] = to_date

        where = "WHERE " + " AND ".join(conditions)

        allowed_sort = {"created_at", "total_price", "seats_count"}
        order = "ORDER BY b.created_at DESC"
        if sort:
            col = sort.lstrip("-")
            if col in allowed_sort:
                direction = "DESC" if sort.startswith("-") else "ASC"
                order = f"ORDER BY b.{col} {direction}"

        # JOIN query: bookings with full context
        rows = self.db.execute(text(f"""
            SELECT
                b.id, b.user_id, b.event_id, b.status, b.seats_count,
                b.total_price, b.created_at, b.updated_at,
                u.name  AS user_name,
                u.email AS user_email,
                e.title AS event_title,
                e.start_time AS event_start_time,
                v.name  AS venue_name
            FROM bookings b
            JOIN users  u ON b.user_id  = u.id
            JOIN events e ON b.event_id = e.id
            JOIN venues v ON e.venue_id = v.id
            {where}
            {order}
            LIMIT :limit OFFSET :skip
        """), params).fetchall()
        return [dict(r._mapping) for r in rows]

    def count(
        self,
        status: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
    ) -> int:
        query = self.db.query(Booking)
        if status:
            query = query.filter(Booking.status == status)
        if from_date:
            query = query.filter(Booking.created_at >= from_date)
        if to_date:
            query = query.filter(Booking.created_at <= to_date)
        return query.count()

    def get_by_id(self, booking_id: int) -> Optional[dict]:
        row = self.db.execute(text("""
            SELECT
                b.id, b.user_id, b.event_id, b.status, b.seats_count,
                b.total_price, b.created_at, b.updated_at,
                u.name  AS user_name,
                u.email AS user_email,
                e.title AS event_title,
                e.start_time AS event_start_time,
                v.name  AS venue_name
            FROM bookings b
            JOIN users  u ON b.user_id  = u.id
            JOIN events e ON b.event_id = e.id
            JOIN venues v ON e.venue_id = v.id
            WHERE b.id = :booking_id
        """), {"booking_id": booking_id}).fetchone()
        return dict(row._mapping) if row else None

    def get_by_user_id(self, user_id: int, skip: int = 0, limit: int = 20) -> List[dict]:
        rows = self.db.execute(text("""
            SELECT
                b.id, b.user_id, b.event_id, b.status, b.seats_count,
                b.total_price, b.created_at, b.updated_at,
                e.title      AS event_title,
                e.start_time AS event_start_time,
                v.name       AS venue_name
            FROM bookings b
            JOIN events e ON b.event_id = e.id
            JOIN venues v ON e.venue_id = v.id
            WHERE b.user_id = :user_id
            ORDER BY b.created_at DESC
            LIMIT :limit OFFSET :skip
        """), {"user_id": user_id, "limit": limit, "skip": skip}).fetchall()
        return [dict(r._mapping) for r in rows]

    def get_revenue_by_event(self) -> List[dict]:
        # Aggregation query: revenue per event
        rows = self.db.execute(text("""
            SELECT
                e.id         AS event_id,
                e.title      AS event_title,
                e.start_time,
                v.name       AS venue_name,
                COUNT(b.id)           AS total_bookings,
                SUM(b.seats_count)    AS total_seats_booked,
                COALESCE(SUM(b.total_price), 0)  AS total_revenue,
                COALESCE(AVG(b.total_price), 0)  AS avg_booking_price
            FROM events e
            JOIN venues v ON e.venue_id = v.id
            LEFT JOIN bookings b ON e.id = b.event_id AND b.status != 'cancelled'
            GROUP BY e.id, e.title, e.start_time, v.name
            ORDER BY total_revenue DESC NULLS LAST
        """)).fetchall()
        return [dict(r._mapping) for r in rows]

    def create(self, data: BookingCreate, total_price: float) -> Booking:
        booking = Booking(
            user_id=data.user_id,
            event_id=data.event_id,
            seats_count=data.seats_count,
            total_price=total_price,
            status="confirmed",
        )
        self.db.add(booking)
        self.db.commit()
        self.db.refresh(booking)
        return booking

    def update(self, booking_id: int, data: BookingUpdate) -> Optional[Booking]:
        booking = self.db.query(Booking).filter(Booking.id == booking_id).first()
        if not booking:
            return None
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(booking, key, value)
        self.db.commit()
        self.db.refresh(booking)
        return booking

    def delete(self, booking_id: int) -> bool:
        booking = self.db.query(Booking).filter(Booking.id == booking_id).first()
        if not booking:
            return False
        self.db.delete(booking)
        self.db.commit()
        return True
