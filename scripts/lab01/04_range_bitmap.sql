\pset pager off
\echo '################ ЗАДАНИЕ 9. Range Query — ДО индекса по created_at ################'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE created_at > NOW() - INTERVAL '7 days';

\echo ''
\echo '--- Создаём индекс по created_at ---'
CREATE INDEX idx_orders_created_at ON orders(created_at);
ANALYZE orders;
SELECT pg_size_pretty(pg_relation_size('idx_orders_created_at')) AS idx_orders_created_at_size;

\echo ''
\echo '--- ПОСЛЕ индекса: created_at > NOW() - 7 days ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE created_at > NOW() - INTERVAL '7 days';

\echo ''
\echo '--- Диапазон 1 day ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE created_at > NOW() - INTERVAL '1 day';
\echo '--- Диапазон 1 month ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE created_at > NOW() - INTERVAL '1 month';
\echo '--- Диапазон 6 months ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE created_at > NOW() - INTERVAL '6 months';
\echo '--- Диапазон 1 year ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE created_at > NOW() - INTERVAL '1 year';

\echo ''
\echo '--- Сколько строк попадает в каждый диапазон ---'
SELECT '1 day'    AS range, count(*), round(100.0*count(*)/1000000,2) AS pct FROM orders WHERE created_at > NOW() - INTERVAL '1 day'
UNION ALL SELECT '7 days',   count(*), round(100.0*count(*)/1000000,2) FROM orders WHERE created_at > NOW() - INTERVAL '7 days'
UNION ALL SELECT '1 month',  count(*), round(100.0*count(*)/1000000,2) FROM orders WHERE created_at > NOW() - INTERVAL '1 month'
UNION ALL SELECT '6 months', count(*), round(100.0*count(*)/1000000,2) FROM orders WHERE created_at > NOW() - INTERVAL '6 months'
UNION ALL SELECT '1 year',   count(*), round(100.0*count(*)/1000000,2) FROM orders WHERE created_at > NOW() - INTERVAL '1 year';

\echo ''
\echo '################ ЗАДАНИЕ 10. Bitmap Index Scan + Bitmap Heap Scan ################'
\echo '--- amount BETWEEN 1000 AND 3000 (индекса по amount нет) ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE amount BETWEEN 1000 AND 3000;

\echo ''
\echo '--- создаём индекс по amount и повторяем ---'
CREATE INDEX idx_orders_amount ON orders(amount);
ANALYZE orders;
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE amount BETWEEN 1000 AND 3000;

\echo ''
\echo '--- «Средний» объём строк: user_id BETWEEN 100 AND 400 (≈0.3% таблицы) ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE user_id BETWEEN 100 AND 400;

\echo ''
\echo '--- Для сравнения: точечный Index Scan (1 строка) vs Bitmap ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE user_id = 123 ORDER BY id LIMIT 1;
