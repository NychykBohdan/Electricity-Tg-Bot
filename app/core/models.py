"""Єдиний (нормалізований) формат графіка, незалежний від джерела."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum

from app.config import TZ


class DayStatus(StrEnum):
    SCHEDULE = "schedule"       # діє графік
    NO_OUTAGES = "no_outages"   # відключень не заплановано
    WAITING = "waiting"         # графік ще не опубліковано
    EMERGENCY = "emergency"     # аварійні відключення, графік не діє


@dataclass(frozen=True, order=True)
class Interval:
    """Відключення з start до end (хвилини від початку доби, end ≤ 1440)."""
    start: int
    end: int

    @property
    def minutes(self) -> int:
        return self.end - self.start

    def to_dt(self, day: date) -> tuple[datetime, datetime]:
        base = datetime.combine(day, time(0), tzinfo=TZ)
        return base + timedelta(minutes=self.start), base + timedelta(minutes=self.end)


@dataclass(frozen=True)
class DaySchedule:
    day: date
    status: DayStatus
    outages: tuple[Interval, ...] = ()

    @property
    def total_off_minutes(self) -> int:
        return sum(i.minutes for i in self.outages)

    def fingerprint(self) -> str:
        payload = json.dumps(
            {"s": self.status.value, "o": [[i.start, i.end] for i in self.outages]},
            separators=(",", ":"),
        )
        return hashlib.sha1(payload.encode()).hexdigest()

    def outages_json(self) -> list[list[int]]:
        return [[i.start, i.end] for i in self.outages]

    @classmethod
    def from_stored(cls, day: date, status: str, outages: list[list[int]]) -> DaySchedule:
        return cls(day, DayStatus(status), tuple(Interval(s, e) for s, e in outages))


def merge_intervals(intervals: list[Interval]) -> tuple[Interval, ...]:
    """Склеює інтервали, що перетинаються або йдуть впритул."""
    result: list[Interval] = []
    for it in sorted(intervals):
        if result and it.start <= result[-1].end:
            last = result[-1]
            result[-1] = Interval(last.start, max(last.end, it.end))
        else:
            result.append(it)
    return tuple(result)


@dataclass(frozen=True)
class Event:
    """Відключення в абсолютному часі (може переходити через північ)."""
    start: datetime
    end: datetime


def to_events(days: list[DaySchedule]) -> list[Event]:
    """Переводить кілька днів у список подій, склеюючи 23:00–24:00 + 00:00–02:00."""
    events: list[Event] = []
    for d in sorted(days, key=lambda x: x.day):
        if d.status != DayStatus.SCHEDULE:
            continue
        for it in d.outages:
            s, e = it.to_dt(d.day)
            if events and events[-1].end >= s:
                events[-1] = Event(events[-1].start, max(events[-1].end, e))
            else:
                events.append(Event(s, e))
    return events
