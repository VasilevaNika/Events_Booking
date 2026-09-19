from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.routers import health, users, venues, categories, events, bookings, partitions, shards
from app.sharding.executor import ShardUnavailable

app = FastAPI(
    title="Booking Service API",
    description="Сервис бронирования мероприятий",
    version="1.0.0",
)


@app.exception_handler(ShardUnavailable)
def shard_unavailable(request: Request, exc: ShardUnavailable) -> JSONResponse:
    """
    Отказ шарда (лабораторная №6) — это временная недоступность части данных,
    а не ошибка сервера: 503 и Retry-After вместо 500 со стектрейсом.
    Пользователи на остальных шардах продолжают работать.
    """
    return JSONResponse(
        status_code=503,
        content={"detail": f"данные временно недоступны: {exc.shard} не отвечает",
                 "shard": exc.shard, "reason": exc.reason},
        headers={"Retry-After": "30", "X-Shard": exc.shard},
    )

app.include_router(health.router)
app.include_router(users.router)
app.include_router(venues.router)
app.include_router(categories.router)
app.include_router(events.router)
app.include_router(bookings.router)
app.include_router(partitions.router)
app.include_router(shards.router)
