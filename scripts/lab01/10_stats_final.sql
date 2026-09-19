\pset pager off
\echo '################ ЗАДАНИЕ 20. Статистика использования индексов ################'
SELECT schemaname, relname, indexrelname, idx_scan,
       idx_tup_read, idx_tup_fetch,
       pg_size_pretty(pg_relation_size(indexrelid)) AS size
FROM pg_stat_user_indexes
WHERE relname IN ('orders','users')
ORDER BY idx_scan DESC, indexrelname;

\echo ''
\echo '--- Индексы, которые ни разу не использовались (idx_scan = 0) ---'
SELECT relname, indexrelname, pg_size_pretty(pg_relation_size(indexrelid)) AS wasted_space
FROM pg_stat_user_indexes WHERE idx_scan = 0 ORDER BY pg_relation_size(indexrelid) DESC;

\echo ''
\echo '--- Общий объём: таблица vs все её индексы ---'
SELECT pg_size_pretty(pg_relation_size('orders'))       AS heap,
       pg_size_pretty(pg_indexes_size('orders'))        AS all_indexes,
       pg_size_pretty(pg_total_relation_size('orders')) AS total;

\echo ''
\echo '################ ЗАДАНИЕ 21. Финальная оптимизация запроса ################'
\echo '--- ДО: текущее состояние индексов ---'
SELECT indexname FROM pg_indexes WHERE tablename='orders' ORDER BY indexname;

\echo ''
\echo '--- ДО. EXPLAIN (без ANALYZE) ---'
EXPLAIN
SELECT id, amount, status, created_at FROM orders
WHERE user_id = 123 AND status = 'PAID' AND created_at >= NOW() - INTERVAL '30 days'
ORDER BY created_at DESC LIMIT 50;

\echo '--- ДО. EXPLAIN ANALYZE (user_id = 123, у которого мало заказов) ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, amount, status, created_at FROM orders
WHERE user_id = 123 AND status = 'PAID' AND created_at >= NOW() - INTERVAL '30 days'
ORDER BY created_at DESC LIMIT 50;

\echo ''
\echo '--- ДО. Нагляднее: тот же запрос по диапазону пользователей (реальная нагрузка) ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, amount, status, created_at FROM orders
WHERE user_id BETWEEN 1 AND 20000 AND status = 'PAID' AND created_at >= NOW() - INTERVAL '30 days'
ORDER BY created_at DESC LIMIT 50;

\echo ''
\echo '--- ДО. «Чистый» замер: все составные индексы временно скрыты ---'
BEGIN;
DROP INDEX idx_orders_user_status;
DROP INDEX idx_orders_user_created_at;
DROP INDEX idx_orders_user_created_at_desc;
DROP INDEX idx_orders_created_at_user;
DROP INDEX idx_orders_user_id_include;
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, amount, status, created_at FROM orders
WHERE user_id = 123 AND status = 'PAID' AND created_at >= NOW() - INTERVAL '30 days'
ORDER BY created_at DESC LIMIT 50;
ROLLBACK;

\echo ''
\echo '################ Создаём целевой индекс (равенства слева, диапазон справа) ################'
CREATE INDEX idx_orders_user_status_created_at
    ON orders(user_id, status, created_at DESC);
VACUUM ANALYZE orders;
SELECT pg_size_pretty(pg_relation_size('idx_orders_user_status_created_at')) AS size;

\echo ''
\echo '--- ПОСЛЕ. EXPLAIN ANALYZE ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, amount, status, created_at FROM orders
WHERE user_id = 123 AND status = 'PAID' AND created_at >= NOW() - INTERVAL '30 days'
ORDER BY created_at DESC LIMIT 50;
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, amount, status, created_at FROM orders
WHERE user_id = 123 AND status = 'PAID' AND created_at >= NOW() - INTERVAL '30 days'
ORDER BY created_at DESC LIMIT 50;

\echo ''
\echo '--- ПОСЛЕ. По диапазону пользователей ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, amount, status, created_at FROM orders
WHERE user_id BETWEEN 1 AND 20000 AND status = 'PAID' AND created_at >= NOW() - INTERVAL '30 days'
ORDER BY created_at DESC LIMIT 50;

\echo ''
\echo '--- Альтернатива: покрывающий индекс с INCLUDE (Index Only Scan) ---'
CREATE INDEX idx_orders_uid_status_created_incl
    ON orders(user_id, status, created_at DESC) INCLUDE (id, amount);
VACUUM ANALYZE orders;
SELECT pg_size_pretty(pg_relation_size('idx_orders_uid_status_created_incl')) AS size;
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, amount, status, created_at FROM orders
WHERE user_id = 123 AND status = 'PAID' AND created_at >= NOW() - INTERVAL '30 days'
ORDER BY created_at DESC LIMIT 50;

\echo ''
\echo '--- Неправильный порядок колонок для сравнения: (created_at, user_id, status) ---'
CREATE INDEX idx_orders_bad_order ON orders(created_at DESC, user_id, status);
VACUUM ANALYZE orders;
BEGIN;
DROP INDEX idx_orders_user_status_created_at;
DROP INDEX idx_orders_uid_status_created_incl;
DROP INDEX idx_orders_user_status;
DROP INDEX idx_orders_user_created_at;
DROP INDEX idx_orders_user_created_at_desc;
DROP INDEX idx_orders_user_id;
DROP INDEX idx_orders_user_id_include;
SET enable_seqscan = off;
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, amount, status, created_at FROM orders
WHERE user_id = 123 AND status = 'PAID' AND created_at >= NOW() - INTERVAL '30 days'
ORDER BY created_at DESC LIMIT 50;
ROLLBACK;
DROP INDEX idx_orders_bad_order;
