#!/usr/bin/env bash
# ============================================================================
#  Лабораторная №6, задание 6: отказ одного шарда.
#
#  До отказа → shard-2 остановлен → shard-2 снова запущен.
#
#  Запуск:  bash scripts/lab06/e01_shard_failure.sh
# ============================================================================
set -u
export MSYS_NO_PATHCONV=1

B="docker exec booking_backend python scripts/lab06/e_matrix.py"

echo "=== подготовка ==="
$B prepare

$B run "до отказа: все 4 шарда работают"

echo
echo ">>> docker stop booking_shard_2"
docker stop booking_shard_2 > /dev/null
$B run "shard-2 недоступен"

echo
echo ">>> docker start booking_shard_2"
docker start booking_shard_2 > /dev/null
for i in $(seq 1 30); do
    [ "$(docker inspect -f '{{.State.Health.Status}}' booking_shard_2)" = healthy ] && break
    sleep 2
done
echo "shard-2: $(docker inspect -f '{{.State.Health.Status}}' booking_shard_2)"
$B run "после восстановления shard-2"
