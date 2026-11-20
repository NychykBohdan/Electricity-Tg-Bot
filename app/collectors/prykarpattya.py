"""Колектор Прикарпаттяобленерго (Івано-Франківськ та область).

Використовує публічне JSON API сайту svitlo.oe.if.ua (бекенд be-svitlo.oe.if.ua):
- POST /schedule-by-search — визначити чергу за особовим рахунком.
  accountNumber=<номер>&userSearchChoice=pob&address=
  -> {"current": {"queue": "6", "subQueue": "2", "hasQueue": "yes", ...}, "schedule": [...]}
- GET  /schedule-by-queue?queue=X.Y — розклад відключень для черги.
  [] — відключень немає. Інакше — по елементу на кожну дату (підтверджено живим запитом 20.11.2026):
  [{"eventDate": "20.11.2026",
    "queues": {"1.1": [{"shutdownHours": "10:30-13:30", "from": "10:30", "to": "13:30", "status": 1}, ...]},
    "createdAt": "20.11.2026 07:43", "scheduleApprovedSince": "20.11.2026 13:35"}]
  Значення поля status не задокументоване (бачили лише 1) — інтервали беремо всі.
  На нерозпізнаних даних парсер падає з CollectorError, а не вигадує час.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any

import aiohttp

from app.collectors.base import CollectorError, SourceData
from app.config import TZ
from app.core.models import DaySchedule, DayStatus, Interval, merge_intervals
from app.regions import GROUPS

log = logging.getLogger(__name__)

BASE = "https://be-svitlo.oe.if.ua"
SEARCH_URL = BASE + "/schedule-by-search"
QUEUE_URL = BASE + "/schedule-by-queue"

HEADERS = {
    "User-Agent": "svitlo-tg-bot/0.1",
    "Accept": "application/json",
    "Origin": "https://svitlo.oe.if.ua",
    "Referer": "https://svitlo.oe.if.ua/",
}
TIMEOUT = aiohttp.ClientTimeout(total=20)


class PrykarpattyaClient:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def resolve_queue_by_account(self, account_number: str) -> str | None:
        """Повертає чергу у форматі "6.2", або None якщо рахунок не знайдено."""
        try:
            async with self.session.post(
                SEARCH_URL,
                data={"accountNumber": account_number, "userSearchChoice": "pob", "address": ""},
                headers=HEADERS,
                timeout=TIMEOUT,
            ) as r:
                r.raise_for_status()
                data = await r.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as e:
            raise CollectorError(f"Prykarpattya account lookup failed: {e}") from e
        current = data.get("current") if isinstance(data, dict) else None
        if not isinstance(current, dict) or current.get("hasQueue") != "yes":
            return None
        queue, sub = current.get("queue"), current.get("subQueue")
        if not queue or not sub:
            return None
        return f"{queue}.{sub}"

    async def _fetch_queue(self, group: str) -> list[Any]:
        try:
            async with self.session.get(
                QUEUE_URL, params={"queue": group}, headers=HEADERS, timeout=TIMEOUT
            ) as r:
                r.raise_for_status()
                data = await r.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as e:
            raise CollectorError(f"Prykarpattya queue fetch failed ({group}): {e}") from e
        return data if isinstance(data, list) else []

    async def fetch(self, *_ignored: Any) -> SourceData:
        """Опитує всі 12 черг паралельно. *_ignored — для сумісності з викликом Poller."""
        today = datetime.now(TZ).date()  # не date.today(): на Vercel годинник у UTC
        raws = await asyncio.gather(*(self._fetch_queue(g) for g in GROUPS))
        result: SourceData = {}
        for group, raw in zip(GROUPS, raws):
            try:
                result[group] = parse_queue(raw, group, today)
            except CollectorError as e:
                # дані для цієї черги незрозумілі — пропускаємо лише її, решта черг не страждають
                log.error("Skipping queue %s this tick: %s", group, e)
        return result


def parse_queue(raw: list[Any], group: str, today: date) -> list[DaySchedule]:
    """Відповідь /schedule-by-queue → графік по днях."""
    if not raw:
        return [DaySchedule(today, DayStatus.NO_OUTAGES, ())]
    days: dict[date, DaySchedule] = {}
    for entry in raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("queues"), dict):
            raise CollectorError(f"unrecognized schedule entry for queue {group}: {entry!r:.200}")
        try:
            day = datetime.strptime(str(entry.get("eventDate")), "%d.%m.%Y").date()
        except ValueError as e:
            raise CollectorError(f"bad eventDate for queue {group}: {entry.get('eventDate')!r}") from e
        items = entry["queues"].get(group) or []
        intervals = []
        for item in items:
            interval = _parse_interval(item)
            if interval is None:
                raise CollectorError(f"unrecognized interval for queue {group}: {item!r:.200}")
            intervals.append(interval)
        if intervals:
            days[day] = DaySchedule(day, DayStatus.SCHEDULE, merge_intervals(intervals))
        else:
            days[day] = DaySchedule(day, DayStatus.NO_OUTAGES, ())
    return [days[d] for d in sorted(days)]


def _minutes(hhmm: Any) -> int:
    h, m = str(hhmm).strip().split(":")[:2]
    value = int(h) * 60 + int(m)
    if not 0 <= value <= 1440:
        raise ValueError(hhmm)
    return value


def _parse_interval(item: Any) -> Interval | None:
    if not isinstance(item, dict):
        return None
    start, end = item.get("from"), item.get("to")
    if (start is None or end is None) and isinstance(item.get("shutdownHours"), str):
        start, _, end = item["shutdownHours"].partition("-")
    try:
        s, e = _minutes(start), _minutes(end)
    except (ValueError, AttributeError, TypeError):
        return None
    if e == 0 and s > 0:  # "22:00-00:00" — до кінця доби
        e = 1440
    if e <= s:
        return None
    return Interval(s, e)
