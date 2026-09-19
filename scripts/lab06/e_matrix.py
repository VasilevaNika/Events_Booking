"""
Лабораторная №6, задание 6: матрица «endpoint → ответ» при разных состояниях шардов.

    docker exec booking_backend python scripts/lab06/e_matrix.py prepare
    docker exec booking_backend python scripts/lab06/e_matrix.py run "<подпись>"

prepare — выбирает пользователя и бронирование на shard-0 и на shard-2
и запоминает их: во время отказа shard-2 узнать это уже нельзя.
"""
import json
import sys
from pathlib import Path

import _common  # noqa: F401  — sys.path
from _common import get, http

from app.sharding.registry import get_router
from app.sharding.tools import connect_shards

STATE = Path("/tmp/lab6_failure_ids.json")
DOWN = "shard-2"
UP = "shard-0"


def prepare() -> None:
    router = get_router()
    picked = {}
    for shard in (UP, DOWN):
        user = next(u for u in range(1, 50_001) if router.shard_for(u) == shard)
        conn = connect_shards("lab6-failure")[shard]
        with conn.cursor() as cur:
            cur.execute("SELECT min(id) FROM bookings WHERE user_id = %s", (user,))
            booking = cur.fetchone()[0]
        conn.close()
        picked[shard] = {"user": user, "booking": booking}
    STATE.write_text(json.dumps(picked))
    _, overview, _, _ = get("/api/shards")
    down = next(s for s in overview["shards"] if s["shard"] == DOWN)
    print(f"пользователь на {UP}: {picked[UP]['user']}, бронирование {picked[UP]['booking']}")
    print(f"пользователь на {DOWN}: {picked[DOWN]['user']}, бронирование {picked[DOWN]['booking']}")
    print(f"на {DOWN}: {down['users']} пользователей, {down['bookings']} бронирований "
          f"({down['share_pct']} % данных)")


def short(payload) -> str:
    if isinstance(payload, dict) and "partial" in payload:          # успешный scatter-gather
        down = payload.get("unavailable_shards") or []
        total = f", total={payload['total']}" if "total" in payload else ""
        return f"partial={str(payload['partial']).lower()}{total}" + (
            f", без: {', '.join(down)}" if down else "")
    if isinstance(payload, dict):
        d = payload.get("detail", payload)
        if isinstance(d, dict):
            if "unavailable_shards" in d:
                return "недоступны: " + ", ".join(d["unavailable_shards"])
            return d.get("message", "")[:60]
        if isinstance(d, str):
            return d[:60]
        if "partial" in payload:
            return (f"partial={payload['partial']}, total={payload.get('total', '')}"
                    if "total" in payload else f"partial={payload['partial']}")
        if "shards" in payload and "strategy" in payload:
            down = [s["shard"] for s in payload["shards"] if s["status"] == "down"]
            return "down: " + (", ".join(down) or "нет")
    return ""


def run(label: str) -> None:
    ids = json.loads(STATE.read_text())
    u0, b0 = ids[UP]["user"], ids[UP]["booking"]
    u2, b2 = ids[DOWN]["user"], ids[DOWN]["booking"]
    cases = [
        ("шарды", f"GET  бронирования пользователя с {UP}", "GET", f"/api/shards/users/{u0}/bookings"),
        ("шарды", f"GET  бронирования пользователя с {DOWN}", "GET", f"/api/shards/users/{u2}/bookings"),
        ("шарды", f"GET  статистика пользователя с {DOWN}", "GET", f"/api/shards/users/{u2}/stats"),
        ("шарды", f"POST бронирование пользователю с {DOWN}", "POST", "/api/shards/bookings"),
        ("шарды", f"GET  бронь по id, лежит на {UP}", "GET", f"/api/shards/bookings/{b0}"),
        ("шарды", f"GET  бронь по id, лежит на {DOWN}", "GET", f"/api/shards/bookings/{b2}"),
        ("шарды", "GET  несуществующая бронь по id", "GET", "/api/shards/bookings/999999999"),
        ("шарды", "GET  COUNT(*) по всем шардам", "GET", "/api/shards/stats/count"),
        ("шарды", "     то же, allow_partial=true", "GET", "/api/shards/stats/count?allow_partial=true"),
        ("шарды", "GET  выручка по мероприятиям", "GET", "/api/shards/stats/revenue"),
        ("шарды", "     то же, allow_partial=true", "GET", "/api/shards/stats/revenue?allow_partial=true"),
        ("шарды", "GET  последние бронирования", "GET", "/api/shards/bookings/latest"),
        ("шарды", "GET  обзор шардов", "GET", "/api/shards"),
        ("primary", "GET  каталог мероприятий", "GET", "/api/events?page_size=3"),
        ("primary", "GET  пользователь", "GET", f"/api/users/{u2}"),
        ("primary", "GET  бронирования (основное API)", "GET", f"/api/users/{u2}/bookings"),
        ("primary", "GET  /health", "GET", "/health"),
    ]
    print(f"\n=== {label} ===")
    print(f"  {'':8} {'endpoint':<44} {'HTTP':>4} {'мс':>7}  подробности")
    for group, title, method, path in cases:
        if method == "POST" and "недоступен" not in label:
            continue                      # запись — только во время отказа, где она получит 503;
                                          # иначе на шарде появилась бы строка, которой нет на primary
        body = {"user_id": u2, "event_id": 5, "seats_count": 1} if method == "POST" else None
        status, payload, headers, ms = http(method, path, body)
        extra = short(payload)
        if status == 200 and headers.get("X-Shard"):
            extra = f"X-Shard: {headers.get('X-Shard')} " + extra
        print(f"  {group:<8} {title:<44} {status:>4} {ms:>7.1f}  {extra}")


if __name__ == "__main__":
    if sys.argv[1] == "prepare":
        prepare()
    else:
        run(sys.argv[2])
