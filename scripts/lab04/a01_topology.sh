#!/usr/bin/env bash
# ============================================================================
#  Лабораторная №4, части 1–2: топология Primary + Replica и состояние
#  streaming replication.
#
#  Запуск:  bash scripts/lab04/a01_topology.sh
# ============================================================================
set -u
export MSYS_NO_PATHCONV=1   # Git Bash на Windows не должен переписывать пути

P="docker exec booking_postgres psql -U booking_user -d booking_db"
R="docker exec booking_postgres_replica psql -U booking_user -d booking_db"

hr()   { printf '%.0s─' {1..78}; echo; }
step() { echo; hr; echo "  $1"; hr; }

step "1. Контейнеры PostgreSQL"
docker ps --filter "name=booking_postgres" \
          --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

step "2. Кто primary, а кто replica: pg_is_in_recovery()"
for node in P R; do
    [ "$node" = P ] && { echo "PRIMARY  (booking_postgres, localhost:5435)"; cmd=$P; } \
                    || { echo "REPLICA  (booking_postgres_replica, localhost:5438)"; cmd=$R; }
    $cmd -c "SELECT pg_is_in_recovery()                      AS in_recovery,
                    inet_server_addr()                       AS server_addr,
                    current_setting('transaction_read_only') AS transaction_read_only,
                    version()                                AS version;"
done

step "3. Настройки репликации на primary"
$P -c "SELECT name, setting
         FROM pg_settings
        WHERE name IN ('wal_level', 'max_wal_senders', 'max_replication_slots',
                       'hba_file', 'synchronous_commit', 'synchronous_standby_names')
        ORDER BY name;"

step "4. Правила pg_hba для replication-подключений"
$P -c "SELECT line_number, type, database, user_name, address, auth_method
         FROM pg_hba_file_rules
        WHERE 'replication' = ANY(database);"

step "5. Роли"
$P -c "SELECT rolname, rolsuper, rolreplication
         FROM pg_roles
        WHERE rolname IN ('booking_user', 'replicator');"

step "6. pg_stat_replication на PRIMARY — подключённые реплики"
$P -x -c "SELECT pid, usename, application_name, client_addr, state, sync_state,
                 sent_lsn, write_lsn, flush_lsn, replay_lsn,
                 write_lag, flush_lag, replay_lag,
                 pg_wal_lsn_diff(sent_lsn, replay_lsn) AS replay_gap_bytes,
                 backend_start
            FROM pg_stat_replication;"

step "7. Слот репликации на PRIMARY"
$P -x -c "SELECT slot_name, slot_type, active, active_pid, restart_lsn, wal_status,
                 pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS retained_wal
            FROM pg_replication_slots;"

step "8. pg_stat_wal_receiver на REPLICA — приёмник потока WAL"
$R -x -c "SELECT pid, status, sender_host, sender_port, slot_name,
                 receive_start_lsn, written_lsn, flushed_lsn, latest_end_lsn,
                 last_msg_send_time, last_msg_receipt_time
            FROM pg_stat_wal_receiver;"

step "9. Позиция в WAL: primary против replica"
$P -c "SELECT pg_current_wal_lsn() AS primary_current_lsn;"
$R -c "SELECT pg_last_wal_receive_lsn()  AS received_lsn,
              pg_last_wal_replay_lsn()   AS replayed_lsn,
              pg_is_wal_replay_paused()  AS replay_paused;"

step "10. Конфигурация standby на реплике"
docker exec booking_postgres_replica sh -c '
    ls -l /var/lib/postgresql/data/standby.signal
    grep -E "^primary_(conninfo|slot_name)" /var/lib/postgresql/data/postgresql.auto.conf \
      | sed -E "s/password=[^ ]+/password=***/"'

step "11. Копия одинаковая: объём и структура на обоих серверах"
for node in P R; do
    [ "$node" = P ] && { echo "PRIMARY"; cmd=$P; } || { echo "REPLICA"; cmd=$R; }
    $cmd -c "SELECT (SELECT count(*) FROM bookings)                              AS bookings,
                    (SELECT count(*) FROM pg_inherits
                      WHERE inhparent = 'public.bookings'::regclass)            AS bookings_partitions,
                    (SELECT version_num FROM alembic_version)                   AS alembic,
                    pg_size_pretty(pg_database_size('booking_db'))              AS db_size;"
done
