"""
Лабораторная №6, задание 3: агрегация по нескольким шардам.

    docker exec booking_backend python scripts/lab06/b01_aggregation.py
"""
import itertools
import time
from collections import defaultdict

import _common  # noqa: F401  — sys.path
from _common import bench, fmt, get, hr, p50

from sqlalchemy import text

from app.sharding.executor import scatter
from app.sharding.registry import get_router
from app.sharding.tools import connect_primary, connect_shards


def main() -> None:
    nodes = get_router().nodes
    primary = connect_primary("lab6-agg")

    # ------------------------------------------------------------------
    hr("1. COUNT(*): каждый шард считает свою часть, приложение складывает")
    _, d, _, _ = get("/api/shards/stats/count")
    for shard, n in d["per_shard"].items():
        print(f"  {shard} → {fmt(n):>11}")
    print(f"  {'':8}   {'-' * 11}")
    print(f"  сумма   → {fmt(d['total']):>11}")
    with primary.cursor() as cur:
        cur.execute("SELECT count(*) FROM bookings")
        truth = cur.fetchone()[0]
    primary.commit()
    print(f"  primary → {fmt(truth):>11}   {'совпадает' if truth == d['total'] else 'НЕ СОВПАДАЕТ'}")

    # ------------------------------------------------------------------
    hr("2. Выручка по мероприятиям: частичные агрегаты на шардах + слияние")
    _, sharded, _, _ = get("/api/shards/stats/revenue?limit=2000")
    status, main_api, headers, _ = get("/api/bookings/stats/revenue")
    print(f"  эталон: основное API GET /api/bookings/stats/revenue "
          f"(X-DB-Node: {headers.get('X-DB-Node')}) — {len(main_api)} мероприятий")
    print(f"  шарды вернули {fmt(sharded['groups_from_shards'])} частичных групп "
          f"→ после слияния {sharded['events_total']} мероприятий")

    truth_by_event = {r["event_id"]: r for r in main_api}
    bad = 0
    worst = 0.0
    for r in sharded["items"]:
        t = truth_by_event[r["event_id"]]
        diff = abs(r["total_revenue"] - float(t["total_revenue"]))
        worst = max(worst, diff)
        if (r["total_bookings"] != t["total_bookings"]
                or (r["total_seats_booked"] or 0) != (t["total_seats_booked"] or 0)
                or diff > 0.02
                or abs(r["avg_booking_price"] - float(t["avg_booking_price"])) > 0.02):
            bad += 1
    print(f"  расхождений с эталоном: {bad} из {len(sharded['items'])}; "
          f"наибольшая разница выручки {worst:.4f}")
    print("\n  топ-3:  шарды                                   | primary")
    for s, t in zip(sharded["items"][:3], main_api[:3]):
        print(f"    event {s['event_id']:>4}: {s['total_revenue']:>12.2f} ({s['total_bookings']} брон.)"
              f"        | event {t['event_id']:>4}: {float(t['total_revenue']):>12.2f}")

    # ------------------------------------------------------------------
    hr("3. Почему AVG нельзя просто усреднить между шардами")
    per_shard = defaultdict(list)          # event_id → [(count, avg)]
    for name, conn in connect_shards("lab6-agg").items():
        with conn.cursor() as cur:
            cur.execute("""SELECT event_id, count(*), avg(total_price)
                           FROM bookings WHERE status <> 'cancelled' GROUP BY event_id""")
            for event_id, cnt, avg in cur.fetchall():
                per_shard[event_id].append((cnt, float(avg)))
        conn.commit()
    errors = []
    for event_id, parts in per_shard.items():
        naive = sum(a for _, a in parts) / len(parts)
        correct = sum(c * a for c, a in parts) / sum(c for c, _ in parts)
        errors.append((abs(naive - correct), event_id, parts, naive, correct))
    errors.sort(reverse=True)
    err, event_id, parts, naive, correct = errors[0]
    print(f"  мероприятие {event_id}, частичные результаты шардов (count, avg):")
    for (cnt, avg), name in zip(parts, nodes):
        print(f"    {cnt:>4} брон., средний {avg:9.2f}")
    print(f"  среднее средних:             {naive:9.2f}  ← неверно")
    print(f"  SUM/COUNT по всем шардам:    {correct:9.2f}  ← верно (совпадает с primary)")
    print(f"  ошибка на этом мероприятии {err:.2f}; "
          f"мероприятий с ошибкой больше 1 ₽: {sum(1 for e in errors if e[0] > 1)} из {len(errors)}")

    # ------------------------------------------------------------------
    hr("4. Время: primary против шардов")
    print("  a) через API (p50)")
    cases = [
        ("primary, основное API", "/api/bookings/stats/revenue", 3),
        ("шарды, параллельно", "/api/shards/stats/revenue?limit=20&parallel=true", 7),
        ("шарды, по очереди", "/api/shards/stats/revenue?limit=20&parallel=false", 7),
    ]
    for label, path, n in cases:
        get(path)                                   # прогрев
        times = bench(lambda: get(path), n)
        print(f"     {label:<30} p50 {p50(times):8.1f} мс   ({n} запросов)")

    print("\n  b) тот же расчёт SQL-запросом прямо на primary — без HTTP и без шардов")
    original = """
        SELECT e.id, e.title, e.start_time, v.name,
               COUNT(b.id), SUM(b.seats_count),
               COALESCE(SUM(b.total_price), 0), COALESCE(AVG(b.total_price), 0)
        FROM events e
        JOIN venues v ON e.venue_id = v.id
        LEFT JOIN bookings b ON e.id = b.event_id AND b.status != 'cancelled'
        GROUP BY e.id, e.title, e.start_time, v.name
        ORDER BY 7 DESC NULLS LAST"""
    rewritten = """
        WITH agg AS (
            SELECT event_id, count(*) AS cnt, sum(seats_count) AS seats, sum(total_price) AS revenue
            FROM bookings WHERE status <> 'cancelled' GROUP BY event_id)
        SELECT e.id, e.title, e.start_time, v.name,
               coalesce(a.cnt, 0), a.seats, coalesce(a.revenue, 0), coalesce(a.revenue / a.cnt, 0)
        FROM events e JOIN venues v ON e.venue_id = v.id
        LEFT JOIN agg a ON a.event_id = e.id
        ORDER BY 7 DESC NULLS LAST"""

    def sql_ms(sql: str) -> float:
        with primary.cursor() as cur:
            started = time.perf_counter()
            cur.execute(sql)
            cur.fetchall()
            ms = (time.perf_counter() - started) * 1000
        primary.commit()
        return ms

    t_orig = [sql_ms(original) for _ in range(2)]
    t_rew = [sql_ms(rewritten) for _ in range(3)]
    print(f"     {'как в BookingRepository (JOIN → GROUP BY)':<44} p50 {p50(t_orig):8.1f} мс")
    print(f"     {'сначала агрегация по event_id, потом JOIN':<44} p50 {p50(t_rew):8.1f} мс")
    _, last, _, _ = get("/api/shards/stats/revenue?limit=20&parallel=true")
    detail = ", ".join(f"{s['shard']} {s['ms']:.0f} мс" for s in last["shards"])
    print(f"  параллельно по шардам: {detail}; слияние {last['merge_ms']:.1f} мс")
    _, last, _, _ = get("/api/shards/stats/revenue?limit=20&parallel=false")
    detail = ", ".join(f"{s['shard']} {s['ms']:.0f} мс" for s in last["shards"])
    print(f"  по очереди по шардам:  {detail}")

    # ------------------------------------------------------------------
    hr("5. Время ответа и число опрашиваемых шардов")
    print("  a) запрос SELECT 1 — чистая цена обращения к шарду (200 повторов, p50)")
    print(f"     {'шардов':>7} {'по очереди':>12} {'параллельно':>13}")
    for k in (1, 2, 4, 8, 16):
        targets = list(itertools.islice(itertools.cycle(nodes), k))
        seq = bench(lambda: scatter(lambda c: c.execute(text("SELECT 1")).scalar(), targets, False), 200)
        par = bench(lambda: scatter(lambda c: c.execute(text("SELECT 1")).scalar(), targets, True), 200)
        print(f"     {k:>7} {p50(seq):>9.2f} мс {p50(par):>10.2f} мс")

    print("\n  b) каждый шард отвечает со случайной задержкой 0–20 мс (60 повторов, p50):")
    print("     параллельный ответ ждёт САМЫЙ МЕДЛЕННЫЙ шард.")
    print("     Медиана максимума из k равномерных задержек 0–20 мс = 20 · 0.5^(1/k);")
    print("     остаток — цена самого обращения к k шардам из пункта a).")
    print(f"     {'шардов':>7} {'параллельно':>13} {'медиана max':>13}")
    for k in (1, 2, 4, 8, 16):
        targets = list(itertools.islice(itertools.cycle(nodes), k))
        par = bench(lambda: scatter(
            lambda c: c.execute(text("SELECT pg_sleep(random() * 0.02)")).scalar(), targets, True), 60)
        print(f"     {k:>7} {p50(par):>10.2f} мс {20 * 0.5 ** (1 / k):>10.2f} мс")
    print("\n  (8 и 16 «шардов» — те же 4 сервера, опрошенные по несколько раз:")
    print("   каждое обращение — отдельный запрос по отдельному соединению)")


if __name__ == "__main__":
    main()
