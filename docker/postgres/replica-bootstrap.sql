-- Выполняется на PRIMARY один раз, при первом развёртывании реплики
-- (вызывается из replica-entrypoint.sh). Идемпотентно.
--
-- Переменные psql: repl_user, repl_pass, slot

-- 1. Отдельная роль для репликации.
--    Атрибут REPLICATION разрешает открывать replication-подключения
--    (pg_basebackup, walreceiver), но не даёт прав на сами данные.
--    Приложение и реплика не должны пользоваться суперпользователем.
SELECT format('CREATE ROLE %I WITH LOGIN REPLICATION PASSWORD %L', :'repl_user', :'repl_pass')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'repl_user')
\gexec

-- 2. Физический слот репликации.
--    Пока реплика отключена, primary не удаляет WAL, который она ещё не забрала.
--    Без слота долгая остановка реплики могла бы закончиться тем, что нужные
--    сегменты уже удалены, и реплику пришлось бы пересоздавать с нуля.
SELECT slot_name, lsn
  FROM pg_create_physical_replication_slot(:'slot')
 WHERE NOT EXISTS (SELECT 1 FROM pg_replication_slots WHERE slot_name = :'slot');
