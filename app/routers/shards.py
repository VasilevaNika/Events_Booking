"""
Шардированные бронирования (лабораторные №5–6).

Бронирования распределены между независимыми PostgreSQL по ключу user_id.
Router (app/sharding) по user_id выбирает шард; справочники (users, events,
venues) остаются на primary.

Заголовки ответа:
  X-Shard           — единственный шард, в который ушёл запрос
  X-Shards-Queried  — сколько шардов пришлось опросить (scatter-gather)

Типы запросов (лабораторная №6):
  single-shard  — shard key в запросе: бронирования и статистика пользователя
  scatter       — shard key нет: подсчёт, выручка, последние бронирования, поиск по id

Этот API работает параллельно с основным /api/bookings, который по-прежнему
пишет в primary, — см. «Ограничения» в docs/lab-05-sharding.md.
"""
import heapq
import time
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.sharding.executor import (
    ShardResult, query_counts, reset_query_counts, run_on_shard, scatter,
)
from app.sharding.registry import get_router, known_shards
from app.sharding.router import ConsistentHashRouter, ModuloRouter

router = APIRouter(prefix="/api/shards", tags=["shards"])

BOOKING_COLUMNS = "id, user_id, event_id, status, seats_count, total_price, created_at, updated_at"


def _router():
    try:
        return get_router()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=f"шарды не настроены: {exc}")


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def _require_all(results: list[ShardResult], allow_partial: bool) -> list[ShardResult]:
    """
    Отказ шарда при scatter-gather: по умолчанию — 503, потому что сумма
    или «топ-20» без одного шарда — это неверный ответ, а не неполный.
    allow_partial=true — осознанно принять неполный ответ.
    """
    failed = [r for r in results if not r.ok]
    if failed and not allow_partial:
        raise HTTPException(status_code=503, detail={
            "message": "часть шардов недоступна — ответ был бы неверным",
            "unavailable_shards": {r.shard: r.error for r in failed},
            "hint": "allow_partial=true вернёт ответ по доступным шардам",
        })
    return [r for r in results if r.ok]


def _timings(results: list[ShardResult]) -> list[dict]:
    return [{"shard": r.shard, "ms": round(r.ms, 2), "status": "ok" if r.ok else "down",
             **({"error": r.error} if not r.ok else {})} for r in results]


def _partial_info(results: list[ShardResult]) -> dict:
    failed = [r.shard for r in results if not r.ok]
    return {"partial": bool(failed), "unavailable_shards": failed}


# --------------------------------------------------------------------------
#  Обзор и router
# --------------------------------------------------------------------------

@router.get("")
def overview():
    """
    Конфигурация router'а и распределение бронирований по шардам.

    Каждый шард опрашивается отдельно — общего COUNT по нескольким независимым
    PostgreSQL не существует. Недоступный шард показывается со status=down.
    """
    active = _router()
    results = scatter(
        lambda c: c.execute(text("SELECT count(*), count(DISTINCT user_id) FROM bookings")).one(),
        known_shards(),
    )
    shards = []
    for r in results:
        if r.ok:
            shards.append({"shard": r.shard, "status": "up",
                           "bookings": r.value[0], "users": r.value[1]})
        else:
            shards.append({"shard": r.shard, "status": "down", "error": r.error})

    total = sum(s.get("bookings", 0) for s in shards)
    ideal = total / len(active.nodes) if total else 0
    for s in shards:
        s["in_router"] = s["shard"] in active.nodes
        if "bookings" in s and total:
            s["share_pct"] = round(100 * s["bookings"] / total, 2)
            if s["in_router"]:
                s["deviation_from_ideal_pct"] = round(100 * (s["bookings"] - ideal) / ideal, 2)

    result = {
        "strategy": active.strategy,
        "shards_in_router": active.nodes,
        "total_bookings": total,
        "shards": shards,
    }
    if isinstance(active, ConsistentHashRouter):
        result["vnodes_per_shard"] = active.vnodes
        result["ring_share_pct"] = {n: round(100 * v, 2) for n, v in active.ring_share().items()}
    return result


@router.get("/route/{user_id}")
def route(user_id: int):
    """Куда router отправит пользователя — по текущей стратегии и по обеим для сравнения."""
    active = _router()
    return {
        "user_id": user_id,
        "active_strategy": active.strategy,
        "shard": active.shard_for(user_id),
        "modulo": ModuloRouter(active.nodes).explain(user_id),
        "consistent": ConsistentHashRouter(active.nodes, settings.shard_vnodes).explain(user_id),
    }


# --------------------------------------------------------------------------
#  Single-shard: shard key в запросе
# --------------------------------------------------------------------------

@router.get("/users/{user_id}/bookings")
def user_bookings(
    user_id: int,
    response: Response,
    limit: int = Query(20, ge=1, le=100),
    join: Literal["app", "local"] = Query(
        "app",
        description="app — названия мероприятий вторым запросом к primary; "
                    "local — JOIN на самом шарде по копии справочников (scripts/lab06/sync_reference.py)",
    ),
    db: Session = Depends(get_db),
):
    """
    Бронирования пользователя — тот же запрос, что BookingRepository.get_by_user_id.
    shard key есть в запросе, поэтому опрашивается ровно один шард.
    """
    shard = _router().shard_for(user_id)
    response.headers["X-Shard"] = shard
    response.headers["X-Shards-Queried"] = "1"

    if join == "local":
        try:
            rows = run_on_shard(shard, lambda c: c.execute(text("""
                SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count,
                       b.total_price, b.created_at, b.updated_at,
                       e.title AS event_title, e.start_time AS event_start_time,
                       v.name AS venue_name
                FROM bookings b
                JOIN events e ON b.event_id = e.id
                JOIN venues v ON e.venue_id = v.id
                WHERE b.user_id = :user_id
                ORDER BY b.created_at DESC
                LIMIT :limit"""), {"user_id": user_id, "limit": limit}).mappings().all())
        except ProgrammingError:
            raise HTTPException(status_code=409, detail=(
                "на шарде нет копии справочников events/venues — "
                "запустите scripts/lab06/sync_reference.py"))
        return {"user_id": user_id, "shard": shard, "join": "local",
                "items": [dict(r) for r in rows]}

    rows = run_on_shard(shard, lambda c: c.execute(text(f"""
        SELECT {BOOKING_COLUMNS} FROM bookings
        WHERE user_id = :user_id
        ORDER BY created_at DESC
        LIMIT :limit"""), {"user_id": user_id, "limit": limit}).mappings().all())

    # JOIN бронирований шарда с events/venues на primary средствами SQL
    # невозможен: это разные серверы. Добираем вторым запросом и склеиваем
    # в приложении (application-level join).
    event_ids = sorted({r["event_id"] for r in rows})
    events = {}
    if event_ids:
        events = {r["id"]: r for r in db.execute(text("""
            SELECT e.id, e.title, e.start_time, v.name AS venue_name
            FROM events e JOIN venues v ON e.venue_id = v.id
            WHERE e.id = ANY(:ids)"""), {"ids": event_ids}).mappings()}

    items = []
    for r in rows:
        e = events.get(r["event_id"], {})
        items.append({**r, "event_title": e.get("title"),
                      "event_start_time": e.get("start_time"), "venue_name": e.get("venue_name")})
    return {"user_id": user_id, "shard": shard, "join": "app", "items": items}


@router.get("/users/{user_id}/stats")
def user_stats(user_id: int, response: Response, db: Session = Depends(get_db)):
    """
    Статистика пользователя — агрегация, но с shard key: COUNT/SUM/AVG
    считаются целиком на одном шарде. Тот же расчёт, что UserRepository.get_user_stats.
    """
    user = db.execute(text("SELECT id, name, email FROM users WHERE id = :id"),
                      {"id": user_id}).mappings().first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    shard = _router().shard_for(user_id)
    response.headers["X-Shard"] = shard
    response.headers["X-Shards-Queried"] = "1"
    stats = run_on_shard(shard, lambda c: c.execute(text("""
        SELECT count(*)                           AS total_bookings,
               coalesce(sum(total_price), 0)      AS total_spent,
               coalesce(avg(total_price), 0)      AS avg_booking_price
        FROM bookings
        WHERE user_id = :user_id AND status <> 'cancelled'"""), {"user_id": user_id}).mappings().one())
    return {**user, **stats, "shard": shard}


class ShardedBookingCreate(BaseModel):
    user_id: int
    event_id: int
    seats_count: int = Field(1, ge=1, le=10)


@router.post("/bookings", status_code=201)
def create_booking(data: ShardedBookingCreate, response: Response, db: Session = Depends(get_db)):
    """Создать бронирование: router по user_id выбирает шард, запись идёт только туда."""
    active = _router()

    # Справочники живут на primary. Внешнего ключа на шарде нет, поэтому
    # существование пользователя и мероприятия проверяет приложение.
    if db.execute(text("SELECT 1 FROM users WHERE id = :id"), {"id": data.user_id}).first() is None:
        raise HTTPException(status_code=404, detail="User not found")
    price = db.execute(text("SELECT price FROM events WHERE id = :id"),
                       {"id": data.event_id}).scalar()
    if price is None:
        raise HTTPException(status_code=404, detail="Event not found")

    # id должен быть уникален во всей системе, а не внутри шарда: берём его
    # из общей последовательности на primary — той же, что у основного API.
    booking_id = db.execute(text("SELECT nextval('bookings_id_seq')")).scalar()
    db.commit()

    shard = active.shard_for(data.user_id)
    row = run_on_shard(shard, lambda c: c.execute(text(f"""
        INSERT INTO bookings (id, user_id, event_id, status, seats_count, total_price)
        VALUES (:id, :user_id, :event_id, 'confirmed', :seats, :total)
        RETURNING {BOOKING_COLUMNS}"""), {
            "id": booking_id, "user_id": data.user_id, "event_id": data.event_id,
            "seats": data.seats_count, "total": round(price * data.seats_count, 2),
        }).mappings().one(), write=True)

    response.headers["X-Shard"] = shard
    return {"shard": shard, **row}


# --------------------------------------------------------------------------
#  Scatter-gather: shard key в запросе нет
# --------------------------------------------------------------------------

@router.get("/bookings/latest")
def latest_bookings(
    response: Response,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100_000),
    after_created_at: Optional[datetime] = Query(None, description="keyset-курсор: created_at последней строки"),
    after_id: Optional[int] = Query(None, description="keyset-курсор: id последней строки"),
    parallel: bool = True,
    allow_partial: bool = False,
):
    """
    Последние бронирования всей системы — ORDER BY created_at DESC LIMIT,
    как в BookingRepository.get_all.

    Самые свежие строки могут лежать на любом шарде, поэтому каждый шард
    отдаёт свой «топ», а приложение сливает их слиянием отсортированных списков
    и отрезает нужную страницу.

    OFFSET: каждый шард обязан вернуть offset + limit строк — никто не знает
    заранее, сколько из них окажется в итоговой странице. Keyset-курсор
    (after_created_at + after_id) убирает эту проблему.
    """
    active = _router()
    keyset = after_created_at is not None and after_id is not None
    per_shard = limit if keyset else offset + limit
    where = "WHERE (created_at, id) < (:ac, :aid)" if keyset else ""
    params = {"n": per_shard, "ac": after_created_at, "aid": after_id}

    started = time.perf_counter()
    results = scatter(lambda c: c.execute(text(f"""
        SELECT {BOOKING_COLUMNS} FROM bookings {where}
        ORDER BY created_at DESC, id DESC
        LIMIT :n"""), params).mappings().all(), active.nodes, parallel)
    ok = _require_all(results, allow_partial)
    fetch_ms = _ms(started)

    merge_started = time.perf_counter()
    streams = [[{**row, "shard": r.shard} for row in r.value] for r in ok]
    merged = heapq.merge(*streams, key=lambda x: (x["created_at"], x["id"]), reverse=True)
    page = list(merged)[0 if keyset else offset:][:limit]
    merge_ms = _ms(merge_started)

    response.headers["X-Shards-Queried"] = str(len(active.nodes))
    cursor = ({"after_created_at": page[-1]["created_at"], "after_id": page[-1]["id"]}
              if page else None)
    return {
        "items": page,
        "rows_fetched_from_shards": sum(len(s) for s in streams),
        "rows_returned": len(page),
        "next_cursor": cursor,
        "fetch_ms": fetch_ms,
        "merge_ms": merge_ms,
        "shards": _timings(results),
        **_partial_info(results),
    }


@router.get("/bookings/{booking_id}")
def get_booking(
    booking_id: int,
    response: Response,
    user_id: Optional[int] = Query(
        None, description="shard key. Без него router не знает шард и опрашивает все."
    ),
):
    """
    Бронирование по id. id не является shard key, поэтому без user_id запрос
    уходит во все шарды. Если хоть один шард недоступен и строка не найдена
    на остальных, честный ответ — 503, а не 404: отсутствие не доказано.
    """
    active = _router()
    targets = [active.shard_for(user_id)] if user_id is not None else list(active.nodes)
    response.headers["X-Shards-Queried"] = str(len(targets))

    results = scatter(lambda c: c.execute(
        text(f"SELECT {BOOKING_COLUMNS} FROM bookings WHERE id = :id"), {"id": booking_id}
    ).mappings().first(), targets)

    found = next((r for r in results if r.ok and r.value is not None), None)
    if found is not None:
        response.headers["X-Shard"] = found.shard
        return {"shard": found.shard, **found.value, "shards_queried": targets}

    failed = {r.shard: r.error for r in results if not r.ok}
    if failed:
        raise HTTPException(status_code=503, detail={
            "message": "бронирование не найдено на доступных шардах, "
                       "но часть шардов недоступна — нельзя утверждать, что его нет",
            "unavailable_shards": failed,
        })
    raise HTTPException(status_code=404, detail="Booking not found",
                        headers={"X-Shards-Queried": str(len(targets))})


@router.get("/stats/count")
def count_bookings(response: Response, parallel: bool = True, allow_partial: bool = False):
    """COUNT(*) по всем шардам: каждый считает свою часть, приложение складывает."""
    active = _router()
    started = time.perf_counter()
    results = scatter(lambda c: c.execute(text("SELECT count(*) FROM bookings")).scalar(),
                      active.nodes, parallel)
    ok = _require_all(results, allow_partial)
    response.headers["X-Shards-Queried"] = str(len(active.nodes))
    return {
        "total": sum(r.value for r in ok),
        "per_shard": {r.shard: r.value for r in ok},
        "total_ms": _ms(started),
        "parallel": parallel,
        "shards": _timings(results),
        **_partial_info(results),
    }


@router.get("/stats/revenue")
def revenue(
    response: Response,
    limit: int = Query(20, ge=1, le=2000),
    parallel: bool = True,
    allow_partial: bool = False,
    db: Session = Depends(get_db),
):
    """
    Выручка по мероприятиям — BookingRepository.get_revenue_by_event на шардах.

    Каждый шард считает ЧАСТИЧНЫЕ агрегаты по своим бронированиям:
    COUNT, SUM(seats), SUM(price). Приложение складывает их по event_id.
    AVG нельзя усреднять между шардами — он пересчитывается как SUM / COUNT.
    Названия мероприятий и площадок берутся с primary.
    """
    active = _router()
    started = time.perf_counter()
    results = scatter(lambda c: c.execute(text("""
        SELECT event_id, count(*) AS cnt, sum(seats_count) AS seats, sum(total_price) AS revenue
        FROM bookings
        WHERE status <> 'cancelled'
        GROUP BY event_id""")).all(), active.nodes, parallel)
    ok = _require_all(results, allow_partial)
    shards_ms = _ms(started)

    merge_started = time.perf_counter()
    merged: dict[int, list] = {}
    for r in ok:
        for event_id, cnt, seats, rev in r.value:
            acc = merged.setdefault(event_id, [0, 0, 0.0])
            acc[0] += cnt
            acc[1] += seats
            acc[2] += rev
    merge_ms = _ms(merge_started)

    events = db.execute(text("""
        SELECT e.id, e.title, e.start_time, v.name AS venue_name
        FROM events e JOIN venues v ON e.venue_id = v.id""")).mappings().all()
    rows = []
    for e in events:
        cnt, seats, rev = merged.get(e["id"], (0, None, 0.0))
        rows.append({
            "event_id": e["id"], "event_title": e["title"], "start_time": e["start_time"],
            "venue_name": e["venue_name"], "total_bookings": cnt, "total_seats_booked": seats,
            "total_revenue": round(rev, 2),
            "avg_booking_price": round(rev / cnt, 2) if cnt else 0,
        })
    rows.sort(key=lambda x: x["total_revenue"], reverse=True)

    response.headers["X-Shards-Queried"] = str(len(active.nodes))
    return {
        "items": rows[:limit],
        "events_total": len(rows),
        "groups_from_shards": sum(len(r.value) for r in ok),
        "shards_ms": shards_ms,
        "merge_ms": merge_ms,
        "total_ms": _ms(started),
        "parallel": parallel,
        "shards": _timings(results),
        **_partial_info(results),
    }


# --------------------------------------------------------------------------
#  Нагрузка по шардам
# --------------------------------------------------------------------------

@router.get("/metrics")
def metrics():
    """
    Сколько запросов бэкенд отправил на каждый шард с последнего сброса.
    Показывает распределение НАГРУЗКИ — в отличие от GET /api/shards,
    который показывает распределение ДАННЫХ.
    """
    counts = query_counts()
    total = sum(counts.values())
    return {
        "total_queries": total,
        "per_shard": {s: {"queries": counts.get(s, 0),
                          "share_pct": round(100 * counts.get(s, 0) / total, 2) if total else 0}
                      for s in known_shards()},
    }


@router.post("/metrics/reset", status_code=204)
def metrics_reset():
    reset_query_counts()
