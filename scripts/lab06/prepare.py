"""
Лабораторная №6, подготовка шардов.

1. Удаляет бронирования, которые есть только на шардах и отсутствуют на primary
   (тестовая запись из демонстрации ЛР5). После этого шарды в сумме содержат
   ровно те же бронирования, что primary, и результаты распределённых запросов
   можно сверять с primary как с эталоном.
2. Создаёт индекс (created_at DESC, id DESC) для «последних бронирований системы»
   на шардах, созданных до его появления в docker/shards/init.sql.
3. VACUUM ANALYZE.

    docker exec booking_backend python scripts/lab06/prepare.py
"""
import _common  # noqa: F401  — sys.path
from _common import fmt

from app.sharding.tools import connect_primary, connect_shards, vacuum_analyze


def main() -> None:
    primary = connect_primary("lab6-prepare")
    with primary.cursor() as cur:
        cur.execute("SELECT count(*), max(id) FROM bookings")
        primary_rows, primary_max_id = cur.fetchone()
    print(f"primary: {fmt(primary_rows)} бронирований, max id {primary_max_id}")

    shards = connect_shards("lab6-prepare")
    for name, conn in shards.items():
        with conn.cursor() as cur:
            cur.execute("DELETE FROM bookings WHERE id > %s RETURNING id, user_id", (primary_max_id,))
            removed = cur.fetchall()
            cur.execute("""CREATE INDEX IF NOT EXISTS ix_bookings_created_at_id
                           ON bookings (created_at DESC, id DESC)""")
        conn.commit()
        if removed:
            print(f"{name}: удалены записи, которых нет на primary: {removed}")

    vacuum_analyze(shards)

    total = 0
    for name, conn in shards.items():
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM bookings")
            n = cur.fetchone()[0]
            cur.execute("""SELECT indexname FROM pg_indexes
                           WHERE tablename = 'bookings' ORDER BY indexname""")
            indexes = ", ".join(r[0] for r in cur.fetchall())
        conn.commit()
        total += n
        print(f"{name}: {fmt(n):>11} бронирований; индексы: {indexes}")

    verdict = "совпадает" if total == primary_rows else "НЕ СОВПАДАЕТ"
    print(f"\nсумма по шардам {fmt(total)}, на primary {fmt(primary_rows)} — {verdict}")


if __name__ == "__main__":
    main()
