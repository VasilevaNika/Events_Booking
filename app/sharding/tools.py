"""
Операции над шардами для скриптов лабораторной №5: загрузка, перенос
при изменении числа шардов, подсчёт распределения.

Работают на psycopg2 напрямую — нужен COPY, самый быстрый способ перегнать
миллионы строк между серверами.
"""
import io
from collections import defaultdict

import psycopg2

from app.config import settings

COLUMNS = "id, user_id, event_id, status, seats_count, total_price, created_at, updated_at"
FLUSH_LINES = 200_000


def connect_shards(application_name: str) -> dict:
    return {
        name: psycopg2.connect(url, application_name=application_name)
        for name, url in settings.shard_urls.items()
    }


def connect_primary(application_name: str):
    return psycopg2.connect(settings.database_url, application_name=application_name)


class ShardDispatcher(io.TextIOBase):
    """
    Приёмник для COPY ... TO STDOUT.

    psycopg2 пишет в него текст COPY кусками; каждая строка отправляется
    в шард, который router выбрал по её user_id (вторая колонка), и пишется
    туда пачками через COPY ... FROM STDIN. Одна строка — одно бронирование.
    """

    def __init__(self, router, conns: dict):
        super().__init__()
        self._router = router
        self._conns = conns
        self._cache: dict[str, str] = {}      # user_id → шард: 50 000 хэшей вместо 5 млн
        self._buffers = defaultdict(list)
        self._tail = ""
        self.rows = defaultdict(int)

    def writable(self) -> bool:
        return True

    def _route(self, line: str) -> str:
        user_id = line.split("\t", 2)[1]
        shard = self._cache.get(user_id)
        if shard is None:
            shard = self._cache[user_id] = self._router.shard_for(int(user_id))
        return shard

    def write(self, chunk: str) -> int:
        lines = (self._tail + chunk).split("\n")
        self._tail = lines.pop()               # последняя строка может быть неполной
        for line in lines:
            if line:
                shard = self._route(line)
                buffer = self._buffers[shard]
                buffer.append(line)
                if len(buffer) >= FLUSH_LINES:
                    self._flush(shard)
        return len(chunk)

    def finish(self) -> None:
        if self._tail:
            self._buffers[self._route(self._tail)].append(self._tail)
            self._tail = ""
        for shard in list(self._buffers):
            self._flush(shard)

    def _flush(self, shard: str) -> None:
        lines = self._buffers.pop(shard, [])
        if not lines:
            return
        with self._conns[shard].cursor() as cur:
            cur.copy_expert(
                f"COPY bookings ({COLUMNS}) FROM STDIN",
                io.StringIO("\n".join(lines) + "\n"),
            )
        self.rows[shard] += len(lines)


def placement(conns: dict) -> dict:
    """Где фактически лежит каждый пользователь: user_id → (шард, бронирований)."""
    where = {}
    for name, conn in conns.items():
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, count(*) FROM bookings GROUP BY user_id")
            for user_id, cnt in cur.fetchall():
                where[user_id] = (name, cnt)
        conn.commit()
    return where


def misplaced_users(conns: dict, router) -> int:
    """Сколько пользователей лежит не в том шарде, куда их отправил бы router."""
    return sum(1 for user_id, (shard, _) in placement(conns).items()
               if router.shard_for(user_id) != shard)


def vacuum_analyze(conns: dict) -> None:
    for conn in conns.values():
        old = conn.autocommit
        conn.autocommit = True                 # VACUUM нельзя выполнять в транзакции
        with conn.cursor() as cur:
            cur.execute("VACUUM ANALYZE bookings")
        conn.autocommit = old


def distribution(conns: dict, router_nodes: list[str]) -> list[dict]:
    """Фактическое распределение: бронирования, пользователи, размер на каждом шарде."""
    stats = []
    for name, conn in conns.items():
        with conn.cursor() as cur:
            cur.execute("""
                SELECT count(*), count(DISTINCT user_id),
                       pg_size_pretty(pg_total_relation_size('bookings'))
                FROM bookings""")
            bookings, users, size = cur.fetchone()
        conn.commit()
        stats.append({"shard": name, "in_router": name in router_nodes,
                      "bookings": bookings, "users": users, "size": size})
    return stats


def print_distribution(stats: list[dict]) -> None:
    active = [s for s in stats if s["in_router"]]
    total = sum(s["bookings"] for s in stats)
    ideal = total / len(active) if active else 0
    print(f"  {'шард':<9} {'в router':<9} {'пользователей':>14} {'бронирований':>14} "
          f"{'доля':>8} {'от идеала':>10} {'размер':>9}")
    for s in stats:
        share = 100 * s["bookings"] / total if total else 0
        dev = (f"{100 * (s['bookings'] - ideal) / ideal:+.2f} %"
               if s["in_router"] and ideal else "—")
        print(f"  {s['shard']:<9} {'да' if s['in_router'] else 'нет':<9} {s['users']:>14,} "
              f"{s['bookings']:>14,} {share:>7.2f}% {dev:>10} {s['size']:>9}".replace(",", " "))
    print(f"  {'итого':<9} {'':<9} {sum(s['users'] for s in stats):>14,} {total:>14,}"
          .replace(",", " "))
    if active and ideal:
        counts = [s["bookings"] for s in active]
        print(f"  идеально на шард: {ideal:,.0f};  max/min = {max(counts) / min(counts):.4f};  "
              f"наибольшее отклонение от идеала: "
              f"{100 * max(abs(c - ideal) for c in counts) / ideal:.2f} %".replace(",", " "))
