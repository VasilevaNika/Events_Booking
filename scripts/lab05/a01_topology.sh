#!/usr/bin/env bash
# ============================================================================
#  Лабораторная №5, задание 2: несколько независимых PostgreSQL.
#
#  Запуск:  bash scripts/lab05/a01_topology.sh
# ============================================================================
set -u
export MSYS_NO_PATHCONV=1

hr()   { printf '%.0s─' {1..78}; echo; }
step() { echo; hr; echo "  $1"; hr; }

step "1. Контейнеры шардов"
docker ps --filter "name=booking_shard_" \
          --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | sort

step "2. Каждый шард — отдельный сервер со своей базой и своей копией схемы"
for i in 0 1 2 3; do
    docker exec "booking_shard_$i" psql -U booking_user -d booking_shard -Atc "
        SELECT 'shard-$i: ' || split_part(version(), ' ', 2)
            || ', system_identifier=' || (SELECT system_identifier FROM pg_control_system())
            || ', база ' || current_database()
            || ', бронирований: ' || (SELECT count(*) FROM bookings)"
done
echo
echo "system_identifier у всех разный: это четыре независимых кластера,"
echo "а не реплики одного (у реплики он совпадает с primary)."

step "3. Схема шарда"
docker exec booking_shard_0 psql -U booking_user -d booking_shard -c '\d bookings'

step "4. Бэкенд видит все шарды: GET /api/shards"
curl -s http://localhost:8000/api/shards | docker exec -i booking_backend python -m json.tool
