from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Primary: запись и чтение, которому нужны свежие данные
    database_url: str = "postgresql://booking_user:booking_pass@localhost:5432/booking_db"

    # Replica (лабораторная №4): чтение, допускающее отставание.
    # Пусто — все запросы идут на primary.
    replica_database_url: Optional[str] = None
    replica_database_urls: str = ""

    @property
    def replica_urls(self) -> list[str]:
        raw = self.replica_database_urls or self.replica_database_url or ""
        return [u.strip() for u in raw.split(",") if u.strip()]

    # Сколько секунд ждать подключения к реплике, прежде чем уйти на primary
    replica_connect_timeout: int = 2

    # Шардирование (лабораторная №5).
    # shards — все известные шарды: "shard-0=postgresql://...,shard-1=postgresql://..."
    # shard_router_nodes — какие из них участвуют в router'е; пусто — все
    shards: str = ""
    shard_router_nodes: str = ""
    shard_strategy: str = "consistent"   # consistent | modulo
    shard_vnodes: int = 100              # виртуальных узлов на шард в hash ring

    @property
    def shard_urls(self) -> dict[str, str]:
        urls = {}
        for item in self.shards.split(","):
            name, sep, url = item.strip().partition("=")
            if sep:
                urls[name.strip()] = url.strip()
        return urls

    @property
    def shard_router_names(self) -> list[str]:
        chosen = [n.strip() for n in self.shard_router_nodes.split(",") if n.strip()]
        return chosen or list(self.shard_urls)

    class Config:
        env_file = ".env"


settings = Settings()
