#!/usr/bin/env bash
# ============================================================================
#  Лабораторная №4, часть 4: read-only поведение реплики.
#
#  Каждая команда здесь ДОЛЖНА завершиться ошибкой — это и есть результат.
#
#  Запуск:  bash scripts/lab04/a03_readonly.sh
# ============================================================================
set -u
export MSYS_NO_PATHCONV=1

P="docker exec booking_postgres psql -U booking_user -d booking_db"
R="docker exec booking_postgres_replica psql -U booking_user -d booking_db"

hr()   { printf '%.0s─' {1..78}; echo; }
step() { echo; hr; echo "  $1"; hr; }

step "0. Реплика находится в режиме восстановления"
$R -c "SELECT pg_is_in_recovery()                               AS in_recovery,
              current_setting('transaction_read_only')          AS transaction_read_only,
              current_setting('default_transaction_read_only')  AS default_transaction_read_only;"

step "1. INSERT на REPLICA"
$R -c "INSERT INTO lab4.replication_probe (marker) VALUES ('write-on-replica');"

step "2. UPDATE на REPLICA"
$R -c "UPDATE events SET title = title WHERE id = 1;"

step "3. DELETE на REPLICA"
$R -c "DELETE FROM lab4.replication_probe WHERE marker LIKE 'lab4-sql-%';"

step "4. DDL на REPLICA"
$R -c "CREATE TABLE lab4.should_not_exist (id int);"

step "5. Попытка явно открыть пишущую транзакцию"
$R -c "BEGIN READ WRITE;"

step "6. Даже nextval() — это запись (сдвиг последовательности)"
$R -c "SELECT nextval('lab4.replication_probe_id_seq');"

step "7. Временная таблица тоже запрещена"
$R -c "CREATE TEMP TABLE scratch (x int);"

step "8. На PRIMARY ничего из этого не появилось"
$P -c "SELECT count(*) AS leaked_rows
         FROM lab4.replication_probe
        WHERE marker = 'write-on-replica';"
$P -c "SELECT to_regclass('lab4.should_not_exist') AS leaked_table;"

step "9. Читающая сессия бэкенда read-only даже при откате на primary"
docker exec -i booking_backend python - <<'PY'
from sqlalchemy import text
from app.database import FallbackReadSessionLocal

db = FallbackReadSessionLocal()
try:
    node = db.execute(text(
        "SELECT CASE WHEN pg_is_in_recovery() THEN 'replica' ELSE 'primary' END"
    )).scalar()
    print(f"сессия подключена к: {node}")
    db.execute(text(
        "INSERT INTO lab4.replication_probe (marker) VALUES ('via-fallback-read-session')"
    ))
    db.commit()
    print("ЗАПИСАЛОСЬ — так быть не должно")
except Exception as exc:
    print("отказ:", type(getattr(exc, "orig", exc)).__name__, "-",
          str(getattr(exc, "orig", exc)).strip())
finally:
    db.close()
PY
