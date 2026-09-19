\pset pager off
\echo '################ ЗАДАНИЕ 7 (дополнение). Bitmap vs Seq Scan на одном и том же запросе ################'
\echo '--- status = ''PAID'' с ЗАПРЕЩЁННЫМ bitmap/index scan => чистый Seq Scan ---'
SET enable_bitmapscan = off;
SET enable_indexscan = off;
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE status = 'PAID';
SET enable_bitmapscan = on;
SET enable_indexscan = on;

\echo ''
\echo '--- Условие на 50% таблицы: status IN (''PAID'',''NEW'') ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE status IN ('PAID','NEW');

\echo ''
\echo '--- Условие на 75% таблицы: status <> ''NEW'' ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE status <> 'NEW';

\echo ''
\echo '--- Условие на 100% таблицы: status IS NOT NULL ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE status IS NOT NULL;

\echo ''
\echo '################ Сводка селективности по status ################'
WITH t AS (SELECT count(*)::numeric AS total FROM orders)
SELECT s.status,
       s.cnt                                   AS "строк возвращает",
       round(100.0 * s.cnt / t.total, 2)       AS "% таблицы",
       round(s.cnt / t.total, 4)               AS "доля (селективность условия)"
FROM (SELECT status, count(*)::numeric AS cnt FROM orders GROUP BY status) s, t
ORDER BY s.cnt DESC;
