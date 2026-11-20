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
from app.db.models import effective_prefs
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
    await msg("Офіс <2>")
    sub = (await db.user_subscriptions(42))[0]
    assert sub.title == "Офіс <2>" and sub.group == "4.1" and sub.address == "Особовий рахунок №24100994"
    assert sub.overridden() == []   # нова адреса бере загальні налаштування

    # загальні налаштування: до відключення 30 → 15 хв, період «зараз і далі»
    await msg("⚙️ Налаштування")
    assert "Загальні налаштування" in s.texts()[-1]
    await press("st:u:off")
    assert "вимкнеться" in s.texts()[-1]
    await press("ss:u:off:2")       # 15 хв
    await press("ss:u:pd:1")        # поточний і наступний період
    await press("ss:u:off:-")       # «як у загальних» для самих загальних не буває — ігнорується
    await press("ss:u:off:99")      # не з переліку — ігнорується
    user = (await db.users_by_ids({42}))[42]
    assert user.remind_minutes == 15 and user.schedule_period == "now"

    # своє для адреси: до появи світла 10 хв, без сповіщень про зміни; решта — із загальних
    await press(f"sub:{sub.id}")
    assert "Офіс &lt;2&gt;" in s.texts()[-1]          # назва екранована в HTML
    await press(f"st:{sub.id}:on")
    assert "з'явиться" in s.texts()[-1] and "Як у загальних (вимк)" in str(s.calls[-1].reply_markup)
    await press(f"ss:{sub.id}:on:1")   # 10 хв
    await press(f"ss:{sub.id}:nc:1")   # вимк
    sub = await db.get_subscription(sub.id, 42)
    prefs = effective_prefs(user, sub)
    assert (prefs.remind_minutes, prefs.restore_remind_minutes, prefs.notify_changes) == (15, 10, False)
    assert prefs.schedule_period == "now" and sub.overridden() == ["restore_remind_minutes", "notify_changes"]
    # знову «як у загальних» для одного налаштування
    await press(f"ss:{sub.id}:nc:-")
    assert (await db.get_subscription(sub.id, 42)).notify_changes is None

    # чужу адресу змінити не можна
    await db.ensure_user(7)
    other = await db.add_subscription(user_id=7, title="Чужа", city_key=sub.city_key, provider_id=1,
                                      source_key=sub.source_key, group="1.1")
    await press(f"ss:{other.id}:on:0")
    await press(f"st:{other.id}:on")
    assert (await db.get_subscription(other.id, 7)).restore_remind_minutes is None
    assert "Чужа" not in s.texts()[-1]

    # скинути власні налаштування всіх адрес
    await msg("⚙️ Налаштування")
    assert "Свої налаштування мають: Офіс &lt;2&gt;" in s.texts()[-1]
    await press("rst")
    await press("rstok")
    assert (await db.get_subscription(sub.id, 42)).overridden() == []

    # кнопка зі старого повідомлення (до цієї версії) — відповідаємо, а не мовчимо
    await press(f"rm:off:{sub.id}")
    assert "не діє" in s.calls[-1].text

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
