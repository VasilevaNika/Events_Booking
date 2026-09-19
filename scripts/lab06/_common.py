"""
Общие помощники для скриптов лабораторной №6.

Скрипты запускаются внутри контейнера бэкенда и обращаются к API сервиса
по http://localhost:8000 — так измеряется полный путь запроса через router.
"""
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

API = "http://localhost:8000"


def http(method: str, path: str, body=None, timeout: float = 120):
    """(status, payload, headers, ms). headers — регистронезависимые."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw, status, headers = resp.read(), resp.status, resp.headers
    except urllib.error.HTTPError as exc:
        raw, status, headers = exc.read(), exc.code, exc.headers
    ms = (time.perf_counter() - started) * 1000
    try:
        payload = json.loads(raw) if raw else None
    except ValueError:
        payload = raw.decode("utf-8", "replace")
    return status, payload, headers, ms


def get(path: str):
    return http("GET", path)


def bench(fn, n: int) -> list[float]:
    """Время n вызовов fn() в миллисекундах."""
    out = []
    for _ in range(n):
        started = time.perf_counter()
        fn()
        out.append((time.perf_counter() - started) * 1000)
    return out


def p(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q / 100 * (len(ordered) - 1))))]


def p50(values: list[float]) -> float:
    return statistics.median(values)


def fmt(n) -> str:
    return f"{n:,}".replace(",", " ")


def hr(title: str) -> None:
    print("\n" + "─" * 78 + f"\n  {title}\n" + "─" * 78)
