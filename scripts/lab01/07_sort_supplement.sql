\pset pager off
\echo '################ ЗАДАНИЕ 14/15 (дополнение). Почему Sort не исчез для user_id = 123 ################'
\echo '--- Тот же запрос, но bitmap scan запрещён => чистый Index Scan по (user_id, created_at DESC) ---'
SET enable_bitmapscan = off;
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE user_id = 123 ORDER BY created_at DESC;
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE user_id = 123 ORDER BY created_at DESC LIMIT 20;
\echo '--- А теперь запрещён ещё и индекс (user_id, created_at DESC): виден Sort ---'
SET enable_bitmapscan = on;
BEGIN;
DROP INDEX idx_orders_user_created_at_desc;
DROP INDEX idx_orders_user_created_at;
DROP INDEX idx_orders_created_at_user;
DROP INDEX idx_orders_user_id_include;
SET enable_bitmapscan = off;
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE user_id = 123 ORDER BY created_at DESC LIMIT 20;
ROLLBACK;
SET enable_bitmapscan = on;

\echo ''
\echo '--- Пагинация «тяжёлого» пользователя (27 заказов) ---'
SELECT user_id, count(*) FROM orders GROUP BY user_id ORDER BY count(*) DESC LIMIT 1 \gset heavy_
\echo '--- ДО (без составного индекса) ---'
BEGIN;
DROP INDEX idx_orders_user_created_at_desc;
DROP INDEX idx_orders_user_created_at;
DROP INDEX idx_orders_created_at_user;
DROP INDEX idx_orders_user_id_include;
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE user_id = :heavy_user_id ORDER BY created_at DESC LIMIT 20;
ROLLBACK;
\echo '--- ПОСЛЕ (составной индекс есть) ---'
SET enable_bitmapscan = off;
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE user_id = :heavy_user_id ORDER BY created_at DESC LIMIT 20;
SET enable_bitmapscan = on;
