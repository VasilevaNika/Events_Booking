"""
Лабораторная №5, задания 3–4: раскладываем бронирования сервиса по шардам.

Реальные бронирования читаются с primary одним потоком COPY; каждая строка
уходит в шард, который router выбрал по её user_id, и пишется туда пачками
через COPY. Перед загрузкой очищаются ВСЕ шарды из SHARDS — шард,
не входящий в --nodes, после загрузки пуст.

    docker exec booking_backend python scripts/lab05/load.py \\
        --strategy modulo --nodes shard-0,shard-1,shard-2
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import settings                                   # noqa: E402
from app.sharding.router import make_router                       # noqa: E402
from app.sharding.tools import (                                   # noqa: E402
    COLUMNS, ShardDispatcher, connect_primary, connect_shards,
    distribution, print_distribution, vacuum_analyze,
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", choices=("modulo", "consistent"), required=True)
    ap.add_argument("--nodes", required=True, help="шарды в router'е через запятую")
    ap.add_argument("--vnodes", type=int, default=settings.shard_vnodes)
    args = ap.parse_args()

    nodes = [n.strip() for n in args.nodes.split(",") if n.strip()]
    router = make_router(args.strategy, nodes, args.vnodes)
    shards = connect_shards("lab5-load")
    unknown = set(nodes) - set(shards)
    if unknown:
        sys.exit(f"нет подключения к шардам: {sorted(unknown)}")

    label = args.strategy + (f", vnodes={args.vnodes}" if args.strategy == "consistent" else "")
    print(f"router: {label}, шарды: {', '.join(nodes)}")

    for conn in shards.values():
        with conn.cursor() as cur:
            cur.execute("TRUNCATE bookings")
        conn.commit()
    print("все шарды очищены")

    primary = connect_primary("lab5-load")
    with primary.cursor() as cur:
        cur.execute("SELECT count(*) FROM bookings")
        expected = cur.fetchone()[0]

    started = time.perf_counter()
    dispatcher = ShardDispatcher(router, shards)
    with primary.cursor() as cur:
        cur.copy_expert(f"COPY (SELECT {COLUMNS} FROM bookings) TO STDOUT", dispatcher)
    dispatcher.finish()
    for conn in shards.values():
        conn.commit()
    elapsed = time.perf_counter() - started

    loaded = sum(dispatcher.rows.values())
    print(f"загружено {loaded:,} бронирований за {elapsed:.1f} с "
          f"(на primary {expected:,})".replace(",", " "))
    if loaded != expected:
        sys.exit("ОШИБКА: число загруженных строк не совпало с primary")

    vacuum_analyze(shards)
    print("\nраспределение по шардам:")
    print_distribution(distribution(shards, nodes))


if __name__ == "__main__":
    main()
