"""
Лабораторная №6, задание 4: копия справочников на каждом шарде.

Бронирования шардированы по user_id, а мероприятия и площадки живут на primary.
JOIN между ними средствами SQL невозможен — это разные серверы. Стандартное
решение для маленьких справочников — reference tables (broadcast tables):
полная копия на каждом шарде. Тогда JOIN выполняется на шарде локально.

Цена: копию нужно обновлять при каждом изменении мероприятия. Этот скрипт —
простейший вариант: полная перезаливка. В реальной системе — логическая
репликация или событие «мероприятие изменено».

    docker exec booking_backend python scripts/lab06/sync_reference.py
"""
import io
import time

import _common  # noqa: F401  — sys.path
from _common import fmt

from app.sharding.tools import connect_primary, connect_shards

TABLES = {
    "venues": ("id INTEGER PRIMARY KEY, name TEXT NOT NULL", "id, name"),
    "events": ("id INTEGER PRIMARY KEY, title TEXT NOT NULL, venue_id INTEGER NOT NULL, "
               "start_time TIMESTAMPTZ NOT NULL, price DOUBLE PRECISION NOT NULL",
               "id, title, venue_id, start_time, price"),
}


def main() -> None:
    primary = connect_primary("lab6-sync-reference")
    dumps = {}
    for table, (_, cols) in TABLES.items():
        buf = io.StringIO()
        with primary.cursor() as cur:
            cur.copy_expert(f"COPY (SELECT {cols} FROM {table}) TO STDOUT", buf)
        dumps[table] = buf.getvalue()

    started = time.perf_counter()
    for name, conn in connect_shards("lab6-sync-reference").items():
        report = []
        with conn.cursor() as cur:
            for table, (ddl, cols) in TABLES.items():
                cur.execute(f"CREATE TABLE IF NOT EXISTS {table} ({ddl})")
                cur.execute(f"TRUNCATE {table}")
                cur.copy_expert(f"COPY {table} ({cols}) FROM STDIN", io.StringIO(dumps[table]))
                cur.execute(f"ANALYZE {table}")
                cur.execute(f"SELECT count(*), pg_size_pretty(pg_total_relation_size('{table}')) FROM {table}")
                rows, size = cur.fetchone()
                report.append(f"{table}: {fmt(rows)} строк, {size}")
        conn.commit()
        print(f"{name}: " + "; ".join(report))
    print(f"\nкопии справочников обновлены на всех шардах за {(time.perf_counter() - started) * 1000:.0f} мс")


if __name__ == "__main__":
    main()
