#!/usr/bin/env bash
# ============================================================================
#  Лабораторная №5: router внутри сервиса — через API.
#
#  Заголовки ответа читаются через curl -i из stdout: под MSYS_NO_PATHCONV
#  Windows-сборка curl не понимает пути /dev/null, /dev/stderr, /tmp/...
#
#  Запуск:  bash scripts/lab05/a02_api_demo.sh
# ============================================================================
set -u
export MSYS_NO_PATHCONV=1

API="http://localhost:8000/api/shards"
hr()     { printf '%.0s─' {1..78}; echo; }
step()   { echo; hr; echo "  $1"; hr; }
pretty() { docker exec -i booking_backend python -m json.tool; }
# stdin: ответ curl -i. Печатает заголовки X-Shard*, в stdout — ничего больше
shard_headers() { tr -d '\r' | sed '/^$/q' | grep -iE '^x-shard' | sed 's/^/  /'; }
# stdin: ответ curl -i. Печатает только тело
body() { tr -d '\r' | sed '1,/^$/d'; }

step "1. Куда router отправит пользователей 101, 102, 103"
for uid in 101 102 103; do
    curl -s "$API/route/$uid" | docker exec -i booking_backend python -c "
import sys, json
d = json.load(sys.stdin)
m, c = d['modulo'], d['consistent']
print(f\"  user {d['user_id']}: активная стратегия {d['active_strategy']} -> {d['shard']}\")
print(f\"      modulo:     {m['formula']:<24} -> {m['shard']}\")
print(f\"      consistent: hash {c['hash']} -> ближайшая точка {c['next_point_clockwise']} ({c['vnode']}) -> {c['shard']}\")"
done

step "2. Бронирования пользователя: shard key в запросе → ровно один шард"
RAW=$(curl -s -i "$API/users/101/bookings?limit=3")
printf '%s\n' "$RAW" | shard_headers
printf '%s\n' "$RAW" | body | pretty

step "3. Новое бронирование: router выбирает шард по user_id"
RAW=$(curl -s -i -X POST "$API/bookings" -H 'Content-Type: application/json' \
           -d '{"user_id": 101, "event_id": 5, "seats_count": 2}')
printf '%s\n' "$RAW" | head -1 | tr -d '\r' | sed 's/^/  /'
printf '%s\n' "$RAW" | shard_headers
BODY=$(printf '%s\n' "$RAW" | body)
printf '%s\n' "$BODY" | pretty
BID=$(printf '%s\n' "$BODY" | docker exec -i booking_backend \
        python -c 'import sys, json; print(json.load(sys.stdin)["id"])')
echo "  id нового бронирования: $BID (выдан общей последовательностью на primary)"

step "4. Поиск по id без shard key — scatter-gather по всем шардам"
RAW=$(curl -s -i "$API/bookings/$BID")
printf '%s\n' "$RAW" | head -1 | tr -d '\r' | sed 's/^/  /'
printf '%s\n' "$RAW" | shard_headers

step "5. Тот же поиск с user_id — ровно один шард"
RAW=$(curl -s -i "$API/bookings/$BID?user_id=101")
printf '%s\n' "$RAW" | head -1 | tr -d '\r' | sed 's/^/  /'
printf '%s\n' "$RAW" | shard_headers

step "6. Бронирование физически лежит только на одном шарде"
for i in 0 1 2 3; do
    docker exec "booking_shard_$i" psql -U booking_user -d booking_shard -Atc \
        "SELECT '  shard-$i: ' || count(*) FROM bookings WHERE id = $BID"
done

step "7. Распределение по шардам: GET /api/shards"
curl -s "$API" | pretty
