\pset pager off
\echo '################ ЗАДАНИЕ 6. Создание B-tree индекса по user_id ################'
CREATE INDEX idx_orders_user_id ON orders(user_id);
ANALYZE orders;

\echo ''
\echo '--- Размер индекса ---'
SELECT pg_size_pretty(pg_relation_size('idx_orders_user_id')) AS idx_orders_user_id_size,
       pg_size_pretty(pg_relation_size('orders'))             AS orders_heap_size;

\echo ''
\echo '--- ЗАДАНИЕ 6. EXPLAIN ANALYZE ПОСЛЕ индекса (прогон 1) ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE user_id = 123;
\echo '--- ЗАДАНИЕ 6. EXPLAIN ANALYZE ПОСЛЕ индекса (прогон 2, тёплый) ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE user_id = 123;

\echo ''
\echo '################ ЗАДАНИЕ 8. Распределение значений status ################'
SELECT status,
       count(*) AS rows,
       round(100.0 * count(*) / sum(count(*)) OVER (), 2) AS pct_of_table
FROM orders GROUP BY status ORDER BY rows DESC;

\echo ''
\echo '--- Селективность колонок (n_distinct из статистики) ---'
SELECT attname, n_distinct, correlation
FROM pg_stats WHERE tablename = 'orders' ORDER BY attname;

\echo ''
\echo '################ ЗАДАНИЕ 7. Индекс по низкоселективной колонке status ################'
CREATE INDEX idx_orders_status ON orders(status);
ANALYZE orders;
SELECT pg_size_pretty(pg_relation_size('idx_orders_status')) AS idx_orders_status_size;

\echo ''
\echo '--- WHERE status = ''PAID'' ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE status = 'PAID';
\echo '--- WHERE status = ''NEW'' ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE status = 'NEW';
\echo '--- WHERE status = ''DELIVERED'' ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE status = 'DELIVERED';
\echo '--- WHERE status = ''CANCELLED'' ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE status = 'CANCELLED';

\echo ''
\echo '--- Доказательство: тот же запрос при принудительно отключённом Seq Scan ---'
SET enable_seqscan = off;
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE status = 'PAID';
SET enable_seqscan = on;

\echo ''
\echo '--- Контрпример: высокоселективное условие по той же таблице (COUNT(*) = 1) ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE id = 500000;
