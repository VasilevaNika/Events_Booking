"""
Подключения к шардам и router, собранный из настроек (лабораторная №5).

Пул соединений на каждый шард создаётся при первом обращении, поэтому бэкенд
стартует, даже если какой-то шард ещё не поднят: упадёт только запрос,
которому нужен именно этот шард.
"""
from functools import lru_cache
from threading import Lock

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from app.config import settings
from app.sharding.router import make_router

_engines: dict[str, Engine] = {}
_lock = Lock()


def known_shards() -> list[str]:
    """Все шарды, к которым настроено подключение (SHARDS)."""
    return list(settings.shard_urls)


def get_engine(name: str) -> Engine:
    with _lock:
        eng = _engines.get(name)
        if eng is None:
            urls = settings.shard_urls
            if name not in urls:
                raise KeyError(f"неизвестный шард: {name}")
            eng = create_engine(
                urls[name],
                pool_pre_ping=True,
                pool_size=5,
                max_overflow=5,
                connect_args={
                    "application_name": f"booking-backend-{name}",
                    "connect_timeout": 3,
                },
            )
            _engines[name] = eng
        return eng


@lru_cache(maxsize=1)
def get_router():
    """
    Router текущей конфигурации: стратегия SHARD_STRATEGY по шардам
    SHARD_ROUTER_NODES. Смена конфигурации — только перезапуском бэкенда,
    и только после переноса данных (scripts/lab05/rebalance.py).
    """
    return make_router(
        settings.shard_strategy,
        settings.shard_router_names,
        settings.shard_vnodes,
    )
