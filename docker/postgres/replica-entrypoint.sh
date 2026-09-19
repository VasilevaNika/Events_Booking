#!/usr/bin/env bash
# ============================================================================
#  Точка входа REPLICA (лабораторная №4)
#
#  При первом старте (пустой том) реплика сама разворачивается из primary:
#    1. ждёт, пока primary начнёт принимать подключения;
#    2. создаёт на primary роль replicator и физический слот репликации
#       (идемпотентно — повторный запуск ничего не ломает);
#    3. снимает полную физическую копию кластера через pg_basebackup;
#    4. кладёт standby.signal и primary_conninfo — сервер стартует
#       в режиме постоянного восстановления и принимает поток WAL.
#
#  При последующих стартах шаги 1–4 пропускаются: данные уже на месте,
#  реплика продолжает стриминг с того места, где остановилась
#  (позицию хранит слот на primary).
# ============================================================================
set -euo pipefail

PGDATA="${PGDATA:-/var/lib/postgresql/data}"

log() { echo "[replica-bootstrap] $*"; }

cleanup_on_error() {
    log "ОШИБКА — очищаю ${PGDATA}, чтобы следующий запуск начал заново"
    find "${PGDATA:?}" -mindepth 1 -delete 2>/dev/null || true
}

if [ ! -s "${PGDATA}/PG_VERSION" ]; then
    trap cleanup_on_error ERR

    log "том пуст — разворачиваю реплику с ${PRIMARY_HOST}:${PRIMARY_PORT}"

    until pg_isready -h "$PRIMARY_HOST" -p "$PRIMARY_PORT" \
                     -U "$PRIMARY_ADMIN_USER" -d "$PRIMARY_DB" >/dev/null 2>&1; do
        log "жду primary..."
        sleep 2
    done

    log "создаю роль ${REPLICATION_USER} и слот ${REPLICATION_SLOT} (если их ещё нет)"
    PGPASSWORD="$PRIMARY_ADMIN_PASSWORD" psql -v ON_ERROR_STOP=1 \
        -h "$PRIMARY_HOST" -p "$PRIMARY_PORT" \
        -U "$PRIMARY_ADMIN_USER" -d "$PRIMARY_DB" \
        -v repl_user="$REPLICATION_USER" \
        -v repl_pass="$REPLICATION_PASSWORD" \
        -v slot="$REPLICATION_SLOT" \
        -f /docker/replica-bootstrap.sql

    log "pg_basebackup: копирую кластер (на большой базе это займёт время)"
    mkdir -p "$PGDATA"
    chown postgres:postgres "$PGDATA"
    chmod 700 "$PGDATA"
    started=$(date +%s)
    PGPASSWORD="$REPLICATION_PASSWORD" gosu postgres pg_basebackup \
        -h "$PRIMARY_HOST" -p "$PRIMARY_PORT" -U "$REPLICATION_USER" \
        -D "$PGDATA" \
        --wal-method=stream \
        --slot="$REPLICATION_SLOT" \
        --checkpoint=fast \
        --verbose
    finished=$(date +%s)
    log "копия снята за $((finished - started)) с"

    # standby.signal — стартовать как standby, а не как самостоятельный сервер
    gosu postgres touch "${PGDATA}/standby.signal"

    # Откуда брать поток WAL. Прописывается явно, а не через pg_basebackup -R,
    # чтобы каждый параметр был виден и объясним в отчёте.
    {
        echo ""
        echo "# --- добавлено replica-entrypoint.sh (лабораторная №4) ---"
        echo "primary_conninfo = 'host=${PRIMARY_HOST} port=${PRIMARY_PORT} user=${REPLICATION_USER} password=${REPLICATION_PASSWORD} application_name=${REPLICA_APPLICATION_NAME}'"
        echo "primary_slot_name = '${REPLICATION_SLOT}'"
    } >> "${PGDATA}/postgresql.auto.conf"
    chown postgres:postgres "${PGDATA}/postgresql.auto.conf"

    trap - ERR
    log "готово: standby.signal и primary_conninfo записаны"
else
    log "данные уже есть — стартую как standby без повторной копии"
fi

exec docker-entrypoint.sh "$@"
