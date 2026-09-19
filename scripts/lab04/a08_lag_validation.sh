#!/usr/bin/env bash
# ============================================================================
#  Лабораторная №4, часть 6: проверка самого замера lag.
#
#  В спокойной системе пробник показал, что ПЕРВОЕ чтение с реплики сразу
#  после COMMIT устаревает почти всегда. Прежде чем опираться на эту цифру,
#  её надо проверить:
#
#    1. Контроль. Тот же пробник читает с PRIMARY — там устаревших чтений
#       быть не может. Если бы они были, ошибка была бы в самом замере.
#    2. Пауза перед чтением. Если причина — отставание реплики, то с ростом
#       паузы доля устаревших чтений должна падать до нуля.
#    3. Большая транзакция ×3 — сколько реплика догоняет после COMMIT.
#
#  Запуск:  bash scripts/lab04/a08_lag_validation.sh
# ============================================================================
set -u
export MSYS_NO_PATHCONV=1

P="docker exec booking_postgres psql -U booking_user -d booking_db"
PROBE="scripts/lab04/a05_lag_probe.py"

hr()    { printf '%.0s─' {1..78}; echo; }
step()  { echo; hr; echo "  $1"; hr; }
probe() { docker exec -i booking_backend python - "$@" < "$PROBE"; }

step "1. Контроль: 300 итераций, читаем с PRIMARY"
probe --iterations 300 --label control --read-from primary

step "2. Пауза между COMMIT и чтением с REPLICA: 200 итераций на каждую точку"
for delay in 0 1 2 5 10 20 50; do
    probe --iterations 200 --label "delay-$delay" --delay-ms "$delay" --quiet
done
echo
echo "  для сравнения — те же точки при чтении с primary:"
for delay in 0 5; do
    probe --iterations 200 --label "control-delay-$delay" --delay-ms "$delay" --read-from primary --quiet
done

step "3. Большая транзакция: 1 000 000 строк, три прогона"
probe --big-tx 1000000 --iterations 3 --label big-tx
$P -qc "TRUNCATE lab4.bulk_load;"
