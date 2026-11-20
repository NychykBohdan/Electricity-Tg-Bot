"""Надсилання повідомлень з урахуванням лімітів Telegram."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup

from app.db.repo import Database

log = logging.getLogger(__name__)

SEND_DELAY = 0.05  # ~20 повідомлень/сек, нижче ліміту Telegram (30/сек)


class Notifier:
    def __init__(self, bot: Bot, db: Database):
        self.bot = bot
        self.db = db
        self._lock = asyncio.Lock()

    async def send(
        self,
        chat_id: int,
        text: str,
        markup: InlineKeyboardMarkup | None = None,
        silent: bool = False,
    ) -> bool:
        async with self._lock:
            for _ in range(3):
                try:
                    await self.bot.send_message(
                        chat_id, text, reply_markup=markup, disable_notification=silent
                    )
                    await asyncio.sleep(SEND_DELAY)
                    return True
                except TelegramRetryAfter as e:
                    log.warning("Flood control, sleeping %ss", e.retry_after)
                    await asyncio.sleep(e.retry_after + 1)
                except TelegramForbiddenError:
                    log.info("User %s blocked the bot, removing", chat_id)
                    await self.db.delete_user(chat_id)
                    return False
                except TelegramBadRequest as e:
                    log.error("Bad request for %s: %s", chat_id, e)
                    return False
            return False
