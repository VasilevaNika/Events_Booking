"""
Лабораторная №4, часть 6: ловим replication lag естественным путём.

Режим по умолчанию — много маленьких транзакций. Повторяет сценарий
приложения «записал на primary → сразу прочитал» и для каждой итерации
фиксирует:
  - увидел ли читающий сервер строку при ПЕРВОМ же SELECT;
  - если нет — через сколько миллисекунд после COMMIT строка появилась;
  - на сколько байт WAL реплика отставала (оценка, см. ниже).

Параметры, которые делают замер проверяемым:
  --read-from primary   контрольный прогон: на primary строка обязана быть сразу,
                        устаревших чтений должно быть 0
  --delay-ms N          пауза между COMMIT и первым чтением — через сколько
                        миллисекунд реплика уже показывает запись

Режим --big-tx ROWS — одна большая транзакция: INSERT на ROWS строк,
затем опрос реплики каждую миллисекунду до появления последней строки.

Об оценке байт WAL: pg_last_wal_replay_lsn() вычисляется уже после того, как
запрос взял снимок данных, поэтому значение слегка «из будущего» и отставание
занижено. Это ориентир, а не точная величина.

Запуск внутри контейнера бэкенда, где есть оба адреса БД:

    docker exec -i booking_backend python - --iterations 300 --label idle \
        < scripts/lab04/a05_lag_probe.py
"""
import argparse
import os
import statistics
import time
import uuid

import psycopg2


def pct(values, q):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q / 100 * (len(ordered) - 1))))]


def connect(url: str, name: str):
    conn = psycopg2.connect(url, application_name=name)
    conn.autocommit = True   # каждая команда — отдельная транзакция, как у API
    return conn


def small_transactions(args, primary, reader) -> None:
    pc, rc = primary.cursor(), reader.cursor()

    stale = 0
    catch_up_ms: list[float] = []
    gap_bytes: list[int] = []
    examples: list[str] = []
    never_seen = 0

    started = time.perf_counter()
    for _ in range(args.iterations):
        marker = f"probe-{args.label}-{uuid.uuid4().hex[:12]}"

        # 1. запись на primary; к возврату из execute транзакция закоммичена
        pc.execute(
            "INSERT INTO lab4.replication_probe (marker, note) VALUES (%s, %s)",
            (marker, f"lag probe: {args.label}"),
        )
        committed_at = time.perf_counter()

        if args.delay_ms:
            time.sleep(args.delay_ms / 1000)

        # 2. первое чтение — ровно то, что сделал бы следующий GET
        rc.execute(
            "SELECT count(*), "
            "       CASE WHEN pg_is_in_recovery() THEN pg_last_wal_replay_lsn()::text END "
            "FROM lab4.replication_probe WHERE marker = %s",
            (marker,),
        )
        found, replay_lsn = rc.fetchone()
        if found:
            continue

        # 3. строки ещё нет — насколько читающий сервер позади
        stale += 1
        gap = None
        if replay_lsn is not None:
            pc.execute(
                "SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), %s::pg_lsn)::bigint",
                (replay_lsn,),
            )
            gap = int(pc.fetchone()[0])
            gap_bytes.append(gap)

        # 4. ждём появления
        seen = False
        while time.perf_counter() - committed_at < args.timeout:
            rc.execute("SELECT count(*) FROM lab4.replication_probe WHERE marker = %s", (marker,))
            if rc.fetchone()[0]:
                seen = True
                break
            time.sleep(0.0005)
        waited = (time.perf_counter() - committed_at) * 1000
        if seen:
            catch_up_ms.append(waited)
        else:
            never_seen += 1

        if len(examples) < 5:
            gap_txt = f"позади примерно на {gap} байт WAL, " if gap is not None else ""
            examples.append(
                f"  {marker}: на primary закоммичено, при первом SELECT строки нет; "
                f"{gap_txt}строка появилась через {waited:.2f} мс"
            )

    elapsed = time.perf_counter() - started
    share = 100 * stale / args.iterations

    if args.quiet:
        if catch_up_ms:
            dist = (f"p50 {statistics.median(catch_up_ms):6.2f}  "
                    f"p95 {pct(catch_up_ms, 95):6.2f}  max {max(catch_up_ms):7.2f} мс")
        else:
            dist = "—"
        print(f"  {args.read_from:<8} задержка {args.delay_ms:>5.1f} мс │ "
              f"устарело {stale:>3}/{args.iterations} ({share:5.1f} %) │ "
              f"до появления: {dist}")
        return

    print(f"сценарий:                {args.label}")
    print(f"читаем с:                {args.read_from}")
    print(f"пауза перед чтением:     {args.delay_ms} мс")
    print(f"итераций:                {args.iterations}  (за {elapsed:.1f} с)")
    print(f"первое чтение устарело:  {stale}  ({share:.1f} %)")
    if catch_up_ms:
        print("время от COMMIT до появления строки (для устаревших чтений):")
        print(f"  p50 = {statistics.median(catch_up_ms):.2f} мс   "
              f"p95 = {pct(catch_up_ms, 95):.2f} мс   max = {max(catch_up_ms):.2f} мс")
    if gap_bytes:
        print(f"отставание по WAL в момент чтения (оценка): "
              f"p50 = {statistics.median(gap_bytes):.0f} байт, max = {max(gap_bytes)} байт")
    if never_seen:
        print(f"не дождались за {args.timeout} с: {never_seen}")
    if examples:
        print("примеры моментов «на primary уже есть, на читающем сервере ещё нет»:")
        print("\n".join(examples))


def big_transaction(args, primary, replica) -> None:
    pc, rc = primary.cursor(), replica.cursor()
    pc.execute("""
        CREATE TABLE IF NOT EXISTS lab4.bulk_load (
            id         BIGSERIAL PRIMARY KEY,
            payload    TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")

    print(f"большая транзакция: {args.big_tx:,} строк одним INSERT".replace(",", " "))
    results = []
    for run in range(1, args.iterations + 1):
        pc.execute("TRUNCATE lab4.bulk_load")
        # дождаться, пока TRUNCATE доедет до реплики, чтобы старт был честным
        while True:
            rc.execute("SELECT NOT EXISTS (SELECT 1 FROM lab4.bulk_load)")
            if rc.fetchone()[0]:
                break
            time.sleep(0.01)

        insert_started = time.perf_counter()
        # max(id) считается в той же команде — лишнего запроса после COMMIT нет
        pc.execute(
            "WITH ins AS ("
            "  INSERT INTO lab4.bulk_load (payload) "
            "  SELECT md5(g::text) || repeat('x', 200) FROM generate_series(1, %s) g "
            "  RETURNING id"
            ") SELECT max(id) FROM ins",
            (args.big_tx,),
        )
        last_id = pc.fetchone()[0]
        committed_at = time.perf_counter()

        polls = 0
        visible = False
        while time.perf_counter() - committed_at < args.timeout:
            rc.execute("SELECT EXISTS (SELECT 1 FROM lab4.bulk_load WHERE id = %s)", (last_id,))
            polls += 1
            if rc.fetchone()[0]:
                visible = True
                break
            time.sleep(0.001)
        waited = (time.perf_counter() - committed_at) * 1000

        state = (f"на реплике видна через {waited:.1f} мс после COMMIT (опросов: {polls})"
                 if visible else f"не появилась за {args.timeout} с")
        print(f"  прогон {run}: INSERT на primary {committed_at - insert_started:.2f} с; {state}")
        if visible:
            results.append(waited)

    if len(results) > 1:
        print(f"  итого: min {min(results):.1f} мс, медиана {statistics.median(results):.1f} мс, "
              f"max {max(results):.1f} мс")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=300)
    ap.add_argument("--label", default="idle")
    ap.add_argument("--timeout", type=float, default=10.0, help="сек ожидания появления")
    ap.add_argument("--delay-ms", type=float, default=0.0,
                    help="пауза между COMMIT на primary и первым чтением")
    ap.add_argument("--read-from", choices=("replica", "primary"), default="replica",
                    help="primary — контрольный прогон")
    ap.add_argument("--big-tx", type=int, default=0,
                    help="режим одной большой транзакции: сколько строк вставить")
    ap.add_argument("--quiet", action="store_true", help="одна строка итога")
    args = ap.parse_args()

    primary = connect(os.environ["DATABASE_URL"], "lab4-lag-probe-writer")
    replica = connect(os.environ["REPLICA_DATABASE_URL"], "lab4-lag-probe-replica")

    # Индекс по marker: стоимость чтения не должна расти вместе с таблицей,
    # иначе замер мерил бы уже не репликацию, а seq scan.
    primary.cursor().execute(
        "CREATE INDEX IF NOT EXISTS replication_probe_marker_idx "
        "ON lab4.replication_probe (marker)"
    )
    time.sleep(0.5)   # дать индексу доехать до реплики

    try:
        if args.big_tx:
            big_transaction(args, primary, replica)
        else:
            reader = (connect(os.environ["DATABASE_URL"], "lab4-lag-probe-control")
                      if args.read_from == "primary" else replica)
            small_transactions(args, primary, reader)
            if reader is not replica:
                reader.close()
    finally:
        primary.close()
        replica.close()


if __name__ == "__main__":
    main()
