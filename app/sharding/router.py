"""
Router шардирования (лабораторная №5).

По shard key — у нас это user_id — определяет, в каком экземпляре PostgreSQL
лежат бронирования пользователя. Две стратегии:

  ModuloRouter          shard = hash(key) % N
  ConsistentHashRouter  hash(key) — точка на кольце 0 … 2^32-1;
                        запись принадлежит первому узлу по часовой стрелке

Модуль ничего не знает про базы и подключения — только отображает ключ в имя
шарда. Поэтому один и тот же код используют API сервиса, загрузка данных,
перенос при решардинге и расчёты для отчёта.
"""
import bisect
import hashlib
from collections import defaultdict

RING_SIZE = 2 ** 32


def stable_hash(value) -> int:
    """
    Стабильный хэш в диапазоне 0 … 2^32-1 (первые 4 байта MD5).

    Встроенный hash() не подходит:
      - для строк он случайный в каждом процессе (PYTHONHASHSEED) — два воркера
        бэкенда отправили бы одного пользователя в разные шарды;
      - для чисел hash(101) == 101, то есть это вообще не хэш: при
        последовательных user_id шард определялся бы остатком от самого id.
    Router обязан давать один ответ в любом процессе и после любого перезапуска,
    иначе запись «потеряется» в чужом шарде.

    MD5 здесь не ради криптографии, а ради равномерности и стабильности.
    """
    digest = hashlib.md5(str(value).encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


class ModuloRouter:
    """shard = hash(key) % N."""

    strategy = "modulo"

    def __init__(self, nodes: list[str]):
        if not nodes:
            raise ValueError("router без шардов")
        self.nodes = list(nodes)

    def shard_for(self, key) -> str:
        return self.nodes[stable_hash(key) % len(self.nodes)]

    def explain(self, key) -> dict:
        h = stable_hash(key)
        n = len(self.nodes)
        return {
            "strategy": self.strategy,
            "hash": h,
            "formula": f"{h} % {n} = {h % n}",
            "shard": self.nodes[h % n],
        }


class ConsistentHashRouter:
    """
    Consistent hash ring с виртуальными узлами.

    Каждый шард ставится на кольцо vnodes раз — в точках hash("shard-0#0"),
    hash("shard-0#1"), … Ключ попадает в точку hash(key) и принадлежит первой
    точке шарда по часовой стрелке. После последней точки кольцо замыкается
    на первую.

    vnodes=1 — базовая версия: одна точка на шард.
    """

    strategy = "consistent"

    def __init__(self, nodes: list[str], vnodes: int = 100):
        if not nodes:
            raise ValueError("router без шардов")
        if vnodes < 1:
            raise ValueError("vnodes должно быть >= 1")
        self.nodes = list(nodes)
        self.vnodes = vnodes
        points = sorted(
            (stable_hash(f"{node}#{i}"), node, i)
            for node in self.nodes
            for i in range(vnodes)
        )
        self._points = [p[0] for p in points]
        self._owners = [p[1] for p in points]
        self._labels = [f"{p[1]}#{p[2]}" for p in points]

    def _position(self, h: int) -> int:
        """Индекс первой точки кольца с координатой >= h; за концом — начало кольца."""
        i = bisect.bisect_left(self._points, h)
        return i if i < len(self._points) else 0

    def shard_for(self, key) -> str:
        return self._owners[self._position(stable_hash(key))]

    def explain(self, key) -> dict:
        h = stable_hash(key)
        i = self._position(h)
        return {
            "strategy": self.strategy,
            "vnodes_per_shard": self.vnodes,
            "hash": h,
            "next_point_clockwise": self._points[i],
            "vnode": self._labels[i],
            "wrapped_around_ring": h > self._points[-1],
            "shard": self._owners[i],
        }

    def ring_share(self) -> dict[str, float]:
        """
        Доля окружности, за которую отвечает каждый шард.

        Точка владеет дугой от предыдущей точки (не включая её) до себя.
        Для первой точки предыдущая — последняя точка, сдвинутая на круг назад.
        """
        share = defaultdict(int)
        for i, point in enumerate(self._points):
            previous = self._points[i - 1] if i else self._points[-1] - RING_SIZE
            share[self._owners[i]] += point - previous
        return {node: share[node] / RING_SIZE for node in self.nodes}


def make_router(strategy: str, nodes: list[str], vnodes: int = 100):
    if strategy == "modulo":
        return ModuloRouter(nodes)
    if strategy == "consistent":
        return ConsistentHashRouter(nodes, vnodes)
    raise ValueError(f"неизвестная стратегия шардирования: {strategy!r}")
