import asyncio
import tg_bot

async def main():
    # tg_bot.main() pings database, starts web_server (Admin Panel) and background monitor, and starts Telegram polling.
    await tg_bot.main()

if __name__ == "__main__":
    asyncio.run(main())
