"""
Лабораторная №5, задания 5 и 7: физический перенос данных при изменении
числа шардов.

Сравнивает, где пользователь лежит сейчас (по фактическому содержимому
шардов), с тем, куда его отправит новый router. Переносит только тех, у кого
шард изменился; все бронирования пользователя переезжают вместе.

Порядок для каждого исходного шарда:
  1. на целевых шардах удалить строки переезжающих пользователей —
     повторный запуск после сбоя не создаст дубликатов;
  2. COPY строк переезжающих пользователей с исходного шарда на целевые;
  3. COMMIT на целевых;
  4. DELETE на исходном шарде, COMMIT.
Между шагами 3 и 4 строки на короткое время существуют в двух местах —
общей транзакции у независимых PostgreSQL нет. Поэтому router сервиса
переключают на новую конфигурацию только после окончания переноса.

    docker exec booking_backend python scripts/lab05/rebalance.py \\
        --strategy consistent --nodes shard-0,shard-1,shard-2,shard-3 [--dry-run]
"""
import argparse
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import settings                                   # noqa: E402
from app.sharding.router import make_router                       # noqa: E402
from app.sharding.tools import (                                   # noqa: E402
    COLUMNS, ShardDispatcher, connect_shards, distribution,
    misplaced_users, placement, print_distribution, vacuum_analyze,
)


def fmt(n) -> str:
    return f"{n:,}".replace(",", " ")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", choices=("modulo", "consistent"), required=True)
    ap.add_argument("--nodes", required=True, help="шарды НОВОГО router'а через запятую")
    ap.add_argument("--vnodes", type=int, default=settings.shard_vnodes)
    ap.add_argument("--dry-run", action="store_true", help="только посчитать, ничего не переносить")
    args = ap.parse_args()

    nodes = [n.strip() for n in args.nodes.split(",") if n.strip()]
    router = make_router(args.strategy, nodes, args.vnodes)
    shards = connect_shards("lab5-rebalance")

    label = args.strategy + (f", vnodes={args.vnodes}" if args.strategy == "consistent" else "")
    print(f"новый router: {label}, шарды: {', '.join(nodes)}")

    # --- план ---------------------------------------------------------------
    where = placement(shards)
    total_users = len(where)
    total_rows = sum(cnt for _, cnt in where.values())

    moves = defaultdict(lambda: defaultdict(list))      # src → dst → [user_id]
    matrix = defaultdict(int)                             # (src, dst) → строк
    moved_users = moved_rows = 0
    for user_id, (src, cnt) in where.items():
        dst = router.shard_for(user_id)
        if dst != src:
            moves[src][dst].append(user_id)
            matrix[(src, dst)] += cnt
            moved_users += 1
            moved_rows += cnt

    print(f"\nвсего: {fmt(total_users)} пользователей, {fmt(total_rows)} бронирований")
    print(f"переедут: {fmt(moved_users)} пользователей ({100 * moved_users / total_users:.2f} %), "
          f"{fmt(moved_rows)} бронирований ({100 * moved_rows / total_rows:.2f} %)")
    print(f"останутся на месте: {fmt(total_rows - moved_rows)} бронирований "
          f"({100 * (total_rows - moved_rows) / total_rows:.2f} %)")

    names = list(shards)
    print("\nкуда переезжают бронирования (строка — откуда, столбец — куда):")
    print("  " + " " * 9 + "".join(f"{n:>12}" for n in names))
    for src in names:
        cells = "".join(f"{(fmt(matrix[(src, dst)]) if matrix[(src, dst)] else '·'):>12}"
                        for dst in names)
        print(f"  {src:<9}{cells}")

    if args.dry_run:
        print("\n--dry-run: данные не переносились")
        return

    # --- перенос -------------------------------------------------------------
    started = time.perf_counter()
    for src, by_dst in moves.items():
        users = [u for lst in by_dst.values() for u in lst]
        # 1. идемпотентность: вычистить на целевых остатки прерванного запуска
        for dst, dst_users in by_dst.items():
            with shards[dst].cursor() as cur:
                cur.execute("DELETE FROM bookings WHERE user_id = ANY(%s)", (dst_users,))
        # 2. COPY с исходного шарда, каждая строка — в свой целевой шард
        dispatcher = ShardDispatcher(router, shards)
        with shards[src].cursor() as cur:
            query = cur.mogrify(
                f"COPY (SELECT {COLUMNS} FROM bookings WHERE user_id = ANY(%s)) TO STDOUT",
                (users,),
            ).decode()
            cur.copy_expert(query, dispatcher)
        dispatcher.finish()
        # 3. фиксация на целевых
        for dst in by_dst:
            shards[dst].commit()
        # 4. удаление с исходного
        with shards[src].cursor() as cur:
            cur.execute("DELETE FROM bookings WHERE user_id = ANY(%s)", (users,))
            deleted = cur.rowcount
        shards[src].commit()
        print(f"  {src}: перенесено {fmt(sum(dispatcher.rows.values()))}, удалено {fmt(deleted)}")
    elapsed = time.perf_counter() - started
    print(f"\nперенос занял {elapsed:.1f} с")

    # --- проверка ------------------------------------------------------------
    vacuum_analyze(shards)
    bad = misplaced_users(shards, router)
    after = distribution(shards, nodes)
    rows_after = sum(s["bookings"] for s in after)
    print(f"проверка: пользователей не на своём шарде — {bad}; "
          f"бронирований до {fmt(total_rows)}, после {fmt(rows_after)}")
    if bad or rows_after != total_rows:
        sys.exit("ОШИБКА: после переноса данные не соответствуют router'у")

    print("\nраспределение после переноса:")
    print_distribution(after)


if __name__ == "__main__":
    main()
