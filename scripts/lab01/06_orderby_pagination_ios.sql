\pset pager off
\echo '################ Справка: сколько заказов у user_id = 123 и вообще на пользователя ################'
SELECT count(*) AS orders_of_user_123 FROM orders WHERE user_id = 123;
SELECT round(avg(c),2) AS avg_orders_per_user, max(c) AS max_orders_per_user
FROM (SELECT user_id, count(*) c FROM orders GROUP BY user_id) t;

\echo ''
\echo '################ ЗАДАНИЕ 14. WHERE + ORDER BY — состояние ДО (составных индексов нет) ################'
BEGIN;
DROP INDEX idx_orders_user_created_at;
DROP INDEX idx_orders_created_at_user;
DROP INDEX idx_orders_user_status;
\echo '--- ДО: user_id = 123 ORDER BY created_at DESC ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE user_id = 123 ORDER BY created_at DESC;
\echo '--- ДО (нагляднее): 5000 пользователей, ORDER BY created_at DESC LIMIT 20 ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE user_id BETWEEN 1 AND 5000 ORDER BY created_at DESC LIMIT 20;
\echo '--- ДО: пагинация user_id = 123 ORDER BY created_at DESC LIMIT 20 (ЗАДАНИЕ 15) ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE user_id = 123 ORDER BY created_at DESC LIMIT 20;
ROLLBACK;

\echo ''
\echo '################ ЗАДАНИЕ 14. Создаём индекс (user_id, created_at DESC) ################'
CREATE INDEX idx_orders_user_created_at_desc ON orders(user_id, created_at DESC);
ANALYZE orders;
SELECT pg_size_pretty(pg_relation_size('idx_orders_user_created_at_desc')) AS size;

\echo '--- ПОСЛЕ: user_id = 123 ORDER BY created_at DESC ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE user_id = 123 ORDER BY created_at DESC;
\echo '--- ПОСЛЕ (нагляднее): 5000 пользователей, ORDER BY created_at DESC LIMIT 20 ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE user_id BETWEEN 1 AND 5000 ORDER BY created_at DESC LIMIT 20;

\echo ''
\echo '################ ЗАДАНИЕ 15. Pagination Query — ПОСЛЕ индекса ################'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE user_id = 123 ORDER BY created_at DESC LIMIT 20;
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE user_id = 123 ORDER BY created_at DESC LIMIT 20;

\echo ''
\echo '################ ЗАДАНИЕ 16. Index Only Scan ################'
\echo '--- SELECT id, user_id WHERE user_id = 123 (есть idx_orders_user_id) ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT id, user_id FROM orders WHERE user_id = 123;
\echo '--- SELECT user_id (только ключ индекса) ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT user_id FROM orders WHERE user_id = 123;
\echo '--- Проверка карты видимости ---'
SELECT relname, relpages, reltuples::bigint, relallvisible FROM pg_class WHERE relname = 'orders';
\echo '--- VACUUM ANALYZE и повтор ---'
VACUUM ANALYZE orders;
SELECT relname, relpages, reltuples::bigint, relallvisible FROM pg_class WHERE relname = 'orders';
EXPLAIN (ANALYZE, BUFFERS) SELECT id, user_id FROM orders WHERE user_id = 123;

\echo ''
\echo '################ ЗАДАНИЕ 16 (доп). Покрывающий индекс INCLUDE ################'
CREATE INDEX idx_orders_user_id_include ON orders(user_id) INCLUDE (id, status, created_at);
VACUUM ANALYZE orders;
SELECT pg_size_pretty(pg_relation_size('idx_orders_user_id_include')) AS include_index_size,
       pg_size_pretty(pg_relation_size('idx_orders_user_id'))         AS plain_index_size;
\echo '--- SELECT id, status, created_at WHERE user_id = 123 ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT id, status, created_at FROM orders WHERE user_id = 123;
EXPLAIN (ANALYZE, BUFFERS) SELECT id, status, created_at FROM orders WHERE user_id = 123;
\echo '--- Контрпример: добавили amount, которого нет в индексе ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT id, status, created_at, amount FROM orders WHERE user_id = 123;
