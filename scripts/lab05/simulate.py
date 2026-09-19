"""
Лабораторная №5, задания 3–7: расчёты на реальных данных сервиса без переноса.

Берёт с primary всех пользователей и число их бронирований и для разных
конфигураций router'а считает:
  - распределение по шардам;
  - сколько пользователей и бронирований сменят шард при изменении их числа.

    docker exec booking_backend python scripts/lab05/simulate.py
"""
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.sharding.router import (                                  # noqa: E402
    ConsistentHashRouter, ModuloRouter, stable_hash,
)
from app.sharding.tools import connect_primary                    # noqa: E402

S3 = ["shard-0", "shard-1", "shard-2"]
S4 = S3 + ["shard-3"]
S5 = S4 + ["shard-4"]
S6 = S4 + ["shard-4", "shard-5"]


def fmt(n) -> str:
    return f"{n:,}".replace(",", " ")


def hr(title: str) -> None:
    print("\n" + "─" * 78 + f"\n  {title}\n" + "─" * 78)


def load_users() -> dict[int, int]:
    conn = connect_primary("lab5-simulate")
    with conn.cursor() as cur:
        cur.execute("SELECT user_id, count(*) FROM bookings GROUP BY user_id")
        data = dict(cur.fetchall())
    conn.close()
    return data


def spread(router, users: dict) -> dict:
    rows = defaultdict(int)
    for user_id, cnt in users.items():
        rows[router.shard_for(user_id)] += cnt
    return {n: rows[n] for n in router.nodes}


def show_spread(title: str, router, users: dict) -> None:
    rows = spread(router, users)
    total = sum(rows.values())
    ideal = total / len(rows)
    counts = list(rows.values())
    cells = "  ".join(f"{n}: {fmt(c)} ({100 * c / total:5.2f} %)" for n, c in rows.items())
    worst = 100 * max(abs(c - ideal) for c in counts) / ideal
    cv = 100 * statistics.pstdev(counts) / ideal
    print(f"  {title:<26} {cells}")
    print(f"  {'':<26} max/min = {max(counts) / min(counts):.3f},  "
          f"худшее отклонение от идеала {worst:.2f} %,  коэф. вариации {cv:.2f} %")


def moved(old, new, users: dict) -> tuple:
    mu = mr = 0
    flows = defaultdict(int)
    for user_id, cnt in users.items():
        a, b = old.shard_for(user_id), new.shard_for(user_id)
        if a != b:
            mu += 1
            mr += cnt
            flows[(a, b)] += cnt
    return mu, mr, flows


def compare(title: str, pairs: list, users: dict, theory: dict) -> None:
    hr(title)
    total_users = len(users)
    total_rows = sum(users.values())
    print(f"  {'стратегия':<34} {'пользователей':>22} {'бронирований':>24}  теория")
    for label, old, new in pairs:
        mu, mr, flows = moved(old, new, users)
        print(f"  {label:<34} {fmt(mu):>10} ({100 * mu / total_users:6.2f} %) "
              f"{fmt(mr):>11} ({100 * mr / total_rows:6.2f} %)  {theory.get(label, '')}")
        dests = defaultdict(int)
        for (_, b), c in flows.items():
            dests[b] += c
        where = ", ".join(f"{b}: {fmt(c)}" for b, c in sorted(dests.items()))
        print(f"  {'':<34} куда уходят: {where}")


def main() -> None:
    users = load_users()
    total_rows = sum(users.values())
    print(f"данные сервиса: {fmt(len(users))} пользователей, {fmt(total_rows)} бронирований")

    hr("Пример работы router'а: user_id 101, 102, 103")
    m3 = ModuloRouter(S3)
    for uid in (101, 102, 103):
        h = stable_hash(uid)
        print(f"  hash({uid}) = {h:>10}   {h} % 3 = {h % 3}  → {m3.shard_for(uid)}")

    hr("Распределение по 3 шардам (задание 4)")
    show_spread("hash % 3", ModuloRouter(S3), users)
    for v in (1, 10, 100, 200):
        show_spread(f"consistent, vnodes={v}", ConsistentHashRouter(S3, v), users)

    hr("Доля кольца у каждого шарда (задание 6, virtual nodes)")
    for v in (1, 10, 100, 200):
        share = ConsistentHashRouter(S3, v).ring_share()
        cells = "  ".join(f"{n}: {100 * s:5.2f} %" for n, s in share.items())
        print(f"  vnodes={v:<4} {cells}")

    compare("3 → 4 шарда: добавляем shard-3 (задания 5 и 7)", [
        ("hash % N", ModuloRouter(S3), ModuloRouter(S4)),
        ("consistent, vnodes=1", ConsistentHashRouter(S3, 1), ConsistentHashRouter(S4, 1)),
        ("consistent, vnodes=100", ConsistentHashRouter(S3, 100), ConsistentHashRouter(S4, 100)),
    ], users, {"hash % N": "75.00 %", "consistent, vnodes=100": "25.00 %"})

    compare("4 → 5 шардов: добавляем ещё один", [
        ("hash % N", ModuloRouter(S4), ModuloRouter(S5)),
        ("consistent, vnodes=100", ConsistentHashRouter(S4, 100), ConsistentHashRouter(S5, 100)),
    ], users, {"hash % N": "80.00 %", "consistent, vnodes=100": "20.00 %"})

    compare("3 → 2 шарда: удаляем shard-1", [
        ("hash % N", ModuloRouter(S3), ModuloRouter(["shard-0", "shard-2"])),
        ("consistent, vnodes=100", ConsistentHashRouter(S3, 100),
         ConsistentHashRouter(["shard-0", "shard-2"], 100)),
    ], users, {"hash % N": "66.67 %", "consistent, vnodes=100": "33.33 %"})

    compare("4 → 3 шарда: удаляем shard-1", [
        ("hash % N", ModuloRouter(S4), ModuloRouter(["shard-0", "shard-2", "shard-3"])),
        ("consistent, vnodes=100", ConsistentHashRouter(S4, 100),
         ConsistentHashRouter(["shard-0", "shard-2", "shard-3"], 100)),
    ], users, {"hash % N": "75.00 %", "consistent, vnodes=100": "25.00 %"})

    compare("3 → 6 шардов: удвоение", [
        ("hash % N", ModuloRouter(S3), ModuloRouter(S6)),
        ("consistent, vnodes=100", ConsistentHashRouter(S3, 100), ConsistentHashRouter(S6, 100)),
    ], users, {"hash % N": "50.00 %", "consistent, vnodes=100": "50.00 %"})


if __name__ == "__main__":
    main()
