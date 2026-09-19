"""
Лабораторная №6, задание 5: ORDER BY ... LIMIT на шардах.

    docker exec booking_backend python scripts/lab06/d01_order_limit.py
"""
import time
from urllib.parse import quote

import _common  # noqa: F401  — sys.path
from _common import fmt, get, hr, p50

from app.sharding.tools import connect_primary, connect_shards

LIMIT = 20
ORDER = "ORDER BY created_at DESC, id DESC"


def primary_page(cur, offset: int, limit: int = LIMIT):
    started = time.perf_counter()
    cur.execute(f"SELECT id, created_at FROM bookings {ORDER} LIMIT %s OFFSET %s", (limit, offset))
    rows = cur.fetchall()
    return rows, (time.perf_counter() - started) * 1000


def main() -> None:
    primary = connect_primary("lab6-order")
    pcur = primary.cursor()
    truth, _ = primary_page(pcur, 0)
    truth_ids = [r[0] for r in truth]
    print(f"эталон: 20 последних бронирований системы на primary, "
          f"от {truth[0][1]:%Y-%m-%d %H:%M:%S} до {truth[-1][1]:%Y-%m-%d %H:%M:%S}")

    shards = connect_shards("lab6-order")

    hr("1. Топ-20 только с одного шарда")
    tops = {}
    for name, conn in shards.items():
        with conn.cursor() as cur:
            cur.execute(f"SELECT id, created_at FROM bookings {ORDER} LIMIT %s", (LIMIT,))
            tops[name] = cur.fetchall()
        conn.commit()
        hit = len(set(r[0] for r in tops[name]) & set(truth_ids))
        print(f"  {name}: из его топ-20 в настоящий топ-20 попали {hit:>2}; "
              f"пропущено {LIMIT - hit:>2} более свежих бронирований с других шардов")

    hr("2. «По-честному поровну»: по 5 с каждого шарда")
    per = LIMIT // len(shards)
    mixed = sorted((r for rows in tops.values() for r in rows[:per]),
                   key=lambda r: (r[1], r[0]), reverse=True)
    hit = len(set(r[0] for r in mixed) & set(truth_ids))
    print(f"  совпало с эталоном {hit} из {LIMIT} — свежие бронирования распределены неравномерно")

    hr("3. Правильно: топ-20 с каждого шарда → слияние → первые 20")
    _, d, headers, _ = get(f"/api/shards/bookings/latest?limit={LIMIT}")
    got = [r["id"] for r in d["items"]]
    print(f"  опрошено шардов: {headers.get('X-Shards-Queried')}, получено строк с шардов "
          f"{d['rows_fetched_from_shards']}, отдано {d['rows_returned']}")
    print(f"  совпадает с эталоном: {'да, все 20 в том же порядке' if got == truth_ids else 'НЕТ'}")
    from collections import Counter
    origin = Counter(r["shard"] for r in d["items"])
    print("  откуда пришли строки итогового топа: "
          + ", ".join(f"{s}: {n}" for s, n in sorted(origin.items())))

    hr("4. Глубокие страницы: OFFSET")
    print(f"  {'offset':>8} {'строк с шардов':>15} {'шарды, мс':>10} {'primary, мс':>12}  совпало")
    cursors = {}
    for offset in (0, 1_000, 10_000, 100_000):
        path = f"/api/shards/bookings/latest?limit={LIMIT}&offset={offset}"
        get(path)
        runs = [get(path) for _ in range(3)]
        d = runs[-1][1]
        rows, _ = primary_page(pcur, offset)
        ptimes = [primary_page(pcur, offset)[1] for _ in range(3)]
        same = [r["id"] for r in d["items"]] == [r[0] for r in rows]
        print(f"  {fmt(offset):>8} {fmt(d['rows_fetched_from_shards']):>15} "
              f"{p50([r[1]['fetch_ms'] for r in runs]):>10.1f} {p50(ptimes):>12.1f}  "
              f"{'да' if same else 'НЕТ'}")
        if offset:
            prev, _ = primary_page(pcur, offset - 1, 1)
            cursors[offset] = (prev[0][1], prev[0][0], [r[0] for r in rows])

    hr("5. Keyset-пагинация: курсор вместо OFFSET")
    print(f"  {'страница с':>11} {'строк с шардов':>15} {'шарды, мс':>10}  совпало с OFFSET")
    for offset, (created_at, row_id, expected) in cursors.items():
        path = (f"/api/shards/bookings/latest?limit={LIMIT}"
                f"&after_created_at={quote(created_at.isoformat())}&after_id={row_id}")
        get(path)
        runs = [get(path) for _ in range(3)]
        d = runs[-1][1]
        same = [r["id"] for r in d["items"]] == expected
        print(f"  {fmt(offset):>11} {fmt(d['rows_fetched_from_shards']):>15} "
              f"{p50([r[1]['fetch_ms'] for r in runs]):>10.1f}  {'да' if same else 'НЕТ'}")


if __name__ == "__main__":
    main()
