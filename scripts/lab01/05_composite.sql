\pset pager off
\echo '################ ЗАДАНИЕ 11. Два отдельных индекса: user_id + status ################'
\echo '--- Индексы idx_orders_user_id и idx_orders_status уже созданы ---'
SELECT indexname FROM pg_indexes WHERE tablename='orders' ORDER BY indexname;

\echo ''
\echo '--- WHERE user_id = 123 AND status = ''PAID'' (два отдельных индекса) ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE user_id = 123 AND status = 'PAID';

\echo ''
\echo '--- Когда BitmapAnd действительно появляется: оба условия среднеселективны ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE user_id BETWEEN 100 AND 400 AND status = 'PAID';

\echo ''
\echo '################ ЗАДАНИЕ 12. Составной индекс (user_id, status) ################'
CREATE INDEX idx_orders_user_status ON orders(user_id, status);
ANALYZE orders;
SELECT pg_size_pretty(pg_relation_size('idx_orders_user_status')) AS idx_orders_user_status_size;

\echo ''
\echo '--- WHERE user_id = 123 AND status = ''PAID'' (составной индекс доступен) ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE user_id = 123 AND status = 'PAID';
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE user_id = 123 AND status = 'PAID';

\echo ''
\echo '--- Тот же запрос с ЗАПРЕЩЁННЫМ составным индексом (для честного сравнения) ---'
BEGIN;
DROP INDEX idx_orders_user_status;
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE user_id = 123 AND status = 'PAID';
ROLLBACK;

\echo ''
\echo '--- BitmapAnd-запрос со составным индексом ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE user_id BETWEEN 100 AND 400 AND status = 'PAID';

\echo ''
\echo '################ ЗАДАНИЕ 13. Порядок колонок: (user_id, created_at) ################'
CREATE INDEX idx_orders_user_created_at ON orders(user_id, created_at);
ANALYZE orders;

\echo '--- Запрос 1: WHERE user_id = 123 ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE user_id = 123;
\echo '--- Запрос 2: WHERE user_id = 123 AND created_at > NOW() - 30 days ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE user_id = 123 AND created_at > NOW() - INTERVAL '30 days';
\echo '--- Запрос 3: WHERE created_at > NOW() - 30 days ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE created_at > NOW() - INTERVAL '30 days';

\echo ''
\echo '--- Проверка «левого префикса»: только по created_at, все прочие индексы по created_at скрыты ---'
BEGIN;
DROP INDEX idx_orders_created_at;
SET enable_seqscan = off;
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE created_at > NOW() - INTERVAL '30 days';
ROLLBACK;

\echo ''
\echo '################ ЗАДАНИЕ 13. Обратный порядок: (created_at, user_id) ################'
CREATE INDEX idx_orders_created_at_user ON orders(created_at, user_id);
ANALYZE orders;
SELECT indexname, pg_size_pretty(pg_relation_size(indexname::regclass)) AS size
FROM pg_indexes WHERE tablename='orders' ORDER BY indexname;

\echo '--- Запрос 1: WHERE user_id = 123 ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE user_id = 123;
\echo '--- Запрос 2: WHERE user_id = 123 AND created_at > NOW() - 30 days ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE user_id = 123 AND created_at > NOW() - INTERVAL '30 days';
\echo '--- Запрос 3: WHERE created_at > NOW() - 30 days ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE created_at > NOW() - INTERVAL '30 days';

\echo ''
\echo '--- Запрос 2 принудительно через (created_at, user_id) ---'
BEGIN;
DROP INDEX idx_orders_user_created_at;
DROP INDEX idx_orders_user_id;
DROP INDEX idx_orders_user_status;
SET enable_seqscan = off;
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE user_id = 123 AND created_at > NOW() - INTERVAL '30 days';
ROLLBACK;
