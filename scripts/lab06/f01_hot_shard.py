"""
Лабораторная №6, задание 7: hot shard.

Данные распределены почти поровну. Проверяем, как распределится НАГРУЗКА
при разном составе запросов. Нагрузка считается двумя независимыми способами:
  - счётчик запросов в бэкенде (GET /api/shards/metrics);
  - счётчики самих PostgreSQL: pg_stat_database.xact_commit и tup_returned
    (сколько транзакций и прочитанных строк пришлось на шард).

    docker exec booking_backend python scripts/lab06/f01_hot_shard.py
"""
import random

import _common  # noqa: F401  — sys.path
from _common import fmt, get, hr, http

from app.sharding.registry import get_router
from app.sharding.tools import connect_shards

random.seed(6)
N = 2000


def db_counters(shards) -> dict:
    out = {}
    for name, conn in shards.items():
        with conn.cursor() as cur:
            cur.execute("""SELECT xact_commit, tup_returned + tup_fetched
                           FROM pg_stat_database WHERE datname = current_database()""")
            out[name] = cur.fetchone()
        conn.commit()
    return out


def scenario(title: str, paths: list[str], shards, data_share: dict) -> None:
    hr(title)
    http("POST", "/api/shards/metrics/reset")
    before = db_counters(shards)
    for path in paths:
        status, _, _, _ = get(path)
        assert status == 200, (path, status)
    import time
    time.sleep(1.5)                       # статистика PostgreSQL сбрасывается не мгновенно
    after = db_counters(shards)
    _, m, _, _ = get("/api/shards/metrics")

    tx = {s: after[s][0] - before[s][0] for s in shards}
    rows = {s: after[s][1] - before[s][1] for s in shards}
    tx_total, rows_total = sum(tx.values()), sum(rows.values())
    print(f"  HTTP-запросов: {fmt(len(paths))}, запросов к шардам: {fmt(m['total_queries'])}")
    print(f"  {'шард':<9} {'доля данных':>12} {'запросов':>10} {'доля':>8} "
          f"{'транзакций PG':>14} {'прочитано строк':>16} {'доля':>7}")
    for s in shards:
        q = m["per_shard"][s]
        print(f"  {s:<9} {data_share[s]:>11.2f}% {fmt(q['queries']):>10} {q['share_pct']:>7.2f}% "
              f"{fmt(tx[s]):>14} {fmt(rows[s]):>16} {100 * rows[s] / rows_total:>6.2f}%")


def main() -> None:
    router = get_router()
    shards = connect_shards("lab6-hot")
    _, overview, _, _ = get("/api/shards")
    data_share = {s["shard"]: s["share_pct"] for s in overview["shards"]}
    users = list(range(1, 50_001))

    hot_shard = "shard-2"
    celebrity = next(u for u in users if router.shard_for(u) == hot_shard)
    print(f"данные по шардам: " + ", ".join(f"{s}: {p} %" for s, p in data_share.items()))
    print(f"«популярный» пользователь для сценария B: {celebrity} (лежит на {hot_shard})")

    scenario("A. Равномерный трафик: 2000 запросов «мои бронирования» от случайных пользователей",
             [f"/api/shards/users/{random.choice(users)}/bookings" for _ in range(N)],
             shards, data_share)

    mixed = [f"/api/shards/users/{celebrity}/bookings" if random.random() < 0.7
             else f"/api/shards/users/{random.choice(users)}/bookings" for _ in range(N)]
    scenario(f"B. Популярный пользователь: 70 % запросов — от пользователя {celebrity}",
             mixed, shards, data_share)

    typed = ([f"/api/shards/users/{random.choice(users)}/bookings" for _ in range(1000)]
             + ["/api/shards/stats/revenue?limit=5"] * 50)
    random.shuffle(typed)
    scenario("C. Тип запроса: 1000 «мои бронирования» + 50 отчётов о выручке (scatter-gather)",
             typed, shards, data_share)


if __name__ == "__main__":
    main()
