"""
Эксплуатационные endpoint'ы для партиционированных таблиц (лабораторная №3).

Нужны, чтобы состояние партиций можно было посмотреть и починить из API,
а не только через psql: ровно этот сценарий показывается на защите.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.partition_service import PartitionService

router = APIRouter(prefix="/api/admin/partitions", tags=["partitions"])


@router.get("")
def overview(db: Session = Depends(get_db)):
    """Сводка: какие таблицы обслуживаются, сколько партиций, статус проверки."""
    return {"tables": PartitionService(db).overview()}


@router.get("/list")
def list_partitions(
    schema_name: str = Query("public", description="Схема таблицы"),
    table_name: str = Query("bookings", description="Имя партиционированной таблицы"),
    db: Session = Depends(get_db),
):
    """Партиции конкретной таблицы: границы, размер, приблизительное число строк."""
    return {
        "table": f"{schema_name}.{table_name}",
        "partitions": PartitionService(db).partitions(schema_name, table_name),
    }


@router.post("/ensure")
def ensure_partitions(
    config_id: int | None = Query(None, description="Только для одной записи конфига"),
    db: Session = Depends(get_db),
):
    """CreatePartitionsJob: создаёт недостающие партиции. Безопасна при повторе."""
    return {"log": PartitionService(db).ensure_partitions(config_id)}


@router.get("/health")
def partition_health(
    config_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    """
    PartitionHealthCheck: OK или CRITICAL.

    При смене состояния кладёт уведомление в outbox — повторные вызовы
    при том же состоянии новых уведомлений не создают.
    """
    results = PartitionService(db).health_check(config_id)
    overall = "CRITICAL" if any(r["check_status"] == "CRITICAL" for r in results) else "OK"
    return {"status": overall, "checks": results}


@router.post("/alerts/dispatch")
def dispatch_alerts(db: Session = Depends(get_db)):
    """Отправляет накопившиеся уведомления в настроенный канал."""
    return PartitionService(db).dispatch_alerts()


@router.get("/alerts")
def recent_alerts(
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """История уведомлений: что отправлено, что ждёт отправки."""
    return {"alerts": PartitionService(db).recent_alerts(limit)}
