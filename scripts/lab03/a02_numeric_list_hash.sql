\pset pager off
\set ON_ERROR_STOP on
\timing on

\echo '################ ЧАСТЬ 3. RANGE по числовому значению ################'
CREATE TABLE lab3.products (
    id    BIGINT NOT NULL,
    name  TEXT NOT NULL,
    price NUMERIC NOT NULL
) PARTITION BY RANGE (price);

CREATE TABLE lab3.products_cheap     PARTITION OF lab3.products FOR VALUES FROM (0)    TO (100);
CREATE TABLE lab3.products_medium    PARTITION OF lab3.products FOR VALUES FROM (100)  TO (1000);
CREATE TABLE lab3.products_expensive PARTITION OF lab3.products FOR VALUES FROM (1000) TO (MAXVALUE);

INSERT INTO lab3.products (id, name, price) VALUES
    (1, 'Ручка',          49.90),
    (2, 'Блокнот',        99.99),
    (3, 'Клавиатура',    100.00),
    (4, 'Монитор',       999.99),
    (5, 'Ноутбук',      1000.00),
    (6, 'Сервер',      250000.00);

INSERT INTO lab3.products (id, name, price)
SELECT 100 + s, 'Товар ' || s, round((random() * 5000)::numeric, 2)
FROM generate_series(1, 60000) s;

\timing off
VACUUM ANALYZE lab3.products;
\timing on

\echo '--- распределение по партициям ---'
SELECT tableoid::regclass AS partition_name, COUNT(*) FROM lab3.products
GROUP BY tableoid ORDER BY partition_name;

\echo '--- куда попали именованные товары (границы 100 и 1000) ---'
SELECT id, name, price, tableoid::regclass AS partition_name
FROM lab3.products WHERE id <= 6 ORDER BY id;

\echo '--- запрос price >= 100 AND price < 500 ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM lab3.products WHERE price >= 100 AND price < 500;

\echo ''
\echo '################ ЧАСТЬ 4. LIST PARTITIONING ################'
CREATE TABLE lab3.customers (
    id            BIGINT NOT NULL,
    name          TEXT NOT NULL,
    customer_type VARCHAR(30) NOT NULL
) PARTITION BY LIST (customer_type);

CREATE TABLE lab3.customers_b2c        PARTITION OF lab3.customers FOR VALUES IN ('B2C');
CREATE TABLE lab3.customers_b2b        PARTITION OF lab3.customers FOR VALUES IN ('B2B');
CREATE TABLE lab3.customers_enterprise PARTITION OF lab3.customers FOR VALUES IN ('Enterprise');

INSERT INTO lab3.customers (id, name, customer_type)
SELECT s, 'Клиент ' || s,
       CASE WHEN random() < 0.70 THEN 'B2C'
            WHEN random() < 0.95 THEN 'B2B'
            ELSE 'Enterprise' END
FROM generate_series(1, 60000) s;

\timing off
VACUUM ANALYZE lab3.customers;
\timing on

SELECT tableoid::regclass AS partition_name, COUNT(*) FROM lab3.customers
GROUP BY tableoid ORDER BY partition_name;

\echo '--- запрос customer_type = B2B ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM lab3.customers WHERE customer_type = 'B2B';

\echo ''
\echo '################ ЧАСТЬ 5. LIST: неизвестное значение ################'
\echo '--- вставка VIP без DEFAULT-партиции — ожидаем ошибку ---'
\set ON_ERROR_STOP off
INSERT INTO lab3.customers VALUES (100, 'Test User', 'VIP');
\set ON_ERROR_STOP on

\echo '--- создаём DEFAULT-партицию и повторяем вставку ---'
CREATE TABLE lab3.customers_default PARTITION OF lab3.customers DEFAULT;
INSERT INTO lab3.customers VALUES (100, 'Test User', 'VIP');
SELECT id, name, customer_type, tableoid::regclass AS partition_name
FROM lab3.customers WHERE id = 100;

\echo '--- цена DEFAULT: теперь её приходится проверять при поиске ---'
VACUUM ANALYZE lab3.customers;
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM lab3.customers WHERE customer_type = 'VIP';

\echo '--- и добавить новую партицию для VIP уже нельзя, пока строка лежит в DEFAULT ---'
\set ON_ERROR_STOP off
CREATE TABLE lab3.customers_vip PARTITION OF lab3.customers FOR VALUES IN ('VIP');
\set ON_ERROR_STOP on

\echo ''
\echo '################ ЧАСТЬ 6. HASH PARTITIONING ################'
CREATE TABLE lab3.user_events (
    id         BIGINT NOT NULL,
    user_id    BIGINT NOT NULL,
    event_type VARCHAR(50),
    created_at TIMESTAMP NOT NULL
) PARTITION BY HASH (user_id);

CREATE TABLE lab3.user_events_0 PARTITION OF lab3.user_events FOR VALUES WITH (MODULUS 4, REMAINDER 0);
CREATE TABLE lab3.user_events_1 PARTITION OF lab3.user_events FOR VALUES WITH (MODULUS 4, REMAINDER 1);
CREATE TABLE lab3.user_events_2 PARTITION OF lab3.user_events FOR VALUES WITH (MODULUS 4, REMAINDER 2);
CREATE TABLE lab3.user_events_3 PARTITION OF lab3.user_events FOR VALUES WITH (MODULUS 4, REMAINDER 3);

INSERT INTO lab3.user_events (id, user_id, event_type, created_at)
SELECT s, (random() * 100000)::bigint, 'click',
       NOW() - (random() * INTERVAL '365 days')
FROM generate_series(1, 800000) s;

\timing off
VACUUM ANALYZE lab3.user_events;
\timing on

\echo '--- распределение по hash-партициям ---'
SELECT tableoid::regclass AS partition_name,
       COUNT(*),
       round(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct
FROM lab3.user_events GROUP BY tableoid ORDER BY partition_name;

\echo '--- поиск по ключу хэширования: pruning работает ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT COUNT(*) FROM lab3.user_events WHERE user_id = 12345;

\echo '--- поиск по дате: pruning НЕ работает, читаются все партиции ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT COUNT(*) FROM lab3.user_events WHERE created_at >= NOW() - INTERVAL '30 days';
