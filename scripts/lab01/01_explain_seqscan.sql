\pset pager off
\echo '################ ЗАДАНИЕ 3. EXPLAIN (без ANALYZE) ################'
EXPLAIN
SELECT * FROM orders WHERE user_id = 123;

\echo ''
\echo '################ ЗАДАНИЕ 4. EXPLAIN ANALYZE (прогон 1, холодный) ################'
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE user_id = 123;

\echo ''
\echo '################ ЗАДАНИЕ 4. EXPLAIN ANALYZE (прогон 2, тёплый кэш) ################'
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE user_id = 123;

\echo ''
\echo '################ ЗАДАНИЕ 5.1. Seq Scan: SELECT * FROM orders (без WHERE) ################'
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders;

\echo ''
\echo '################ ЗАДАНИЕ 5.2. Seq Scan: WHERE amount > 0 (условие ничего не отсекает) ################'
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE amount > 0;

\echo ''
\echo '################ Справка: сколько строк реально попадает под amount > 0 ################'
SELECT count(*) AS rows_amount_gt_0, (SELECT count(*) FROM orders) AS rows_total FROM orders WHERE amount > 0;

\echo ''
\echo '################ Справка: параметры планировщика ################'
SELECT name, setting FROM pg_settings
WHERE name IN ('seq_page_cost','random_page_cost','effective_cache_size','shared_buffers',
               'work_mem','max_parallel_workers_per_gather','min_parallel_table_scan_size');

\echo ''
\echo '################ Справка: список индексов на orders ДО оптимизации ################'
SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'orders' ORDER BY indexname;
