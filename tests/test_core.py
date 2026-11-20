from datetime import date, datetime

import pytest

from app.collectors.base import CollectorError
from app.collectors.prykarpattya import parse_queue
from app.config import TZ
from app.core.diff import ChangeKind, compare
from app.core.formatter import format_change, format_schedule, length, status_block
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


def test_lengths():
    assert [length(m) for m in (210, 540, 45, 75, 60)] == ["3,5 год", "9 год", "45 хв", "1 год 15 хв", "1 год"]


def test_status_block():
    day = DaySchedule(D, DayStatus.SCHEDULE, (Interval(960, 1140),))  # 16:00–19:00
    before = "\n".join(status_block([day], datetime(2026, 11, 9, 14, 40, tzinfo=TZ)))
    assert "Світло є</b> · ще 1 год 20 хв" in before and "вимкнуть о 16:00" in before
    assert "⏭ Наступне: 16:00–19:00 (3 год)" in before
    during = "\n".join(status_block([day], datetime(2026, 11, 9, 17, 0, tzinfo=TZ)))
    assert "Світла немає</b> · ще 2 год" in during and "увімкнуть о 19:00" in during
    assert "Наступне" not in during
    after = "\n".join(status_block([day], datetime(2026, 11, 9, 20, 0, tzinfo=TZ)))
    assert "не заплановано" in after


# приклад формату, який попросив користувач (пт 20 листопада, група 6.2, 16:27)
EXAMPLE = """📍 <b>Дім</b>

🔴 <b>Світла немає</b> · ще 1 год 03 хв
   увімкнуть о 17:30

⏭ Наступне: 19:00–22:30 (3,5 год)

📅 <b>Пт, 20 листопада</b> · група 6.2
✔︎ 08:30–12:00
▶ <b>15:30–17:30</b>  ← зараз
  19:00–22:30
Всього без світла: <b>9 год</b>"""


def test_format_outputs():
    d = date(2026, 11, 20)
    now = datetime(2026, 11, 20, 16, 27, tzinfo=TZ)
    day = DaySchedule(d, DayStatus.SCHEDULE, (Interval(510, 720), Interval(930, 1050), Interval(1140, 1350)))
    text = format_schedule(title="Дім", group="6.2", day=day, now=now, view="off")
    print("\n" + text)
    assert text == EXAMPLE

    # усі години: і світло, і відключення, з позначками 🟢/🔴
    full = format_schedule(title="Дім", group="6.2", day=day, now=now)
    assert "✔︎ 🟢 00:00–08:30" in full and "▶ 🔴 <b>15:30–17:30</b>  ← зараз" in full
    assert "  🟢 17:30–19:00" in full
    # лише години зі світлом
    on_only = format_schedule(title="Дім", group="6.2", day=day, now=now, view="on")
    assert "✔︎ 12:00–15:30" in on_only and "15:30–17:30" not in on_only.split("📅")[1]
    assert "Всього зі світлом: <b>15 год</b>" in on_only
    full_off = DaySchedule(d, DayStatus.SCHEDULE, (Interval(0, 1440),))
    assert "Світла не буде цілу добу" in format_schedule(
        title="Дім", group="6.2", day=full_off, now=now, view="on"
    )
    # завтра — без позначок «минуло/зараз»
    tm = format_schedule(title="Дім", group="6.2", day=day, now=datetime(2026, 11, 19, 12, tzinfo=TZ), view="off")
    assert "Пт, 20 листопада (завтра)" in tm and "\n08:30–12:00" in tm and "Світла немає" not in tm

    old = DaySchedule(d, DayStatus.SCHEDULE, (Interval(510, 720),))
    msg = format_change("Дім", "6.2", compare(old, day), now, view="off")
    print("\n" + msg)
    assert "Графік змінився" in msg and "➕ Додано відключення: 15:30–17:30, 19:00–22:30" in msg
    assert "▶ <b>15:30–17:30</b>  ← зараз" in msg
