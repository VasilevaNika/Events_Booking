#!/usr/bin/env bash
# ============================================================================
#  Лабораторная №4, часть 3: доказать, что репликация работает.
#
#  Запись делается на PRIMARY двумя способами — прямым SQL и через API
#  сервиса, — затем та же запись ищется SELECT'ом на REPLICA.
#
#  Запуск:  bash scripts/lab04/a02_prove_replication.sh
# ============================================================================
set -u
export MSYS_NO_PATHCONV=1

P="docker exec booking_postgres psql -U booking_user -d booking_db"
R="docker exec booking_postgres_replica psql -U booking_user -d booking_db"
API="http://localhost:8000"

hr()   { printf '%.0s─' {1..78}; echo; }
step() { echo; hr; echo "  $1"; hr; }

step "0. Подготовка: таблица lab4.replication_probe создаётся только на PRIMARY"
$P -c "CREATE SCHEMA IF NOT EXISTS lab4;
       CREATE TABLE IF NOT EXISTS lab4.replication_probe (
           id         BIGSERIAL PRIMARY KEY,
           marker     TEXT        NOT NULL,
           note       TEXT,
           created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
       );"
sleep 1
echo "На REPLICA ничего не создавали руками — DDL тоже приходит через WAL:"
$R -c "SELECT to_regclass('lab4.replication_probe') AS table_on_replica;"

MARK="lab4-sql-$(date +%s)"

step "1. INSERT на PRIMARY (прямой SQL)"
$P -c "INSERT INTO lab4.replication_probe (marker, note)
       VALUES ('$MARK', 'вставлено на primary')
       RETURNING id, marker, note, created_at;"
$P -c "SELECT pg_current_wal_lsn() AS primary_lsn_after_commit;"

step "2. SELECT на REPLICA — та же строка"
$R -c "SELECT id, marker, note, created_at
         FROM lab4.replication_probe
        WHERE marker = '$MARK';"
$R -c "SELECT pg_last_wal_replay_lsn() AS replica_replayed_lsn;"

step "3. Запись через API сервиса: POST /api/bookings (уходит на primary)"
RESP=$(curl -s -X POST "$API/api/bookings" \
            -H 'Content-Type: application/json' \
            -d '{"user_id": 1, "event_id": 1, "seats_count": 2}')
echo "$RESP"
BID=$(printf '%s' "$RESP" | docker exec -i booking_backend \
        python -c 'import sys, json; print(json.load(sys.stdin)["id"])')
echo "id нового бронирования: $BID"

step "4. Это бронирование на REPLICA"
$R -c "SELECT id, user_id, event_id, seats_count, total_price, status, created_at,
              tableoid::regclass AS partition
         FROM bookings
        WHERE id = $BID;"

step "5. UPDATE на PRIMARY → новое значение на REPLICA"
$P -c "UPDATE bookings SET status = 'cancelled' WHERE id = $BID RETURNING id, status;"
sleep 1
$R -c "SELECT id, status FROM bookings WHERE id = $BID;"

step "6. Подтверждение: реплика подключена и в состоянии streaming"
$P -x -c "SELECT application_name, client_addr, state, sync_state,
                 sent_lsn, replay_lsn, replay_lag
            FROM pg_stat_replication;"
