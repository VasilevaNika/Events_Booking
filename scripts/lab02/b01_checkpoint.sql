\pset pager off
\set ON_ERROR_STOP on
\timing on

\echo ''
\echo '################################################################'
\echo '####  ЧАСТЬ B. КОНТРОЛЬНАЯ ТОЧКА: добавляем :add бронирований ####'
\echo '################################################################'

WITH u AS (SELECT array_agg(id) AS ids FROM public.users),
     e AS (SELECT array_agg(id) AS ids FROM public.events)
INSERT INTO public.bookings (user_id, event_id, status, seats_count, total_price, created_at)
SELECT
    u.ids[1 + floor(random() * array_length(u.ids, 1))::int],
    e.ids[1 + floor(random() * array_length(e.ids, 1))::int],
    (ARRAY['pending','confirmed','cancelled','completed'])[1 + floor(random() * 4)::int],
    1 + floor(random() * 4)::int,
    round((random() * 5000)::numeric, 2),
    NOW() - (random() * INTERVAL '365 days')
FROM generate_series(1, :add), u, e;

\timing off
VACUUM ANALYZE public.bookings;
\timing on

\echo '--- объём и размер ---'
SELECT count(*) AS bookings_total FROM public.bookings;
SELECT pg_size_pretty(pg_relation_size('public.bookings'))       AS heap,
       pg_size_pretty(pg_total_relation_size('public.bookings')) AS total,
       pg_size_pretty(pg_total_relation_size('public.bookings')
                    - pg_relation_size('public.bookings'))       AS indexes;
SELECT relpages AS heap_pages FROM pg_class WHERE oid = 'public.bookings'::regclass;

-- фиксированный тестовый пользователь, одинаковый на всех объёмах
SELECT min(id) AS uid FROM public.users \gset
\echo '--- тестовый пользователь ---'
SELECT :uid AS test_user_id, count(*) AS his_bookings
FROM public.bookings WHERE user_id = :uid;

\echo ''
\echo '=== ЗАПРОС 1: поиск по внешнему ключу (GET /api/users/{id}/stats) ==='
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at
FROM public.bookings b WHERE b.user_id = :uid;
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at
FROM public.bookings b WHERE b.user_id = :uid;

\echo ''
\echo '=== ЗАПРОС 2: диапазон дат (GET /api/bookings?from_date=&to_date=) ==='
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at
FROM public.bookings b
WHERE b.created_at >= NOW() - INTERVAL '30 days' AND b.created_at < NOW();
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at
FROM public.bookings b
WHERE b.created_at >= NOW() - INTERVAL '30 days' AND b.created_at < NOW();

\echo ''
\echo '=== ЗАПРОС 3: фильтр + сортировка + JOIN (GET /api/users/{id}/bookings) ==='
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at,
       e.title AS event_title, v.name AS venue_name
FROM public.bookings b
JOIN public.events e ON b.event_id = e.id
JOIN public.venues v ON e.venue_id = v.id
WHERE b.user_id = :uid
ORDER BY b.created_at DESC
LIMIT 50;
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at,
       e.title AS event_title, v.name AS venue_name
FROM public.bookings b
JOIN public.events e ON b.event_id = e.id
JOIN public.venues v ON e.venue_id = v.id
WHERE b.user_id = :uid
ORDER BY b.created_at DESC
LIMIT 50;

\echo ''
\echo '=== ЗАПРОС 2b: COUNT(*) для пагинации (тот же фильтр) ==='
EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) FROM public.bookings b
WHERE b.created_at >= NOW() - INTERVAL '30 days' AND b.created_at < NOW();
