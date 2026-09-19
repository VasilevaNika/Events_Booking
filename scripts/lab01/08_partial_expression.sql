\pset pager off
\echo '################ ЗАДАНИЕ 17. Partial Index ################'
\echo '--- Доля строк со статусом NEW ---'
SELECT count(*) FILTER (WHERE status='NEW') AS new_rows, count(*) AS total,
       round(100.0*count(*) FILTER (WHERE status='NEW')/count(*),2) AS pct FROM orders;

\echo ''
\echo '--- ДО: WHERE status = ''NEW'' ORDER BY created_at (есть обычный idx_orders_status) ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE status = 'NEW' ORDER BY created_at;

\echo ''
\echo '--- Создаём частичный индекс ---'
CREATE INDEX idx_orders_new ON orders(created_at) WHERE status = 'NEW';
ANALYZE orders;
SELECT pg_size_pretty(pg_relation_size('idx_orders_new'))        AS partial_idx_size,
       pg_size_pretty(pg_relation_size('idx_orders_status'))     AS full_status_idx_size,
       pg_size_pretty(pg_relation_size('idx_orders_created_at')) AS full_created_at_idx_size;

\echo '--- ПОСЛЕ: WHERE status = ''NEW'' ORDER BY created_at ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE status = 'NEW' ORDER BY created_at;
\echo '--- ПОСЛЕ: тот же запрос с LIMIT 100 (типичный API-сценарий) ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE status = 'NEW' ORDER BY created_at LIMIT 100;
\echo '--- Для сравнения: LIMIT 100 без частичного индекса ---'
BEGIN;
DROP INDEX idx_orders_new;
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE status = 'NEW' ORDER BY created_at LIMIT 100;
ROLLBACK;

\echo ''
\echo '--- Partial index по РЕДКОМУ подмножеству (status=NEW AND amount<100, ~0.25% таблицы) ---'
SELECT count(*) AS rare_rows, round(100.0*count(*)/1000000,3) AS pct FROM orders WHERE status='NEW' AND amount < 100;
\echo '  ДО:'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE status='NEW' AND amount < 100 ORDER BY created_at;
CREATE INDEX idx_orders_new_small ON orders(created_at) WHERE status='NEW' AND amount < 100;
ANALYZE orders;
SELECT pg_size_pretty(pg_relation_size('idx_orders_new_small')) AS rare_partial_idx_size;
\echo '  ПОСЛЕ:'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE status='NEW' AND amount < 100 ORDER BY created_at;

\echo ''
\echo '--- Частичный индекс НЕ подходит другому запросу ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM orders WHERE status = 'PAID' ORDER BY created_at LIMIT 100;

\echo ''
\echo '################ ЗАДАНИЕ 18. Expression Index ################'
DROP TABLE IF EXISTS users;
CREATE TABLE users (
    id    BIGSERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL
);
INSERT INTO users (email)
SELECT 'User' || g || '_' || md5(random()::text) || '@Example.COM'
FROM generate_series(1, 500000) g;
INSERT INTO users (email) VALUES ('Test@Example.com');
CREATE INDEX idx_users_email ON users(email);
ANALYZE users;
SELECT count(*) FROM users;

\echo ''
\echo '--- Обычный индекс есть; запрос по LOWER(email) ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM users WHERE LOWER(email) = 'test@example.com';
\echo '--- Для контраста: точное совпадение без функции — индекс работает ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM users WHERE email = 'Test@Example.com';

\echo ''
\echo '--- Создаём индекс по выражению ---'
CREATE INDEX idx_users_lower_email ON users(LOWER(email));
ANALYZE users;
\echo '--- ПОСЛЕ: запрос по LOWER(email) ---'
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM users WHERE LOWER(email) = 'test@example.com';
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM users WHERE LOWER(email) = 'test@example.com';
SELECT pg_size_pretty(pg_relation_size('idx_users_email'))       AS plain_email_idx,
       pg_size_pretty(pg_relation_size('idx_users_lower_email')) AS expression_idx;
