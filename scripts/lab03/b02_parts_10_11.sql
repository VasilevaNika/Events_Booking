\pset pager off
\set ON_ERROR_STOP on

\echo '################ ЧАСТЬ 10. Автоматическое создание партиций ################'

\echo '--- регистрируем lab3.events: партиции по дням, горизонт 3 дня вперёд ---'
INSERT INTO partition_mgmt.partition_config (schema_name, table_name, step, horizon, retention)
VALUES ('lab3', 'events', 'day', 3, NULL)
ON CONFLICT (schema_name, table_name) DO UPDATE
    SET step = EXCLUDED.step, horizon = EXCLUDED.horizon, enabled = true;

\echo '--- что есть сейчас ---'
SELECT partition_name FROM partition_mgmt.existing_partitions('lab3', 'events');

\echo '--- что требуется (сегодня + 3 дня) ---'
SELECT period_start,
       partition_mgmt.partition_name('events', period_start, 'day') AS needed
FROM partition_mgmt.required_periods('day', 3);

\echo ''
\echo '=== ЗАПУСК 1: CreatePartitionsJob ==='
SELECT * FROM partition_mgmt.create_missing_partitions();

\echo '=== ЗАПУСК 2: повторный — job идемпотентна, ничего не создаёт ==='
SELECT * FROM partition_mgmt.create_missing_partitions();

\echo '--- партиции после работы job ---'
SELECT partition_name FROM partition_mgmt.existing_partitions('lab3', 'events');

\echo '--- журнал запусков ---'
SELECT id, to_char(started_at, 'YYYY-MM-DD HH24:MI:SS') AS started,
       existing_count, required_count, missing_count, created_list, status
FROM partition_mgmt.partition_job_log ORDER BY id;

\echo ''
\echo '--- проверка: вставка «в завтра» теперь проходит ---'
INSERT INTO lab3.events (id, user_id, event_type, payload, created_at)
VALUES (9100001, 1, 'click', 'tomorrow', (CURRENT_DATE + 1)::timestamp + INTERVAL '12 hours');
SELECT id, created_at, tableoid::regclass AS landed_in FROM lab3.events WHERE id = 9100001;

\echo ''
\echo '################ ЧАСТЬ 11. Сбой ночной job и alerting ################'

\echo '=== ШАГ 1. Исходное состояние: всё хорошо ==='
SELECT * FROM partition_mgmt.health_check();

\echo '--- состояние и очередь уведомлений ---'
SELECT config_id, status, missing_list, check_count FROM partition_mgmt.partition_alert_state;
SELECT id, severity, subject FROM partition_mgmt.partition_alert_outbox ORDER BY id;

\echo ''
\echo '=== ШАГ 2. Искусственно ломаем: удаляем последнюю партицию горизонта ==='
DO $$
DECLARE last_part TEXT;
BEGIN
    SELECT partition_mgmt.partition_name('events', max(period_start), 'day')
      INTO last_part
      FROM partition_mgmt.required_periods('day', 3);
    EXECUTE format('DROP TABLE lab3.%I', last_part);
    RAISE NOTICE 'Удалена партиция lab3.%', last_part;
END $$;

\echo ''
\echo '=== ШАГ 3. PartitionHealthCheck обнаруживает проблему ==='
SELECT * FROM partition_mgmt.health_check();

\echo '--- в outbox появился CRITICAL ---'
SELECT id, severity, subject, to_char(created_at, 'HH24:MI:SS') AS created
FROM partition_mgmt.partition_alert_outbox ORDER BY id;

\echo '--- текст уведомления ---'
SELECT body FROM partition_mgmt.partition_alert_outbox
WHERE severity = 'CRITICAL' ORDER BY id DESC LIMIT 1;

\echo ''
\echo '=== ШАГ 4. Дедупликация: повторные проверки НЕ создают новых алертов ==='
SELECT * FROM partition_mgmt.health_check();
SELECT * FROM partition_mgmt.health_check();
SELECT * FROM partition_mgmt.health_check();

\echo '--- в outbox по-прежнему один CRITICAL, но check_count вырос ---'
SELECT count(*) AS alerts_in_outbox FROM partition_mgmt.partition_alert_outbox;
SELECT config_id, status, check_count,
       to_char(changed_at, 'HH24:MI:SS') AS changed_at,
       to_char(last_checked_at, 'HH24:MI:SS') AS last_checked
FROM partition_mgmt.partition_alert_state;

\echo ''
\echo '=== ШАГ 5. Восстановление: job создаёт недостающую партицию ==='
SELECT * FROM partition_mgmt.create_missing_partitions();

\echo '=== ШАГ 6. Повторная проверка → OK + recovery-уведомление ==='
SELECT * FROM partition_mgmt.health_check();

SELECT id, severity, subject, to_char(created_at, 'HH24:MI:SS') AS created
FROM partition_mgmt.partition_alert_outbox ORDER BY id;

\echo '--- текст recovery ---'
SELECT body FROM partition_mgmt.partition_alert_outbox
WHERE severity = 'RECOVERY' ORDER BY id DESC LIMIT 1;

\echo ''
\echo '=== ШАГ 7. Повторная проверка после восстановления — снова тишина ==='
SELECT * FROM partition_mgmt.health_check();
SELECT count(*) AS alerts_total FROM partition_mgmt.partition_alert_outbox;

\echo ''
\echo '################ Расписание pg_cron ################'
\echo '--- ночное создание партиций в 01:00 и проверка каждые 5 минут ---'
SELECT cron.schedule('create-partitions', '0 1 * * *',
                     $$SELECT partition_mgmt.create_missing_partitions()$$);
SELECT cron.schedule('partition-healthcheck', '*/5 * * * *',
                     $$SELECT partition_mgmt.health_check()$$);

SELECT jobid, schedule, command, active FROM cron.job ORDER BY jobid;
