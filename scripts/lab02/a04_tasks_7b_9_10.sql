\pset pager off
\set ON_ERROR_STOP on
\timing on

\echo ''
\echo '######## ЗАДАНИЕ 7 (продолжение). Sort на активном пользователе ########'
\echo '(у user_id=123 всего 55 событий — на таком объёме планировщик оставляет Sort.'
\echo ' Моделируем пользователя с 50 000 событий, каким он станет при росте потока.)'
INSERT INTO lab2.events (user_id, event_type, payload, created_at)
SELECT 999999,
       CASE WHEN random() < 0.4 THEN 'MESSAGE'
            WHEN random() < 0.7 THEN 'LOGIN'
            WHEN random() < 0.9 THEN 'PURCHASE'
            ELSE 'OTHER' END,
       '{}'::jsonb,
       NOW() - (random() * INTERVAL '365 days')
FROM generate_series(1, 50000);
\timing off
VACUUM ANALYZE lab2.events;
\timing on
SELECT count(*) AS events_of_heavy_user FROM lab2.events WHERE user_id = 999999;

\echo ''
\echo '--- ДО: только одиночный индекс по user_id (составной временно скрыт) ---'
BEGIN;
DROP INDEX lab2.idx_events_user_created;
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM lab2.events WHERE user_id = 999999 ORDER BY created_at DESC LIMIT 100;
ROLLBACK;

\echo ''
\echo '--- ПОСЛЕ: составной (user_id, created_at DESC) ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM lab2.events WHERE user_id = 999999 ORDER BY created_at DESC LIMIT 100;
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM lab2.events WHERE user_id = 999999 ORDER BY created_at DESC LIMIT 100;

\echo ''
\echo '--- убираем искусственные данные ---'
DELETE FROM lab2.events WHERE user_id = 999999;
\timing off
VACUUM ANALYZE lab2.events;
\timing on
SELECT count(*) AS rows_total FROM lab2.events;

\echo ''
\echo '######## ЗАДАНИЕ 9. Стоимость индексов (INSERT) ########'
DROP TABLE IF EXISTS lab2.events_noidx;
DROP TABLE IF EXISTS lab2.events_idx;
CREATE TABLE lab2.events_noidx (LIKE lab2.events INCLUDING DEFAULTS);
CREATE TABLE lab2.events_idx   (LIKE lab2.events INCLUDING DEFAULTS);
CREATE INDEX ix_ei_user    ON lab2.events_idx(user_id);
CREATE INDEX ix_ei_created ON lab2.events_idx(created_at);
CREATE INDEX ix_ei_comp    ON lab2.events_idx(user_id, created_at DESC);

\echo '--- ВСТАВКА 500 000 строк БЕЗ индексов ---'
EXPLAIN (ANALYZE, BUFFERS)
INSERT INTO lab2.events_noidx (user_id, event_type, payload, created_at)
SELECT (random() * 100000)::bigint,
       CASE WHEN random() < 0.4 THEN 'MESSAGE'
            WHEN random() < 0.7 THEN 'LOGIN'
            WHEN random() < 0.9 THEN 'PURCHASE'
            ELSE 'OTHER' END,
       '{}'::jsonb,
       NOW() - (random() * INTERVAL '365 days')
FROM generate_series(1, 500000);

\echo '--- ВСТАВКА 500 000 строк С ТРЕМЯ индексами ---'
EXPLAIN (ANALYZE, BUFFERS)
INSERT INTO lab2.events_idx (user_id, event_type, payload, created_at)
SELECT (random() * 100000)::bigint,
       CASE WHEN random() < 0.4 THEN 'MESSAGE'
            WHEN random() < 0.7 THEN 'LOGIN'
            WHEN random() < 0.9 THEN 'PURCHASE'
            ELSE 'OTHER' END,
       '{}'::jsonb,
       NOW() - (random() * INTERVAL '365 days')
FROM generate_series(1, 500000);

\echo '--- размеры двух таблиц ---'
SELECT 'events_noidx' AS t,
       pg_size_pretty(pg_relation_size('lab2.events_noidx'))       AS heap,
       pg_size_pretty(pg_total_relation_size('lab2.events_noidx')) AS total,
       pg_size_pretty(pg_total_relation_size('lab2.events_noidx')
                    - pg_relation_size('lab2.events_noidx'))       AS indexes
UNION ALL
SELECT 'events_idx',
       pg_size_pretty(pg_relation_size('lab2.events_idx')),
       pg_size_pretty(pg_total_relation_size('lab2.events_idx')),
       pg_size_pretty(pg_total_relation_size('lab2.events_idx')
                    - pg_relation_size('lab2.events_idx'));

\echo ''
\echo '--- UPDATE колонки, которой НЕТ ни в одном индексе ---'
EXPLAIN (ANALYZE) UPDATE lab2.events_noidx SET payload = '{"x":1}'::jsonb WHERE id % 10 = 0;
EXPLAIN (ANALYZE) UPDATE lab2.events_idx   SET payload = '{"x":1}'::jsonb WHERE id % 10 = 0;

\echo ''
\echo '######## ЗАДАНИЕ 10. Размер индексов ########'
SELECT indexrelname AS index_name,
       pg_size_pretty(pg_relation_size(indexrelid)) AS index_size,
       idx_scan AS scans
FROM pg_stat_user_indexes
WHERE schemaname = 'lab2' AND relname = 'events'
ORDER BY pg_relation_size(indexrelid) DESC;

SELECT pg_size_pretty(pg_relation_size('lab2.events'))       AS heap,
       pg_size_pretty(pg_total_relation_size('lab2.events')) AS total,
       pg_size_pretty(pg_total_relation_size('lab2.events')
                    - pg_relation_size('lab2.events'))       AS all_indexes,
       round(100.0 * (pg_total_relation_size('lab2.events')
                    - pg_relation_size('lab2.events'))
                   / pg_relation_size('lab2.events'), 1)     AS pct_of_heap;
