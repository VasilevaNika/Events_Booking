\pset pager off
\set ON_ERROR_STOP on
\timing on

\echo '################ ЧАСТЬ 12. Партиционирование своего сервиса ################'

\echo '--- структура партиционированной таблицы ---'
SELECT c.relname                               AS partition_name,
       pg_get_expr(c.relpartbound, c.oid)      AS bounds,
       pg_size_pretty(pg_relation_size(c.oid)) AS heap,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS total,
       c.reltuples::bigint                     AS approx_rows
FROM pg_inherits i
JOIN pg_class c     ON c.oid = i.inhrelid
JOIN pg_class p     ON p.oid = i.inhparent
JOIN pg_namespace n ON n.oid = p.relnamespace
WHERE n.nspname = 'public' AND p.relname = 'bookings'
ORDER BY c.relname;

\echo '--- реальное распределение строк ---'
SELECT tableoid::regclass AS partition_name, count(*)
FROM public.bookings GROUP BY tableoid ORDER BY partition_name;

\echo '--- суммарный размер ---'
SELECT pg_size_pretty(sum(pg_relation_size(c.oid)))       AS heap_total,
       pg_size_pretty(sum(pg_total_relation_size(c.oid))) AS with_indexes,
       count(*)                                           AS partitions
FROM pg_inherits i
JOIN pg_class c ON c.oid = i.inhrelid
JOIN pg_class p ON p.oid = i.inhparent
WHERE p.relname = 'bookings';

\echo ''
\echo '=== ЗАПРОС 1: диапазон дат (GET /api/bookings?from_date=&to_date=) ==='
\echo '    ключ партиционирования в WHERE есть → ожидаем pruning'
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at
FROM public.bookings b
WHERE b.created_at >= date_trunc('month', now())
  AND b.created_at <  date_trunc('month', now()) + INTERVAL '1 month';

EXPLAIN (ANALYZE, BUFFERS)
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at
FROM public.bookings b
WHERE b.created_at >= date_trunc('month', now())
  AND b.created_at <  date_trunc('month', now()) + INTERVAL '1 month';

\echo ''
\echo '=== ЗАПРОС 2: COUNT(*) за месяц (пагинация того же эндпоинта) ==='
EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) FROM public.bookings b
WHERE b.created_at >= date_trunc('month', now())
  AND b.created_at <  date_trunc('month', now()) + INTERVAL '1 month';

\echo ''
\echo '=== ЗАПРОС 3: бронирования пользователя (GET /api/users/{id}/bookings) ==='
\echo '    ключа партиционирования в WHERE НЕТ → pruning не сработает'
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at
FROM public.bookings b
WHERE b.user_id = 1
ORDER BY b.created_at DESC
LIMIT 50;

\echo ''
\echo '=== ЗАПРОС 3-БИС: тот же запрос, но с ограничением по дате ==='
\echo '    так делает исправленный эндпоинт — pruning включается'
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.id, b.user_id, b.event_id, b.status, b.seats_count, b.total_price, b.created_at
FROM public.bookings b
WHERE b.user_id = 1
  AND b.created_at >= now() - INTERVAL '3 months'
ORDER BY b.created_at DESC
LIMIT 50;

\echo ''
\echo '=== ЗАПРОС 4: поиск по id (GET /api/bookings/{id}) ==='
\echo '    id больше не уникален глобально — без даты обходятся все партиции'
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM public.bookings WHERE id = 1234567;

\echo ''
\echo '=== ЗАПРОС 5: JOIN с events и venues, с ключом партиционирования ==='
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.id, b.status, b.total_price, b.created_at,
       e.title AS event_title, v.name AS venue_name
FROM public.bookings b
JOIN public.events e ON b.event_id = e.id
JOIN public.venues v ON e.venue_id = v.id
WHERE b.created_at >= date_trunc('month', now())
ORDER BY b.created_at DESC
LIMIT 50;

\echo ''
\echo '--- локальные индексы: по одному на каждую партицию ---'
SELECT c.relname AS index_name,
       CASE c.relkind WHEN 'I' THEN 'логический (на родителе)'
                      WHEN 'i' THEN 'локальный' END AS kind,
       pg_size_pretty(pg_relation_size(c.oid)) AS size
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname LIKE '%user_id_created_at%'
ORDER BY c.relkind DESC, c.relname;

\echo ''
\echo '--- мгновенное удаление старых данных: DROP одной партиции ---'
\echo '    (демонстрация на копии, саму партицию не трогаем)'
SELECT c.relname AS oldest_partition,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS size_freed_by_drop
FROM pg_inherits i
JOIN pg_class c ON c.oid = i.inhrelid
JOIN pg_class p ON p.oid = i.inhparent
WHERE p.relname = 'bookings' AND c.relname <> 'bookings_default'
ORDER BY c.relname
LIMIT 1;
