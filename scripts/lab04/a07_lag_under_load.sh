#!/usr/bin/env bash
# ============================================================================
#  Лабораторная №4, часть 6 (продолжение): lag под нагрузкой.
#
#  A. Пробник «записал → сразу прочитал» в спокойной системе.
#  B. Тот же пробник, пока primary параллельно пишет пачки строк.
#  C. Одна большая транзакция: после COMMIT на primary строки уже видны,
#     а реплика ещё воспроизводит WAL — видно, сколько она догоняет.
#
#  Запуск:  bash scripts/lab04/a07_lag_under_load.sh
# ============================================================================
set -u
export MSYS_NO_PATHCONV=1

P="docker exec booking_postgres psql -U booking_user -d booking_db"
R="docker exec booking_postgres_replica psql -U booking_user -d booking_db"
PROBE="scripts/lab04/a05_lag_probe.py"

hr()   { printf '%.0s─' {1..78}; echo; }
step() { echo; hr; echo "  $1"; hr; }

lag_row() {
    $P -Atc "SELECT to_char(clock_timestamp(), 'HH24:MI:SS.MS')
                    || '  отставание: ' || pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn))
                    || '  write_lag=' || coalesce(write_lag::text, '-')
                    || '  flush_lag=' || coalesce(flush_lag::text, '-')
                    || '  replay_lag=' || coalesce(replay_lag::text, '-')
               FROM pg_stat_replication"
}

$P -qc "CREATE SCHEMA IF NOT EXISTS lab4;
        CREATE TABLE IF NOT EXISTS lab4.bulk_load (
            id         BIGSERIAL PRIMARY KEY,
            payload    TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        TRUNCATE lab4.bulk_load;"

step "A. Спокойная система: 300 итераций «INSERT на primary → SELECT на replica»"
docker exec -i booking_backend python - --iterations 300 --label idle < "$PROBE"

step "B. Под нагрузкой: те же 300 итераций, пока primary пишет пачки по 100 000 строк"
(
    for batch in $(seq 1 10); do
        $P -qc "INSERT INTO lab4.bulk_load (payload)
                SELECT md5(g::text) || repeat('x', 200)
                  FROM generate_series(1, 100000) g;"
    done
) &
LOAD_PID=$!
sleep 1
docker exec -i booking_backend python - --iterations 300 --label under-load < "$PROBE"
wait "$LOAD_PID"

step "C. Одна большая транзакция: 1 000 000 строк одним INSERT"
echo "отставание перед началом:"; lag_row

# Вставку и опрос реплики после COMMIT делает пробник внутри контейнера
# бэкенда — с точностью до миллисекунды. Опрос через docker exec занимает
# ~0.7 с на вызов и не успевает застать отставание.
BIGTX_OUT=$(mktemp)
docker exec -i booking_backend python - --big-tx 1000000 --iterations 1 --label big-tx \
    < "$PROBE" > "$BIGTX_OUT" 2>&1 &
BIG_PID=$!
sleep 1
echo
echo "пока транзакция идёт (WAL уже течёт на реплику, но строки ещё никому не видны):"
while kill -0 "$BIG_PID" 2>/dev/null; do lag_row; sleep 0.5; done
wait "$BIG_PID"
echo
cat "$BIGTX_OUT"
rm -f "$BIGTX_OUT"
echo
echo "итоговое состояние:"; lag_row

$P -qc "TRUNCATE lab4.bulk_load;"
