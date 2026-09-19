"""
Выполнение запросов на шардах (лабораторная №6).

run_on_shard     — запрос к одному шарду; недоступный шард → ShardUnavailable
scatter          — один и тот же запрос на нескольких шардах, параллельно или по очереди
query_counts     — сколько запросов ушло на каждый шард (поиск горячего шарда)

ShardUnavailable превращается в HTTP 503 обработчиком в app/main.py:
отказ шарда — это временная недоступность части данных, а не ошибка сервера.
"""
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable, Optional

from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError

from app.sharding.registry import get_engine

# один пул потоков на процесс: создавать потоки на каждый запрос дорого
_pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="scatter")

_counts: Counter = Counter()
_counts_lock = Lock()


class ShardUnavailable(Exception):
    def __init__(self, shard: str, reason: str):
        super().__init__(f"{shard} недоступен: {reason}")
        self.shard = shard
        self.reason = reason


def _reason(exc: OperationalError) -> str:
    text = str(getattr(exc, "orig", exc)).strip()
    return text.splitlines()[0] if text else type(exc).__name__


def run_on_shard(shard: str, fn: Callable[[Connection], Any], write: bool = False) -> Any:
    """Выполнить fn(conn) на шарде. write=True — внутри транзакции с COMMIT."""
    with _counts_lock:
        _counts[shard] += 1
    try:
        engine = get_engine(shard)
        with (engine.begin() if write else engine.connect()) as conn:
            return fn(conn)
    except OperationalError as exc:
        raise ShardUnavailable(shard, _reason(exc)) from exc


@dataclass
class ShardResult:
    shard: str
    value: Any = None
    error: Optional[str] = None
    ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None


def scatter(fn: Callable[[Connection], Any], shards: list[str], parallel: bool = True) -> list[ShardResult]:
    """
    Scatter-gather: выполнить один и тот же запрос на каждом шарде.

    Отказ шарда не прерывает остальные — его результат приходит с error,
    а решение «отдавать неполный ответ или нет» принимает вызывающий код.
    parallel=False — по очереди: нужно, чтобы сравнить время.
    """
    def one(shard: str) -> ShardResult:
        started = time.perf_counter()
        try:
            value = run_on_shard(shard, fn)
            return ShardResult(shard, value=value, ms=(time.perf_counter() - started) * 1000)
        except ShardUnavailable as exc:
            return ShardResult(shard, error=exc.reason, ms=(time.perf_counter() - started) * 1000)

    if parallel and len(shards) > 1:
        return list(_pool.map(one, shards))
    return [one(s) for s in shards]


def query_counts() -> dict[str, int]:
    with _counts_lock:
        return dict(_counts)


def reset_query_counts() -> None:
    with _counts_lock:
        _counts.clear()
