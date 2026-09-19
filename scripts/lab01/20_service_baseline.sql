\pset pager off
\set uid 5706
\echo '################ ЗАДАНИЕ 22/25. Текущая схема и индексы сервиса ################'
SELECT relname AS "таблица", n_live_tup AS "строк (оценка)",
       pg_size_pretty(pg_relation_size(relid))       AS "данные",
       pg_size_pretty(pg_indexes_size(relid))        AS "индексы",
       pg_size_pretty(pg_total_relation_size(relid)) AS "всего"
FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC;

\echo ''
\echo '--- Все индексы БД сервиса ---'
SELECT tablename, indexname, indexdef, pg_size_pretty(pg_relation_size(indexname::regclass)) AS size
FROM pg_indexes WHERE schemaname='public' ORDER BY tablename, indexname;

\echo ''
\echo '--- Селективность колонок bookings ---'
SELECT attname, n_distinct, correlation FROM pg_stats
WHERE tablename='bookings' ORDER BY attname;

\echo ''
\echo '--- Распределение status в bookings ---'
SELECT status, count(*), round(100.0*count(*)/sum(count(*)) OVER (),2) AS pct
FROM bookings GROUP BY status ORDER BY 2 DESC;

\echo ''
\echo '--- Сколько бронирований у тестового пользователя ---'
SELECT count(*) AS bookings_of_user FROM bookings WHERE user_id = :uid;
SELECT round(avg(c),2) AS avg_per_user, max(c) AS max_per_user
FROM (SELECT user_id, count(*) c FROM bookings GROUP BY user_id) t;

\echo ''
\echo '=============================================================='
\echo '   QUERY 1 - GET /api/users/{user_id}/stats  (поиск по id + агрегация)'
\echo '=============================================================='
\echo '--- EXPLAIN ---'
EXPLAIN
SELECT u.id, u.name, u.email,
       COUNT(b.id) AS total_bookings,
       COALESCE(SUM(b.total_price), 0) AS total_spent,
       COALESCE(AVG(b.total_price), 0) AS avg_booking_price
FROM users u
LEFT JOIN bookings b ON u.id = b.user_id AND b.status != 'cancelled'
WHERE u.id = :uid
GROUP BY u.id, u.name, u.email;

\echo '--- EXPLAIN ANALYZE (прогон 1) ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT u.id, u.name, u.email, COUNT(b.id) AS total_bookings,
       COALESCE(SUM(b.total_price), 0) AS total_spent, COALESCE(AVG(b.total_price), 0) AS avg_booking_price
FROM users u LEFT JOIN bookings b ON u.id = b.user_id AND b.status != 'cancelled'
WHERE u.id = :uid GROUP BY u.id, u.name, u.email;

\echo '--- EXPLAIN ANALYZE (прогон 2) ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT u.id, u.name, u.email, COUNT(b.id) AS total_bookings,
       COALESCE(SUM(b.total_price), 0) AS total_spent, COALESCE(AVG(b.total_price), 0) AS avg_booking_price
FROM users u LEFT JOIN bookings b ON u.id = b.user_id AND b.status != 'cancelled'
WHERE u.id = :uid GROUP BY u.id, u.name, u.email;

\echo ''
\echo '=============================================================='
\echo '   QUERY 2 - GET /api/bookings?status=&from_date=  (несколько условий)'
\echo '=============================================================='
\echo '--- 2a. Основная выборка. EXPLAIN ---'
EXPLAIN
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at, b.updated_at,
       u.name AS user_name, u.email AS user_email, e.title AS event_title,
       e.start_time AS event_start_time, v.name AS venue_name
FROM bookings b
JOIN users u ON b.user_id = u.id
JOIN events e ON b.event_id = e.id
JOIN venues v ON e.venue_id = v.id
WHERE 1=1 AND b.status = 'confirmed' AND b.created_at >= '2026-06-01T00:00:00'::timestamp
ORDER BY b.created_at DESC LIMIT 20 OFFSET 0;

\echo '--- 2a. EXPLAIN ANALYZE ---'
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

\echo '--- 2b. COUNT(*) для пагинации (в логах 91.8 мс). EXPLAIN ANALYZE, прогон 1 ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) AS count_1 FROM (
  SELECT bookings.id, bookings.user_id, bookings.event_id, bookings.status, bookings.seats_count,
         bookings.total_price, bookings.created_at, bookings.updated_at
  FROM bookings
  WHERE bookings.status = 'confirmed' AND bookings.created_at >= '2026-06-01T00:00:00'::timestamp
) AS anon_1;

\echo '--- 2b. прогон 2 ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) AS count_1 FROM (
  SELECT bookings.id, bookings.user_id, bookings.event_id, bookings.status, bookings.seats_count,
         bookings.total_price, bookings.created_at, bookings.updated_at
  FROM bookings
  WHERE bookings.status = 'confirmed' AND bookings.created_at >= '2026-06-01T00:00:00'::timestamp
) AS anon_1;

\echo '--- Селективность условия Query 2 ---'
SELECT count(*) AS matched, round(100.0*count(*)/(SELECT count(*) FROM bookings),2) AS pct
FROM bookings WHERE status='confirmed' AND created_at >= '2026-06-01T00:00:00'::timestamp;

\echo ''
\echo '=============================================================='
\echo '   QUERY 3 - GET /api/users/{user_id}/bookings  (фильтр + сортировка + LIMIT)'
\echo '=============================================================='
\echo '--- EXPLAIN ---'
EXPLAIN
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at, b.updated_at,
       e.title AS event_title, e.start_time AS event_start_time, v.name AS venue_name
FROM bookings b
JOIN events e ON b.event_id = e.id
JOIN venues v ON e.venue_id = v.id
WHERE b.user_id = :uid
ORDER BY b.created_at DESC LIMIT 20 OFFSET 0;

\echo '--- EXPLAIN ANALYZE (прогон 1) ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at, b.updated_at,
       e.title AS event_title, e.start_time AS event_start_time, v.name AS venue_name
FROM bookings b JOIN events e ON b.event_id = e.id JOIN venues v ON e.venue_id = v.id
WHERE b.user_id = :uid ORDER BY b.created_at DESC LIMIT 20 OFFSET 0;

\echo '--- EXPLAIN ANALYZE (прогон 2) ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at, b.updated_at,
       e.title AS event_title, e.start_time AS event_start_time, v.name AS venue_name
FROM bookings b JOIN events e ON b.event_id = e.id JOIN venues v ON e.venue_id = v.id
WHERE b.user_id = :uid ORDER BY b.created_at DESC LIMIT 20 OFFSET 0;

\echo ''
\echo '--- Глубокая пагинация (OFFSET 100) ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at, b.updated_at,
       e.title AS event_title, e.start_time AS event_start_time, v.name AS venue_name
FROM bookings b JOIN events e ON b.event_id = e.id JOIN venues v ON e.venue_id = v.id
WHERE b.user_id = :uid ORDER BY b.created_at DESC LIMIT 20 OFFSET 100;

\echo ''
\echo '################ ЗАДАНИЕ 25. Статистика использования индексов сервиса ################'
SELECT schemaname, relname, indexrelname, idx_scan, idx_tup_read, idx_tup_fetch,
       pg_size_pretty(pg_relation_size(indexrelid)) AS size
FROM pg_stat_user_indexes ORDER BY idx_scan DESC, relname, indexrelname;
