\pset pager off
\timing on
\echo '################ ЗАДАНИЕ 19. Влияние индексов на INSERT ################'

DROP TABLE IF EXISTS orders_no_idx;
DROP TABLE IF EXISTS orders_with_idx;

\echo ''
\echo '=== A. Таблица БЕЗ дополнительных индексов (только PK) ==='
CREATE TABLE orders_no_idx (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL, product_id BIGINT NOT NULL,
    status VARCHAR(20) NOT NULL, amount NUMERIC(10,2) NOT NULL,
    created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL
);
\echo '--- INSERT 300 000 строк в таблицу БЕЗ индексов ---'
INSERT INTO orders_no_idx (user_id, product_id, status, amount, created_at, updated_at)
SELECT (random()*100000)::BIGINT, (random()*10000)::BIGINT,
       (ARRAY['NEW','PAID','DELIVERED','CANCELLED'])[floor(random()*4+1)],
       random()*10000, NOW() - (random() * INTERVAL '2 years'), NOW()
FROM generate_series(1, 300000);

\echo ''
\echo '=== B. Таблица С 6 дополнительными индексами ==='
CREATE TABLE orders_with_idx (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL, product_id BIGINT NOT NULL,
    status VARCHAR(20) NOT NULL, amount NUMERIC(10,2) NOT NULL,
    created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL
);
CREATE INDEX i1 ON orders_with_idx(user_id);
CREATE INDEX i2 ON orders_with_idx(status);
CREATE INDEX i3 ON orders_with_idx(created_at);
CREATE INDEX i4 ON orders_with_idx(amount);
CREATE INDEX i5 ON orders_with_idx(user_id, status);
CREATE INDEX i6 ON orders_with_idx(user_id, created_at DESC);
\echo '--- INSERT 300 000 строк в таблицу С 6 индексами ---'
INSERT INTO orders_with_idx (user_id, product_id, status, amount, created_at, updated_at)
SELECT (random()*100000)::BIGINT, (random()*10000)::BIGINT,
       (ARRAY['NEW','PAID','DELIVERED','CANCELLED'])[floor(random()*4+1)],
       random()*10000, NOW() - (random() * INTERVAL '2 years'), NOW()
FROM generate_series(1, 300000);

\echo ''
\echo '=== C. UPDATE 50 000 строк (колонка НЕ входит ни в один индекс) ==='
UPDATE orders_no_idx   SET product_id = product_id + 1 WHERE id <= 50000;
UPDATE orders_with_idx SET product_id = product_id + 1 WHERE id <= 50000;

\echo ''
\echo '=== D. UPDATE 50 000 строк (колонка ВХОДИТ в индексы) ==='
UPDATE orders_no_idx   SET status = 'PAID' WHERE id BETWEEN 100000 AND 150000;
UPDATE orders_with_idx SET status = 'PAID' WHERE id BETWEEN 100000 AND 150000;

\echo ''
\echo '=== E. DELETE 50 000 строк ==='
DELETE FROM orders_no_idx   WHERE id BETWEEN 200000 AND 250000;
DELETE FROM orders_with_idx WHERE id BETWEEN 200000 AND 250000;

\timing off
\echo ''
\echo '=== Размеры: данные vs индексы ==='
SELECT relname AS table,
       pg_size_pretty(pg_relation_size(c.oid))                                 AS heap,
       pg_size_pretty(pg_indexes_size(c.oid))                                  AS indexes,
       pg_size_pretty(pg_total_relation_size(c.oid))                           AS total,
       round(100.0*pg_indexes_size(c.oid)/NULLIF(pg_relation_size(c.oid),0),1) AS "индексы, % от данных"
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE relname IN ('orders_no_idx','orders_with_idx','orders') AND n.nspname='public'
ORDER BY relname;
