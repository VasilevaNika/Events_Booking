\pset pager off
\set ON_ERROR_STOP on
\timing on

\echo '################ ЧАСТЬ 1. RANGE PARTITIONING по дате ################'
DROP SCHEMA IF EXISTS lab3 CASCADE;
CREATE SCHEMA lab3;

CREATE TABLE lab3.events (
    id         BIGINT NOT NULL,
    user_id    BIGINT NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    payload    TEXT,
    created_at TIMESTAMP NOT NULL
) PARTITION BY RANGE (created_at);

CREATE TABLE lab3.events_2026_09_09 PARTITION OF lab3.events
    FOR VALUES FROM ('2026-09-09') TO ('2026-09-10');
CREATE TABLE lab3.events_2026_09_10 PARTITION OF lab3.events
    FOR VALUES FROM ('2026-09-10') TO ('2026-09-11');
CREATE TABLE lab3.events_2026_09_11 PARTITION OF lab3.events
    FOR VALUES FROM ('2026-09-11') TO ('2026-09-12');

\echo '--- заполняем 900 000 строк (по 300 000 на день) ---'
INSERT INTO lab3.events (id, user_id, event_type, payload, created_at)
SELECT s,
       (random() * 100000)::bigint,
       CASE WHEN random() < 0.35 THEN 'click'
            WHEN random() < 0.60 THEN 'view'
            WHEN random() < 0.85 THEN 'login'
            ELSE 'purchase' END,
       'payload-' || s,
       TIMESTAMP '2026-09-09' + (random() * INTERVAL '3 days')
FROM generate_series(1, 900000) s;

\timing off
VACUUM ANALYZE lab3.events;
\timing on

\echo '--- распределение по партициям ---'
SELECT tableoid::regclass AS partition_name, COUNT(*)
FROM lab3.events GROUP BY tableoid ORDER BY partition_name;

\echo ''
\echo '--- ЗАДАНИЕ: куда попадут конкретные значения ---'
\echo '1) created_at = 2026-09-10 12:00:00'
INSERT INTO lab3.events VALUES (9000001, 1, 'click', 'probe-1', '2026-09-10 12:00:00');
SELECT tableoid::regclass AS landed_in FROM lab3.events WHERE id = 9000001;

\echo '2) created_at = 2026-09-11 00:00:00 (ровно граница)'
INSERT INTO lab3.events VALUES (9000002, 1, 'click', 'probe-2', '2026-09-11 00:00:00');
SELECT tableoid::regclass AS landed_in FROM lab3.events WHERE id = 9000002;

\echo '3) вставка за 2026-09-12 — партиции нет, ожидаем ошибку'
\set ON_ERROR_STOP off
INSERT INTO lab3.events VALUES (9000003, 1, 'click', 'probe-3', '2026-09-12 10:00:00');
\set ON_ERROR_STOP on

\echo '4) граница TO не включается: проверка полуинтервала'
SELECT '2026-09-11 23:59:59.999999'::timestamp < '2026-09-12'::timestamp AS fits_09_11,
       '2026-09-12 00:00:00'::timestamp       < '2026-09-12'::timestamp AS fits_09_12;

\echo ''
\echo '################ ЧАСТЬ 2. Partition pruning ################'
\echo '--- запрос ПО ключу партиционирования ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT COUNT(*) FROM lab3.events
WHERE created_at >= '2026-09-10' AND created_at < '2026-09-11';

EXPLAIN (ANALYZE, BUFFERS)
SELECT COUNT(*) FROM lab3.events
WHERE created_at >= '2026-09-10' AND created_at < '2026-09-11';

\echo ''
\echo '--- запрос БЕЗ ключа партиционирования ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT COUNT(*) FROM lab3.events WHERE event_type = 'click';

EXPLAIN (ANALYZE, BUFFERS)
SELECT COUNT(*) FROM lab3.events WHERE event_type = 'click';

\echo ''
\echo '--- размеры партиций ---'
SELECT c.relname AS partition_name,
       pg_size_pretty(pg_relation_size(c.oid)) AS size,
       c.reltuples::bigint AS approx_rows
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'lab3' AND c.relname LIKE 'events_%'
ORDER BY c.relname;
