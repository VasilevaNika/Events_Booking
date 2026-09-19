"""
Лабораторная №6, задание 4: JOIN между шардами.

    docker exec booking_backend python scripts/lab06/c01_join.py
"""
import _common  # noqa: F401  — sys.path
from _common import bench, fmt, get, hr, p50

from psycopg2.extras import execute_values

from app.sharding.registry import get_router
from app.sharding.router import ConsistentHashRouter
from app.sharding.tools import connect_primary, connect_shards

USER = 101


def main() -> None:
    router = get_router()
    primary = connect_primary("lab6-join")

    hr("1. Где лежат данные JOIN'а bookings ⋈ events ⋈ venues")
    with primary.cursor() as cur:
        # bookings на primary партиционирована: у родителя нет своего хранилища,
        # размер складывается из партиций
        cur.execute("""SELECT (SELECT count(*) FROM events), (SELECT count(*) FROM venues),
                              pg_size_pretty(pg_total_relation_size('events')),
                              pg_size_pretty(pg_total_relation_size('venues')),
                              pg_size_pretty((SELECT sum(pg_total_relation_size(relid))
                                              FROM pg_partition_tree('bookings')))""")
        n_events, n_venues, s_events, s_venues, s_bookings = cur.fetchone()
    primary.commit()
    print(f"  bookings  5 млн строк  — на шардах, по user_id    (на primary: {s_bookings})")
    print(f"  events    {fmt(n_events):>5} строк  — на primary, {s_events}")
    print(f"  venues    {fmt(n_venues):>5} строк  — на primary, {s_venues}")

    hr("2. Если бы мероприятия тоже шардировались — по event_id")
    # раскладка по тем же шардам тем же router'ом; считаем на primary через
    # временные таблицы, чтобы не тянуть 5 млн строк в Python
    event_router = ConsistentHashRouter(router.nodes, router.vnodes)
    with primary.cursor() as cur:
        cur.execute("SELECT id FROM users")
        users = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT id FROM events")
        events = [r[0] for r in cur.fetchall()]
        cur.execute("CREATE TEMP TABLE us (user_id int PRIMARY KEY, shard text)")
        cur.execute("CREATE TEMP TABLE es (event_id int PRIMARY KEY, shard text)")
        execute_values(cur, "INSERT INTO us VALUES %s", [(u, router.shard_for(u)) for u in users])
        execute_values(cur, "INSERT INTO es VALUES %s", [(e, event_router.shard_for(e)) for e in events])
        cur.execute("""
            SELECT count(*) FILTER (WHERE us.shard = es.shard), count(*)
            FROM bookings b JOIN us USING (user_id) JOIN es USING (event_id)""")
        local, total = cur.fetchone()
        cur.execute("""
            SELECT count(DISTINCT es.shard), count(*) FILTER (WHERE es.shard <> us.shard), count(*)
            FROM (SELECT user_id, event_id FROM bookings WHERE user_id = %s
                  ORDER BY created_at DESC LIMIT 20) b
            JOIN us USING (user_id) JOIN es USING (event_id)""", (USER,))
        user_shards, user_remote, user_total = cur.fetchone()
    primary.rollback()
    print(f"  бронирований, у которых мероприятие на том же шарде: {fmt(local)} из {fmt(total)} "
          f"({100 * local / total:.2f} %)")
    print(f"  остальные {100 * (total - local) / total:.2f} % — JOIN между разными серверами")
    print(f"\n  пользователь {USER}, 20 последних бронирований (шард бронирований: "
          f"{router.shard_for(USER)}):")
    print(f"    мероприятия лежат на {user_shards} шардах; "
          f"{user_remote} из {user_total} — не на шарде пользователя")

    hr("3. Решение: копия справочников на каждом шарде (reference tables)")
    shards = connect_shards("lab6-join")
    for name, conn in shards.items():
        with conn.cursor() as cur:
            cur.execute("""SELECT (SELECT count(*) FROM events), (SELECT count(*) FROM venues),
                                  pg_size_pretty(pg_total_relation_size('events')
                                               + pg_total_relation_size('venues'))""")
            e, v, size = cur.fetchone()
        conn.commit()
        print(f"  {name}: events {fmt(e)}, venues {fmt(v)}, вместе {size}")

    shard = router.shard_for(USER)
    print(f"\n  план JOIN на {shard}:")
    with shards[shard].cursor() as cur:
        sql = """EXPLAIN (ANALYZE)
            SELECT b.id, e.title, v.name
            FROM bookings b JOIN events e ON b.event_id = e.id JOIN venues v ON e.venue_id = v.id
            WHERE b.user_id = %s ORDER BY b.created_at DESC LIMIT 20"""
        cur.execute(sql, (USER,))
        cur.execute(sql, (USER,))
        for (line,) in cur.fetchall():
            print("    " + line)
    shards[shard].commit()

    _, app_join, _, _ = get(f"/api/shards/users/{USER}/bookings")
    _, local_join, _, _ = get(f"/api/shards/users/{USER}/bookings?join=local")
    same = ([(r["id"], r["event_title"], r["venue_name"]) for r in app_join["items"]]
            == [(r["id"], r["event_title"], r["venue_name"]) for r in local_join["items"]])
    print(f"\n  JOIN в приложении и JOIN на шарде вернули {'одинаковые' if same else 'РАЗНЫЕ'} строки")

    hr("4. Время ответа, 50 запросов")
    for label, path in [
        ("JOIN в приложении (2 сервера)", f"/api/shards/users/{USER}/bookings"),
        ("JOIN на шарде (1 сервер)", f"/api/shards/users/{USER}/bookings?join=local"),
        ("primary, основное API", f"/api/users/{USER}/bookings"),
    ]:
        get(path)
        times = bench(lambda: get(path), 50)
        print(f"  {label:<32} p50 {p50(times):6.2f} мс")

    hr("5. А если сделать shard key = event_id?")
    print("  bookings ⋈ events станет локальным всегда — бронирование и его мероприятие")
    print("  окажутся на одном шарде по построению. Цена — «мои бронирования»:")
    print(f"  20 последних бронирований пользователя {USER} разложены по {user_shards} шардам,")
    print("  то есть запрос пользователя превратится в scatter-gather.")


if __name__ == "__main__":
    main()
