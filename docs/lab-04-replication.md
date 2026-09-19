# Лабораторная работа №4

## Масштабирование чтения PostgreSQL: Primary + Replica

**Сервис:** система бронирования мероприятий (FastAPI + PostgreSQL + Alembic)
**Дата выполнения:** 13–14 сентября 2026

---

## Параметры окружения

| Параметр | Значение |
|---|---|
| СУБД | PostgreSQL 16.15, образ `booking-postgres-cron:16` (собран в ЛР3) |
| **Primary** | контейнер `booking_postgres`, сервис `postgres`, порт хоста **5435** |
| **Replica** | контейнер `booking_postgres_replica`, сервис `postgres-replica`, порт хоста **5438** |
| Вид репликации | физическая потоковая (streaming replication), **асинхронная** |
| Слот репликации | `booking_replica_slot` (физический) |
| Роль для репликации | `replicator` — `LOGIN REPLICATION`, не суперпользователь |
| Объём данных | `booking_db` 1991 MB; `bookings` — 5 000 002 строк в 17 партициях |

Порты 5432–5437 на этой машине уже заняты: 5432 — локальный PostgreSQL 18,
5433, 5434, 5436 и 5437 — контейнеры других проектов. Поэтому реплика
получила 5438.

### Схема

```
                         ┌──────────────────────────┐
  POST / PUT / DELETE    │  PRIMARY                 │
  GET карточки ─────────►│  booking_postgres :5435  │
  (get_db)               │  wal_level = replica     │
                         └────────────┬─────────────┘
                                      │ поток WAL
                                      │ walsender → walreceiver
                                      │ слот booking_replica_slot
                         ┌────────────▼─────────────┐
  GET каталог,           │  REPLICA (hot standby)   │
  GET аналитика ────────►│  booking_postgres_replica│
  (get_read_db)          │  :5438, только чтение    │
                         └──────────────────────────┘
```

### Как воспроизвести

```bash
docker compose up -d --build
bash scripts/lab04/a01_topology.sh          # части 1–2
bash scripts/lab04/a02_prove_replication.sh # часть 3
bash scripts/lab04/a03_readonly.sh          # часть 4
bash scripts/lab04/a04_routing.sh           # часть 5
bash scripts/lab04/a06_lag_pause.sh         # часть 6: управляемое отставание
bash scripts/lab04/a07_lag_under_load.sh    # часть 6: отставание под нагрузкой
bash scripts/lab04/a08_lag_validation.sh    # часть 6: проверка самого замера
```

Сырой вывод каждого скрипта — в [`docs/lab-04-raw/`](lab-04-raw/).

### Первая попытка развёртывания не удалась — и почему

Первый запуск реплики упал через 9 секунд:

```
pg_basebackup: starting background WAL receiver
pg_basebackup: error: replication stream was terminated before stop point
pg_basebackup: error: background process terminated unexpectedly
```

Настройки репликации оказались ни при чём. Время создания контейнера primary —
`20:57:38`, в логе primary `database system was shut down at 20:57:38`, а реплика
работала с `20:57:30` до `20:57:39`. Primary **пересоздали посреди копирования**:
это сделала команда `docker compose up -d --build backend`. У `backend` есть
зависимость `postgres`, у `postgres` — секция `build`, и compose пересоздал его
заодно.

Отдельная проверка это подтвердила: `pg_receivewal` через тот же слот стримил WAL
без ошибок. Скрипт развёртывания при сбое очищает том, поэтому повторный запуск
`docker compose up -d --no-deps postgres-replica` прошёл с нуля и успешно.

**Вывод для эксплуатации:** реплика зависит от primary на всём протяжении базовой
копии. Пересобирать сервисы, связанные с primary, в это время нельзя; бэкенд
безопасно обновлять командой `docker compose up -d --no-deps backend`.

---

# ЧАСТЬ 1. Поднять Primary и Replica

## Фрагмент `docker-compose.yml`

Полный файл — [`docker-compose.yml`](../docker-compose.yml). Сервис `postgres`
оставлен с прежним именем, чтобы скрипты лабораторных №1–3 продолжали работать.

```yaml
services:
  # PRIMARY — принимает INSERT / UPDATE / DELETE
  postgres:
    image: booking-postgres-cron:16
    container_name: booking_postgres
    command:
      - postgres
      - -c
      - shared_preload_libraries=pg_cron        # из ЛР3
      - -c
      - cron.database_name=booking_db
      - -c
      - hba_file=/etc/postgresql/pg_hba.conf    # правило для роли replicator
      - -c
      - wal_level=replica                       # WAL содержит всё, что нужно реплике
      - -c
      - max_wal_senders=10
      - -c
      - max_replication_slots=10
    ports:
      - "5435:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./docker/postgres/pg_hba.conf:/etc/postgresql/pg_hba.conf:ro

  # REPLICA — физическая копия primary, только чтение
  postgres-replica:
    image: booking-postgres-cron:16
    container_name: booking_postgres_replica
    entrypoint: ["bash", "/docker/replica-entrypoint.sh"]
    command:
      - postgres
      - -c
      - hot_standby=on            # разрешить SELECT во время воспроизведения WAL
      - -c
      - hot_standby_feedback=on   # не отменять долгие SELECT из-за VACUUM на primary
    environment:
      PRIMARY_HOST: postgres
      PRIMARY_PORT: "5432"
      REPLICATION_USER: replicator
      REPLICATION_SLOT: booking_replica_slot
      REPLICA_APPLICATION_NAME: booking_replica
      # ... пароли и учётка для первичной настройки
    ports:
      - "5438:5432"
    volumes:
      - postgres_replica_data:/var/lib/postgresql/data
      - ./docker/postgres/replica-entrypoint.sh:/docker/replica-entrypoint.sh:ro
      - ./docker/postgres/replica-bootstrap.sql:/docker/replica-bootstrap.sql:ro
    depends_on:
      postgres:
        condition: service_healthy

  backend:
    environment:
      DATABASE_URL:         postgresql://booking_user:booking_pass@postgres:5432/booking_db
      REPLICA_DATABASE_URL: postgresql://booking_user:booking_pass@postgres-replica:5432/booking_db
```

## Какой контейнер кем является

| | Primary | Replica |
|---|---|---|
| Контейнер | `booking_postgres` | `booking_postgres_replica` |
| Сервис compose | `postgres` | `postgres-replica` |
| Том | `postgres_data` | `postgres_replica_data` |
| `pg_is_in_recovery()` | **`f`** | **`t`** |
| `transaction_read_only` | `off` | **`on`** |
| Признак на диске | — | файл `standby.signal` |

Роль определяет не имя контейнера, а состояние сервера: `pg_is_in_recovery() = true`
означает, что сервер находится в режиме постоянного восстановления и воспроизводит
чужой WAL.

## Как подключиться к каждому экземпляру

| Откуда | Primary | Replica |
|---|---|---|
| pgAdmin / psql с хоста | `127.0.0.1:5435` | `127.0.0.1:5438` |
| Из контейнеров (backend) | `postgres:5432` | `postgres-replica:5432` |
| Консоль в контейнере | `docker exec -it booking_postgres psql -U booking_user -d booking_db` | `docker exec -it booking_postgres_replica psql -U booking_user -d booking_db` |
| Переменная бэкенда | `DATABASE_URL` | `REPLICA_DATABASE_URL` |

Учётные данные для обоих: `booking_user` / `booking_pass`, база `booking_db`.
Роль `replicator` предназначена только для протокола репликации.

## Проверка: оба запущены и доступны

```
NAMES                      STATUS                   PORTS
booking_postgres_replica   Up 3 minutes (healthy)   0.0.0.0:5438->5432/tcp
booking_postgres           Up 8 minutes (healthy)   0.0.0.0:5435->5432/tcp
```

Подключение с хоста — так же, как из pgAdmin:

```
5435 → PRIMARY, bookings=5000004
5438 → REPLICA (read-only), bookings=5000004
```

---

# ЧАСТЬ 2. Настроить streaming replication

## Что потребовалось на primary

**Параметры WAL.** `wal_level = replica`, `max_wal_senders = 10`
и `max_replication_slots = 10` — это значения по умолчанию в PostgreSQL 16.
Они прописаны в compose явно, чтобы требования к primary были видны в конфигурации.

**Правило `pg_hba.conf`.** Без него ничего не работало бы. Файл
[`docker/postgres/pg_hba.conf`](../docker/postgres/pg_hba.conf) подключён через
`hba_file` и хранится в проекте, а не только внутри тома:

```
host    replication     replicator      samenet                 scram-sha-256
```

Ключевое слово `all` в колонке `DATABASE` **не покрывает** replication-подключения.
В образе по умолчанию было только правило `host all all all scram-sha-256`,
поэтому `pg_basebackup` и приёмник WAL с другого контейнера получили бы отказ.
`samenet` разрешает подключение только из подсети, где находится сам сервер.

```
 line_number | type  |   database    |  user_name   |  address  |  auth_method
-------------+-------+---------------+--------------+-----------+---------------
          15 | local | {replication} | {all}        |           | trust
          16 | host  | {replication} | {all}        | 127.0.0.1 | trust
          17 | host  | {replication} | {all}        | ::1       | trust
          23 | host  | {replication} | {replicator} | samenet   | scram-sha-256
```

**Роль и слот** ([`docker/postgres/replica-bootstrap.sql`](../docker/postgres/replica-bootstrap.sql)):

```
   rolname    | rolsuper | rolreplication
--------------+----------+----------------
 booking_user | t        | t
 replicator   | f        | t
```

Атрибут `REPLICATION` разрешает открывать replication-подключения, но не даёт прав
на сами таблицы. Реплика не пользуется суперпользователем.

Физический слот `booking_replica_slot` заставляет primary хранить WAL, который реплика
ещё не забрала. Без слота долгая остановка реплики закончилась бы тем, что нужные
сегменты удалены и реплику пришлось бы пересоздавать. У слота есть обратная сторона:
`max_slot_wal_keep_size = -1`, то есть ограничения нет. Если реплику удалить
и забыть слот, WAL на primary будет копиться до заполнения диска. Поэтому
`retained_wal` в `pg_replication_slots` нужно мониторить.

## Как разворачивается реплика

[`docker/postgres/replica-entrypoint.sh`](../docker/postgres/replica-entrypoint.sh)
при первом старте с пустым томом:

1. ждёт, пока primary начнёт принимать подключения;
2. создаёт роль `replicator` и слот, если их ещё нет;
3. снимает физическую копию кластера через `pg_basebackup --wal-method=stream --slot=...`;
4. создаёт `standby.signal` и дописывает в `postgresql.auto.conf` адрес primary и имя слота.

```
pg_basebackup: write-ahead log start point: 4/FA000060 on timeline 1
pg_basebackup: starting background WAL receiver
pg_basebackup: write-ahead log end point: 4/FA000138
pg_basebackup: base backup completed
[replica-bootstrap] копия снята за 23 с
```

Затем сервер стартует уже как standby:

```
LOG:  entering standby mode
LOG:  consistent recovery state reached at 4/FA000138
LOG:  database system is ready to accept read-only connections
LOG:  started streaming WAL from primary at 4/FB000000 on timeline 1
```

От пустого тома до `healthy` прошло **33 секунды**. При повторных стартах копия
не снимается: скрипт видит данные и сразу запускает standby, а стриминг продолжается
с позиции, сохранённой в слоте. Это проверено остановкой и запуском реплики в части 5:

```
[replica-bootstrap] данные уже есть — стартую как standby без повторной копии
LOG:  started streaming WAL from primary at 4/FB000000 on timeline 1
```

Конфигурация standby на диске реплики:

```
-rw-r--r-- 1 postgres postgres 0 Sep 13 21:03 /var/lib/postgresql/data/standby.signal
primary_conninfo = 'host=postgres port=5432 user=replicator password=*** application_name=booking_replica'
primary_slot_name = 'booking_replica_slot'
```

Два параметра запуска реплики:

- **`hot_standby = on`** — принимать `SELECT`, пока идёт воспроизведение WAL.
  Без него реплика была бы только горячим резервом, а не источником чтения.
- **`hot_standby_feedback = on`** — реплика сообщает primary, какие версии строк
  ещё нужны её запросам. Иначе `VACUUM` на primary удалит их, и долгий `SELECT`
  на реплике (например, выручка по 5 млн бронирований) будет отменён с ошибкой
  `canceling statement due to conflict with recovery`. Цена — `VACUUM` на primary
  не может чистить такие строки, пока идёт запрос на реплике.

## Цепочка: как изменение попадает на реплику

| Шаг | Где | Процесс | Что видно |
|---|---|---|---|
| 1. Primary изменяет данные | primary | backend-процесс сессии | `INSERT` меняет страницу в `shared_buffers` |
| 2. Изменение фиксируется в WAL | primary | тот же процесс + walwriter | при `COMMIT` запись WAL сбрасывается в `pg_wal`, растёт `pg_current_wal_lsn()` |
| 3. Replica получает поток WAL | primary → replica | **walsender** (pid 630) → **walreceiver** (pid 89) | `sent_lsn` → `write_lsn` → `flush_lsn` |
| 4. Replica воспроизводит изменения | replica | **startup**-процесс | `replay_lsn`, строка видна запросам на реплике |

Каждой стадии соответствует своя позиция в `pg_stat_replication`. Отставание
на любой из них — это и есть replication lag, разложенный по этапам:
`write_lag`, `flush_lag` и `replay_lag`.

## Состояние репликации на primary

```sql
SELECT * FROM pg_stat_replication;
```

```
pid              | 630
usename          | replicator
application_name | booking_replica
client_addr      | 172.20.0.5
state            | streaming
sync_state       | async
sent_lsn         | 4/FB003848
write_lsn        | 4/FB003848
flush_lsn        | 4/FB003848
replay_lsn       | 4/FB003848
write_lag        |
flush_lag        |
replay_lag       |
replay_gap_bytes | 0
backend_start    | 2026-09-13 21:03:39.516216+00
```

- **`state = streaming`** — реплика догнала primary и получает WAL в реальном времени.
  До этого было бы `catchup`.
- **`sync_state = async`** — primary не ждёт реплику при `COMMIT`.
  `synchronous_standby_names` пуст.
- **Все четыре LSN совпадают**, разрыв 0 байт — реплика применила всё.
- **`*_lag` пустые (NULL)** — это не ошибка. Время отставания считается по свежим
  подтверждениям от реплики, и когда на primary давно не было записей, измерять нечего.
  Под нагрузкой значения появляются — см. часть 6.

Слот на primary и приёмник на реплике:

```
slot_name    | booking_replica_slot        pid                   | 89
slot_type    | physical                    status                | streaming
active       | t                           sender_host           | postgres
active_pid   | 630                         slot_name             | booking_replica_slot
restart_lsn  | 4/FB003848                  written_lsn           | 4/FB003848
wal_status   | reserved                    flushed_lsn           | 4/FB003848
retained_wal | 0 bytes                     last_msg_send_time    | 21:06:30.211399
                                           last_msg_receipt_time | 21:06:30.211587
```

`active_pid` слота (630) совпадает с pid walsender'а. Между отправкой служебного
сообщения на primary и его получением на реплике — **0.19 мс**.

## Копия одинаковая

| | Primary | Replica |
|---|---|---|
| Строк в `bookings` | 5 000 002 | 5 000 002 |
| Партиций `bookings` | 17 | 17 |
| Версия миграций Alembic | `003` | `003` |
| Размер `booking_db` | 1991 MB | 1991 MB |

На реплике не выполнялась ни одна миграция и не создавалась ни одна партиция —
всё это пришло физической копией и потоком WAL.

---

# ЧАСТЬ 3. Доказать, что репликация работает

Скрипт: [`scripts/lab04/a02_prove_replication.sh`](../scripts/lab04/a02_prove_replication.sh).

## Шаг 0. DDL тоже реплицируется

Таблица `lab4.replication_probe` создана **только на primary**. Через секунду на реплике:

```
    table_on_replica
------------------------
 lab4.replication_probe
```

Физическая репликация не различает `CREATE TABLE` и `INSERT` — и то и другое
является изменением файлов, записанным в WAL.

## Шаг 1–2. INSERT на primary → SELECT на replica

**Запись на primary:**

```sql
INSERT INTO lab4.replication_probe (marker, note)
VALUES ('lab4-sql-1789333599', 'вставлено на primary')
RETURNING id, marker, note, created_at;
```

```
 id |       marker        |         note         |          created_at
----+---------------------+----------------------+-------------------------------
  1 | lab4-sql-1789333599 | вставлено на primary | 2026-09-13 21:06:40.010655+00

 primary_lsn_after_commit
--------------------------
 4/FB02E768
```

**Чтение на replica:**

```sql
SELECT id, marker, note, created_at FROM lab4.replication_probe
WHERE marker = 'lab4-sql-1789333599';
```

```
 id |       marker        |         note         |          created_at
----+---------------------+----------------------+-------------------------------
  1 | lab4-sql-1789333599 | вставлено на primary | 2026-09-13 21:06:40.010655+00

 replica_replayed_lsn
----------------------
 4/FB02E768
```

Строка та же, вплоть до микросекунд в `created_at`. Значение не вычислялось
на реплике заново — пришло байтами из WAL. Позиция воспроизведения реплики
**совпадает** с позицией primary после `COMMIT`.

## Шаг 3–4. Запись через API сервиса

```bash
curl -X POST http://localhost:8000/api/bookings \
     -H 'Content-Type: application/json' \
     -d '{"user_id": 1, "event_id": 1, "seats_count": 2}'
```

```json
{"event_id":1,"user_id":1,"id":5000003,"status":"confirmed","seats_count":2,
 "total_price":8596.36,"created_at":"2026-09-13T21:06:41.575900+00:00"}
```

На реплике:

```
   id    | user_id | event_id | seats_count | total_price |  status   |    partition
---------+---------+----------+-------------+-------------+-----------+------------------
 5000003 |       1 |        1 |           2 |     8596.36 | confirmed | bookings_2026_09
```

Бронирование пришло в нужную месячную партицию из ЛР3 — реплика повторяет
и данные, и физическое устройство таблицы.

## Шаг 5. UPDATE на primary → новое значение на replica

```
primary: UPDATE bookings SET status = 'cancelled' WHERE id = 5000003  →  cancelled
replica: SELECT id, status FROM bookings WHERE id = 5000003           →  cancelled
```

## Шаг 6. Подтверждение, что реплика подключена

```
application_name | booking_replica
client_addr      | 172.20.0.5
state            | streaming
sync_state       | async
sent_lsn         | 4/FB03DF40
replay_lsn       | 4/FB03DF40
replay_lag       | 00:00:00.002111
```

Теперь `replay_lag` заполнен: после недавних записей реплика применила изменения
через **2.1 мс**.

---

# ЧАСТЬ 4. Read-only поведение Replica

Скрипт: [`scripts/lab04/a03_readonly.sh`](../scripts/lab04/a03_readonly.sh).

## Результат

| Попытка на реплике | Ответ сервера |
|---|---|
| `INSERT INTO lab4.replication_probe ...` | `ERROR: cannot execute INSERT in a read-only transaction` |
| `UPDATE events SET title = title WHERE id = 1` | `ERROR: cannot execute UPDATE in a read-only transaction` |
| `DELETE FROM lab4.replication_probe ...` | `ERROR: cannot execute DELETE in a read-only transaction` |
| `CREATE TABLE lab4.should_not_exist (id int)` | `ERROR: cannot execute CREATE TABLE in a read-only transaction` |
| `BEGIN READ WRITE` | `ERROR: cannot set transaction read-write mode during recovery` |
| `SELECT nextval('lab4.replication_probe_id_seq')` | `ERROR: cannot execute nextval() in a read-only transaction` |
| `CREATE TEMP TABLE scratch (x int)` | `ERROR: cannot execute CREATE TABLE in a read-only transaction` |

На primary после этого: `leaked_rows = 0`, `leaked_table = NULL` — ничего не просочилось.

## Почему так

```
 in_recovery | transaction_read_only | default_transaction_read_only
-------------+-----------------------+-------------------------------
 t           | on                    | off
```

`default_transaction_read_only = off` — в настройках запись не запрещена. Её запрещает
**режим восстановления**: пока сервер воспроизводит чужой WAL, любая его транзакция
принудительно read-only, и снять это нельзя даже явным `BEGIN READ WRITE`.

Два неочевидных пункта таблицы:

- **`nextval()`** — это запись. Сдвиг последовательности меняет её страницу
  и пишется в WAL.
- **Временная таблица** — тоже запись: даже временный объект требует строк
  в системном каталоге.

## Почему приложение не должно писать в Replica как в независимую базу

1. **Физически это невозможно, пока она реплика** — результат выше. Физическая
   реплика — побайтовая копия файлов primary, и любая собственная запись конфликтовала
   бы со страницами, приходящими в WAL.

2. **Если «разрешить» запись повышением (`pg_promote()`), получится split-brain.**
   Повышенный сервер выходит из восстановления, открывает новую timeline и перестаёт
   принимать WAL. Теперь две базы принимают записи независимо и расходятся.
   Физическая репликация не умеет сливать изменения обратно: при возврате
   одну из сторон приходится перематывать (`pg_rewind`) и **терять** её изменения.

3. **Два писателя сломают целостность.** Последовательность `id` у каждого сервера
   своя: бронирование `5000003` появилось бы дважды с разным содержимым. Уникальность,
   внешние ключи и остатки мест проверяются в пределах одного сервера.

4. **Асинхронное отставание приводит к потерянным обновлениям.** Решение, принятое
   по устаревшим данным (например, «мест ещё хватает»), перезапишет или нарушит
   уже сделанные на primary изменения.

Единственный источник истины для записи — primary. Реплика — производная копия
для чтения.

## Защита в самом приложении

Читающие сессии бэкенда открываются с `default_transaction_read_only=on`. Это важно
для случая, когда реплика недоступна и чтение уходит на primary (часть 5):
случайная запись через читающую сессию всё равно не пройдёт.

```
сессия подключена к: primary
отказ: ReadOnlySqlTransaction - cannot execute INSERT in a read-only transaction
```

---

# ЧАСТЬ 5. Направить чтение собственного сервиса на Replica

## Как устроено в коде

[`app/config.py`](../app/config.py) — второй адрес:

```python
database_url: str = "postgresql://...@localhost:5432/booking_db"       # primary
replica_database_url: Optional[str] = None                             # replica
replica_connect_timeout: int = 2
```

[`app/database.py`](../app/database.py) — два пула соединений и две зависимости FastAPI:

```python
engine = create_engine(settings.database_url, pool_pre_ping=True,
    connect_args={"application_name": "booking-backend-primary"})

replica_engine = create_engine(settings.replica_database_url, pool_pre_ping=True,
    connect_args={
        "application_name": "booking-backend-replica",
        "options": "-c default_transaction_read_only=on",
        "connect_timeout": settings.replica_connect_timeout,
    })


def get_db():                        # primary: запись и read-your-writes
    ...


def get_read_db(response: Response): # replica: чтение, допускающее отставание
    db = _open_replica_session()     # None, если реплика не отвечает
    if db is not None:
        node = "replica"
    else:
        db = FallbackReadSessionLocal()   # primary, но read-only
        node = "primary-fallback"
    response.headers["X-DB-Node"] = node
    ...
```

Роутер выбирает сервер одной строкой — зависимостью
([`app/routers/events.py`](../app/routers/events.py)):

```python
@router.get("")
def get_events(..., db: Session = Depends(get_read_db)):      # → replica
    return EventService(db).get_all(...)


@router.get("/{event_id}")
def get_event(event_id: int, db: Session = Depends(get_db)):  # → primary
    return EventService(db).get_by_id(event_id)


@router.put("/{event_id}")
def update_event(event_id: int, data: EventUpdate, db: Session = Depends(get_db)):
    return EventService(db).update(event_id, data)            # → primary
```

Сервисы и репозитории не менялись: они получают сессию и не знают, к какому
серверу она подключена. Миграции Alembic по-прежнему используют только
`DATABASE_URL`, то есть primary.

## Какие запросы куда и почему

| Endpoint | Сервер | Почему |
|---|---|---|
| `GET /api/events` | **replica** | каталог — самое частое чтение; мероприятие, появившееся в списке на миллисекунды позже, никому не вредит |
| `GET /api/events/stats/categories` | **replica** | статистика, отставание несущественно |
| `GET /api/bookings/stats/revenue` | **replica** | агрегация по 5 млн бронирований — тяжёлый запрос, незачем нагружать им сервер, принимающий записи |
| `GET /api/events/{id}` | primary | карточку открывают сразу после создания или правки мероприятия — нужны read-your-writes |
| `GET /api/bookings`, `GET /api/bookings/{id}` | primary | пользователь смотрит бронирование сразу после оформления |
| `GET /api/users/{id}/bookings` | primary | «мои бронирования» после оплаты должны быть на месте |
| все `POST`, `PUT`, `DELETE` | primary | запись возможна только на primary |

Критерий один: **выдержит ли сценарий то, что реплика отстаёт**. Каталог и аналитика —
да. Всё, что пользователь смотрит сразу после своего действия, — нет.
Часть 6 показывает, что этот выбор не перестраховка.

## Доказательство 1: заголовок `X-DB-Node`

```
GET /api/events?page_size=3          → replica
GET /api/events/stats/categories     → replica
GET /api/bookings/stats/revenue      → replica
GET /api/events/1                    → primary (get_db, без заголовка)
GET /api/bookings?page_size=3        → primary (get_db, без заголовка)
GET /api/users/1/bookings            → primary (get_db, без заголовка)
```

## Доказательство 2: соединения бэкенда на каждом сервере

У каждого пула свой `application_name`, поэтому в `pg_stat_activity`
на каждом сервере видно, чьи это соединения:

```
PRIMARY                                   REPLICA
      application_name      | connections       application_name     | connections
----------------------------+-------------    -------------------------+-------------
 booking-backend-primary    |           1      booking-backend-replica |           1
 booking-backend-primary-ro |           1
```

Соединение `booking-backend-primary-ro` — это пул запасного пути для чтения.
Он остался открытым после предыдущего прогона, в котором реплику останавливали.

## Доказательство 3: запись попала на primary

```
POST /api/bookings → HTTP 201, id=5000005
n_tup_ins по партициям bookings на PRIMARY: было 5000004, стало 5000005
n_tup_ins по партициям bookings на REPLICA: было 0, стало 0
строка при этом есть на обоих серверах:
  primary: id=5000005 created_at=2026-09-13 21:11:13.053169+00
  replica: id=5000005 created_at=2026-09-13 21:11:13.053169+00
```

Счётчик вставок вырос только на primary. На реплике строка есть, но счётчик
по-прежнему 0: реплика ничего не вставляла, она **воспроизвела WAL**, а это
не считается выполнением `INSERT`.

При первом прогоне этот шаг показал «было 5000003, стало 5000003» — ожидание
в 2 секунды оказалось короче интервала, с которым простаивающее соединение
из пула сбрасывает статистику (около 10 секунд). После увеличения ожидания
до 12 секунд счётчик отражает вставку.

## Реплика недоступна: чтение уходит на primary

Реплику остановили командой `docker stop booking_postgres_replica`:

```
GET /api/events → HTTP 200
GET /api/events?page_size=3          → primary-fallback
GET /api/bookings/stats/revenue      → primary-fallback
```

`GET /health/db`:

```json
"replica": {
    "status": "down",
    "error": "could not translate host name \"postgres-replica\" to address: Name or service not known"
}
```

Сервис продолжил отвечать `200`. В логе бэкенда:

```
replica недоступна, чтение уходит на primary: could not translate host name "postgres-replica" ...
```

После `docker start` реплика прошла healthcheck, догнала primary через свой слот
и снова стала обслуживать чтение:

```
GET /api/events?page_size=3          → replica
application_name | booking_replica
state            | streaming
replay_lag       | 00:00:00.000395
```

## Диагностика: `GET /health/db`

```json
"replica": {
    "status": "up",
    "role": "replica",
    "in_recovery": true,
    "application_name": "booking-backend-replica",
    "transaction_read_only": "on",
    "wal_lsn": "5/28AF31F0",
    "replay_lsn": "5/28AF31F0",
    "replay_paused": false,
    "seconds_since_last_replayed_tx": 52.321
}
```

`seconds_since_last_replayed_tx = 52.3` при полностью совпадающих `wal_lsn`
и `replay_lsn` — хорошая иллюстрация того, что **время с последней воспроизведённой
транзакции не равно отставанию**. Реплика ничего не догоняет, просто на primary
52 секунды никто не писал. Настоящее отставание смотрят в `pg_stat_replication`
на primary.

## Ограничения этой реализации

- **Маршрутизация по endpoint'ам, а не по запросам.** Read-your-writes обеспечено
  тем, что «свежие» endpoint'ы оставлены на primary. Точнее было бы запоминать LSN
  последней записи пользователя и читать с реплики, только когда она его догнала.
- **Реплика одна, балансировки нет.** Для нескольких реплик понадобится список
  пулов с распределением запросов или прокси вроде PgBouncer, HAProxy или Pgpool-II.
- **Отставание не учитывается при выборе сервера.** Даже сильно отставшая реплика
  продолжит обслуживать каталог — это видно в части 6. Для продакшена стоит отключать
  реплику от чтения при превышении порога `replay_lag`.
- **Запасной путь переносит всё чтение на primary.** Отказ реплики не роняет сервис,
  но primary должен иметь запас мощности на такую нагрузку.

---

# ЧАСТЬ 6. Понять replication lag

Отставание исследовано тремя способами — и отдельно проверено, что замер не врёт:

| Сценарий | Скрипт | Что показывает |
|---|---|---|
| Управляемое отставание | [`a06_lag_pause.sh`](../scripts/lab04/a06_lag_pause.sh) | «на primary новое, на реплике старое» — гарантированно, через API сервиса |
| Запись → немедленное чтение | [`a07_lag_under_load.sh`](../scripts/lab04/a07_lag_under_load.sh) | естественное отставание в спокойной системе и под нагрузкой |
| Проверка замера | [`a08_lag_validation.sh`](../scripts/lab04/a08_lag_validation.sh) | контроль через primary и серия пауз перед чтением |
| Большая транзакция | `a07` часть C и `a08` | сколько реплика догоняет после крупной записи |

Замеры «записал → прочитал» делает пробник
[`a05_lag_probe.py`](../scripts/lab04/a05_lag_probe.py). Он работает внутри контейнера
бэкенда, по той же сети, что и приложение, с точностью до долей миллисекунды.

## 1. Тот самый момент: primary уже новое, replica ещё старое

В локальной сети docker реплика обычно догоняет primary за несколько миллисекунд,
и увидеть расхождение глазами трудно. Поэтому воспроизведение WAL на реплике
ставится на паузу функцией `pg_wal_replay_pause()`. Это **управляемая эмуляция**
отставания: WAL продолжает приходить, но не применяется. Весь сценарий идёт через
API на отдельном тестовом мероприятии.

**До паузы** — мероприятие создано, реплика его уже получила:

```
каталог  GET /api/events?search=lab4-lag-demo   [X-DB-Node: replica] -> lab4-lag-demo OLD title
карточка GET /api/events/2001                   [primary]            -> lab4-lag-demo OLD title
```

**Пауза, затем правка** через `PUT /api/events/2001` (запись идёт на primary):

```
ответ PUT: lab4-lag-demo NEW title
```

**Момент расхождения:**

```
карточка GET /api/events/2001                   [primary]            -> lab4-lag-demo NEW title
каталог  GET /api/events?search=lab4-lag-demo   [X-DB-Node: replica] -> lab4-lag-demo OLD title

прямым SQL:
primary -> lab4-lag-demo NEW title
replica -> lab4-lag-demo OLD title
```

Именно это увидел бы пользователь: открыл карточку — название новое, вернулся
в каталог — старое. Ради этого в части 5 карточка оставлена на primary.

**WAL дошёл, но не применён.** Реплика получила 176 байт — это WAL самой правки:

```
replica: получено 4/FB043DC0, применено 4/FB043D10, не применено 176 bytes, состояние: paused
primary: текущий LSN 4/FB043DC0, реплике отправлено 4/FB043DC0, реплика применила 4/FB043D10, разрыв 176 bytes
```

После ещё 5000 строк на primary «получено» ушло вперёд, «применено» стоит на месте:

```
replica: получено 4/FB135368, применено 4/FB043D10, не применено 966 kB, состояние: paused
primary: текущий LSN 4/FB135368, реплике отправлено 4/FB135368, реплика применила 4/FB043D10, разрыв 966 kB
```

Это разделение этапов из части 2 в чистом виде: доставка WAL (`received`, `sent`)
и воспроизведение (`replayed`, `replay`) — разные стадии, и отставать может каждая.

**Пауза снята** — реплика догнала, каталог показывает новое название:

```
replica: получено 4/FB135368, применено 4/FB135368, не применено 0 bytes, состояние: not paused
карточка GET /api/events/2001                   [primary]            -> lab4-lag-demo NEW title
каталог  GET /api/events?search=lab4-lag-demo   [X-DB-Node: replica] -> lab4-lag-demo NEW title
```

Тестовое мероприятие затем удалено (`DELETE → 204`), каталог его больше не находит.

## 2. Естественное отставание: запись и сразу чтение

Пробник 300 раз подряд делает то, что делало бы приложение: `INSERT` на primary,
а сразу после возврата из `COMMIT` — `SELECT` этой строки с реплики.

| Сценарий | Первое чтение устарело | Строка появилась через (p50 / p95 / max) | Отставание по WAL (оценка) |
|---|---|---|---|
| Спокойная система | **299 из 300 (99.7 %)** | 2.34 / 3.18 / 4.13 мс | p50 296 байт, max 7 152 байт |
| Primary пишет пачки по 100 000 строк | **300 из 300 (100 %)** | 2.62 / 14.07 / 34.23 мс | p50 320 байт, max 2.3 МБ |

Типичная запись в спокойной системе:

```
на primary закоммичено, при первом SELECT строки нет;
позади примерно на 296 байт WAL, строка появилась через 2.50 мс
```

Под нагрузкой — отставание уже на мегабайты:

```
на primary закоммичено, при первом SELECT строки нет;
позади примерно на 1569760 байт WAL, строка появилась через 31.33 мс
```

Почему устаревает почти каждое первое чтение даже без нагрузки: `COMMIT` на primary
возвращается, как только WAL записан на **его** диск. Дальше WAL ещё нужно отправить,
записать и сбросить на диск реплики, а затем воспроизвести. А следующий `SELECT`
уходит на реплику через доли миллисекунды — быстрее, чем проходит этот путь.

> **Об оценке байт WAL.** `pg_last_wal_replay_lsn()` вычисляется уже после того,
> как запрос взял снимок данных, поэтому значение слегка «из будущего».
> Байты — ориентир, а не точная величина; иногда оценка даже равна нулю при невидимой
> строке. Время до появления измеряется независимо и от этого эффекта не страдает.

## 3. Проверка: это отставание, а не ошибка замера

99.7 % — слишком круглая цифра, чтобы принимать её на веру. Проверено двумя способами.

**Контроль.** Тот же пробник читает не с реплики, а с primary:

```
читаем с:                primary
итераций:                300
первое чтение устарело:  0  (0.0 %)
```

Если бы ошибка была в самом пробнике — например, чтение уходило бы раньше `COMMIT`, —
устаревшие чтения появились бы и здесь.

**Пауза между `COMMIT` и чтением.** Если причина в отставании реплики, то с ростом паузы
доля устаревших чтений должна падать:

| Пауза перед чтением | Читаем с | Устарело | Строка появилась через (p50 / p95 / max) |
|---|---|---|---|
| 0 мс | replica | **198 / 200 (99.0 %)** | 3.16 / 32.37 / 38.48 мс |
| 1 мс | replica | **130 / 200 (65.0 %)** | 3.30 / 32.45 / 36.91 мс |
| 2 мс | replica | 9 / 200 (4.5 %) | 39.35 / 43.79 / 43.79 мс |
| 5 мс | replica | 4 / 200 (2.0 %) | 36.37 / 38.50 / 38.50 мс |
| 10 мс | replica | 9 / 200 (4.5 %) | 39.23 / 41.45 / 41.45 мс |
| 20 мс | replica | 4 / 200 (2.0 %) | 37.48 / 40.05 / 40.05 мс |
| 50 мс | replica | **0 / 200 (0 %)** | — |
| 0 мс | primary | 0 / 200 (0 %) | — |
| 5 мс | primary | 0 / 200 (0 %) | — |

Картина двухрежимная:

- **основная масса** записей появляется на реплике за **2–3 мс**: уже при паузе в 2 мс
  устаревает лишь 4.5 % чтений;
- **хвост** из 2–5 % записей доезжает **около 40 мс**. Поэтому при паузах 2–20 мс
  устаревшими остаются только эти случаи, и их p50 около 37–39 мс. Причина хвоста
  отдельно не исследовалась; вероятнее всего, это сброс WAL на диск реплики,
  который делит один диск с primary.
- **при паузе 50 мс** из 200 чтений не устарело ни одно.

Чтение с primary не устаревает ни при какой паузе. Значит, измеряется именно путь
изменения до реплики.

## 4. Большая транзакция

Одна вставка на 1 000 000 строк. Пока транзакция идёт, WAL уже течёт на реплику,
но строки не видны **никому** — ни на primary, ни на реплике: транзакция атомарна.

Отставание во время вставки (опрос `pg_stat_replication` раз в полсекунды):

```
21:16:58.621  отставание: 2753 kB  write_lag=00:00:00.000452  flush_lag=00:00:00.002049  replay_lag=00:00:00.057956
21:17:01.747  отставание: 12 MB    write_lag=00:00:00.034486  flush_lag=00:00:00.034486  replay_lag=00:00:00.246496
21:17:09.776  отставание: 15 MB    write_lag=00:00:00.120857  flush_lag=00:00:00.685289  replay_lag=00:00:01.051335
21:17:12.927  отставание: 24 MB    write_lag=00:00:00.407297  flush_lag=00:00:00.407297  replay_lag=00:00:00.906481
21:17:14.459  отставание: 20 MB    write_lag=00:00:00.440083  flush_lag=00:00:00.440083  replay_lag=00:00:03.296497
21:17:17.180  отставание: 0 bytes  write_lag=00:00:00.486703  flush_lag=00:00:00.623656  replay_lag=00:00:00.685376
```

Под нагрузкой реплика отставала до **24 МБ WAL**, а `replay_lag` доходил до **3.3 секунды**.
Стадии хорошо видны: `write_lag` и `flush_lag` — десятые доли секунды, `replay_lag`
заметно больше. Узкое место — воспроизведение, которое на реплике выполняет один
процесс startup.

Сколько реплика догоняет после `COMMIT` (опрос каждую миллисекунду):

| Прогон | `INSERT` на primary | Строка видна на реплике после `COMMIT` |
|---|---|---|
| `a08`, прогон 1 | 6.02 с | **108.7 мс** |
| `a08`, прогон 2 | 5.55 с | **120.0 мс** |
| `a08`, прогон 3 | 5.35 с | **49.3 мс** |
| `a07`, с параллельным мониторингом | 17.49 с | **686.5 мс** |

В `a07` параллельно раз в полсекунды опрашивался `pg_stat_replication`, а оба сервера
делят один диск и процессор. Вставка шла втрое дольше, и реплика отставала сильнее.
Отставание зависит не только от объёма записи, но и от того, что ещё нагружает
систему.

## Что это значит для сервиса

1. **Чтение сразу после записи с реплики почти всегда устаревшее.** В этой среде —
   99 % случаев при чтении без паузы. Решение части 5 оставить карточку мероприятия,
   бронирования и «мои бронирования» на primary подтверждено измерением,
   а не только теорией.

2. **Каталог и аналитика отставание переносят.** Мероприятие, появившееся в списке
   на 3–40 мс позже, никто не заметит. Даже 3 секунды во время массовой загрузки
   для отчёта о выручке некритичны.

3. **Отставание растёт с нагрузкой на запись.** Спокойно — единицы миллисекунд, под
   пачками вставок — десятки, при крупной транзакции — секунды. Поэтому отставание
   нужно мониторить (`replay_lag` и разрыв в байтах в `pg_stat_replication`)
   и выводить реплику из чтения при превышении порога.

4. **Способы получить свежие данные, не отказываясь от реплики:**
   - запоминать `pg_current_wal_lsn()` после записи пользователя и читать с реплики,
     только когда её `pg_last_wal_replay_lsn()` его догнал;
   - читать с primary несколько секунд после записи этого пользователя;
   - `synchronous_commit = remote_apply` вместе с `synchronous_standby_names` —
     `COMMIT` ждёт применения на реплике. Отставания для чтения нет, но каждая запись
     становится медленнее, а при недоступной реплике запись останавливается.

## Главный вывод

**Репликация не означает мгновенную синхронизацию.** Даже когда оба сервера стоят
на одной машине и связаны виртуальной сетью, между `COMMIT` на primary и появлением
строки на реплике проходит в среднем 2–3 мс, в хвосте около 40 мс, под нагрузкой —
до 3.3 секунды. Этого хватает, чтобы 99 % чтений сразу после записи вернули старые данные.
Асинхронная реплика даёт **согласованность в конечном счёте** (eventual consistency):
данные придут, но не мгновенно.

> Оба сервера работают в Docker Desktop на одном компьютере: общий диск, общий
> процессор и виртуальная сеть без сетевой задержки. Между разными серверами или
> дата-центрами отставание будет больше. Абсолютные миллисекунды привязаны
> к этой среде, а сами закономерности — нет.

---

# Контрольные вопросы

### 1. Чем Primary отличается от Replica?

**Primary** — единственный сервер, который принимает запись. Он источник WAL:
все изменения сначала появляются у него.

**Replica** — сервер в режиме постоянного восстановления: `pg_is_in_recovery() = true`,
на диске лежит `standby.signal`. Он не порождает изменения сам, а воспроизводит
чужой WAL. Любая его транзакция принудительно read-only.

| | Primary | Replica |
|---|---|---|
| Запись | да | нет — `cannot execute INSERT in a read-only transaction` |
| Чтение | да | да (`hot_standby = on`) |
| WAL | порождает и отдаёт (walsender) | получает (walreceiver) и воспроизводит (startup) |
| Сколько в топологии | один | сколько угодно |
| Данные | эталон | побайтовая копия с небольшим отставанием |

Replica можно повысить до primary (`pg_promote()`) — так устроен failover.
Но пока она реплика, она только читает.

### 2. Почему запись выполняем на Primary?

- **Иначе нельзя физически.** Реплика в режиме восстановления отвергает любую запись —
  проверено на семи видах операций в части 4, включая `nextval()` и временные таблицы.
- **Нужен один источник истины.** Последовательности, уникальные ключи, внешние ключи,
  остатки мест проверяются в пределах одного сервера. Два независимых писателя выдали бы
  одинаковые `id` и нарушили ограничения.
- **Физическая репликация однонаправленная.** Если повысить реплику и писать в обе базы,
  они разойдутся (split-brain), а слить изменения обратно физическая репликация не умеет.
- **Реплика отстаёт.** Решение, принятое по её устаревшим данным, привело бы
  к потерянным обновлениям.

### 3. Как изменение из Primary попадает на Replica?

1. Backend-процесс на primary выполняет `INSERT` и меняет страницу в `shared_buffers`.
2. При `COMMIT` запись WAL сбрасывается в `pg_wal` на диск primary. Растёт
   `pg_current_wal_lsn()`, и клиент получает подтверждение.
3. Процесс **walsender** на primary читает новый WAL и отправляет его по
   replication-подключению. На реплике процесс **walreceiver** принимает его,
   записывает и сбрасывает в свой `pg_wal`. Позиции — `sent_lsn`, `write_lsn`, `flush_lsn`.
4. Процесс **startup** на реплике воспроизводит записи WAL, то есть применяет те же
   изменения к своим страницам. Позиция — `replay_lsn`. С этого момента строка
   видна запросам на реплике.

Перед этим реплика получает исходную копию кластера через `pg_basebackup`, а слот
репликации гарантирует, что primary не удалит WAL, который реплика ещё не забрала.
В части 3 позиция primary после `COMMIT` (`4/FB02E768`) совпала с позицией
воспроизведения реплики.

### 4. Что такое WAL в контексте репликации?

**WAL (Write-Ahead Log)** — журнал упреждающей записи. Любое изменение сначала
последовательно дописывается в журнал и сбрасывается на диск, и только потом
транзакция считается зафиксированной. Страницы таблиц обновляются на диске позже.
Каждая запись имеет адрес — **LSN**, позицию в журнале.

Изначально WAL нужен для восстановления после сбоя: после аварии сервер
проигрывает журнал и возвращает данные в согласованное состояние.
**Потоковая репликация — это то же восстановление, только непрерывное и на другом
сервере.** Реплика бесконечно проигрывает WAL, который получает от primary.

Следствия, проверенные в работе:

- WAL описывает изменения **на уровне страниц**, поэтому реплицируется всё:
  `CREATE TABLE` (часть 3), партиции из ЛР3, версия миграций Alembic, последовательности;
- нужен `wal_level = replica`, чтобы журнал содержал достаточно информации;
- отставание удобно измерять в байтах WAL: `pg_wal_lsn_diff(sent_lsn, replay_lsn)`.

### 5. Что такое replication lag?

Задержка между фиксацией изменения на primary и его применением на реплике.
Её измеряют двумя способами:

- **в байтах** — насколько позиция воспроизведения реплики отстаёт от текущей позиции
  WAL primary: `pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn)`;
- **во времени** — по стадиям в `pg_stat_replication`: `write_lag` (записано на реплике),
  `flush_lag` (сброшено на её диск), `replay_lag` (применено).

Измерено в этой работе:

| Условия | Отставание |
|---|---|
| Спокойная система | строка видна через 2.34 мс по медиане, 4.13 мс максимум |
| Пачки записей на primary | p95 14 мс, максимум 34 мс, до 2.3 МБ WAL |
| Крупная транзакция | до 24 МБ WAL, `replay_lag` до 3.3 с |

Отставание возникает, потому что репликация **асинхронная**: primary подтверждает
`COMMIT`, не дожидаясь реплики. Оно растёт с объёмом записи, с сетевой задержкой,
с медленным диском реплики и с долгими запросами на ней.

### 6. Почему следующий SELECT после INSERT может увидеть старые данные, если отправить его на Replica?

Потому что `COMMIT` на primary завершается раньше, чем изменение доедет до реплики
и будет воспроизведено. Клиент получает «успешно» и сразу отправляет `SELECT`,
а реплика ещё не применила эту запись.

Это не теоретическая возможность, а измеренная норма:

- чтение сразу после `COMMIT` оказалось устаревшим в **99.7 %** случаев даже без нагрузки;
- при паузе в 1 мс — ещё в 65 %, при 50 мс — ни разу из 200;
- при чтении с primary — ни разу ни при какой паузе.

В сценарии с паузой воспроизведения это видно через API: карточка с primary
показывает новое название, а каталог с реплики — старое.

Такое поведение называется **eventual consistency**. Поэтому чтения, которым нужны
результаты собственной записи пользователя, отправляются на primary. Альтернативы —
ожидание нужного LSN на реплике или синхронная репликация с `remote_apply`.

### 7. Что именно масштабируется при Read Scaling?

**Способность системы обслуживать больше чтений, а не скорость отдельного запроса.**

Реплика — точная копия: те же 5 млн строк, те же индексы, тот же планировщик. Запрос
на ней выполняется по тому же плану и не быстрее, чем на primary. А если реплика
отстаёт или нагружена — медленнее.

Масштабируются:

- **пропускная способность чтения** — две копии обслуживают примерно вдвое больше
  одновременных `SELECT`, три — втрое;
- **изоляция нагрузки** — тяжёлая выручка по 5 млн бронирований работает на реплике
  и не отнимает процессор и диск у сервера, принимающего оплаты;
- **доступность чтения** — при отказе реплики сервис переключил чтение на primary
  и продолжил отвечать `200`.

Не масштабируется **запись**: primary по-прежнему один.

### 8. Почему наличие Replica не отменяет необходимость индексов и оптимизации SQL?

- **Плохой запрос одинаково плох на любой копии.** В ЛР2 выборка за 30 дней читала
  все 45 461 страницу таблицы за 823 мс. На реплике у неё тот же объём данных и тот же
  план, значит, те же 823 мс. Реплики умножают число таких запросов, которые можно
  выполнить одновременно, но не ускоряют ни один из них.
- **Реплики растут линейно, а стоимость плохого запроса — с объёмом данных.** Добавить
  реплику — это ещё одна полная копия базы (здесь 3.7 ГБ), ещё процессор и память.
  Индекс, ускоряющий запрос в сотни раз, дешевле десятков реплик.
- **Долгие запросы на реплике конфликтуют с репликацией.** Им нужны старые версии строк,
  которые `VACUUM` на primary хочет удалить. Без `hot_standby_feedback` такой запрос
  отменяется, с ним на primary копятся мёртвые строки. Чем быстрее запросы, тем меньше
  этого конфликта.
- **Запись остаётся на primary.** Её не разгружает ни одна реплика, и там по-прежнему
  важны и лишние, и недостающие индексы.

Индексы реплицируются вместе с данными, поэтому хороший индекс ускоряет запрос
сразу на всех копиях.

### 9. CAP-теорема

CAP-теорема говорит о распределённой системе, у которой узлы могут потерять связь
друг с другом. Из трёх свойств одновременно гарантировать можно только два:

- **C (Consistency)** — все узлы видят одни и те же данные: чтение с любого узла
  возвращает последнюю запись или ошибку, но не устаревшие данные;
- **A (Availability)** — любой работающий узел отвечает на запрос успешно, хотя данные
  могут быть не самыми свежими;
- **P (Partition tolerance)** — система продолжает работать при разрыве сети между узлами.

Разрыв сети в распределённой системе не выбирают — он просто случается. Поэтому
реальный выбор делается между **C и A на время разрыва**: CP-система отказывает
части запросов ради согласованности, AP-система отвечает всем, но может вернуть
устаревшие данные.

**Где в этом наша схема.** Асинхронная реплика для чтения ведёт себя как **AP**.
Пауза воспроизведения в части 6 — модель разрыва: реплика перестала применять
изменения, но **продолжала отвечать** каталогу, отдавая старое название. Доступность
сохранена, согласованность — нет. После восстановления реплика догнала primary через
слот — это согласованность в конечном счёте. Запасной путь чтения из части 5 — тоже
выбор в пользу доступности.

Синхронная репликация (`synchronous_standby_names` и `synchronous_commit = remote_apply`)
сдвигает систему к **CP**: чтение с реплики согласовано, но если реплика недоступна,
`COMMIT` на primary ждёт — запись перестаёт быть доступной.

Правило «два из трёх» — упрощение. CAP говорит только о поведении во время разрыва.
Уточнение **PACELC** добавляет: даже **без разрыва** приходится выбирать между
задержкой (Latency) и согласованностью. Наши 2–3 мс отставания при исправной сети —
этот выбор: асинхронная репликация покупает быструю запись ценой того, что реплика
чуть позади.

---

# Общие выводы

1. **Primary + Replica подняты воспроизводимо.** Одно правило `pg_hba`, роль `replicator`,
   слот и скрипт развёртывания. Реплика базы на 2 ГБ сама разворачивается за 33 секунды
   (копия — 23 с), а при перезапуске продолжает стриминг без новой копии.

2. **Физическая репликация копирует всё.** Данные, DDL, 17 партиций из ЛР3,
   версию миграций, последовательности: на обоих серверах 5 000 002 бронирования
   и одинаковые 1991 MB.

3. **Реплика строго read-only.** Все семь видов записи отвергнуты, включая `nextval()`
   и временные таблицы. Приложение дополнительно открывает читающие сессии
   в режиме read-only, чтобы запись не прошла даже при откате на primary.

4. **Чтение сервиса разделено по смыслу, а не по типу запроса.** Каталог, статистика
   категорий и выручка идут на реплику. Всё, что пользователь смотрит сразу после своего
   действия, — на primary. Это доказано заголовком `X-DB-Node`, соединениями
   в `pg_stat_activity` и счётчиком вставок. При отказе реплики сервис продолжил
   отвечать `200`.

5. **Репликация не мгновенна даже в идеальных условиях.** На одной машине строка
   появляется на реплике через 2–3 мс, в хвосте через 40 мс. Чтение сразу после
   `COMMIT` было устаревшим в 99.7 % случаев. Контрольный прогон через primary (0 %)
   и серия пауз подтвердили, что это отставание, а не ошибка замера.

6. **Отставание растёт с нагрузкой на запись.** Пачки вставок — p95 14 мс, крупная
   транзакция — до 24 МБ WAL и `replay_lag` 3.3 с. Узкое место — воспроизведение
   на реплике.

7. **Read Scaling масштабирует количество чтений, а не скорость запроса.** Индексы
   и оптимизация SQL остаются необходимыми: плохой запрос одинаково плох
   на каждой копии.

8. **Реплика зависит от primary и требует присмотра.** Первая базовая копия сорвалась
   из-за пересоздания primary в compose. Слот без `max_slot_wal_keep_size` при забытой
   реплике накапливал бы WAL до заполнения диска. Отставание и слоты нужно мониторить.
