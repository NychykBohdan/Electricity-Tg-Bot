"""Нагадування: за N хвилин до відключення і за M хвилин до появи світла."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta

from app.config import TZ
from app.core.formatter import format_reminder, format_restore_reminder
from app.core.models import DayStatus, Event, to_events
from app.db.models import effective_prefs
from app.db.repo import Database, snapshot_to_day
from app.services.notifier import Notifier

log = logging.getLogger(__name__)


async def load_events(db: Database, source_key: str, group: str, now: datetime) -> tuple[list[Event], set[date]]:
    """Відключення на сьогодні/завтра + дні, графік яких уже відомий."""
    today = now.date()
    rows = await db.get_days(source_key, group, [today, today + timedelta(days=1)])
    days = [snapshot_to_day(r) for r in rows.values()]
    known = {d.day for d in days if d.status != DayStatus.WAITING}
    return to_events(days), known


def restore_is_certain(ev: Event, known: set[date]) -> bool:
    """Відключення до 24:00, а графіка на наступний день ще нема — воно може тривати й далі."""
    return ev.end.timetz().replace(tzinfo=None) != time(0) or ev.end.date() in known


async def check_reminders(db: Database, notifier: Notifier, now: datetime | None = None) -> int:
    now = now or datetime.now(TZ)
    sent = 0
    cache: dict[tuple[str, str], tuple[list[Event], set[date]]] = {}
    subs = await db.all_subscriptions()
    users = await db.users_by_ids({s.user_id for s in subs})
    for sub in subs:
        prefs = effective_prefs(users.get(sub.user_id), sub)
        before_off, before_on = prefs.remind_minutes, prefs.restore_remind_minutes
        if before_off <= 0 and before_on <= 0:
            continue
        k = (sub.source_key, sub.group)
        if k not in cache:
            cache[k] = await load_events(db, sub.source_key, sub.group, now)
        events, known = cache[k]
        for ev in events:
            if (
                before_off > 0
                and now < ev.start <= now + timedelta(minutes=before_off)
                and await db.try_mark_reminder(sub.id, "off", ev.start)
            ):
                if await notifier.send(sub.user_id, format_reminder(sub.title, ev, now)):
                    sent += 1
            if (
                before_on > 0
                and ev.start <= now < ev.end <= now + timedelta(minutes=before_on)  # світла зараз немає
                and restore_is_certain(ev, known)
                and await db.try_mark_reminder(sub.id, "on", ev.end)
            ):
                if await notifier.send(sub.user_id, format_restore_reminder(sub.title, ev, now)):
                    sent += 1
    return sent


async def run(db: Database, notifier: Notifier, interval: int) -> None:
    while True:
        try:
            await check_reminders(db, notifier)
        except Exception:
            log.exception("Reminder check failed")
        await asyncio.sleep(interval)
