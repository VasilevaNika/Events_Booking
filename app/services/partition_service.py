"""
Эксплуатация партиционированных таблиц (лабораторная работа №3).

Три обязанности:
  * CreatePartitionsJob      — создать недостающие партиции на горизонте;
  * PartitionHealthCheck     — проверить, что все нужные партиции на месте;
  * доставка уведомлений     — разослать то, что накопилось в outbox.

Сами проверки живут в SQL (схема ``partition_mgmt``) — так их может запускать
и pg_cron внутри СУБД, и приложение снаружи, не дублируя логику. Python-слой
нужен только для того, чего SQL не умеет: отправить письмо.

Дедупликация alert'ов обеспечивается на уровне SQL: запись в outbox
появляется только при смене состояния OK <-> CRITICAL.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


# ---------------------------------------------------------------------------
# Настройки канала уведомлений
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AlertChannel:
    """Параметры SMTP. Меняются переменными окружения ALERT_*."""

    host: str
    port: int
    sender: str
    recipient: str
    username: str | None
    password: str | None
    use_tls: bool

    @classmethod
    def from_env(cls) -> "AlertChannel":
        return cls(
            host=os.getenv("ALERT_SMTP_HOST", "mailsink"),
            port=int(os.getenv("ALERT_SMTP_PORT", "1025")),
            sender=os.getenv("ALERT_EMAIL_FROM", "partitions@booking.local"),
            recipient=os.getenv("ALERT_EMAIL_TO", "dba@booking.local"),
            username=os.getenv("ALERT_SMTP_USER") or None,
            password=os.getenv("ALERT_SMTP_PASSWORD") or None,
            use_tls=os.getenv("ALERT_SMTP_TLS", "false").lower() == "true",
        )


class PartitionService:
    def __init__(self, db: Session):
        self.db = db

    # ---------------------------------------------------------------- чтение
    def overview(self) -> list[dict[str, Any]]:
        """Сводка по всем обслуживаемым таблицам."""
        rows = self.db.execute(text("""
            SELECT schema_name, table_name, step, horizon,
                   partitions, status, missing_list,
                   last_checked_at, changed_at
            FROM partition_mgmt.v_partition_overview
        """)).fetchall()
        return [dict(r._mapping) for r in rows]

    def partitions(self, schema_name: str, table_name: str) -> list[dict[str, Any]]:
        """Список партиций таблицы с размерами и границами."""
        rows = self.db.execute(text("""
            SELECT c.relname                                   AS partition_name,
                   pg_get_expr(c.relpartbound, c.oid)          AS bounds,
                   pg_size_pretty(pg_relation_size(c.oid))     AS size,
                   c.reltuples::bigint                         AS approx_rows
            FROM pg_inherits i
            JOIN pg_class c     ON c.oid = i.inhrelid
            JOIN pg_class p     ON p.oid = i.inhparent
            JOIN pg_namespace n ON n.oid = p.relnamespace
            WHERE n.nspname = :schema AND p.relname = :table
            ORDER BY c.relname
        """), {"schema": schema_name, "table": table_name}).fetchall()
        return [dict(r._mapping) for r in rows]

    def recent_alerts(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.execute(text("""
            SELECT o.id, c.schema_name || '.' || c.table_name AS table_full_name,
                   o.severity, o.subject, o.created_at, o.sent_at,
                   o.send_attempts, o.last_error
            FROM partition_mgmt.partition_alert_outbox o
            JOIN partition_mgmt.partition_config c ON c.id = o.config_id
            ORDER BY o.id DESC
            LIMIT :limit
        """), {"limit": limit}).fetchall()
        return [dict(r._mapping) for r in rows]

    # ----------------------------------------------------- CreatePartitionsJob
    def ensure_partitions(self, config_id: int | None = None) -> list[str]:
        """Создаёт недостающие партиции. Идемпотентна."""
        rows = self.db.execute(
            text("SELECT log_line FROM partition_mgmt.create_missing_partitions(:cfg)"),
            {"cfg": config_id},
        ).fetchall()
        self.db.commit()
        return [r[0] for r in rows]

    # ---------------------------------------------------- PartitionHealthCheck
    def health_check(self, config_id: int | None = None) -> list[dict[str, Any]]:
        """Проверяет горизонт. При смене состояния кладёт уведомление в outbox."""
        rows = self.db.execute(
            text("""SELECT table_full_name, check_status, missing, notified
                    FROM partition_mgmt.health_check(:cfg)"""),
            {"cfg": config_id},
        ).fetchall()
        self.db.commit()
        return [dict(r._mapping) for r in rows]

    # ------------------------------------------------------ доставка alert'ов
    def dispatch_alerts(self, channel: AlertChannel | None = None) -> dict[str, Any]:
        """
        Разбирает очередь уведомлений и отправляет их письмом.

        Outbox читается с FOR UPDATE SKIP LOCKED, поэтому несколько воркеров
        могут работать параллельно, не отправляя одно и то же дважды.
        """
        channel = channel or AlertChannel.from_env()
        pending = self.db.execute(text("""
            SELECT o.id, o.severity, o.subject, o.body
            FROM partition_mgmt.partition_alert_outbox o
            WHERE o.sent_at IS NULL
            ORDER BY o.id
            FOR UPDATE SKIP LOCKED
        """)).fetchall()

        sent, failed = 0, 0
        for row in pending:
            try:
                self._send_email(channel, row.subject, row.body)
            except Exception as exc:                              # noqa: BLE001
                failed += 1
                self.db.execute(text("""
                    UPDATE partition_mgmt.partition_alert_outbox
                    SET send_attempts = send_attempts + 1, last_error = :err
                    WHERE id = :id
                """), {"id": row.id, "err": str(exc)[:500]})
            else:
                sent += 1
                self.db.execute(text("""
                    UPDATE partition_mgmt.partition_alert_outbox
                    SET sent_at = now(), send_attempts = send_attempts + 1,
                        last_error = NULL
                    WHERE id = :id
                """), {"id": row.id})

        self.db.commit()
        return {"pending": len(pending), "sent": sent, "failed": failed,
                "channel": f"smtp://{channel.host}:{channel.port}",
                "recipient": channel.recipient}

    @staticmethod
    def _send_email(channel: AlertChannel, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = channel.sender
        msg["To"] = channel.recipient
        msg.set_content(body)

        with smtplib.SMTP(channel.host, channel.port, timeout=10) as smtp:
            if channel.use_tls:
                smtp.starttls(context=ssl.create_default_context())
            if channel.username and channel.password:
                smtp.login(channel.username, channel.password)
            smtp.send_message(msg)
