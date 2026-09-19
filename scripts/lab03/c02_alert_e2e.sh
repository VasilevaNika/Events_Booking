#!/usr/bin/env bash
# ============================================================================
#  Сквозная проверка alerting (лабораторная №3, часть 11)
#
#  Проигрывает полный production-сценарий:
#      OK → сбой → CRITICAL → письмо → дедупликация → восстановление → recovery
#
#  Запуск:  bash scripts/lab03/c02_alert_e2e.sh
# ============================================================================
set -u
PSQL="docker exec booking_postgres psql -U booking_user -d booking_db"
API="http://localhost:8000/api/admin/partitions"

hr() { printf '%.0s─' {1..76}; echo; }
step() { echo; hr; echo "  $1"; hr; }

step "ШАГ 0. Исходное состояние"
curl -s "$API" | docker exec -i booking_backend python -m json.tool

step "ШАГ 1. Проверка здоровья — ожидаем OK"
curl -s "$API/health" | docker exec -i booking_backend python -m json.tool

step "ШАГ 2. Ломаем: удаляем последнюю партицию горизонта у lab3.events"
$PSQL -tAc "
DO \$\$
DECLARE last_part TEXT;
BEGIN
    SELECT partition_mgmt.partition_name('events', max(period_start), 'day')
      INTO last_part FROM partition_mgmt.required_periods('day', 3);
    EXECUTE format('DROP TABLE lab3.%I', last_part);
    RAISE NOTICE 'удалена lab3.%', last_part;
END \$\$;"

step "ШАГ 3. Проверка здоровья — ожидаем CRITICAL"
curl -s "$API/health" | docker exec -i booking_backend python -m json.tool

step "ШАГ 4. Отправка уведомления"
curl -s -X POST "$API/alerts/dispatch" | docker exec -i booking_backend python -m json.tool

step "ШАГ 5. Письмо, пришедшее на SMTP-приёмник"
docker logs booking_mailsink 2>&1 | tail -25

step "ШАГ 6. Дедупликация: три проверки подряд не создают новых алертов"
for i in 1 2 3; do
    curl -s "$API/health" -o /dev/null
done
$PSQL -c "SELECT severity, subject, sent_at IS NOT NULL AS sent
          FROM partition_mgmt.partition_alert_outbox ORDER BY id;"
$PSQL -c "SELECT config_id, status, check_count FROM partition_mgmt.partition_alert_state;"

step "ШАГ 7. Восстановление: CreatePartitionsJob"
curl -s -X POST "$API/ensure" | docker exec -i booking_backend python -m json.tool

step "ШАГ 8. Проверка здоровья — снова OK, ставится recovery"
curl -s "$API/health" | docker exec -i booking_backend python -m json.tool

step "ШАГ 9. Отправка recovery-уведомления"
curl -s -X POST "$API/alerts/dispatch" | docker exec -i booking_backend python -m json.tool

step "ШАГ 10. Recovery-письмо"
docker logs booking_mailsink 2>&1 | tail -22

step "ШАГ 11. Итоговая история уведомлений"
$PSQL -c "SELECT id, severity, subject, sent_at IS NOT NULL AS sent, send_attempts
          FROM partition_mgmt.partition_alert_outbox ORDER BY id;"
