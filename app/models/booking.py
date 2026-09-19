from sqlalchemy import Column, Integer, String, DateTime, Float, ForeignKey, Index, text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class Booking(Base):
    __tablename__ = "bookings"

    # Таблица партиционирована по RANGE (created_at) — лабораторная №3,
    # миграция migrations/versions/003_partition_bookings.py.
    #
    # Отсюда два отличия от обычной модели:
    #   * created_at входит в первичный ключ. PostgreSQL требует, чтобы ключ
    #     партиционирования входил в любой UNIQUE-индекс, иначе уникальность
    #     пришлось бы проверять во всех партициях сразу.
    #   * created_at объявлен NOT NULL: строку с NULL-ключом некуда положить.
    # autoincrement=True указан явно: в составном первичном ключе SQLAlchemy
    # по умолчанию отключает автоинкремент и не забирает id через RETURNING.
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="confirmed")
    seats_count = Column(Integer, nullable=False, default=1)
    total_price = Column(Float, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        primary_key=True,
        nullable=False,
        server_default=func.now(),
        index=True,
    )
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user = relationship("User", back_populates="bookings")
    event = relationship("Event", back_populates="bookings")

    # Индексы лабораторной работы №1 (см. docs/lab-01-indexes.md,
    # миграция migrations/versions/002_booking_indexes.py).
    #
    # Одиночные индексы по user_id и status намеренно НЕ создаются: они
    # являются левыми префиксами составных индексов ниже. Отдельный индекс
    # по id тоже не нужен — его роль выполняет первичный ключ bookings_pkey.
    __table_args__ = (
        # GET /api/users/{user_id}/bookings:
        #   WHERE user_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?
        Index(
            "ix_bookings_user_id_created_at",
            "user_id",
            text("created_at DESC"),
        ),
        # GET /api/bookings?status=&from_date=&to_date=  (+ COUNT(*) пагинации):
        #   WHERE status = ? AND created_at >= ? ORDER BY created_at DESC
        Index(
            "ix_bookings_status_created_at",
            "status",
            text("created_at DESC"),
        ),
    )
