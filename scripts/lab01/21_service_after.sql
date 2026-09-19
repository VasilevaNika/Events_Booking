\pset pager off
\set uid 5706
\echo '################ ЗАДАНИЕ 29. Повторное измерение ПОСЛЕ миграции 002 ################'
VACUUM ANALYZE bookings;

\echo '--- Индексы bookings после миграции ---'
SELECT indexname, indexdef, pg_size_pretty(pg_relation_size(indexname::regclass)) AS size
FROM pg_indexes WHERE tablename='bookings' ORDER BY indexname;

\echo ''
\echo '--- Суммарный объём таблицы и индексов ---'
SELECT pg_size_pretty(pg_relation_size('bookings'))       AS "данные",
       pg_size_pretty(pg_indexes_size('bookings'))        AS "индексы",
       pg_size_pretty(pg_total_relation_size('bookings')) AS "всего";

\echo ''
\echo '=============================================================='
\echo '   QUERY 1 - GET /api/users/{user_id}/stats   ПОСЛЕ'
\echo '=============================================================='
EXPLAIN (ANALYZE, BUFFERS)
SELECT u.id, u.name, u.email, COUNT(b.id) AS total_bookings,
       COALESCE(SUM(b.total_price), 0) AS total_spent, COALESCE(AVG(b.total_price), 0) AS avg_booking_price
FROM users u LEFT JOIN bookings b ON u.id = b.user_id AND b.status != 'cancelled'
WHERE u.id = :uid GROUP BY u.id, u.name, u.email;
EXPLAIN (ANALYZE, BUFFERS)
SELECT u.id, u.name, u.email, COUNT(b.id) AS total_bookings,
       COALESCE(SUM(b.total_price), 0) AS total_spent, COALESCE(AVG(b.total_price), 0) AS avg_booking_price
FROM users u LEFT JOIN bookings b ON u.id = b.user_id AND b.status != 'cancelled'
WHERE u.id = :uid GROUP BY u.id, u.name, u.email;

\echo ''
\echo '=============================================================='
\echo '   QUERY 2a - GET /api/bookings?status=&from_date=   ПОСЛЕ'
\echo '=============================================================='
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at, b.updated_at,
       u.name AS user_name, u.email AS user_email, e.title AS event_title,
       e.start_time AS event_start_time, v.name AS venue_name
FROM bookings b
JOIN users u ON b.user_id = u.id
JOIN events e ON b.event_id = e.id
JOIN venues v ON e.venue_id = v.id
WHERE 1=1 AND b.status = 'confirmed' AND b.created_at >= '2026-06-01T00:00:00'::timestamp
ORDER BY b.created_at DESC LIMIT 20 OFFSET 0;

\echo ''
\echo '=============================================================='
\echo '   QUERY 2b - COUNT(*) для пагинации   ПОСЛЕ'
\echo '=============================================================='
EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) AS count_1 FROM (
  SELECT bookings.id, bookings.user_id, bookings.event_id, bookings.status, bookings.seats_count,
         bookings.total_price, bookings.created_at, bookings.updated_at
  FROM bookings
  WHERE bookings.status = 'confirmed' AND bookings.created_at >= '2026-06-01T00:00:00'::timestamp
) AS anon_1;
EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) AS count_1 FROM (
  SELECT bookings.id, bookings.user_id, bookings.event_id, bookings.status, bookings.seats_count,
         bookings.total_price, bookings.created_at, bookings.updated_at
  FROM bookings
  WHERE bookings.status = 'confirmed' AND bookings.created_at >= '2026-06-01T00:00:00'::timestamp
) AS anon_1;

\echo ''
\echo '=============================================================='
\echo '   QUERY 3 - GET /api/users/{user_id}/bookings   ПОСЛЕ'
\echo '=============================================================='
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at, b.updated_at,
       e.title AS event_title, e.start_time AS event_start_time, v.name AS venue_name
FROM bookings b JOIN events e ON b.event_id = e.id JOIN venues v ON e.venue_id = v.id
WHERE b.user_id = :uid ORDER BY b.created_at DESC LIMIT 20 OFFSET 0;
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at, b.updated_at,
       e.title AS event_title, e.start_time AS event_start_time, v.name AS venue_name
FROM bookings b JOIN events e ON b.event_id = e.id JOIN venues v ON e.venue_id = v.id
WHERE b.user_id = :uid ORDER BY b.created_at DESC LIMIT 20 OFFSET 0;

\echo ''
\echo '--- Только таблица bookings, без JOIN (чистый эффект индекса на Query 3) ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at
FROM bookings b WHERE b.user_id = :uid ORDER BY b.created_at DESC LIMIT 20;

\echo ''
\echo '################ Дополнительно: подтверждение, что старые планы были хуже ################'
\echo '--- Query 2b на «старых» индексах (составной временно скрыт) ---'
BEGIN;
DROP INDEX ix_bookings_status_created_at;
CREATE INDEX ix_bookings_status_tmp ON bookings(status);
EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) AS count_1 FROM (
  SELECT bookings.id FROM bookings
  WHERE bookings.status = 'confirmed' AND bookings.created_at >= '2026-06-01T00:00:00'::timestamp
) AS anon_1;
ROLLBACK;

\echo ''
\echo '--- Query 3 на «старых» индексах (составной временно скрыт) ---'
BEGIN;
DROP INDEX ix_bookings_user_id_created_at;
CREATE INDEX ix_bookings_user_id_tmp ON bookings(user_id);
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at
FROM bookings b WHERE b.user_id = :uid ORDER BY b.created_at DESC LIMIT 20;
ROLLBACK;

\echo ''
\echo '################ Статистика использования индексов после прогонов ################'
SELECT relname, indexrelname, idx_scan, idx_tup_read, idx_tup_fetch,
       pg_size_pretty(pg_relation_size(indexrelid)) AS size
FROM pg_stat_user_indexes WHERE relname='bookings' ORDER BY idx_scan DESC;
