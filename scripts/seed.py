"""
Генератор тестовых данных.

Быстрый запуск (небольшой набор для проверки API):
    python scripts/seed.py

Большой объём (для экспериментов с производительностью):
    python scripts/seed.py --users 100000 --venues 500 --categories 20 --events 5000 --bookings 1000000
"""
import sys
import os
import argparse
import random
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from faker import Faker
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models.user import User
from app.models.venue import Venue
from app.models.category import Category
from app.models.event import Event, EventCategory
from app.models.booking import Booking

fake = Faker("ru_RU")
Faker.seed(42)
random.seed(42)

CATEGORY_NAMES = [
    "Концерты", "Театр", "Кино", "Спорт", "Выставки",
    "Конференции", "Мастер-классы", "Фестивали", "Лекции",
    "Корпоративы", "Детские мероприятия", "Стенд-ап", "Квесты",
    "Вечеринки", "Образование", "Бизнес", "Наука", "Искусство",
    "Благотворительность", "Другое",
]


def seed_categories(db: Session, count: int) -> list[int]:
    names = CATEGORY_NAMES[:count]
    cats = []
    for name in names:
        c = Category(name=name, description=fake.sentence())
        db.add(c)
        cats.append(c)
    db.commit()
    print(f"  categories: {len(cats)}")
    return [c.id for c in cats]


def seed_venues(db: Session, count: int) -> list[int]:
    venues = []
    for _ in range(count):
        v = Venue(
            name=fake.company() + " Арена",
            address=fake.address().replace("\n", ", "),
            capacity=random.randint(50, 10_000),
            description=fake.text(max_nb_chars=200),
        )
        db.add(v)
        venues.append(v)
    db.commit()
    print(f"  venues: {count}")
    return [v.id for v in venues]


def seed_users(db: Session, count: int, batch: int = 2000) -> list[int]:
    for i in range(0, count, batch):
        chunk = min(batch, count - i)
        db.bulk_insert_mappings(User, [
            {
                "email": fake.unique.email(),
                "name": fake.name(),
                "phone": fake.phone_number()[:20],
                "created_at": fake.date_time_between(start_date="-2y", end_date="now"),
            }
            for _ in range(chunk)
        ])
        db.commit()
    ids = [r[0] for r in db.query(User.id).all()]
    print(f"  users: {len(ids)}")
    return ids


def seed_events(db: Session, count: int, venue_ids: list[int], cat_ids: list[int]) -> list[int]:
    event_ids = []
    for _ in range(count):
        start = fake.date_time_between(start_date="-1y", end_date="+1y")
        e = Event(
            title=fake.catch_phrase(),
            description=fake.text(max_nb_chars=400),
            venue_id=random.choice(venue_ids),
            start_time=start,
            end_time=start + timedelta(hours=random.randint(1, 8)),
            price=round(random.uniform(0, 5000), 2),
            max_capacity=random.randint(10, 5000),
        )
        db.add(e)
        db.flush()
        for cat_id in random.sample(cat_ids, min(random.randint(1, 3), len(cat_ids))):
            db.add(EventCategory(event_id=e.id, category_id=cat_id))
        event_ids.append(e.id)
    db.commit()
    print(f"  events: {count}")
    return event_ids


def seed_bookings(db: Session, count: int, user_ids: list[int], event_ids: list[int], batch: int = 5000):
    statuses = ["pending", "confirmed", "cancelled", "completed"]
    weights = [0.1, 0.6, 0.15, 0.15]
    prices = {r[0]: r[1] for r in db.query(Event.id, Event.price).all()}

    for i in range(0, count, batch):
        chunk = min(batch, count - i)
        rows = []
        for _ in range(chunk):
            eid = random.choice(event_ids)
            seats = random.randint(1, 4)
            rows.append({
                "user_id": random.choice(user_ids),
                "event_id": eid,
                "status": random.choices(statuses, weights=weights)[0],
                "seats_count": seats,
                "total_price": round(prices.get(eid, 0) * seats, 2),
                "created_at": fake.date_time_between(start_date="-1y", end_date="now"),
            })
        db.bulk_insert_mappings(Booking, rows)
        db.commit()
        print(f"  bookings batch {i}–{i + chunk}")

    print(f"  bookings total: {count}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--users",      type=int, default=100)
    p.add_argument("--venues",     type=int, default=10)
    p.add_argument("--categories", type=int, default=10)
    p.add_argument("--events",     type=int, default=50)
    p.add_argument("--bookings",   type=int, default=500)
    args = p.parse_args()

    db: Session = SessionLocal()
    try:
        print("Seeding…")
        cat_ids   = seed_categories(db, min(args.categories, len(CATEGORY_NAMES)))
        venue_ids = seed_venues(db, args.venues)
        user_ids  = seed_users(db, args.users)
        event_ids = seed_events(db, args.events, venue_ids, cat_ids)
        seed_bookings(db, args.bookings, user_ids, event_ids)

        print("\nReady:")
        print(f"  categories : {db.query(Category).count()}")
        print(f"  venues     : {db.query(Venue).count()}")
        print(f"  users      : {db.query(User).count()}")
        print(f"  events     : {db.query(Event).count()}")
        print(f"  bookings   : {db.query(Booking).count()}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
