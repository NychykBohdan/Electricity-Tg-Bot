"""Прогін діалогів бота без Telegram: підміняємо HTTP-сесію aiogram."""
from datetime import datetime

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from app.bot.handlers import menu, onboarding
from app.bot.storage import SqlStorage, current_db
from app.db.repo import Database
from app.services.poller import Poller
from tests.test_flow import FakeNotifier, FakePrykarpattya

USER = User(id=42, is_bot=False, first_name="Test")
CHAT = Chat(id=42, type="private")


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, (SendMessage, EditMessageText)):
            return Message(message_id=len(self.calls), date=datetime.now(), chat=CHAT,
                           text=getattr(method, "text", ""))
        return True

    async def stream_content(self, *a, **kw):  # pragma: no cover
        yield b""

    async def close(self):
        pass

    def texts(self):
        return [c.text for c in self.calls if isinstance(c, (SendMessage, EditMessageText))]


class FakeApi(FakePrykarpattya):
    async def resolve_queue_by_account(self, account_number: str) -> str | None:
        return "4.1" if account_number == "24100994" else None


@pytest.fixture
async def env(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path}/b.db")
    await db.create_all()
    session = FakeSession()
    bot = Bot("123:abc", session=session, default=DefaultBotProperties(parse_mode="HTML"))
    token = current_db.set(db)
    dp = Dispatcher(storage=SqlStorage())   # як на Vercel: стан діалогу в БД
    dp.include_routers(menu.router, onboarding.router)
    api = FakeApi()
    dp["db"], dp["prk"], dp["poller"] = db, api, Poller(db, api, FakeNotifier())
    n = {"i": 0}

    async def msg(text):
        n["i"] += 1
        m = Message(message_id=n["i"], date=datetime.now(), chat=CHAT, from_user=USER, text=text)
        await dp.feed_update(bot, Update(update_id=n["i"], message=m))

    async def press(data):
        n["i"] += 1
        m = Message(message_id=1, date=datetime.now(), chat=CHAT, from_user=USER, text="x")
        cb = CallbackQuery(id=str(n["i"]), from_user=USER, chat_instance="c", message=m, data=data)
        await dp.feed_update(bot, Update(update_id=n["i"], callback_query=cb))

    yield db, session, msg, press
    # aiogram 3 кешує роутери: від'єднуємо, щоб наступний тест міг підключити знову
    menu.router._parent_router = None
    onboarding.router._parent_router = None
    current_db.reset(token)
    await db.engine.dispose()


async def test_onboarding_by_group(env):
    db, s, msg, press = env
    await msg("/start")
    assert "Обери область" in s.texts()[-1]
    await press("ob:0")       # одна область → одне місто → один постачальник → метод
    assert "Як визначимо" in s.texts()[-1]
    await press("m:group")
    await press("g:3.2")
    await press("tt:0")       # 🏠 Дім
    subs = await db.user_subscriptions(42)
    assert len(subs) == 1 and subs[0].group == "3.2" and subs[0].source_key == "prk:if_oblast:1"
    assert "Збережено" in s.texts()[-2]
    assert "06:00–09:30" in s.texts()[-1]          # графік показано одразу


async def test_onboarding_account_not_found_falls_back_to_manual(env):
    db, s, msg, press = env
    await msg("/start")
    await press("ob:0")
    await press("m:account")
    await msg("00000000")
    assert "Обери чергу вручну" in s.texts()[-1]
    await press("g:5.1")
    await press("tt:1")       # 💼 Робота
    sub = (await db.user_subscriptions(42))[0]
    assert sub.group == "5.1" and sub.title == "💼 Робота"


async def test_onboarding_by_account_and_settings(env):
    db, s, msg, press = env
    await msg("/start")
    await press("ob:0")
    await press("m:account")
    await msg("24100994")
    assert "Твоя черга: <b>4.1</b>" in s.texts()[-1]
    await msg("Офіс")
    sub = (await db.user_subscriptions(42))[0]
    assert sub.title == "Офіс" and sub.group == "4.1" and sub.address == "Особовий рахунок №24100994"

    # налаштування: нагадування 30 → 60, сповіщення вимк
    await press(f"rm:{sub.id}")
    await press(f"nc:{sub.id}")
    sub = await db.get_subscription(sub.id, 42)
    assert sub.remind_minutes == 60 and sub.notify_changes is False

    # кнопка меню посеред додавання адреси скидає діалог
    await msg("➕ Додати адресу")
    await msg("📅 Графік")
    assert "Офіс" in s.texts()[-1]
    await press(f"sv:{sub.id}:1")
    assert "Завтра" in s.texts()[-1] and "ще не опубліковано" in s.texts()[-1]

    await press(f"delok:{sub.id}")
    assert await db.user_subscriptions(42) == []
    answers = [c for c in s.calls if isinstance(c, AnswerCallbackQuery)]
    ids = [a.callback_query_id for a in answers]
    assert len(ids) == len(set(ids)), "callback answered twice"
