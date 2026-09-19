"""lab-03: партиционирование bookings по created_at (RANGE, помесячно)

Лабораторная работа №3 «Партиционирование PostgreSQL».
Обоснование выбора — в docs/lab-03-partitioning.md.

Что делает миграция
-------------------
Превращает обычную таблицу ``bookings`` в партиционированную по диапазону
``created_at`` с шагом в месяц.

Порядок действий (стандартный приём для «перевода на партиции на живую»):
  1. старая таблица переименовывается в ``bookings_legacy``;
  2. создаётся новая ``bookings`` с ``PARTITION BY RANGE (created_at)``;
  3. создаются помесячные партиции, покрывающие диапазон имеющихся данных
     плюс горизонт на 3 месяца вперёд;
  4. данные переливаются одним ``INSERT ... SELECT``;
  5. создаются индексы на родительской таблице — PostgreSQL сам заводит
     локальный индекс в каждой партиции;
  6. старая таблица удаляется, последовательность id переподвешивается.

Два изменения схемы, которых требует партиционирование
------------------------------------------------------
* ``created_at`` становится ``NOT NULL`` — ключ партиционирования не может
  быть NULL, иначе строку некуда положить;
* первичный ключ становится составным ``(id, created_at)`` — PostgreSQL
  требует, чтобы ключ партиционирования входил в любой UNIQUE-индекс,
  иначе уникальность пришлось бы проверять по всем партициям сразу.

Важно: перелив 5 млн строк занимает несколько минут. Для боевой системы
такую миграцию делают отдельным окном обслуживания, а не при старте сервиса.

Revision ID: 003
Revises: 002
Create Date: 2026-09-13
"""
from typing import Sequence, Union

from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# на сколько месяцев вперёд сразу нарезать партиции
HORIZON_MONTHS = 3


def upgrade() -> None:
    # ------------------------------------------------------------------ 1
    op.execute("ALTER TABLE bookings RENAME TO bookings_legacy")
    op.execute("ALTER INDEX bookings_pkey RENAME TO bookings_legacy_pkey")
    for old, new in [
        ("ix_bookings_created_at", "ix_bookings_legacy_created_at"),
        ("ix_bookings_event_id", "ix_bookings_legacy_event_id"),
        ("ix_bookings_status_created_at", "ix_bookings_legacy_status_created_at"),
        ("ix_bookings_user_id_created_at", "ix_bookings_legacy_user_id_created_at"),
    ]:
        op.execute(f"ALTER INDEX IF EXISTS {old} RENAME TO {new}")

    # ------------------------------------------------------------------ 2
    # id по-прежнему берётся из той же последовательности, что и раньше,
    # поэтому нумерация не прерывается.
    op.execute("""
        CREATE TABLE bookings (
            id          INTEGER NOT NULL DEFAULT nextval('bookings_id_seq'::regclass),
            user_id     INTEGER NOT NULL,
            event_id    INTEGER NOT NULL,
            status      VARCHAR(20) NOT NULL,
            seats_count INTEGER NOT NULL,
            total_price DOUBLE PRECISION NOT NULL,
            created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at  TIMESTAMP WITH TIME ZONE,
            CONSTRAINT bookings_pkey PRIMARY KEY (id, created_at),
            CONSTRAINT bookings_user_id_fkey  FOREIGN KEY (user_id)  REFERENCES users(id),
            CONSTRAINT bookings_event_id_fkey FOREIGN KEY (event_id) REFERENCES events(id)
        ) PARTITION BY RANGE (created_at)
    """)

    # ------------------------------------------------------------------ 3
    # Границы берём из самих данных, чтобы миграция работала на любом объёме.
    op.execute(f"""
        DO $$
        DECLARE
            d_from DATE;
            d_to   DATE;
            cur    DATE;
            pname  TEXT;
        BEGIN
            SELECT COALESCE(date_trunc('month', min(created_at))::date, date_trunc('month', now())::date),
                   COALESCE(date_trunc('month', max(created_at))::date, date_trunc('month', now())::date)
              INTO d_from, d_to
              FROM bookings_legacy;

            -- горизонт вперёд от текущего месяца
            d_to := GREATEST(d_to, date_trunc('month', now())::date)
                    + ({HORIZON_MONTHS} || ' months')::interval;

            cur := d_from;
            WHILE cur <= d_to LOOP
                pname := 'bookings_' || to_char(cur, 'YYYY_MM');
                EXECUTE format(
                    'CREATE TABLE %I PARTITION OF bookings FOR VALUES FROM (%L) TO (%L)',
                    pname, cur, (cur + INTERVAL '1 month')::date);
                cur := (cur + INTERVAL '1 month')::date;
            END LOOP;

            -- страховочная партиция: строки, не попавшие ни в один диапазон,
            -- не потеряются и не уронят INSERT
            EXECUTE 'CREATE TABLE bookings_default PARTITION OF bookings DEFAULT';
        END $$;
    """)

    # ------------------------------------------------------------------ 4
    op.execute("""
        INSERT INTO bookings (id, user_id, event_id, status, seats_count,
                              total_price, created_at, updated_at)
        SELECT id, user_id, event_id, status, seats_count,
               total_price, COALESCE(created_at, now()), updated_at
        FROM bookings_legacy
    """)

    # ------------------------------------------------------------------ 5
    # CREATE INDEX на родителе автоматически создаёт локальный индекс
    # в каждой существующей и каждой будущей партиции.
    op.execute("CREATE INDEX ix_bookings_user_id_created_at ON bookings (user_id, created_at DESC)")
    op.execute("CREATE INDEX ix_bookings_status_created_at  ON bookings (status,  created_at DESC)")
    op.execute("CREATE INDEX ix_bookings_created_at         ON bookings (created_at)")
    op.execute("CREATE INDEX ix_bookings_event_id           ON bookings (event_id)")

    # ------------------------------------------------------------------ 6
    # Последовательность принадлежит колонке старой таблицы, а новые партиции
    # уже ссылаются на неё в DEFAULT. Пока владелец не отвязан, DROP старой
    # таблицы падает с «default value ... depends on sequence». Поэтому сначала
    # снимаем владельца, затем удаляем таблицу и подвешиваем последовательность
    # к новой колонке.
    op.execute("ALTER SEQUENCE bookings_id_seq OWNED BY NONE")
    op.execute("DROP TABLE bookings_legacy")
    op.execute("ALTER SEQUENCE bookings_id_seq OWNED BY bookings.id")
    op.execute("ANALYZE bookings")

    # ------------------------------------------------------- регистрация в job
    # Чтобы ночная job сама заводила партиции на будущие месяцы.
    op.execute("""
        INSERT INTO partition_mgmt.partition_config
            (schema_name, table_name, step, horizon, retention)
        VALUES ('public', 'bookings', 'month', 3, NULL)
        ON CONFLICT (schema_name, table_name) DO UPDATE
            SET step = EXCLUDED.step, horizon = EXCLUDED.horizon, enabled = true
    """)


def downgrade() -> None:
    """Возврат к обычной (непартиционированной) таблице."""
    op.execute("""
        DELETE FROM partition_mgmt.partition_config
        WHERE schema_name = 'public' AND table_name = 'bookings'
    """)

    op.execute("ALTER TABLE bookings RENAME TO bookings_partitioned")

    # Переименование таблицы НЕ переименовывает её индексы, а имена индексов
    # уникальны в пределах схемы. Без этого создание новой таблицы падает:
    # relation "bookings_pkey" already exists.
    op.execute("ALTER INDEX bookings_pkey RENAME TO bookings_partitioned_pkey")
    for old, new in [
        ("ix_bookings_created_at", "ix_bookings_part_created_at"),
        ("ix_bookings_event_id", "ix_bookings_part_event_id"),
        ("ix_bookings_status_created_at", "ix_bookings_part_status_created_at"),
        ("ix_bookings_user_id_created_at", "ix_bookings_part_user_id_created_at"),
    ]:
        op.execute(f"ALTER INDEX IF EXISTS {old} RENAME TO {new}")

    # Последовательность отвязывается от старой таблицы, чтобы пережить её
    # удаление. Новая таблица объявляется НЕ через SERIAL: SERIAL создал бы
    # вторую последовательность (bookings_id_seq1), потому что исходное имя
    # занято, и нумерация id начала бы дублироваться.
    op.execute("ALTER SEQUENCE bookings_id_seq OWNED BY NONE")

    op.execute("""
        CREATE TABLE bookings (
            id          INTEGER NOT NULL DEFAULT nextval('bookings_id_seq'::regclass),
            user_id     INTEGER NOT NULL REFERENCES users(id),
            event_id    INTEGER NOT NULL REFERENCES events(id),
            status      VARCHAR(20) NOT NULL,
            seats_count INTEGER NOT NULL,
            total_price DOUBLE PRECISION NOT NULL,
            created_at  TIMESTAMP WITH TIME ZONE DEFAULT now(),
            updated_at  TIMESTAMP WITH TIME ZONE,
            CONSTRAINT bookings_pkey PRIMARY KEY (id)
        )
    """)
    op.execute("""
        INSERT INTO bookings (id, user_id, event_id, status, seats_count,
                              total_price, created_at, updated_at)
        SELECT id, user_id, event_id, status, seats_count,
               total_price, created_at, updated_at
        FROM bookings_partitioned
    """)

    op.execute("DROP TABLE bookings_partitioned")
    op.execute("ALTER SEQUENCE bookings_id_seq OWNED BY bookings.id")
    op.execute("SELECT setval('bookings_id_seq', COALESCE((SELECT max(id) FROM bookings), 1))")

    op.create_index("ix_bookings_created_at", "bookings", ["created_at"])
    op.create_index("ix_bookings_event_id", "bookings", ["event_id"])
    op.execute("CREATE INDEX ix_bookings_user_id_created_at ON bookings (user_id, created_at DESC)")
    op.execute("CREATE INDEX ix_bookings_status_created_at  ON bookings (status,  created_at DESC)")
