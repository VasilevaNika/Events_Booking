from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List, Optional
from app.models.event import Event, EventCategory
from app.schemas.event import EventCreate, EventUpdate


class EventRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_all(
        self,
        skip: int = 0,
        limit: int = 20,
        search: Optional[str] = None,
        sort: Optional[str] = None,
    ) -> List[dict]:
        # JOIN query #1: events with venue and aggregated categories
        where = ""
        params: dict = {"limit": limit, "skip": skip}
        if search:
            where = "WHERE e.title ILIKE :search"
            params["search"] = f"%{search}%"

        allowed_sort = {"created_at", "title", "price", "start_time"}
        order = "ORDER BY e.created_at DESC"
        if sort:
            col = sort.lstrip("-")
            if col in allowed_sort:
                direction = "DESC" if sort.startswith("-") else "ASC"
                order = f"ORDER BY e.{col} {direction}"

        sql = f"""
            SELECT
                e.id, e.title, e.description, e.venue_id, e.start_time, e.end_time,
                e.price, e.max_capacity, e.created_at,
                v.name  AS venue_name,
                v.address AS venue_address,
                COALESCE(
                    array_agg(c.name) FILTER (WHERE c.name IS NOT NULL),
                    ARRAY[]::text[]
                ) AS categories
            FROM events e
            JOIN venues v ON e.venue_id = v.id
            LEFT JOIN event_categories ec ON e.id = ec.event_id
            LEFT JOIN categories c ON ec.category_id = c.id
            {where}
            GROUP BY e.id, e.title, e.description, e.venue_id, e.start_time,
                     e.end_time, e.price, e.max_capacity, e.created_at,
                     v.name, v.address
            {order}
            LIMIT :limit OFFSET :skip
        """
        rows = self.db.execute(text(sql), params).fetchall()
        return [dict(r._mapping) for r in rows]

    def count(self, search: Optional[str] = None) -> int:
        query = self.db.query(Event)
        if search:
            query = query.filter(Event.title.ilike(f"%{search}%"))
        return query.count()

    def get_by_id(self, event_id: int) -> Optional[dict]:
        row = self.db.execute(text("""
            SELECT
                e.id, e.title, e.description, e.venue_id, e.start_time, e.end_time,
                e.price, e.max_capacity, e.created_at,
                v.name  AS venue_name,
                v.address AS venue_address,
                COALESCE(
                    array_agg(c.name) FILTER (WHERE c.name IS NOT NULL),
                    ARRAY[]::text[]
                ) AS categories
            FROM events e
            JOIN venues v ON e.venue_id = v.id
            LEFT JOIN event_categories ec ON e.id = ec.event_id
            LEFT JOIN categories c ON ec.category_id = c.id
            WHERE e.id = :event_id
            GROUP BY e.id, e.title, e.description, e.venue_id, e.start_time,
                     e.end_time, e.price, e.max_capacity, e.created_at,
                     v.name, v.address
        """), {"event_id": event_id}).fetchone()
        return dict(row._mapping) if row else None

    def get_event_bookings(self, event_id: int) -> List[dict]:
        # JOIN query #2: bookings for an event with user info
        rows = self.db.execute(text("""
            SELECT
                b.id, b.status, b.seats_count, b.total_price, b.created_at,
                u.id    AS user_id,
                u.name  AS user_name,
                u.email AS user_email
            FROM bookings b
            JOIN users u ON b.user_id = u.id
            WHERE b.event_id = :event_id
            ORDER BY b.created_at DESC
        """), {"event_id": event_id}).fetchall()
        return [dict(r._mapping) for r in rows]

    def get_category_stats(self) -> List[dict]:
        rows = self.db.execute(text("""
            SELECT
                c.id   AS category_id,
                c.name AS category_name,
                COUNT(DISTINCT e.id)  AS events_count,
                AVG(e.price)          AS avg_price,
                MIN(e.price)          AS min_price,
                MAX(e.price)          AS max_price
            FROM categories c
            LEFT JOIN event_categories ec ON c.id = ec.category_id
            LEFT JOIN events e ON ec.event_id = e.id
            GROUP BY c.id, c.name
            ORDER BY events_count DESC
        """)).fetchall()
        return [dict(r._mapping) for r in rows]

    def create(self, data: EventCreate) -> Event:
        category_ids = data.category_ids or []
        event_data = data.model_dump(exclude={"category_ids"})
        event = Event(**event_data)
        self.db.add(event)
        self.db.flush()
        for cat_id in category_ids:
            self.db.add(EventCategory(event_id=event.id, category_id=cat_id))
        self.db.commit()
        self.db.refresh(event)
        return event

    def update(self, event_id: int, data: EventUpdate) -> Optional[Event]:
        event = self.db.query(Event).filter(Event.id == event_id).first()
        if not event:
            return None
        for key, value in data.model_dump(exclude_unset=True, exclude={"category_ids"}).items():
            setattr(event, key, value)
        if data.category_ids is not None:
            self.db.query(EventCategory).filter(EventCategory.event_id == event_id).delete()
            for cat_id in data.category_ids:
                self.db.add(EventCategory(event_id=event_id, category_id=cat_id))
        self.db.commit()
        self.db.refresh(event)
        return event

    def delete(self, event_id: int) -> bool:
        event = self.db.query(Event).filter(Event.id == event_id).first()
        if not event:
            return False
        self.db.delete(event)
        self.db.commit()
        return True
