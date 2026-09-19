"""
Лабораторная №6, задание 2: single-shard query.

    docker exec booking_backend python scripts/lab06/a01_single_shard.py
"""
import _common  # noqa: F401  — sys.path
from _common import bench, fmt, get, hr, p, p50

from app.sharding.tools import connect_primary, connect_shards

USER = 101
USER_QUERY = """
    SELECT id, user_id, event_id, status, seats_count, total_price, created_at
    FROM bookings
    WHERE user_id = %s
    ORDER BY created_at DESC
    LIMIT 20"""


def explain(conn, sql, params) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("EXPLAIN (ANALYZE, BUFFERS) " + sql, params)   # прогрев
        cur.execute("EXPLAIN (ANALYZE, BUFFERS) " + sql, params)
        lines = [r[0] for r in cur.fetchall()]
    conn.commit()
    return lines


def main() -> None:
    hr(f"1. Куда router отправляет пользователя {USER}")
    _, route, _, _ = get(f"/api/shards/route/{USER}")
    shard = route["shard"]
    c = route["consistent"]
    print(f"  hash({USER}) = {c['hash']} → ближайшая точка кольца {c['next_point_clockwise']} "
          f"({c['vnode']}) → {shard}")

    hr(f"2. Бронирования пользователя {USER} физически лежат на одном шарде")
    shards = connect_shards("lab6-single")
    for name, conn in shards.items():
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM bookings WHERE user_id = %s", (USER,))
            n = cur.fetchone()[0]
        conn.commit()
        mark = "  ← router" if name == shard else ""
        print(f"  {name}: {n:>4} бронирований{mark}")

    hr("3. План запроса «мои бронирования»: шард против primary")
    print(f"  --- {shard} (одна таблица, ~1.2 млн строк)")
    for line in explain(shards[shard], USER_QUERY, (USER,)):
        print("  " + line)
    print("\n  --- primary (17 месячных партиций, 5 млн строк)")
    primary = connect_primary("lab6-single")
    plan = explain(primary, USER_QUERY, (USER,))
    for line in plan[:6] + ["  ..."] + plan[-4:]:
        print("  " + line)

    hr("4. Время ответа API, 50 запросов")
    cases = [
        ("шарды, JOIN в приложении", f"/api/shards/users/{USER}/bookings"),
        ("шарды, JOIN на шарде", f"/api/shards/users/{USER}/bookings?join=local"),
        ("primary, основное API", f"/api/users/{USER}/bookings"),
    ]
    for label, path in cases:
        status, _, headers, _ = get(path)
        if status != 200:
            print(f"  {label:<28} HTTP {status}")
            continue
        times = bench(lambda: get(path), 50)
        print(f"  {label:<28} p50 {p50(times):6.2f} мс   p95 {p(times, 95):6.2f} мс   "
              f"X-Shards-Queried: {headers.get('X-Shards-Queried') or '—'}")

    hr("5. Агрегация с shard key — тоже один шард")
    status, sharded, headers, _ = get(f"/api/shards/users/{USER}/stats")
    _, main_api, _, _ = get(f"/api/users/{USER}/stats")
    print(f"  шарды  [{headers.get('X-Shard')}, опрошено {headers.get('X-Shards-Queried')}]: "
          f"бронирований {sharded['total_bookings']}, потрачено {float(sharded['total_spent']):.2f}, "
          f"средний чек {float(sharded['avg_booking_price']):.2f}")
    print(f"  primary:                   бронирований {main_api['total_bookings']}, "
          f"потрачено {float(main_api['total_spent']):.2f}, "
          f"средний чек {float(main_api['avg_booking_price']):.2f}")
    same = (sharded["total_bookings"] == main_api["total_bookings"]
            and abs(float(sharded["total_spent"]) - float(main_api["total_spent"])) < 0.01)
    print(f"  результаты {'совпадают' if same else 'РАСХОДЯТСЯ'}")


if __name__ == "__main__":
    main()
