-- ============================================================================
--  Подсистема управления партициями (лабораторная №3, части 10–11)
-- ============================================================================
--  Реализует полный жизненный цикл:
--      создать → контролировать → обнаружить сбой → уведомить → восстановить
--
--  Устройство:
--    partition_config        — что обслуживаем: таблица, шаг, горизонт
--    partition_job_log       — журнал запусков CreatePartitionsJob
--    partition_alert_state   — текущее состояние (OK / CRITICAL) по каждой таблице
--    partition_alert_outbox  — очередь уведомлений (transactional outbox)
--
--    create_missing_partitions()  — CreatePartitionsJob
--    health_check()               — PartitionHealthCheck
--
--  Дедупликация alert'ов и recovery-уведомления обеспечиваются тем, что
--  запись в outbox добавляется ТОЛЬКО при смене состояния (OK <-> CRITICAL).
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS partition_mgmt;

-- ---------------------------------------------------------------- конфигурация
CREATE TABLE IF NOT EXISTS partition_mgmt.partition_config (
    id              SERIAL PRIMARY KEY,
    schema_name     TEXT     NOT NULL,
    table_name      TEXT     NOT NULL,
    -- шаг партиции: 'day' или 'month'
    step            TEXT     NOT NULL CHECK (step IN ('day', 'month')),
    -- на сколько шагов вперёд должны существовать партиции
    horizon         INT      NOT NULL CHECK (horizon > 0),
    -- сколько шагов назад хранить (NULL — не удалять ничего)
    retention       INT,
    enabled         BOOLEAN  NOT NULL DEFAULT true,
    UNIQUE (schema_name, table_name)
);

COMMENT ON TABLE partition_mgmt.partition_config IS
    'Какие партиционированные таблицы обслуживаются автоматически';

-- ------------------------------------------------------------------- журнал
CREATE TABLE IF NOT EXISTS partition_mgmt.partition_job_log (
    id             BIGSERIAL PRIMARY KEY,
    config_id      INT REFERENCES partition_mgmt.partition_config(id) ON DELETE CASCADE,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ,
    existing_count INT,
    required_count INT,
    missing_count  INT,
    created_list   TEXT[],
    dropped_list   TEXT[],
    status         TEXT,
    message        TEXT
);

-- ------------------------------------------------------- состояние проверки
CREATE TABLE IF NOT EXISTS partition_mgmt.partition_alert_state (
    config_id        INT PRIMARY KEY REFERENCES partition_mgmt.partition_config(id) ON DELETE CASCADE,
    status           TEXT        NOT NULL,          -- OK | CRITICAL
    missing_list     TEXT[],
    changed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_checked_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    check_count      BIGINT      NOT NULL DEFAULT 1
);

COMMENT ON COLUMN partition_mgmt.partition_alert_state.changed_at IS
    'Когда состояние изменилось в последний раз — на этом строится дедупликация';

-- ------------------------------------------------------- очередь уведомлений
CREATE TABLE IF NOT EXISTS partition_mgmt.partition_alert_outbox (
    id            BIGSERIAL PRIMARY KEY,
    config_id     INT REFERENCES partition_mgmt.partition_config(id) ON DELETE CASCADE,
    severity      TEXT        NOT NULL,             -- CRITICAL | RECOVERY
    subject       TEXT        NOT NULL,
    body          TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at       TIMESTAMPTZ,
    send_attempts INT         NOT NULL DEFAULT 0,
    last_error    TEXT
);

CREATE INDEX IF NOT EXISTS ix_alert_outbox_pending
    ON partition_mgmt.partition_alert_outbox (created_at)
    WHERE sent_at IS NULL;

-- ============================================================================
--  Вспомогательные функции
-- ============================================================================

-- Начало периода, которому принадлежит момент времени
CREATE OR REPLACE FUNCTION partition_mgmt.period_start(p_ts TIMESTAMPTZ, p_step TEXT)
RETURNS DATE LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE p_step
               WHEN 'day'   THEN date_trunc('day',   p_ts)::date
               WHEN 'month' THEN date_trunc('month', p_ts)::date
           END;
$$;

-- Имя партиции для периода: events_2026_09_13 или bookings_2026_09
CREATE OR REPLACE FUNCTION partition_mgmt.partition_name(
    p_table TEXT, p_period_start DATE, p_step TEXT)
RETURNS TEXT LANGUAGE sql IMMUTABLE AS $$
    SELECT p_table || '_' || CASE p_step
               WHEN 'day'   THEN to_char(p_period_start, 'YYYY_MM_DD')
               WHEN 'month' THEN to_char(p_period_start, 'YYYY_MM')
           END;
$$;

-- Список периодов, которые обязаны существовать: [сегодня .. сегодня+horizon]
CREATE OR REPLACE FUNCTION partition_mgmt.required_periods(
    p_step TEXT, p_horizon INT, p_now TIMESTAMPTZ DEFAULT now())
RETURNS TABLE (period_start DATE) LANGUAGE sql STABLE AS $$
    SELECT (partition_mgmt.period_start(p_now, p_step)
            + (n || ' ' || p_step)::interval)::date
    FROM generate_series(0, p_horizon) AS n;
$$;

-- Существующие партиции таблицы
CREATE OR REPLACE FUNCTION partition_mgmt.existing_partitions(
    p_schema TEXT, p_table TEXT)
RETURNS TABLE (partition_name TEXT) LANGUAGE sql STABLE AS $$
    SELECT c.relname::text
    FROM pg_inherits i
    JOIN pg_class c   ON c.oid = i.inhrelid
    JOIN pg_class p   ON p.oid = i.inhparent
    JOIN pg_namespace n ON n.oid = p.relnamespace
    WHERE n.nspname = p_schema AND p.relname = p_table
    ORDER BY 1;
$$;

-- ============================================================================
--  CreatePartitionsJob — создание недостающих партиций
-- ============================================================================
--  Идемпотентна: повторный запуск ничего не ломает и ничего не дублирует.
--  Возвращает текстовый лог в формате из методички.
-- ============================================================================
CREATE OR REPLACE FUNCTION partition_mgmt.create_missing_partitions(
    p_config_id INT DEFAULT NULL,
    p_now       TIMESTAMPTZ DEFAULT now())
RETURNS TABLE (log_line TEXT)
LANGUAGE plpgsql AS $$
DECLARE
    cfg            RECORD;
    period         DATE;
    part_name      TEXT;
    next_start     DATE;
    created        TEXT[];
    dropped        TEXT[];
    existing_cnt   INT;
    required_cnt   INT;
    log_id         BIGINT;
    cutoff         DATE;
BEGIN
    FOR cfg IN
        SELECT * FROM partition_mgmt.partition_config
        WHERE enabled AND (p_config_id IS NULL OR id = p_config_id)
        ORDER BY id
    LOOP
        created := ARRAY[]::TEXT[];
        dropped := ARRAY[]::TEXT[];

        INSERT INTO partition_mgmt.partition_job_log (config_id, started_at, status)
        VALUES (cfg.id, p_now, 'RUNNING')
        RETURNING id INTO log_id;

        SELECT count(*) INTO existing_cnt
        FROM partition_mgmt.existing_partitions(cfg.schema_name, cfg.table_name);

        SELECT count(*) INTO required_cnt
        FROM partition_mgmt.required_periods(cfg.step, cfg.horizon, p_now);

        RETURN QUERY SELECT format('%s  Partition job started. Table: %s.%s',
                                   to_char(p_now, 'YYYY-MM-DD HH24:MI'),
                                   cfg.schema_name, cfg.table_name);
        RETURN QUERY SELECT format('Existing partitions: %s', existing_cnt);
        RETURN QUERY SELECT format('Required partitions: %s (horizon %s %s)',
                                   required_cnt, cfg.horizon, cfg.step);

        -- ---- создание недостающих -----------------------------------------
        FOR period IN
            SELECT rp.period_start
            FROM partition_mgmt.required_periods(cfg.step, cfg.horizon, p_now) rp
            ORDER BY 1
        LOOP
            part_name  := partition_mgmt.partition_name(cfg.table_name, period, cfg.step);
            next_start := (period + ('1 ' || cfg.step)::interval)::date;

            IF NOT EXISTS (
                SELECT 1 FROM partition_mgmt.existing_partitions(cfg.schema_name, cfg.table_name) e
                WHERE e.partition_name = part_name
            ) THEN
                EXECUTE format(
                    'CREATE TABLE %I.%I PARTITION OF %I.%I FOR VALUES FROM (%L) TO (%L)',
                    cfg.schema_name, part_name,
                    cfg.schema_name, cfg.table_name,
                    period, next_start);
                created := created || part_name;
                RETURN QUERY SELECT format('Creating: %s  [%s .. %s)',
                                           part_name, period, next_start);
            END IF;
        END LOOP;

        RETURN QUERY SELECT format('Missing partitions: %s', COALESCE(array_length(created, 1), 0));

        -- ---- удаление устаревших (retention) -------------------------------
        IF cfg.retention IS NOT NULL THEN
            cutoff := (partition_mgmt.period_start(p_now, cfg.step)
                       - (cfg.retention || ' ' || cfg.step)::interval)::date;
            FOR part_name IN
                SELECT e.partition_name
                FROM partition_mgmt.existing_partitions(cfg.schema_name, cfg.table_name) e
            LOOP
                -- имя вида <table>_YYYY_MM[_DD] → восстанавливаем дату
                DECLARE
                    suffix TEXT := right(part_name, length(part_name) - length(cfg.table_name) - 1);
                    pdate  DATE;
                BEGIN
                    pdate := CASE cfg.step
                                WHEN 'day'   THEN to_date(suffix, 'YYYY_MM_DD')
                                WHEN 'month' THEN to_date(suffix, 'YYYY_MM')
                             END;
                    IF pdate < cutoff THEN
                        EXECUTE format('DROP TABLE %I.%I', cfg.schema_name, part_name);
                        dropped := dropped || part_name;
                        RETURN QUERY SELECT format('Dropping (retention %s %s): %s',
                                                   cfg.retention, cfg.step, part_name);
                    END IF;
                EXCEPTION WHEN others THEN
                    -- партиция с нестандартным именем — не трогаем
                    NULL;
                END;
            END LOOP;
        END IF;

        UPDATE partition_mgmt.partition_job_log
        SET finished_at    = clock_timestamp(),
            existing_count = existing_cnt,
            required_count = required_cnt,
            missing_count  = COALESCE(array_length(created, 1), 0),
            created_list   = created,
            dropped_list   = dropped,
            status         = 'SUCCESS',
            message        = format('created=%s dropped=%s',
                                    COALESCE(array_length(created, 1), 0),
                                    COALESCE(array_length(dropped, 1), 0))
        WHERE id = log_id;

        IF COALESCE(array_length(created, 1), 0) = 0 THEN
            RETURN QUERY SELECT 'Nothing to create — all required partitions already exist.'::TEXT;
        ELSE
            RETURN QUERY SELECT 'Partition created successfully.'::TEXT;
        END IF;
        RETURN QUERY SELECT 'Partition job finished.'::TEXT;
        RETURN QUERY SELECT ''::TEXT;
    END LOOP;
END;
$$;

-- ============================================================================
--  PartitionHealthCheck — проверка наличия партиций на горизонте
-- ============================================================================
--  Возвращает OK или CRITICAL и список отсутствующих партиций.
--  Кладёт уведомление в outbox ТОЛЬКО если состояние изменилось:
--      OK -> CRITICAL  = алерт
--      CRITICAL -> OK  = recovery
--      CRITICAL -> CRITICAL = молчим (дедупликация)
-- ============================================================================
CREATE OR REPLACE FUNCTION partition_mgmt.health_check(
    p_config_id INT DEFAULT NULL,
    p_now       TIMESTAMPTZ DEFAULT now())
RETURNS TABLE (
    table_full_name TEXT,
    check_status    TEXT,
    missing         TEXT[],
    notified        BOOLEAN)
LANGUAGE plpgsql AS $$
DECLARE
    cfg           RECORD;
    period        DATE;
    part_name     TEXT;
    v_missing     TEXT[];
    v_new_status  TEXT;
    v_prev_status TEXT;
    v_do_notify   BOOLEAN;
    v_subj        TEXT;
    v_body        TEXT;
BEGIN
    FOR cfg IN
        SELECT * FROM partition_mgmt.partition_config
        WHERE enabled AND (p_config_id IS NULL OR id = p_config_id)
        ORDER BY id
    LOOP
        v_missing := ARRAY[]::TEXT[];

        FOR period IN
            SELECT rp.period_start
            FROM partition_mgmt.required_periods(cfg.step, cfg.horizon, p_now) rp
            ORDER BY 1
        LOOP
            part_name := partition_mgmt.partition_name(cfg.table_name, period, cfg.step);
            IF NOT EXISTS (
                SELECT 1 FROM partition_mgmt.existing_partitions(cfg.schema_name, cfg.table_name) e
                WHERE e.partition_name = part_name
            ) THEN
                v_missing := v_missing || part_name;
            END IF;
        END LOOP;

        v_new_status := CASE WHEN array_length(v_missing, 1) IS NULL
                             THEN 'OK' ELSE 'CRITICAL' END;

        SELECT s.status INTO v_prev_status
        FROM partition_mgmt.partition_alert_state s WHERE s.config_id = cfg.id;

        v_do_notify := (v_prev_status IS DISTINCT FROM v_new_status);

        IF v_prev_status IS NULL THEN
            INSERT INTO partition_mgmt.partition_alert_state
                (config_id, status, missing_list, changed_at, last_checked_at, check_count)
            VALUES (cfg.id, v_new_status, v_missing, p_now, p_now, 1);
            -- первая проверка: шумим только если уже плохо
            v_do_notify := (v_new_status = 'CRITICAL');
        ELSIF v_do_notify THEN
            UPDATE partition_mgmt.partition_alert_state s
            SET status = v_new_status, missing_list = v_missing,
                changed_at = p_now, last_checked_at = p_now,
                check_count = s.check_count + 1
            WHERE s.config_id = cfg.id;
        ELSE
            UPDATE partition_mgmt.partition_alert_state s
            SET missing_list = v_missing, last_checked_at = p_now,
                check_count = s.check_count + 1
            WHERE s.config_id = cfg.id;
        END IF;

        IF v_do_notify THEN
            IF v_new_status = 'CRITICAL' THEN
                v_subj := format('[CRITICAL] Partition alert: %s.%s',
                                 cfg.schema_name, cfg.table_name);
                v_body := format(E'\U0001F534 Partition alert\n\n'
                                  'Table: %s.%s\n\n'
                                  'Missing partitions:\n%s\n\n'
                                  'Expected horizon: %s %s\n\n'
                                  'Checked at:\n%s',
                                 cfg.schema_name, cfg.table_name,
                                 array_to_string(v_missing, E'\n'),
                                 cfg.horizon, cfg.step,
                                 to_char(p_now, 'YYYY-MM-DD HH24:MI:SS'));
            ELSE
                v_subj := format('[RECOVERY] Partition check OK: %s.%s',
                                 cfg.schema_name, cfg.table_name);
                v_body := format(E'\U0001F7E2 Partition check OK\n\n'
                                  'Table: %s.%s\n\n'
                                  'All required partitions exist.\n\n'
                                  'Expected horizon: %s %s\n\n'
                                  'Checked at:\n%s',
                                 cfg.schema_name, cfg.table_name,
                                 cfg.horizon, cfg.step,
                                 to_char(p_now, 'YYYY-MM-DD HH24:MI:SS'));
            END IF;

            INSERT INTO partition_mgmt.partition_alert_outbox
                (config_id, severity, subject, body, created_at)
            VALUES (cfg.id,
                    CASE WHEN v_new_status = 'CRITICAL' THEN 'CRITICAL' ELSE 'RECOVERY' END,
                    v_subj, v_body, p_now);
        END IF;

        table_full_name := cfg.schema_name || '.' || cfg.table_name;
        check_status    := v_new_status;
        missing         := v_missing;
        notified        := v_do_notify;
        RETURN NEXT;
    END LOOP;
END;
$$;

-- ============================================================================
--  Удобное представление: что сейчас с партициями
-- ============================================================================
CREATE OR REPLACE VIEW partition_mgmt.v_partition_overview AS
SELECT c.schema_name,
       c.table_name,
       c.step,
       c.horizon,
       (SELECT count(*) FROM partition_mgmt.existing_partitions(c.schema_name, c.table_name)) AS partitions,
       s.status,
       s.missing_list,
       s.last_checked_at,
       s.changed_at
FROM partition_mgmt.partition_config c
LEFT JOIN partition_mgmt.partition_alert_state s ON s.config_id = c.id
ORDER BY c.id;
