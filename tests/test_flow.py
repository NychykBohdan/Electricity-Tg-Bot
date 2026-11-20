"""Інтеграційний тест: опитування → зміна → сповіщення → нагадування (з реальною SQLite)."""
from datetime import datetime, timedelta

import pytest

from app.config import TZ
from app.core.models import DaySchedule, DayStatus, Interval, merge_intervals
from app.db.repo import Database
from app.regions import CITIES, source_key
from app.services.poller import Poller
from app.services.reminders import check_reminders


class FakePrykarpattya:
    def __init__(self):
        self.today_pairs = [(360, 570)]
        self.tomorrow = None

    async def fetch(self, *_args):
        today = datetime.now(TZ).date()
        tm = today + timedelta(days=1)
        tomorrow_day = (
            DaySchedule(tm, DayStatus.SCHEDULE, merge_intervals([Interval(s, e) for s, e in self.tomorrow]))
            if self.tomorrow is not None
            else DaySchedule(tm, DayStatus.WAITING, ())
        )
        return {
            "3.2": [
                DaySchedule(today, DayStatus.SCHEDULE, merge_intervals([Interval(s, e) for s, e in self.today_pairs])),
                tomorrow_day,
            ],
            "1.1": [DaySchedule(today, DayStatus.NO_OUTAGES, ())],
        }


class FakeNotifier:
    def __init__(self):
        self.sent = []

    async def send(self, chat_id, text, markup=None, silent=False):
        self.sent.append((chat_id, text))
        return True


@pytest.mark.asyncio
async def test_full_cycle(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    await db.create_all()
    city = CITIES[0]
    key = source_key(city, 1)
    await db.ensure_user(42)
    sub = await db.add_subscription(user_id=42, title="Дім", city_key=city.key, provider_id=1,
                                    source_key=key, group="3.2", remind_minutes=30, notify_changes=True)

    api, notifier = FakePrykarpattya(), FakeNotifier()
    poller = Poller(db, api, notifier)

    # 1) перше опитування — тихе наповнення БД
    await poller.tick()
    assert notifier.sent == []

    # 2) без змін — нічого
    await poller.tick()
    assert notifier.sent == []

    # 3) графік змінився
    api.today_pairs = [(360, 570), (960, 1140)]
    await poller.tick()
    assert len(notifier.sent) == 1 and "Графік змінився" in notifier.sent[0][1]

    # 4) опубліковано графік на завтра
    api.tomorrow = [(0, 120)]
    await poller.tick()
    assert "Опубліковано графік" in notifier.sent[-1][1]

    # 5) нагадування: за 20 хв до 16:00 — одне, повторно не шле
    notifier.sent.clear()
    today = datetime.now(TZ).date()
    t = datetime(today.year, today.month, today.day, 15, 40, tzinfo=TZ)
    assert await check_reminders(db, notifier, t) == 1
    assert "Відключення через 20 хв" in notifier.sent[0][1]
    assert await check_reminders(db, notifier, t + timedelta(minutes=1)) == 0

    # 5б) поява світла: відключення 16:00–19:00, попередження за 15 хв
    await db.update_subscription(sub.id, restore_remind_minutes=15)
    notifier.sent.clear()
    assert await check_reminders(db, notifier, t) == 0          # світло ще є — рано
    t_on = datetime(today.year, today.month, today.day, 18, 50, tzinfo=TZ)
    assert await check_reminders(db, notifier, t_on) == 1
    assert "Світло з'явиться через 10 хв" in notifier.sent[0][1]
    assert await check_reminders(db, notifier, t_on + timedelta(minutes=1)) == 0

    # 6) вимкнені сповіщення не шлються
    await db.update_subscription(sub.id, notify_changes=False)
    api.today_pairs = [(360, 570)]
    notifier.sent.clear()
    await poller.tick()
    assert notifier.sent == []
    await db.engine.dispose()


async def test_tick_if_due_and_failure_alert(tmp_path):
    from app.collectors.base import CollectorError
    from app.services.poller import ALERT_AFTER_FAILURES

    db = Database(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    await db.create_all()
    api, notifier = FakePrykarpattya(), FakeNotifier()
    t0 = datetime(2026, 11, 20, 12, 0, tzinfo=TZ)

    # кожен виклик — новий Poller, як на Vercel (новий запит = нові об'єкти)
    assert await Poller(db, api, notifier).tick_if_due(300, t0) is True
    assert await Poller(db, api, notifier).tick_if_due(300, t0 + timedelta(minutes=1)) is False
    assert await Poller(db, api, notifier).tick_if_due(300, t0 + timedelta(minutes=5)) is True

    class Broken:
        async def fetch(self, *_):
            raise CollectorError("down")

    for _ in range(ALERT_AFTER_FAILURES):
        await Poller(db, Broken(), notifier, admin_id=7).tick()
    assert notifier.sent == [(7, notifier.sent[0][1])] and "не відповідає" in notifier.sent[0][1]
    await db.engine.dispose()
