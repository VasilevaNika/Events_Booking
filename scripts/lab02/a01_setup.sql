\pset pager off
\set ON_ERROR_STOP on
\echo '######## ЗАДАНИЕ 1. Создание экспериментальной таблицы ########'
DROP SCHEMA IF EXISTS lab2 CASCADE;
CREATE SCHEMA lab2;

CREATE TABLE lab2.events (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    payload    JSONB,
    created_at TIMESTAMP NOT NULL
);

\d lab2.events
