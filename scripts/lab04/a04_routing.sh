#!/usr/bin/env bash
# ============================================================================
#  Лабораторная №4, часть 5: чтение сервиса идёт на реплику, запись — на primary.
#
#  Доказательства трёх видов:
#    1) заголовок X-DB-Node в ответе API;
#    2) соединения бэкенда в pg_stat_activity на каждом сервере;
#    3) счётчики вставок: POST меняет их на primary.
#  В конце — отказ реплики: чтение автоматически уходит на primary.
#
#  Запуск:  bash scripts/lab04/a04_routing.sh
# ============================================================================
set -u
export MSYS_NO_PATHCONV=1

P="docker exec booking_postgres psql -U booking_user -d booking_db"
R="docker exec booking_postgres_replica psql -U booking_user -d booking_db"
API="http://localhost:8000"

hr()   { printf '%.0s─' {1..78}; echo; }
step() { echo; hr; echo "  $1"; hr; }

db_node() {   # какой сервер обслужил GET-запрос
    local node
    node=$(curl -s -D - "$API$1" | grep -i '^x-db-node:' | tr -d '\r' | awk '{print $2}')
    printf '  %-36s → %s\n' "GET $1" "${node:-primary (get_db, без заголовка)}"
}

step "1. Бэкенд видит оба сервера: GET /health/db"
curl -s "$API/health/db" | docker exec -i booking_backend python -m json.tool

step "2. Куда уходит каждый GET — заголовок X-DB-Node"
db_node "/api/events?page_size=3"
db_node "/api/events/stats/categories"
db_node "/api/bookings/stats/revenue"
db_node "/api/events/1"
db_node "/api/bookings?page_size=3"
db_node "/api/users/1/bookings"

step "3. Соединения бэкенда на каждом сервере (pg_stat_activity)"
for i in 1 2 3; do curl -s "$API/api/events?page_size=1" > /dev/null; done
for node in P R; do
    [ "$node" = P ] && { echo "PRIMARY"; cmd=$P; } || { echo "REPLICA"; cmd=$R; }
    $cmd -c "SELECT application_name, count(*) AS connections
               FROM pg_stat_activity
              WHERE application_name LIKE 'booking-backend%'
              GROUP BY application_name
              ORDER BY application_name;"
done

step "4. Запись через API попадает на primary"
ins() { $P -Atc "SELECT coalesce(sum(n_tup_ins), 0)
                   FROM pg_stat_user_tables
                  WHERE relname LIKE 'bookings\_%'"; }
# Код ответа без записи тела в файл: под MSYS_NO_PATHCONV Windows-сборка curl
# не понимает пути вида /tmp/... и /dev/null
http_code() {
    local out
    out=$(curl -s -w '\n%{http_code}' "$@")
    printf '%s' "${out##*$'\n'}"
}

rins() { $R -Atc "SELECT coalesce(sum(n_tup_ins), 0)
                    FROM pg_stat_user_tables
                   WHERE relname LIKE 'bookings\_%'"; }
BEFORE=$(ins)
REPLICA_BEFORE=$(rins)
OUT=$(curl -s -w '\n%{http_code}' -X POST "$API/api/bookings" \
           -H 'Content-Type: application/json' \
           -d '{"user_id": 2, "event_id": 2, "seats_count": 1}')
CODE=${OUT##*$'\n'}
NEW_ID=$(printf '%s' "${OUT%$'\n'*}" | docker exec -i booking_backend \
           python -c 'import sys, json; print(json.load(sys.stdin)["id"])')
echo "  POST /api/bookings → HTTP $CODE, id=$NEW_ID"
# Бэкенд держит соединение из пула в простое, а простаивающий процесс
# сбрасывает накопленную статистику в разделяемую память не на каждом
# COMMIT, а примерно раз в 10 секунд. Поэтому ждём дольше этого интервала.
sleep 12
AFTER=$(ins)
REPLICA_AFTER=$(rins)
echo "  n_tup_ins по партициям bookings на PRIMARY: было $BEFORE, стало $AFTER"
echo "  n_tup_ins по партициям bookings на REPLICA: было $REPLICA_BEFORE, стало $REPLICA_AFTER"
echo "  строка при этом есть на обоих серверах:"
$P -Atc "SELECT '    primary: id=' || id || ' created_at=' || created_at FROM bookings WHERE id = $NEW_ID"
$R -Atc "SELECT '    replica: id=' || id || ' created_at=' || created_at FROM bookings WHERE id = $NEW_ID"
echo "  INSERT выполнил primary. Реплика ничего не вставляла — она воспроизвела WAL,"
echo "  поэтому её счётчик вставок не меняется, хотя строка у неё появилась."

step "5. Отказ реплики: останавливаем booking_postgres_replica"
docker stop booking_postgres_replica > /dev/null && echo "  реплика остановлена"
sleep 1
CODE=$(http_code "$API/api/events?page_size=3")
echo "  GET /api/events → HTTP $CODE"
db_node "/api/events?page_size=3"
db_node "/api/bookings/stats/revenue"
echo
curl -s "$API/health/db" | docker exec -i booking_backend python -m json.tool

step "6. Реплика снова запущена — чтение возвращается на неё"
docker start booking_postgres_replica > /dev/null
for i in $(seq 1 30); do
    [ "$(docker inspect -f '{{.State.Health.Status}}' booking_postgres_replica)" = "healthy" ] && break
    sleep 2
done
echo "  реплика: $(docker inspect -f '{{.State.Health.Status}}' booking_postgres_replica)"
sleep 2
db_node "/api/events?page_size=3"
$P -x -c "SELECT application_name, state, replay_lag FROM pg_stat_replication;"
