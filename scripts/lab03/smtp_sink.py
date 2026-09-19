"""
Минимальный SMTP-приёмник для демонстрации alerting (лабораторная №3).

Зачем свой, а не MailHog: образ mailhog/mailhog не скачивался с Docker Hub
(TLS handshake timeout), а тянуть внешние зависимости ради одного сервиса
не хотелось. Здесь ~100 строк на голой стандартной библиотеке — настоящий
SMTP-диалог, письма сохраняются на диск и печатаются в лог контейнера.

Запуск:
    python scripts/lab03/smtp_sink.py --host 0.0.0.0 --port 1025 --out /app/mailbox

Заменить на реальный канал (VK / Max / корпоративный SMTP) можно, не трогая
этот файл: достаточно поменять переменные ALERT_SMTP_* у сервиса backend.
"""
import argparse
import asyncio
import email
import os
import re
from datetime import datetime, timezone


class SMTPSession:
    """Одно соединение: разбирает команды SMTP и складывает письмо в файл."""

    def __init__(self, reader, writer, out_dir):
        self.reader = reader
        self.writer = writer
        self.out_dir = out_dir
        self.mail_from = ""
        self.rcpt_to = []

    async def send(self, line: str) -> None:
        self.writer.write((line + "\r\n").encode())
        await self.writer.drain()

    async def read_line(self) -> str:
        data = await self.reader.readline()
        return data.decode("utf-8", errors="replace").rstrip("\r\n")

    async def handle(self) -> None:
        await self.send("220 booking-mailsink ESMTP ready")
        while True:
            line = await self.read_line()
            if not line:
                break
            cmd = line.split(" ", 1)[0].upper()

            if cmd == "EHLO" or cmd == "HELO":
                await self.send("250-booking-mailsink")
                await self.send("250 SIZE 10485760")
            elif cmd == "MAIL":
                self.mail_from = self._extract_addr(line)
                await self.send("250 OK")
            elif cmd == "RCPT":
                self.rcpt_to.append(self._extract_addr(line))
                await self.send("250 OK")
            elif cmd == "DATA":
                await self.send("354 End data with <CR><LF>.<CR><LF>")
                raw = await self._read_data()
                path = self._store(raw)
                await self.send("250 OK: queued")
                self._log(raw, path)
                self.mail_from, self.rcpt_to = "", []
            elif cmd == "RSET":
                self.mail_from, self.rcpt_to = "", []
                await self.send("250 OK")
            elif cmd == "NOOP":
                await self.send("250 OK")
            elif cmd == "QUIT":
                await self.send("221 Bye")
                break
            else:
                await self.send("502 Command not implemented")

        self.writer.close()

    @staticmethod
    def _extract_addr(line: str) -> str:
        m = re.search(r"<([^>]*)>", line)
        return m.group(1) if m else line.partition(":")[2].strip()

    async def _read_data(self) -> bytes:
        # Письмо читается и хранится в БАЙТАХ. Если разобрать его как str,
        # email.message_from_string при Content-Transfer-Encoding: 8bit
        # прогоняет тело через raw-unicode-escape, и символы вне BMP
        # (например, эмодзи 🔴) превращаются в литерал \U0001f534.
        chunks = []
        while True:
            raw = await self.reader.readline()
            line = raw.rstrip(b"\r\n")
            if line == b".":
                break
            # точка в начале строки экранируется удвоением
            chunks.append(line[1:] if line.startswith(b"..") else line)
        return b"\n".join(chunks)

    def _store(self, raw: bytes) -> str:
        os.makedirs(self.out_dir, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        path = os.path.join(self.out_dir, f"{stamp}.eml")
        with open(path, "wb") as fh:
            fh.write(raw)
        return path

    def _log(self, raw: bytes, path: str) -> None:
        msg = email.message_from_bytes(raw)
        subject = str(email.header.make_header(email.header.decode_header(
            msg.get("Subject", "(без темы)"))))
        payload = msg.get_payload(decode=True)
        text = (payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
                if payload else msg.get_payload())

        print("=" * 72, flush=True)
        print(f"ПОЛУЧЕНО ПИСЬМО   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
        print(f"From:    {self.mail_from}", flush=True)
        print(f"To:      {', '.join(self.rcpt_to)}", flush=True)
        print(f"Subject: {subject}", flush=True)
        print(f"Файл:    {path}", flush=True)
        print("-" * 72, flush=True)
        print(text, flush=True)
        print("=" * 72, flush=True)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=1025)
    ap.add_argument("--out", default="/app/mailbox")
    args = ap.parse_args()

    async def on_connect(reader, writer):
        try:
            await SMTPSession(reader, writer, args.out).handle()
        except Exception as exc:                       # noqa: BLE001
            print(f"[smtp-sink] ошибка сессии: {exc}", flush=True)

    server = await asyncio.start_server(on_connect, args.host, args.port)
    print(f"[smtp-sink] слушаю {args.host}:{args.port}, письма в {args.out}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
