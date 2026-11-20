"""Порівняння двох версій графіка одного дня."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.core.models import DaySchedule, DayStatus, Interval

DAY_MINUTES = 1440


class ChangeKind(StrEnum):
    NONE = "none"
    PUBLISHED = "published"   # з'явився графік (раніше не було або "очікується")
    CHANGED = "changed"


@dataclass
class DayChange:
    kind: ChangeKind
    new: DaySchedule
    old: DaySchedule | None = None
    became_off: list[Interval] = field(default_factory=list)   # нові години без світла
    became_on: list[Interval] = field(default_factory=list)    # скасовані відключення
    status_changed: bool = False


def _mask(day: DaySchedule | None) -> list[bool]:
    m = [False] * DAY_MINUTES
    if day and day.status == DayStatus.SCHEDULE:
        for it in day.outages:
            for i in range(max(0, it.start), min(DAY_MINUTES, it.end)):
                m[i] = True
    return m


def _runs(flags: list[bool]) -> list[Interval]:
    out: list[Interval] = []
    start = None
    for i, f in enumerate(flags + [False]):
        if f and start is None:
            start = i
        elif not f and start is not None:
            out.append(Interval(start, i))
            start = None
    return out


def compare(old: DaySchedule | None, new: DaySchedule) -> DayChange:
    if old is not None and old.fingerprint() == new.fingerprint():
        return DayChange(ChangeKind.NONE, new, old)

    if old is None or old.status == DayStatus.WAITING:
        if new.status == DayStatus.WAITING:
            return DayChange(ChangeKind.NONE, new, old)
        return DayChange(ChangeKind.PUBLISHED, new, old)

    a, b = _mask(old), _mask(new)
    return DayChange(
        ChangeKind.CHANGED,
        new,
        old,
        became_off=_runs([not x and y for x, y in zip(a, b)]),
        became_on=_runs([x and not y for x, y in zip(a, b)]),
        status_changed=old.status != new.status,
    )
