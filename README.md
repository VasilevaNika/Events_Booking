# Booking Service — Сервис бронирования мероприятий

## О проекте

Backend-сервис для управления бронированием мест на мероприятия. Пользователи могут
просматривать мероприятия (концерты, театр, спорт, конференции и т.д.), бронировать
места и управлять своими бронированиями.

**Предметная область** хорошо подходит для масштабирования: количество бронирований
постоянно растёт по мере проведения новых мероприятий, что делает таблицу `bookings`
идеальным кандидатом для партиционирования и шардирования.

---

## Технологический стек

| Компонент     | Технология               |
|---------------|--------------------------|
| Язык          | Python 3.12              |
| Фреймворк     | FastAPI                  |
| База данных   | PostgreSQL 16 + pg_cron  |
| Репликация    | Streaming replication: primary + hot standby |
| ORM           | SQLAlchemy 2             |
| Миграции      | Alembic                  |
| Контейнеры    | Docker + Docker Compose  |
| Документация  | Swagger / OpenAPI (встроен в FastAPI) |

---

## Запуск

```bash
docker compose up --build
```

Это запустит:

| Сервис | Контейнер | Порт на хосте | Роль |
|---|---|---|---|
| `postgres` | `booking_postgres` | `5435` | **Primary** — запись и чтение свежих данных |
| `postgres-replica` | `booking_postgres_replica` | `5438` | **Replica** — hot standby, только чтение |
| `backend` | `booking_backend` | `8000` | FastAPI; миграции применяются при старте |
| `mailsink` | `booking_mailsink` | `1025` | SMTP-приёмник для alert'ов о партициях |

При первом запуске реплика сама снимает `pg_basebackup` с primary и включает
стриминг WAL — на базе в несколько гигабайт это десятки секунд.

После запуска:
- API: http://localhost:8000
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- Health check: http://localhost:8000/health
- Состояние primary и replica: http://localhost:8000/health/db

---

## Архитектура

```
Client
  ↓
FastAPI Routers  (app/routers/)      — HTTP-слой, валидация входных данных
  ↓
Services         (app/services/)     — бизнес-логика
  ↓
Repositories     (app/repositories/) — работа с БД (SQL-запросы)
  ↓
PostgreSQL
```

Контроллеры (routers) никогда не обращаются к БД напрямую.

### Чтение и запись: primary + replica

```
                 get_db ───────────────►  PRIMARY  ── запись, read-your-writes
FastAPI                                      │
                 get_read_db ─────────►  REPLICA   ── каталог, аналитика
                     │                       ▲
                     └ реплика недоступна    │ поток WAL (streaming replication)
                       → primary read-only ──┘
```

Зависимости FastAPI из [`app/database.py`](app/database.py):

| Зависимость | Сервер | Где используется |
|---|---|---|
| `get_db` | primary | все записи; карточки `GET /api/events/{id}`, `GET /api/bookings/{id}`, списки бронирований |
| `get_read_db` | replica | `GET /api/events`, `GET /api/events/stats/categories`, `GET /api/bookings/stats/revenue` |

Какой сервер обслужил запрос, видно в заголовке ответа `X-DB-Node`
(`replica`, `primary-fallback` или `primary`). Если реплика недоступна, читающие
запросы автоматически уходят на primary, но в режиме `read-only` — случайная
запись через читающую сессию невозможна.

Подробности — в [`docs/lab-04-replication.md`](docs/lab-04-replication.md).

---

## Схема БД

### Таблицы

```
users
  id            PK
  email         UNIQUE, NOT NULL
  name          NOT NULL
  phone
  created_at

venues
  id            PK
  name          NOT NULL
  address       NOT NULL
  capacity      NOT NULL
  description
  created_at

categories
  id            PK
  name          UNIQUE, NOT NULL
  description

events
  id            PK
  title         NOT NULL
  description
  venue_id      FK → venues.id
  start_time    NOT NULL
  end_time      NOT NULL
  price         NOT NULL
  max_capacity  NOT NULL
  created_at
  updated_at

event_categories            ← many-to-many: events ↔ categories
  event_id      FK → events.id
  category_id   FK → categories.id

bookings                    ← основная растущая сущность
  id            PK
  user_id       FK → users.id
  event_id      FK → events.id
  status        (pending | confirmed | cancelled | completed)
  seats_count   NOT NULL
  total_price   NOT NULL
  created_at    NOT NULL, INDEX
  updated_at
```

### Связи

| Тип          | Пример                          |
|--------------|---------------------------------|
| one-to-many  | venues → events                 |
| one-to-many  | users → bookings                |
| one-to-many  | events → bookings               |
| many-to-many | events ↔ categories (через event_categories) |

---

## Основная сущность для масштабирования

**Таблица:** `bookings`

**Почему она подходит:**
Каждое бронирование — это транзакционная запись, привязанная к конкретному
мероприятию и пользователю. При активной работе сервиса новые бронирования
создаются непрерывно. Таблица будет расти пропорционально количеству пользователей
и мероприятий. Она содержит поле `created_at` (идеально для партиционирования по дате),
индексируемые поля `status`, `user_id`, `event_id`, и участвует в сложных JOIN-запросах.

Остальные таблицы — справочники с ограниченным ростом: число площадок упирается
в физическое количество залов, категории почти не меняются, а один пользователь
даёт ровно одну строку навсегда. `bookings` же растёт как
**пользователи × их активность × время** и ничем не ограничена.

### Индексы `bookings`

Набор индексов подобран по результатам замеров `EXPLAIN ANALYZE` на миллионе строк —
см. [`docs/lab-01-indexes.md`](docs/lab-01-indexes.md), миграция
[`002_booking_indexes.py`](migrations/versions/002_booking_indexes.py).

| Индекс | Колонки | Под какие запросы |
|---|---|---|
| `bookings_pkey` | `id` (UNIQUE) | `GET/PUT/DELETE /api/bookings/{id}` |
| `ix_bookings_user_id_created_at` | `user_id`, `created_at DESC` | `GET /api/users/{id}/bookings`, `GET /api/users/{id}/stats` |
| `ix_bookings_status_created_at` | `status`, `created_at DESC` | `GET /api/bookings?status=&from_date=` + `COUNT(*)` пагинации |
| `ix_bookings_created_at` | `created_at` | фильтры по дате без статуса, сортировки |
| `ix_bookings_event_id` | `event_id` | `GET /api/events/{id}/bookings`, поддержка FK |

Порядок колонок в составных индексах подчинён правилу **«равенства слева, диапазон
и сортировка справа»**: `WHERE user_id = ? ORDER BY created_at DESC LIMIT 20`
обслуживается одним индексом без операции `Sort`.

Одиночные индексы по `user_id` и `status` намеренно **отсутствуют** — они являются
левыми префиксами составных. Отдельный индекс по `id` тоже не создаётся: его роль
выполняет первичный ключ.

---

## Основные endpoint'ы

### Health

| Метод | URL         | Описание                                              |
|-------|-------------|-------------------------------------------------------|
| GET   | /health     | Проверка состояния сервиса и БД                       |
| GET   | /health/db  | Роль, позиция в WAL и отставание primary и replica    |

### Users

| Метод  | URL                        | Описание                                      |
|--------|----------------------------|-----------------------------------------------|
| GET    | /api/users                 | Список пользователей (pagination, поиск)      |
| GET    | /api/users/{id}            | Пользователь по ID                            |
| GET    | /api/users/{id}/stats      | Статистика бронирований пользователя          |
| GET    | /api/users/{id}/bookings   | Бронирования пользователя                     |
| POST   | /api/users                 | Создать пользователя                          |
| PUT    | /api/users/{id}            | Обновить пользователя                         |
| DELETE | /api/users/{id}            | Удалить пользователя                          |

### Venues

| Метод  | URL                | Описание                                |
|--------|--------------------|-----------------------------------------|
| GET    | /api/venues        | Список площадок (pagination, поиск)     |
| GET    | /api/venues/{id}   | Площадка по ID                          |
| POST   | /api/venues        | Создать площадку                        |
| PUT    | /api/venues/{id}   | Обновить площадку                       |
| DELETE | /api/venues/{id}   | Удалить площадку                        |

### Categories

| Метод  | URL                    | Описание             |
|--------|------------------------|----------------------|
| GET    | /api/categories        | Все категории        |
| GET    | /api/categories/{id}   | Категория по ID      |
| POST   | /api/categories        | Создать категорию    |
| PUT    | /api/categories/{id}   | Обновить категорию   |
| DELETE | /api/categories/{id}   | Удалить категорию    |

### Events

| Метод  | URL                             | Описание                                          |
|--------|---------------------------------|---------------------------------------------------|
| GET    | /api/events                     | Список мероприятий (pagination, поиск, сортировка) — **replica** |
| GET    | /api/events/{id}                | Мероприятие с площадкой и категориями             |
| GET    | /api/events/{id}/bookings       | Бронирования на мероприятие                       |
| GET    | /api/events/stats/categories    | Статистика мероприятий по категориям — **replica** |
| POST   | /api/events                     | Создать мероприятие                               |
| PUT    | /api/events/{id}                | Обновить мероприятие                              |
| DELETE | /api/events/{id}                | Удалить мероприятие                               |

### Bookings

| Метод  | URL                        | Описание                                              |
|--------|----------------------------|-------------------------------------------------------|
| GET    | /api/bookings              | Список бронирований (pagination, фильтр, сортировка)  |
| GET    | /api/bookings/{id}         | Бронирование с деталями                               |
| GET    | /api/bookings/stats/revenue| Выручка и статистика по мероприятиям — **replica**    |
| POST   | /api/bookings              | Создать бронирование                                  |
| PUT    | /api/bookings/{id}         | Обновить бронирование (статус, кол-во мест)           |
| DELETE | /api/bookings/{id}         | Удалить бронирование                                  |

### Параметры запросов

```
GET /api/bookings?page=1&page_size=20
GET /api/bookings?status=confirmed
GET /api/bookings?from_date=2026-01-01T00:00:00&to_date=2026-06-01T00:00:00
GET /api/bookings?sort=created_at          # сортировка по возрастанию
GET /api/bookings?sort=-created_at         # сортировка по убыванию

GET /api/events?search=концерт
GET /api/events?sort=price
GET /api/events?sort=-price
```

---

## Сложные SQL-запросы

### JOIN-запрос #1 — мероприятия с площадкой и категориями

```sql
SELECT
    e.id, e.title, e.description, e.venue_id, e.start_time, e.end_time,
    e.price, e.max_capacity, e.created_at,
    v.name  AS venue_name,
    v.address AS venue_address,
    COALESCE(
        array_agg(c.name) FILTER (WHERE c.name IS NOT NULL),
        ARRAY[]::text[]
    ) AS categories
FROM events e
JOIN venues v ON e.venue_id = v.id
LEFT JOIN event_categories ec ON e.id = ec.event_id
LEFT JOIN categories c ON ec.category_id = c.id
WHERE e.title ILIKE '%концерт%'
GROUP BY e.id, e.title, e.description, e.venue_id, e.start_time,
         e.end_time, e.price, e.max_capacity, e.created_at,
         v.name, v.address
ORDER BY e.created_at DESC
LIMIT 20 OFFSET 0;
```

Используется в: `EventRepository.get_all()` и `EventRepository.get_by_id()`

---

### JOIN-запрос #2 — бронирования с данными пользователя, мероприятия и площадки

```sql
SELECT
    b.id, b.user_id, b.event_id, b.status, b.seats_count,
    b.total_price, b.created_at, b.updated_at,
    u.name  AS user_name,
    u.email AS user_email,
    e.title AS event_title,
    e.start_time AS event_start_time,
    v.name  AS venue_name
FROM bookings b
JOIN users  u ON b.user_id  = u.id
JOIN events e ON b.event_id = e.id
JOIN venues v ON e.venue_id = v.id
WHERE b.status = 'confirmed'
  AND b.created_at BETWEEN '2026-01-01' AND '2026-09-01'
ORDER BY b.created_at DESC
LIMIT 20 OFFSET 0;
```

Используется в: `BookingRepository.get_all()` и `BookingRepository.get_by_id()`

---

### Агрегирующий запрос — выручка и статистика по мероприятиям

```sql
SELECT
    e.id         AS event_id,
    e.title      AS event_title,
    e.start_time,
    v.name       AS venue_name,
    COUNT(b.id)                         AS total_bookings,
    SUM(b.seats_count)                  AS total_seats_booked,
    COALESCE(SUM(b.total_price), 0)     AS total_revenue,
    COALESCE(AVG(b.total_price), 0)     AS avg_booking_price
FROM events e
JOIN venues v ON e.venue_id = v.id
LEFT JOIN bookings b ON e.id = b.event_id AND b.status != 'cancelled'
GROUP BY e.id, e.title, e.start_time, v.name
ORDER BY total_revenue DESC NULLS LAST;
```

Используется в: `BookingRepository.get_revenue_by_event()` → `GET /api/bookings/stats/revenue`

---

### Агрегирующий запрос — статистика пользователя

```sql
SELECT
    u.id, u.name, u.email,
    COUNT(b.id)                         AS total_bookings,
    COALESCE(SUM(b.total_price), 0)     AS total_spent,
    COALESCE(AVG(b.total_price), 0)     AS avg_booking_price
FROM users u
LEFT JOIN bookings b ON u.id = b.user_id AND b.status != 'cancelled'
WHERE u.id = 1
GROUP BY u.id, u.name, u.email;
```

Используется в: `UserRepository.get_user_stats()` → `GET /api/users/{id}/stats`

---

## Генерация тестовых данных

Для первоначального заполнения БД (запускается отдельно, не через API):

```bash
# Установить зависимости локально (если запускаете вне Docker)
pip install -r requirements.txt

# Небольшой набор для проверки API
python scripts/seed.py

# Указать свои объёмы
python scripts/seed.py --users 1000 --venues 50 --categories 15 --events 200 --bookings 10000

# Масштабный набор для экспериментов с производительностью
python scripts/seed.py --users 100000 --venues 500 --categories 20 --events 5000 --bookings 1000000
```

Данные генерируются батчами через `bulk_insert_mappings`, что обеспечивает высокую
скорость вставки даже при миллионах записей.

---

## Чек-лист требований

- [x] Backend-сервис запускается
- [x] PostgreSQL запускается через Docker
- [x] Миграции применяются автоматически (`alembic upgrade head` при старте)
- [x] В БД минимум 4 связанные таблицы (6 таблиц: users, venues, categories, events, event_categories, bookings)
- [x] CRUD для основных сущностей
- [x] Связи между сущностями (`/api/users/{id}/bookings`, `/api/events/{id}/bookings`)
- [x] Минимум 2 JOIN-запроса
- [x] Минимум 1 агрегирующий запрос
- [x] Pagination (`?page=1&page_size=20`)
- [x] Filtering (`?status=confirmed`, `?from_date=...&to_date=...`, `?search=...`)
- [x] Сортировка (`?sort=created_at`, `?sort=-price`)
- [x] Поле `created_at` у основной растущей сущности (`bookings`)
- [x] Генерация большого объёма тестовых данных (`scripts/seed.py`)
- [x] `/health` endpoint
- [x] API документация (Swagger на `/docs`)
- [x] README
- [x] Проект запускается одной командой: `docker compose up`

## Лабораторные работы

| № | Тема | Отчёт | Скрипты | Сырые логи |
|---|---|---|---|---|
| 1 | Индексы и EXPLAIN ANALYZE в PostgreSQL | [`docs/lab-01-indexes.md`](docs/lab-01-indexes.md) | [`scripts/lab01/`](scripts/lab01/) | [`docs/lab-01-raw/`](docs/lab-01-raw/) |
| 2 | Когда индексов недостаточно: производительность при росте данных | [`docs/lab-02-scaling.md`](docs/lab-02-scaling.md) | [`scripts/lab02/`](scripts/lab02/) | [`docs/lab-02-raw/`](docs/lab-02-raw/) |
| 3 | Партиционирование и автоматизация создания партиций | [`docs/lab-03-partitioning.md`](docs/lab-03-partitioning.md) | [`scripts/lab03/`](scripts/lab03/) | [`docs/lab-03-raw/`](docs/lab-03-raw/) |
| 4 | Масштабирование чтения: Primary + Replica | [`docs/lab-04-replication.md`](docs/lab-04-replication.md) | [`scripts/lab04/`](scripts/lab04/) | [`docs/lab-04-raw/`](docs/lab-04-raw/) |
| 5 | Шардирование: Router и Consistent Hashing | [`docs/lab-05-sharding.md`](docs/lab-05-sharding.md) | [`scripts/lab05/`](scripts/lab05/) | [`docs/lab-05-raw/`](docs/lab-05-raw/) |
| 6 | Запросы и архитектура после шардирования | [`docs/lab-06-after-sharding.md`](docs/lab-06-after-sharding.md) | [`scripts/lab06/`](scripts/lab06/) | [`docs/lab-06-raw/`](docs/lab-06-raw/) |
