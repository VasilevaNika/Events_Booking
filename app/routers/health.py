from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.database import engine, get_db, replica_engines

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception as exc:
        return {"status": "error", "database": str(exc)}


# Для каждого сервера: какая у него роль, куда он дошёл по WAL и насколько
# реплика отстаёт. Функции восстановления вызываются только на standby —
# на primary они бросают ошибку «recovery is not in progress».
_NODE_INFO_SQL = text("""
    SELECT
        CASE WHEN pg_is_in_recovery() THEN 'replica' ELSE 'primary' END AS role,
        pg_is_in_recovery()                               AS in_recovery,
        inet_server_addr()::text                          AS server_addr,
        current_setting('application_name')               AS application_name,
        current_setting('transaction_read_only')          AS transaction_read_only,
        CASE WHEN pg_is_in_recovery()
             THEN pg_last_wal_receive_lsn()::text
             ELSE pg_current_wal_lsn()::text END          AS wal_lsn,
        CASE WHEN pg_is_in_recovery()
             THEN pg_last_wal_replay_lsn()::text END      AS replay_lsn,
        CASE WHEN pg_is_in_recovery()
             THEN pg_is_wal_replay_paused() END           AS replay_paused,
        CASE WHEN pg_is_in_recovery()
             THEN round(extract(epoch FROM now() - pg_last_xact_replay_timestamp())::numeric, 3)
        END                                               AS seconds_since_last_replayed_tx
""")


def _node_info(eng: Engine) -> dict:
    try:
        with eng.connect() as conn:
            row = conn.execute(_NODE_INFO_SQL).mappings().one()
            return {"status": "up", **dict(row)}
    except Exception as exc:  # сервер недоступен — показываем, а не падаем
        return {"status": "down", "error": str(getattr(exc, "orig", exc))}


@router.get("/health/db")
def database_nodes():
    """
    Лабораторная №4: состояние primary и replica.

    seconds_since_last_replayed_tx — время с момента последней транзакции,
    воспроизведённой репликой. Это не чистый lag: если на primary никто не пишет,
    значение растёт, хотя реплика полностью догнала primary. Точный lag —
    в pg_stat_replication на primary (write_lag / flush_lag / replay_lag).
    """
    return {
        "primary": _node_info(engine),
        "replicas": [_node_info(e) for e in replica_engines],
    }