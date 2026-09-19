import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy import text as sa_text

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.database import Base
from app.models import *  # noqa: F401,F403 — registers all models with Base.metadata

config = context.config

database_url = os.environ.get("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _partition_names(connection) -> set[str]:
    """Имена таблиц, которые являются партициями."""
    rows = connection.execute(sa_text("""
        SELECT c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relispartition AND n.nspname = current_schema()
    """)).fetchall()
    return {r[0] for r in rows}


def _make_include_object(partitions: set[str]):
    """
    Фильтр для autogenerate.

    Партиции (bookings_2026_09 и т.д.) не описаны в моделях — их создаёт
    миграция 003 и ночная job. Без этого фильтра autogenerate принимает их
    за лишние таблицы и генерирует DROP INDEX для их локальных индексов,
    что сломало бы партиционированную таблицу.
    """
    def include_object(object_, name, type_, reflected, compare_to) -> bool:
        if type_ == "table" and name in partitions:
            return False
        if type_ == "index":
            table = getattr(object_, "table", None)
            if table is not None and table.name in partitions:
                return False
        return True

    return include_object


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    # Список партиций читается ОТДЕЛЬНЫМ соединением. Если выполнить этот
    # SELECT на рабочем соединении до context.begin_transaction(), SQLAlchemy 2.0
    # неявно откроет транзакцию, которую Alembic не закоммитит, — и миграция
    # молча откатится при закрытии соединения (alembic при этом завершится с 0).
    with connectable.connect() as introspection:
        partitions = _partition_names(introspection)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=_make_include_object(partitions),
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
