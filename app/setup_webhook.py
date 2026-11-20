"""Одноразове налаштування після деплою на Vercel. Запускати локально:

    python -m app.setup_webhook https://<твій-проєкт>.vercel.app

Що робить: створює таблиці в БД (DATABASE_URL), реєструє webhook у Telegram
із секретом WEBHOOK_SECRET і оновлює меню команд. Значення беруться з .env —
вони мають збігатися з тими, що задані у Vercel.

    python -m app.setup_webhook --delete    # зняти webhook (повернутись до long polling)
    python -m app.setup_webhook --info      # що зараз зареєстровано в Telegram
"""
import asyncio
import sys

from app.config import settings
from app.db.repo import Database
from app.runtime import COMMANDS, get_dispatcher, make_bot


async def setup(base_url: str) -> None:
    if not settings.webhook_secret:
        sys.exit("Задай WEBHOOK_SECRET у .env (такий самий, як у Vercel)")
    db = Database(settings.database_url)
    await db.create_all()
    await db.close()
    print("✓ таблиці в БД на місці")

    bot = make_bot()
    try:
        url = base_url.rstrip("/") + "/telegram"
        await bot.set_webhook(
            url,
            secret_token=settings.webhook_secret,
            allowed_updates=get_dispatcher().resolve_used_update_types(),
        )
        await bot.set_my_commands(COMMANDS)
        print(f"✓ webhook: {url}")
        await info(bot)
    finally:
        await bot.session.close()


async def info(bot=None) -> None:
    own = bot is None
    bot = bot or make_bot()
    try:
        wh = await bot.get_webhook_info()
        print(f"url={wh.url or '—'} pending={wh.pending_update_count} last_error={wh.last_error_message or '—'}")
    finally:
        if own:
            await bot.session.close()


async def delete() -> None:
    bot = make_bot()
    try:
        await bot.delete_webhook()
        print("✓ webhook знято — тепер можна запускати python -m app.main")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--delete":
        asyncio.run(delete())
    elif arg == "--info":
        asyncio.run(info())
    elif arg.startswith("https://"):
        asyncio.run(setup(arg))
    else:
        sys.exit(__doc__)
