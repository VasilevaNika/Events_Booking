\pset pager off
\set ON_ERROR_STOP on
\timing on

\echo '####  ЗАМЕР НА ТЕКУЩЕМ ОБЪЁМЕ (без вставки)  ####'
DROP INDEX IF EXISTS lab2.idx_events_user_id;
\timing off
VACUUM ANALYZE lab2.events;
\timing on

\echo ''
\echo '--- ЗАДАНИЕ 3: размер таблицы ---'
SELECT count(*) AS rows_total FROM lab2.events;
SELECT pg_size_pretty(pg_relation_size('lab2.events'))       AS table_size,
       pg_size_pretty(pg_total_relation_size('lab2.events')) AS total_size;
SELECT relpages AS heap_pages FROM pg_class WHERE oid = 'lab2.events'::regclass;
SELECT count(*) AS rows_for_user_123 FROM lab2.events WHERE user_id = 123;

\echo ''
\echo '--- ЗАДАНИЕ 4: SELECT БЕЗ дополнительного индекса (прогон 1) ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab2.events WHERE user_id = 123;
\echo '--- ЗАДАНИЕ 4: прогон 2 (в отчёт) ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab2.events WHERE user_id = 123;

\echo ''
\echo '--- ЗАДАНИЕ 5: создаём индекс ---'
CREATE INDEX idx_events_user_id ON lab2.events(user_id);
\timing off
VACUUM ANALYZE lab2.events;
\timing on

\echo '--- ЗАДАНИЕ 5: SELECT С индексом (прогон 1) ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab2.events WHERE user_id = 123;
\echo '--- ЗАДАНИЕ 5: прогон 2 (в отчёт) ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab2.events WHERE user_id = 123;

\echo ''
\echo '--- ЗАДАНИЕ 10 (частично): размер индекса ---'
SELECT indexrelname AS index_name,
       pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
FROM pg_stat_user_indexes
WHERE schemaname = 'lab2' AND relname = 'events'
ORDER BY indexrelname;
SELECT pg_size_pretty(pg_total_relation_size('lab2.events')) AS total_with_index;
