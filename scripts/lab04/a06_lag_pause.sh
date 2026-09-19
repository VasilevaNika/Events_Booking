#!/usr/bin/env bash
# ============================================================================
#  Лабораторная №4, часть 6: гарантированно поймать момент
#  «на primary уже новое значение, на реплике — ещё старое».
#
#  В локальной сети docker реплика обычно догоняет primary за доли
#  миллисекунды, поэтому для наглядности воспроизведение WAL на реплике
#  ставится на паузу: pg_wal_replay_pause(). Это управляемая эмуляция
#  отставания — WAL продолжает ПРИХОДИТЬ (receive_lsn растёт),
#  но не ПРИМЕНЯЕТСЯ (replay_lsn стоит на месте).
#
#  Весь сценарий идёт через API сервиса на отдельном тестовом мероприятии,
#  реальные данные не трогаются.
#
#  Запуск:  bash scripts/lab04/a06_lag_pause.sh
# ============================================================================
set -u
export MSYS_NO_PATHCONV=1

P="docker exec booking_postgres psql -U booking_user -d booking_db"
R="docker exec booking_postgres_replica psql -U booking_user -d booking_db"
API="http://localhost:8000"
TAG="lab4-lag-demo"

hr()   { printf '%.0s─' {1..78}; echo; }
step() { echo; hr; echo "  $1"; hr; }
py()   { docker exec -i booking_backend python -c "$1"; }

catalog_view() {   # что показывает каталог: GET /api/events → replica
    curl -s -D - "$API/api/events?search=$TAG&page_size=5" | py "
import sys, json
raw = sys.stdin.buffer.read().decode('utf-8', 'replace')
head, _, body = raw.partition('\r\n\r\n')
node = next((l.split(':', 1)[1].strip() for l in head.split('\r\n')
             if l.lower().startswith('x-db-node:')), '?')
titles = [i['title'] for i in json.loads(body)['items']] or ['(мероприятие не найдено)']
print(f'  каталог  GET /api/events?search=$TAG   [X-DB-Node: {node}] ->', ', '.join(titles))"
}

card_view() {      # карточка: GET /api/events/{id} → primary
    curl -s "$API/api/events/$1" | py "
import sys, json
d = json.load(sys.stdin)
print('  карточка GET /api/events/$1               [primary]          ->', d.get('title', d))"
}

lsn_view() {
    $R -Atc "SELECT '  replica: получено '    || pg_last_wal_receive_lsn()
                 || ', применено '             || pg_last_wal_replay_lsn()
                 || ', не применено '          || pg_size_pretty(pg_wal_lsn_diff(pg_last_wal_receive_lsn(), pg_last_wal_replay_lsn()))
                 || ', состояние: '            || pg_get_wal_replay_pause_state()"
    $P -Atc "SELECT '  primary: текущий LSN '  || pg_current_wal_lsn()
                 || ', реплике отправлено '    || sent_lsn
                 || ', реплика применила '     || replay_lsn
                 || ', разрыв '                || pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn))
               FROM pg_stat_replication"
}

step "0. Создаём тестовое мероприятие через API (запись → primary)"
VENUE_ID=$($P -Atc "SELECT min(id) FROM venues")
RESP=$(curl -s -X POST "$API/api/events" -H 'Content-Type: application/json' -d "{
    \"title\": \"$TAG OLD title\",
    \"venue_id\": $VENUE_ID,
    \"start_time\": \"2026-12-01T19:00:00\",
    \"end_time\": \"2026-12-01T21:00:00\",
    \"price\": 1000,
    \"max_capacity\": 100
}")
EVENT_ID=$(printf '%s' "$RESP" | py 'import sys, json; print(json.load(sys.stdin)["id"])')
echo "  создано мероприятие id=$EVENT_ID"
sleep 1
catalog_view
card_view "$EVENT_ID"

step "1. Ставим воспроизведение WAL на реплике на паузу"
$R -c "SELECT pg_wal_replay_pause();" > /dev/null
sleep 1
lsn_view

step "2. Переименовываем мероприятие: PUT /api/events/$EVENT_ID (→ primary)"
curl -s -X PUT "$API/api/events/$EVENT_ID" -H 'Content-Type: application/json' \
     -d "{\"title\": \"$TAG NEW title\"}" | py "
import sys, json
print('  ответ PUT:', json.load(sys.stdin).get('title'))"
sleep 2

step "3. ТОТ САМЫЙ МОМЕНТ: primary уже новое, replica ещё старое"
card_view "$EVENT_ID"
catalog_view
echo
echo "  то же самое прямым SQL:"
$P -Atc "SELECT '  primary -> ' || title FROM events WHERE id = $EVENT_ID"
$R -Atc "SELECT '  replica -> ' || title FROM events WHERE id = $EVENT_ID"

step "4. WAL до реплики дошёл, но не применён"
lsn_view
echo
echo "  ещё 5000 строк на primary — «получено» растёт, «применено» стоит:"
$P -qc "INSERT INTO lab4.replication_probe (marker, note)
        SELECT 'pause-demo-' || g, 'пока реплика на паузе' FROM generate_series(1, 5000) g;"
sleep 2
lsn_view

step "5. Снимаем паузу — реплика догоняет"
$R -c "SELECT pg_wal_replay_resume();" > /dev/null
sleep 1
lsn_view
echo
card_view "$EVENT_ID"
catalog_view

step "6. Уборка: удаляем тестовое мероприятие (→ primary)"
CODE=$(curl -s -w '%{http_code}' -X DELETE "$API/api/events/$EVENT_ID")
echo "  DELETE /api/events/$EVENT_ID → HTTP $CODE"
sleep 1
catalog_view
