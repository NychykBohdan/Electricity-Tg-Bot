"""Спільна збірка бота для обох режимів: webhook (Vercel) і long polling (локально/Docker)."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from app.bot.handlers import menu, onboarding
from app.bot.storage import SqlStorage, current_db
from app.collectors.prykarpattya import PrykarpattyaClient
from app.config import settings
from app.db.repo import Database
from app.services.notifier import Notifier
from app.services.poller import Poller

log = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="schedule", description="Графік відключень"),
    BotCommand(command="addresses", description="Мої адреси"),
    BotCommand(command="start", description="Почати / додати адресу"),
    BotCommand(command="help", description="Допомога"),
]

_dispatcher: Dispatcher | None = None
_schema_ready = False


def get_dispatcher() -> Dispatcher:
    """Один Dispatcher на процес: роутер aiogram можна під'єднати лише один раз."""
    global _dispatcher
    if _dispatcher is None:
        dp = Dispatcher(storage=SqlStorage())
        # menu першим: кнопки меню працюють навіть посеред додавання адреси
        dp.include_routers(menu.router, onboarding.router)
        _dispatcher = dp
    return _dispatcher


def make_bot() -> Bot:
    return Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


@dataclass
class Runtime:
    db: Database
    bot: Bot
    prk: PrykarpattyaClient
    notifier: Notifier
    poller: Poller

    @property
    def handler_data(self) -> dict:
        """Що отримують хендлери як аргументи (db, prk, poller)."""
        return {"db": self.db, "prk": self.prk, "poller": self.poller}


@asynccontextmanager
async def runtime() -> AsyncIterator[Runtime]:
    """Ресурси на один запит/запуск. Усе закривається в кінці: на serverless
    наступний запит може прийти в інший процес або інший event loop."""
    global _schema_ready
    db = Database(settings.database_url)
    token = current_db.set(db)
    bot = make_bot()
    try:
        if not _schema_ready:  # раз на процес: дешевий захист, якщо таблиць ще нема
            await db.create_all()
            _schema_ready = True
        async with aiohttp.ClientSession() as http:
            prk = PrykarpattyaClient(http)
            notifier = Notifier(bot, db)
            poller = Poller(db, prk, notifier, settings.admin_id)
            yield Runtime(db, bot, prk, notifier, poller)
    finally:
        current_db.reset(token)
        await bot.session.close()
        await db.close()
