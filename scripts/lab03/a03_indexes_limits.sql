\pset pager off
\set ON_ERROR_STOP on
\timing on

\echo '################ ЧАСТЬ 8. Партиционирование и индексы ################'
\echo '--- ДО индекса: pruning есть, но внутри партиции Seq Scan ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM lab3.events
WHERE created_at >= '2026-09-10' AND created_at < '2026-09-11'
  AND user_id = 12345;

\echo '--- создаём индекс на РОДИТЕЛЬСКОЙ таблице ---'
CREATE INDEX idx_events_user_id ON lab3.events (user_id);
\timing off
VACUUM ANALYZE lab3.events;
\timing on

\echo '--- что физически создалось: локальные индексы в каждой партиции ---'
SELECT c.relname AS index_name,
       c.relkind,
       CASE c.relkind WHEN 'I' THEN 'партиционированный (логический)'
                      WHEN 'i' THEN 'локальный (физический)' END AS kind,
       COALESCE(t.relname, '-') AS on_table,
       pg_size_pretty(pg_relation_size(c.oid)) AS size
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_index ix ON ix.indexrelid = c.oid
LEFT JOIN pg_class t ON t.oid = ix.indrelid
WHERE n.nspname = 'lab3' AND c.relname LIKE 'idx_events_user_id%'
ORDER BY c.relkind DESC, c.relname;

\echo '--- ПОСЛЕ индекса: pruning + локальный индекс ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM lab3.events
WHERE created_at >= '2026-09-10' AND created_at < '2026-09-11'
  AND user_id = 12345;

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM lab3.events
WHERE created_at >= '2026-09-10' AND created_at < '2026-09-11'
  AND user_id = 12345;

\echo '--- КОНТРАСТ: тот же поиск по user_id БЕЗ ключа партиционирования ---'
\echo '    (pruning не сработает — обходятся локальные индексы всех партиций)'
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM lab3.events WHERE user_id = 12345;

\echo ''
\echo '################ ЧАСТЬ 9. Когда партиционирование не помогает ################'
\echo '--- запрос по event_type: ключа партиционирования в WHERE нет ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT COUNT(*) FROM lab3.events WHERE event_type = 'click';

\echo '--- добавляем индекс по event_type ---'
CREATE INDEX idx_events_event_type ON lab3.events (event_type);
\timing off
VACUUM ANALYZE lab3.events;
\timing on

EXPLAIN (ANALYZE, BUFFERS)
SELECT COUNT(*) FROM lab3.events WHERE event_type = 'click';

EXPLAIN (ANALYZE, BUFFERS)
SELECT COUNT(*) FROM lab3.events WHERE event_type = 'click';

\echo '--- а вот комбинация «ключ партиционирования + индекс» ---'
EXPLAIN (ANALYZE, BUFFERS)
SELECT COUNT(*) FROM lab3.events
WHERE created_at >= '2026-09-10' AND created_at < '2026-09-11'
  AND event_type = 'click';

\echo '--- селективность event_type ---'
SELECT event_type, COUNT(*),
       round(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct
FROM lab3.events GROUP BY event_type ORDER BY 2 DESC;
