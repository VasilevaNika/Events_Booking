"""
Подключения к PostgreSQL.

Лабораторная №4 (Read Scaling): у сервиса два экземпляра БД.

  PRIMARY — принимает все записи и чтения, которым нужны самые свежие данные
            (read-your-writes: «покажи бронирование, которое я только что создал»).
  REPLICA — физическая копия primary; обслуживает чтения, которые переживут
            отставание в доли секунды: каталог мероприятий, аналитика.

Роутинг — на уровне зависимостей FastAPI:
  get_db       → primary, чтение и запись
  get_read_db  → replica, только чтение; если реплика недоступна —
                 primary в read-only режиме
"""
import logging
from typing import Iterator, Optional

from fastapi import Response
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.config import settings

from itertools import cycle
from threading import Lock

logger = logging.getLogger("app.database")

# application_name виден в pg_stat_activity: по нему на каждом сервере
# понятно, какие соединения чьи
PRIMARY_APP_NAME = "booking-backend-primary"
REPLICA_APP_NAME = "booking-backend-replica"
FALLBACK_APP_NAME = "booking-backend-primary-ro"

# Защита от случайной записи через читающую сессию: даже если чтение
# откатилось на primary, транзакция останется read-only и INSERT/UPDATE упадут.
READ_ONLY_OPTIONS = "-c default_transaction_read_only=on"

# --- primary ------------------------------------------------------------------
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args={"application_name": PRIMARY_APP_NAME},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- replica ------------------------------------------------------------------
_replicas = []                      # [(engine, sessionmaker)]
for i, url in enumerate(settings.replica_urls, start=1):
    eng = create_engine(url, pool_pre_ping=True, connect_args={
        "application_name": f"booking-backend-replica-{i}",
        "options": READ_ONLY_OPTIONS,
        "connect_timeout": settings.replica_connect_timeout,
    })
    _replicas.append((eng, sessionmaker(autocommit=False, autoflush=False, bind=eng)))

replica_engines = [e for e, _ in _replicas]
_next = cycle(range(len(_replicas))) if _replicas else None
_next_lock = Lock()

# --- запасной путь для чтения: primary, но read-only -----------------------------
_fallback_read_engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args={"application_name": FALLBACK_APP_NAME, "options": READ_ONLY_OPTIONS},
)
FallbackReadSessionLocal = sessionmaker(
    autocommit=False, autoflush=False, bind=_fallback_read_engine
)


def get_db() -> Iterator[Session]:
    """Primary: запись и чтение, которому нужны самые свежие данные."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()



def _open_replica_session():
    """Сессия на любой живой реплике. Возвращает (сессия, номер) или (None, None)."""
    if not _replicas:
        return None, None
    with _next_lock:
        start = next(_next)
    for shift in range(len(_replicas)):       
        idx = (start + shift) % len(_replicas)
        _, factory = _replicas[idx]
        db = factory()
        try:
            db.connection()
        except OperationalError as exc:
            db.close()
            logger.warning("replica-%s недоступна: %s", idx + 1, exc.orig)
            continue
        return db, idx + 1
    return None, None


def get_read_db(response: Response):
    db, num = _open_replica_session()
    if db is not None:
        node = f"replica-{num}"
    else:
        db = FallbackReadSessionLocal()
        node = "primary-fallback" if _replicas else "primary"
    response.headers["X-DB-Node"] = node
    try:
        yield db
    finally:
        db.close()

# from itertools import cycle
# from threading import Lock

# _replicas = []                      # [(engine, sessionmaker)]
# for i, url in enumerate(settings.replica_urls, start=1):
#     eng = create_engine(url, pool_pre_ping=True, connect_args={
#         "application_name": f"booking-backend-replica-{i}",
#         "options": READ_ONLY_OPTIONS,
#         "connect_timeout": settings.replica_connect_timeout,
#     })
#     _replicas.append((eng, sessionmaker(autocommit=False, autoflush=False, bind=eng)))

# replica_engines = [e for e, _ in _replicas]
# _next = cycle(range(len(_replicas))) if _replicas else None
# _next_lock = Lock()


# def _open_replica_session():
#     """Сессия на любой живой реплике. Возвращает (сессия, номер) или (None, None)."""
#     if not _replicas:
#         return None, None
#     with _next_lock:
#         start = next(_next)
#     for shift in range(len(_replicas)):        # не ответила одна — пробуем следующую
#         idx = (start + shift) % len(_replicas)
#         _, factory = _replicas[idx]
#         db = factory()
#         try:
#             db.connection()
#         except OperationalError as exc:
#             db.close()
#             logger.warning("replica-%s недоступна: %s", idx + 1, exc.orig)
#             continue
#         return db, idx + 1
#     return None, None


# def get_read_db(response: Response):
#     db, num = _open_replica_session()
#     if db is not None:
#         node = f"replica-{num}"
#     else:
#         db = FallbackReadSessionLocal()
#         node = "primary-fallback" if _replicas else "primary"
#     response.headers["X-DB-Node"] = node
#     try:
#         yield db
#     finally:
#         db.close()