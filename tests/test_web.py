"""Ендпоінти Vercel: кожен запит збирає бота заново, тож стан має жити в БД."""
import httpx
import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.methods import SendMessage

from app import runtime as rt_mod
from app.bot.handlers import menu, onboarding
from app.config import settings
from app.db.repo import Database
from app.web import app
from tests.test_bot import FakeApi, FakeSession

SECRET = "s3cret_value-1"


@pytest.fixture
async def client(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path}/w.db"
    monkeypatch.setattr(settings, "database_url", url)
    monkeypatch.setattr(settings, "webhook_secret", SECRET)
    monkeypatch.setattr(settings, "cron_secret", SECRET)
    monkeypatch.setattr(rt_mod, "_schema_ready", False)
    monkeypatch.setattr(rt_mod, "_dispatcher", None)
    session = FakeSession()
    monkeypatch.setattr(rt_mod, "make_bot", lambda: Bot("123:abc", session=session,
                                                         default=DefaultBotProperties(parse_mode="HTML")))
    monkeypatch.setattr(rt_mod, "PrykarpattyaClient", lambda _http: FakeApi())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c, session, url
    menu.router._parent_router = None
    onboarding.router._parent_router = None


def update(i, **payload):
    base = {"date": 1763640000, "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "is_bot": False, "first_name": "T"}}
    if "text" in payload:
        return {"update_id": i, "message": {"message_id": i, **base, "text": payload["text"]}}
    return {"update_id": i, "callback_query": {
        "id": str(i), "chat_instance": "c", "data": payload["data"], "from": base["from"],
        "message": {"message_id": 1, **base, "text": "x"}}}


async def test_webhook_rejects_wrong_secret(client):
    c, session, _ = client
    r = await c.post("/telegram", json=update(1, text="/start"),
                     headers={"X-Telegram-Bot-Api-Secret-Token": "nope"})
    assert r.status_code == 403 and session.calls == []


async def test_dialog_survives_between_requests(client):
    c, session, url = client
    h = {"X-Telegram-Bot-Api-Secret-Token": SECRET}
    steps = [{"text": "/start"}, {"data": "ob:0"}, {"data": "m:group"}, {"data": "g:3.2"}, {"data": "tt:0"}]
    for i, step in enumerate(steps, 1):
        r = await c.post("/telegram", json=update(i, **step), headers=h)
        assert r.status_code == 200
    db = Database(url)
    subs = await db.user_subscriptions(42)
    await db.close()
    assert len(subs) == 1 and subs[0].group == "3.2" and subs[0].title == "🏠 Дім"


async def test_cron(client):
    c, session, _ = client
    assert (await c.get("/cron")).status_code == 403
    r = await c.get(f"/cron?key={SECRET}")
    assert r.json() == {"ok": True, "reminders": 0, "polled": True}
    r = await c.get("/cron", headers={"Authorization": f"Bearer {SECRET}"})
    assert r.json()["polled"] is False          # 5 хв ще не минуло
