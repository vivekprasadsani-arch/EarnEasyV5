import asyncio
import logging
import os

import database as db
import tg_bot

logger = logging.getLogger(__name__)


async def handle_health_check(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
        request_line = await reader.readline()
        path = "/"
        if request_line:
            parts = request_line.decode("utf-8", errors="ignore").split()
            if len(parts) >= 2:
                path = parts[1]

        body = b"ok"
        status = "200 OK"
        if path in ("/health", "/healthz"):
            try:
                await db.ping()
            except Exception as exc:
                logger.warning("Health check DB ping failed: %s", exc)
                body = b"db unavailable"
                status = "503 Service Unavailable"
        elif path != "/":
            body = b"not found"
            status = "404 Not Found"

        response = (
            f"HTTP/1.1 {status}\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("utf-8") + body
        writer.write(response)
        await writer.drain()
    except Exception as exc:
        logger.warning("Health check handler error: %s", exc)
    finally:
        writer.close()
        await writer.wait_closed()


async def main():
    port = int(os.getenv("PORT", "10000"))
    server = await asyncio.start_server(handle_health_check, host="0.0.0.0", port=port)
    logger.info("Health server listening on 0.0.0.0:%s", port)

    async with server:
        bot_task = asyncio.create_task(tg_bot.main())
        try:
            await bot_task
        finally:
            server.close()
            await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
