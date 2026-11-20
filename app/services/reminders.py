"""Нагадування за N хвилин до відключення."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from app.config import TZ
from app.core.formatter import format_reminder
from app.core.models import Event, to_events
from app.db.repo import Database, snapshot_to_day
from app.services.notifier import Notifier

log = logging.getLogger(__name__)


async def load_events(db: Database, source_key: str, group: str, now: datetime) -> list[Event]:
    today = now.date()
    rows = await db.get_days(source_key, group, [today, today + timedelta(days=1)])
    return to_events([snapshot_to_day(r) for r in rows.values()])


async def check_reminders(db: Database, notifier: Notifier, now: datetime | None = None) -> int:
    now = now or datetime.now(TZ)
    sent = 0
    cache: dict[tuple[str, str], list[Event]] = {}
    for sub in await db.all_subscriptions():
        if sub.remind_minutes <= 0:
            continue
        k = (sub.source_key, sub.group)
        if k not in cache:
            cache[k] = await load_events(db, sub.source_key, sub.group, now)
        horizon = now + timedelta(minutes=sub.remind_minutes)
        for ev in cache[k]:
            if now < ev.start <= horizon and await db.try_mark_reminder(sub.id, ev.start):
                if await notifier.send(sub.user_id, format_reminder(sub.title, ev, now)):
                    sent += 1
    return sent


async def run(db: Database, notifier: Notifier, interval: int) -> None:
    while True:
        try:
            await check_reminders(db, notifier)
        except Exception:
            log.exception("Reminder check failed")
        await asyncio.sleep(interval)
