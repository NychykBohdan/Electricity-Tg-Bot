"""Режим long polling: локальна розробка або Docker на власному сервері.

На Vercel працює app/web.py (webhook). Одночасно обидва режими працювати не можуть:
поки в Telegram зареєстровано webhook, long polling отримує помилку Conflict.
Зняти webhook: python -m app.setup_webhook --delete
"""
import asyncio
import logging
from pathlib import Path

from app.config import settings
from app.runtime import COMMANDS, get_dispatcher, runtime
from app.services import reminders


async def main() -> None:
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    if settings.database_url.startswith("sqlite"):
        Path("data").mkdir(exist_ok=True)

    async with runtime() as rt:
        dp = get_dispatcher()
        await rt.bot.set_my_commands(COMMANDS)
        tasks = [
            asyncio.create_task(rt.poller.run(settings.poll_interval_sec)),
            asyncio.create_task(reminders.run(rt.db, rt.notifier, settings.reminder_check_sec)),
        ]
        try:
            await dp.start_polling(rt.bot, **rt.handler_data)
        finally:
            for t in tasks:
                t.cancel()


if __name__ == "__main__":
    asyncio.run(main())
