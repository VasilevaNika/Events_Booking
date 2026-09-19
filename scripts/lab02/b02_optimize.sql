\pset pager off
\set ON_ERROR_STOP on
\timing on

\echo '######## ЗАДАНИЕ 18. Оптимизация Запроса 2 ########'
\echo '--- ИСХОДНО (только ix_bookings_created_at) ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at
FROM public.bookings b
WHERE b.created_at >= NOW() - INTERVAL '30 days' AND b.created_at < NOW();

\echo ''
\echo '=== ВАРИАНТ 1: покрывающий индекс (INCLUDE) ==='
CREATE INDEX ix_bookings_created_covering
ON public.bookings (created_at DESC)
INCLUDE (id, user_id, event_id, status, seats_count, total_price);
\timing off
VACUUM ANALYZE public.bookings;
\timing on
SELECT pg_size_pretty(pg_relation_size('ix_bookings_created_covering')) AS covering_size;
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at
FROM public.bookings b
WHERE b.created_at >= NOW() - INTERVAL '30 days' AND b.created_at < NOW();
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at
FROM public.bookings b
WHERE b.created_at >= NOW() - INTERVAL '30 days' AND b.created_at < NOW();
DROP INDEX public.ix_bookings_created_covering;

\echo ''
\echo '=== ВАРИАНТ 2: BRIN вместо B-tree ==='
CREATE INDEX ix_bookings_created_brin ON public.bookings USING BRIN (created_at);
\timing off
VACUUM ANALYZE public.bookings;
\timing on
SELECT pg_size_pretty(pg_relation_size('ix_bookings_created_brin'))  AS brin_size,
       pg_size_pretty(pg_relation_size('ix_bookings_created_at'))    AS btree_size;
BEGIN;
DROP INDEX public.ix_bookings_created_at;
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at
FROM public.bookings b
WHERE b.created_at >= NOW() - INTERVAL '30 days' AND b.created_at < NOW();
ROLLBACK;
DROP INDEX public.ix_bookings_created_brin;

\echo ''
\echo '=== ВАРИАНТ 3: LIMIT (реальный сценарий API с пагинацией) ==='
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at
FROM public.bookings b
WHERE b.created_at >= NOW() - INTERVAL '30 days' AND b.created_at < NOW()
ORDER BY b.created_at DESC
LIMIT 50;
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at
FROM public.bookings b
WHERE b.created_at >= NOW() - INTERVAL '30 days' AND b.created_at < NOW()
ORDER BY b.created_at DESC
LIMIT 50;

\echo ''
\echo '=== ВАРИАНТ 4: keyset-пагинация (без OFFSET) ==='
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at
FROM public.bookings b
WHERE b.created_at < NOW() - INTERVAL '10 days'
ORDER BY b.created_at DESC
LIMIT 50;

\echo ''
\echo '=== ВАРИАНТ 5: сузить окно до 1 дня (селективность 0.27%) ==='
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at
FROM public.bookings b
WHERE b.created_at >= NOW() - INTERVAL '1 day' AND b.created_at < NOW();
