\pset pager off
\set ON_ERROR_STOP on
\timing on

\echo ''
\echo '################ ЗАДАНИЕ 6. Поиск по диапазону дат ################'
\echo '--- ДО индекса: 1 день ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab2.events WHERE created_at >= NOW() - INTERVAL '1 day';
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab2.events WHERE created_at >= NOW() - INTERVAL '1 day';

\echo '--- Создаём индекс по created_at ---'
CREATE INDEX idx_events_created_at ON lab2.events(created_at);
\timing off
VACUUM ANALYZE lab2.events;
\timing on

\echo '--- ПОСЛЕ индекса: 1 день (~0.27% таблицы) ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab2.events WHERE created_at >= NOW() - INTERVAL '1 day';
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab2.events WHERE created_at >= NOW() - INTERVAL '1 day';

\echo '--- КОНТРАСТ: 30 дней (~8%) ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab2.events WHERE created_at >= NOW() - INTERVAL '30 days';
\echo '--- КОНТРАСТ: 180 дней (~50%) ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab2.events WHERE created_at >= NOW() - INTERVAL '180 days';
\echo '--- КОНТРАСТ: 365 дней (100%) ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab2.events WHERE created_at >= NOW() - INTERVAL '365 days';

\echo '--- сколько всего страниц в таблице (для сравнения с Heap Blocks) ---'
SELECT relpages AS heap_pages FROM pg_class WHERE oid = 'lab2.events'::regclass;

\echo ''
\echo '################ ЗАДАНИЕ 7. Фильтрация и сортировка ################'
\echo '--- ДО составного индекса (есть только idx_events_user_id) ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM lab2.events WHERE user_id = 123 ORDER BY created_at DESC LIMIT 100;
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM lab2.events WHERE user_id = 123 ORDER BY created_at DESC LIMIT 100;

\echo '--- Создаём составной (user_id, created_at DESC) ---'
CREATE INDEX idx_events_user_created ON lab2.events(user_id, created_at DESC);
\timing off
VACUUM ANALYZE lab2.events;
\timing on

\echo '--- ПОСЛЕ составного индекса ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM lab2.events WHERE user_id = 123 ORDER BY created_at DESC LIMIT 100;
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM lab2.events WHERE user_id = 123 ORDER BY created_at DESC LIMIT 100;

\echo '--- ПРОВЕРКА ПОРЯДКА КОЛОНОК: создаём обратный (created_at DESC, user_id) ---'
CREATE INDEX idx_events_created_user ON lab2.events(created_at DESC, user_id);
\timing off
VACUUM ANALYZE lab2.events;
\timing on
\echo '--- прячем правильный составной, чтобы планировщик взял обратный ---'
BEGIN;
DROP INDEX lab2.idx_events_user_created;
DROP INDEX lab2.idx_events_user_id;
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM lab2.events WHERE user_id = 123 ORDER BY created_at DESC LIMIT 100;
ROLLBACK;
\echo '--- обратный индекс больше не нужен ---'
DROP INDEX lab2.idx_events_created_user;

\echo ''
\echo '################ ЗАДАНИЕ 8. Агрегация ################'
\echo '--- 30 дней, GROUP BY event_type ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT event_type, COUNT(*) FROM lab2.events
WHERE created_at >= NOW() - INTERVAL '30 days' GROUP BY event_type;
EXPLAIN (ANALYZE, BUFFERS)
SELECT event_type, COUNT(*) FROM lab2.events
WHERE created_at >= NOW() - INTERVAL '30 days' GROUP BY event_type;

\echo '--- то же, но с принудительно выключенными индексами ---'
SET enable_indexscan = off;
SET enable_bitmapscan = off;
EXPLAIN (ANALYZE, BUFFERS)
SELECT event_type, COUNT(*) FROM lab2.events
WHERE created_at >= NOW() - INTERVAL '30 days' GROUP BY event_type;
SET enable_indexscan = on;
SET enable_bitmapscan = on;

\echo '--- КОНТРАСТ: за весь год (100% таблицы) ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT event_type, COUNT(*) FROM lab2.events
WHERE created_at >= NOW() - INTERVAL '365 days' GROUP BY event_type;

\echo '--- распределение event_type ---'
SELECT event_type, count(*),
       round(100.0 * count(*) / sum(count(*)) OVER (), 2) AS pct
FROM lab2.events GROUP BY event_type ORDER BY 2 DESC;

\echo ''
\echo '################ ЗАДАНИЕ 11. Когда индекс уже не спасает ################'
\echo '--- GROUP BY DATE(created_at) за 365 дней ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT DATE(created_at), COUNT(*) FROM lab2.events
WHERE created_at >= NOW() - INTERVAL '365 days' GROUP BY DATE(created_at);

\echo '--- сколько групп получается ---'
SELECT count(DISTINCT DATE(created_at)) AS distinct_days FROM lab2.events;
