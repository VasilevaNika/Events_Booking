"""
Доставка уведомлений о партициях (лабораторная №3, часть 11).

Разбирает очередь ``partition_mgmt.partition_alert_outbox`` и отправляет
накопившиеся сообщения письмом. Это классический transactional outbox:
health_check внутри СУБД только кладёт запись в таблицу, а отправкой
занимается отдельный процесс — если канал недоступен, уведомление
не теряется, а остаётся в очереди до следующей попытки.

Режимы:
    python scripts/lab03/alert_worker.py           # один проход и выход
    python scripts/lab03/alert_worker.py --loop 30 # демон, опрос каждые 30 с
    python scripts/lab03/alert_worker.py --check   # сначала health_check, потом отправка
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.database import SessionLocal                      # noqa: E402
from app.services.partition_service import PartitionService  # noqa: E402


def run_once(with_check: bool) -> dict:
    db = SessionLocal()
    try:
        svc = PartitionService(db)
        if with_check:
            for row in svc.health_check():
                mark = "OK" if row["check_status"] == "OK" else "CRITICAL"
                missing = ", ".join(row["missing"]) if row["missing"] else "-"
                print(f"[health] {row['table_full_name']}: {mark} "
                      f"(отсутствуют: {missing}, уведомление: "
                      f"{'да' if row['notified'] else 'нет'})", flush=True)
        return svc.dispatch_alerts()
    finally:
        db.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=0,
                    help="работать демоном с указанным интервалом в секундах")
    ap.add_argument("--check", action="store_true",
                    help="перед отправкой выполнить PartitionHealthCheck")
    args = ap.parse_args()

    while True:
        result = run_once(args.check)
        if result["pending"]:
            print(f"[alerts] в очереди {result['pending']}, "
                  f"отправлено {result['sent']}, ошибок {result['failed']} "
                  f"→ {result['channel']} ({result['recipient']})", flush=True)
        if not args.loop:
            break
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
