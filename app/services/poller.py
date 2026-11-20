"""Періодичне опитування джерел, пошук змін і розсилка."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from app.collectors.base import CollectorError
from app.collectors.prykarpattya import PrykarpattyaClient
from app.config import TZ
from app.core.diff import ChangeKind, DayChange, compare
from app.core.formatter import format_change
from app.db.repo import Database, snapshot_to_day
from app.regions import City, all_sources, get_city
from app.services.notifier import Notifier

log = logging.getLogger(__name__)

ALERT_AFTER_FAILURES = 6  # ≈30 хв при інтервалі 5 хв
LAST_POLL_KEY = "poller:last_run"


def sub_subtitle(city_key: str, group: str) -> str:
    return f"{get_city(city_key).title} · графік вимкнення світла за групою {group}"


class Poller:
    def __init__(self, db: Database, client: PrykarpattyaClient, notifier: Notifier, admin_id: int | None = None):
        self.db = db
        self.client = client
        self.notifier = notifier
        self.admin_id = admin_id
        self._locks: dict[str, asyncio.Lock] = {}

    async def poll_source(self, key: str, city: City, provider_id: int) -> list[tuple[str, DayChange]]:
        """Тягне дані джерела, зберігає, повертає список змін (група, зміна)."""
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            data = await self.client.fetch(city, provider_id)
            first_time = not await self.db.has_source(key)
            now = datetime.now(TZ)
            changes: list[tuple[str, DayChange]] = []

            for group, days in data.items():
                for day in days:
                    row = await self.db.get_snapshot(key, group, day.day)
                    old = snapshot_to_day(row) if row else None
                    if old is not None and old.fingerprint() == day.fingerprint():
                        continue
                    await self.db.save_snapshot(key, group, day, now)
                    ch = compare(old, day)
                    if first_time or ch.kind == ChangeKind.NONE or day.day < now.date():
                        continue
                    changes.append((group, ch))
            if first_time:
                log.info("Seeded source %s (%d groups)", key, len(data))
            return changes

    async def notify(self, key: str, group: str, ch: DayChange) -> None:
        from app.bot.keyboards import schedule_kb  # уникаємо циклічного імпорту

        now = datetime.now(TZ)
        for sub in await self.db.subscriptions_for(key, group):
            if not sub.notify_changes:
                continue
            text = format_change(sub.title, sub_subtitle(sub.city_key, sub.group), ch, now)
            day_idx = (ch.new.day - now.date()).days
            await self.notifier.send(sub.user_id, text, schedule_kb(sub.id, day_idx))

    async def _bump_failures(self, key: str, reset: bool = False) -> int:
        """Лічильник збоїв поспіль — у БД, бо на Vercel процес між запусками не живе."""
        db_key = f"poller:failures:{key}"
        n = 0 if reset else int(await self.db.kv_get(db_key) or 0) + 1
        await self.db.kv_set(db_key, str(n))
        return n

    async def tick(self) -> None:
        for key, city, provider_id in all_sources():
            try:
                changes = await self.poll_source(key, city, provider_id)
                await self._bump_failures(key, reset=True)
            except CollectorError as e:
                n = await self._bump_failures(key)
                log.warning("Source %s failed (%d): %s", key, n, e)
                if n == ALERT_AFTER_FAILURES and self.admin_id:
                    await self.notifier.send(self.admin_id, f"⚠️ Джерело {key} не відповідає вже {n} разів поспіль\n{e}")
                continue
            for group, ch in changes:
                log.info("Change %s %s %s: %s", key, group, ch.new.day, ch.kind)
                await self.notify(key, group, ch)
        await self.db.cleanup(datetime.now(TZ).date() - timedelta(days=2))

    async def tick_if_due(self, interval: int, now: datetime | None = None) -> bool:
        """Для зовнішнього cron, що смикає бота щохвилини: опитує джерела не частіше за interval.

        Час запуску записуємо ДО опитування — якщо cron викличе нас удруге, поки перше
        опитування ще йде, друге його пропустить."""
        now = now or datetime.now(TZ)
        last = await self.db.kv_get(LAST_POLL_KEY)
        # запас 20 с: виклики cron приходять не точно щохвилини
        if last and (now - datetime.fromisoformat(last)).total_seconds() < interval - 20:
            return False
        await self.db.kv_set(LAST_POLL_KEY, now.isoformat())
        await self.tick()
        return True

    async def run(self, interval: int) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # фонова задача не повинна падати
                log.exception("Poller tick failed")
            await asyncio.sleep(interval)
