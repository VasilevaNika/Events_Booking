-- =====================================================================
-- Лабораторная работа №1, часть «Работа со своим сервисом».
-- Быстрая генерация 1 000 000 бронирований напрямую средствами SQL.
-- (scripts/seed.py делает то же самое через ORM, но на миллионе строк
--  это заметно медленнее; здесь нужен только объём для EXPLAIN ANALYZE.)
--
-- Запуск:
--   docker exec -i booking_postgres psql -U booking_user -d booking_db \
--       -f /tmp/lab01/seed_bookings_bulk.sql
-- =====================================================================

INSERT INTO bookings (user_id, event_id, status, seats_count, total_price, created_at)
SELECT
    u.id,
    e.id,
    CASE
        WHEN r < 0.10 THEN 'pending'
        WHEN r < 0.70 THEN 'confirmed'
        WHEN r < 0.85 THEN 'cancelled'
        ELSE                 'completed'
    END,
    seats,
    round((e.price * seats)::numeric, 2),
    NOW() - (random() * INTERVAL '1 year')
FROM (
    SELECT
        (SELECT min(id) FROM users)  + (random() * ((SELECT max(id) FROM users)  - (SELECT min(id) FROM users)))::int  AS uid,
        (SELECT min(id) FROM events) + (random() * ((SELECT max(id) FROM events) - (SELECT min(id) FROM events)))::int AS eid,
        random()                     AS r,
        (1 + floor(random() * 4))::int AS seats
    FROM generate_series(1, 1000000)
) g
JOIN users  u ON u.id = g.uid
JOIN events e ON e.id = g.eid;

VACUUM ANALYZE bookings;

SELECT count(*) AS bookings_total FROM bookings;
SELECT status, count(*), round(100.0*count(*)/sum(count(*)) OVER (),2) AS pct
FROM bookings GROUP BY status ORDER BY 2 DESC;
SELECT pg_size_pretty(pg_relation_size('bookings'))       AS heap,
       pg_size_pretty(pg_indexes_size('bookings'))        AS indexes,
       pg_size_pretty(pg_total_relation_size('bookings')) AS total;
