\pset pager off
\echo '######## Демонстрация масштабирования: пользователь с 20 000 бронирований ########'
\echo '(в текущих данных максимум 40 заказов на пользователя — эффект индекса не виден;'
\echo ' моделируем «активного» пользователя, каким он станет через год роста сервиса)'

SELECT min(id) AS heavy_user FROM users \gset
INSERT INTO bookings (user_id, event_id, status, seats_count, total_price, created_at)
SELECT :heavy_user, e.id,
       (ARRAY['pending','confirmed','cancelled','completed'])[1+floor(random()*4)],
       1, 100.0, NOW() - (random() * INTERVAL '1 year')
FROM (SELECT id FROM events ORDER BY random() LIMIT 1) e, generate_series(1, 20000);
VACUUM ANALYZE bookings;
SELECT count(*) AS bookings_of_heavy_user FROM bookings WHERE user_id = :heavy_user;

\echo ''
\echo '--- ДО: только одиночный индекс по user_id (составной временно скрыт) ---'
BEGIN;
DROP INDEX ix_bookings_user_id_created_at;
CREATE INDEX ix_bookings_user_id_tmp ON bookings(user_id);
ANALYZE bookings;
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at
FROM bookings b WHERE b.user_id = :heavy_user ORDER BY b.created_at DESC LIMIT 20;
ROLLBACK;

\echo ''
\echo '--- ПОСЛЕ: составной индекс (user_id, created_at DESC) ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at
FROM bookings b WHERE b.user_id = :heavy_user ORDER BY b.created_at DESC LIMIT 20;
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at
FROM bookings b WHERE b.user_id = :heavy_user ORDER BY b.created_at DESC LIMIT 20;

\echo ''
\echo '--- Полный запрос endpoint-а с JOIN, ПОСЛЕ ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at, b.updated_at,
       e.title AS event_title, e.start_time AS event_start_time, v.name AS venue_name
FROM bookings b JOIN events e ON b.event_id = e.id JOIN venues v ON e.venue_id = v.id
WHERE b.user_id = :heavy_user ORDER BY b.created_at DESC LIMIT 20 OFFSET 0;

\echo ''
\echo '--- Убираем искусственные данные ---'
DELETE FROM bookings WHERE user_id = :heavy_user AND total_price = 100.0 AND seats_count = 1;
VACUUM ANALYZE bookings;
SELECT count(*) AS bookings_total FROM bookings;
