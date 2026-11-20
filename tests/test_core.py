from datetime import date, datetime

import pytest

from app.collectors.base import CollectorError
from app.collectors.prykarpattya import parse_queue
from app.config import TZ
from app.core.diff import ChangeKind, compare
from app.core.formatter import format_change, format_schedule, now_line, timeline
from app.core.models import DaySchedule, DayStatus, Interval, to_events

D = date(2026, 11, 9)


# справжня відповідь be-svitlo.oe.if.ua/schedule-by-queue?queue=1.1 (20.11.2026), скорочена
LIVE = [{
    "eventDate": "20.11.2026",
    "queues": {"1.1": [
        {"shutdownHours": "10:30-13:30", "from": "10:30", "to": "13:30", "status": 1},
        {"shutdownHours": "15:30-19:00", "from": "15:30", "to": "19:00", "status": 1},
        {"shutdownHours": "21:00-22:30", "from": "21:00", "to": "22:30", "status": 1},
    ]},
    "createdAt": "20.11.2026 07:43",
    "scheduleApprovedSince": "20.11.2026 13:35",
}]


def test_parse_live_response():
    [day] = parse_queue(LIVE, "1.1", date(2026, 11, 20))
    assert day.day == date(2026, 11, 20) and day.status == DayStatus.SCHEDULE
    assert day.outages == (Interval(630, 810), Interval(930, 1140), Interval(1260, 1350))


def test_parse_two_days_merge_and_midnight():
    raw = [
        {"eventDate": "21.11.2026", "queues": {"3.2": []}},
        {"eventDate": "20.11.2026", "queues": {"3.2": [
            {"from": "06:00", "to": "08:00"}, {"from": "08:00", "to": "09:30"},
            {"shutdownHours": "22:00-00:00"},
        ]}},
    ]
    today, tomorrow = parse_queue(raw, "3.2", date(2026, 11, 20))
    assert today.outages == (Interval(360, 570), Interval(1320, 1440))
    assert tomorrow.day == date(2026, 11, 21) and tomorrow.status == DayStatus.NO_OUTAGES


def test_parse_empty_means_no_outages():
    assert parse_queue([], "1.1", D) == [DaySchedule(D, DayStatus.NO_OUTAGES, ())]


@pytest.mark.parametrize("raw", [
    [{"foo": "bar"}],
    [{"eventDate": "20.11.2026", "queues": {"1.1": [{"from": "xx", "to": "13:30"}]}}],
    [{"eventDate": "2026-11-20", "queues": {}}],
])
def test_parse_rejects_unrecognized_shape(raw):
    with pytest.raises(CollectorError):
        parse_queue(raw, "1.1", D)


def test_diff_minutes():
    old = DaySchedule(D, DayStatus.SCHEDULE, (Interval(360, 540),))       # 06:00–09:00
    new = DaySchedule(D, DayStatus.SCHEDULE, (Interval(420, 600),))       # 07:00–10:00
    ch = compare(old, new)
    assert ch.kind == ChangeKind.CHANGED
    assert ch.became_off == [Interval(540, 600)]
    assert ch.became_on == [Interval(360, 420)]
    assert compare(new, new).kind == ChangeKind.NONE


def test_diff_published():
    waiting = DaySchedule(D, DayStatus.WAITING)
    new = DaySchedule(D, DayStatus.SCHEDULE, (Interval(0, 120),))
    assert compare(waiting, new).kind == ChangeKind.PUBLISHED
    assert compare(None, waiting).kind == ChangeKind.NONE


def test_events_cross_midnight():
    d1 = DaySchedule(D, DayStatus.SCHEDULE, (Interval(1320, 1440),))
    d2 = DaySchedule(date(2026, 11, 10), DayStatus.SCHEDULE, (Interval(0, 120),))
    ev = to_events([d2, d1])
    assert len(ev) == 1
    assert ev[0].start.hour == 22 and ev[0].end.hour == 2


def test_now_line():
    day = DaySchedule(D, DayStatus.SCHEDULE, (Interval(960, 1140),))  # 16:00–19:00
    before = datetime(2026, 11, 9, 14, 40, tzinfo=TZ)
    during = datetime(2026, 11, 9, 17, 0, tzinfo=TZ)
    assert "о 16:00 (через 1 год 20 хв)" in now_line([day], before)
    assert "увімкнуть о 19:00 (через 2 год)" in now_line([day], during)


def test_timeline_partial_hours():
    day = DaySchedule(D, DayStatus.SCHEDULE, (Interval(360, 570),))  # 06:00–09:30
    bar = timeline(day).split("\n")[0].removeprefix("<code>")
    assert bar[5] == "░" and bar[6] == "█" and bar[9] == "▓" and len(bar) == 24


def test_format_outputs():
    now = datetime(2026, 11, 9, 14, 30, tzinfo=TZ)
    day = DaySchedule(D, DayStatus.SCHEDULE, (Interval(360, 570), Interval(960, 1140)))
    subtitle = "Івано-Франківськ та область · графік вимкнення світла за групою 3.2"
    text = format_schedule(title="Дім", subtitle=subtitle, day=day, now=now)
    print("\n" + text)
    assert "Сьогодні, пн 9 листопада" in text
    assert "06:00–09:30" in text and "Без світла: <b>6 год 30 хв</b>" in text

    old = DaySchedule(D, DayStatus.SCHEDULE, (Interval(360, 570),))
    msg = format_change("Дім", subtitle, compare(old, day), now)
    print("\n" + msg)
    assert "Графік змінився" in msg and "➕ Додано відключення: 16:00–19:00" in msg
