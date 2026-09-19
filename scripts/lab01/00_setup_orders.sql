-- =====================================================================
-- Лабораторная работа №1. Часть 1. Подготовка тестовой базы данных
-- Задание 1. Создание таблицы orders
-- Задание 2. Генерация 1 000 000 записей
-- =====================================================================

DROP TABLE IF EXISTS orders;

CREATE TABLE orders (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT        NOT NULL,
    product_id  BIGINT        NOT NULL,
    status      VARCHAR(20)   NOT NULL,
    amount      NUMERIC(10,2) NOT NULL,
    created_at  TIMESTAMP     NOT NULL,
    updated_at  TIMESTAMP     NOT NULL
);

INSERT INTO orders (user_id, product_id, status, amount, created_at, updated_at)
SELECT
    (random() * 100000)::BIGINT,
    (random() * 10000)::BIGINT,
    (ARRAY['NEW','PAID','DELIVERED','CANCELLED'])[floor(random() * 4 + 1)],
    random() * 10000,
    NOW() - (random() * INTERVAL '2 years'),
    NOW()
FROM generate_series(1, 1000000);

ANALYZE orders;

SELECT count(*) AS rows_total FROM orders;
SELECT pg_size_pretty(pg_total_relation_size('orders')) AS table_total_size;
