from datetime import date, datetime, timedelta

import pytest

from app.collectors.base import CollectorError
from app.collectors.prykarpattya import parse_queue
from app.config import TZ
from app.core.diff import ChangeKind, compare
from app.core.formatter import SEPARATOR, format_change, format_schedule, now_line
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


def test_format_outputs():
    now = datetime(2026, 11, 9, 14, 30, tzinfo=TZ)
    day = DaySchedule(D, DayStatus.SCHEDULE, (Interval(360, 570), Interval(960, 1140)))
    subtitle = "Івано-Франківськ та область · графік вимкнення світла за групою 3.2"
    text = format_schedule(title="Дім", subtitle=subtitle, day=day, now=now, updated_at=now.replace(minute=25))
    print("\n" + text)
    # три блоки через роздільник: адреса / перелік годин / решта;
    # минулі періоди закреслені; підсумок за день — поруч із часом оновлення; шкали доби немає
    assert text == f"""📍 <b>Дім</b>
<i>{subtitle}</i>
{SEPARATOR}
📅 <b>Сьогодні, пн 9 листопада</b>

🟢 <s>00:00–06:00</s>

🔴 <s>06:00–09:30  без світла</s>

🟢 09:30–16:00

🔴 <b>16:00–19:00</b>  без світла

🟢 19:00–24:00
{SEPARATOR}
🟢 Зараз світло є · відключення о 16:00 (через 1 год 30 хв)

⚡️ Сьогодні без світла: <b>6 год 30 хв</b>
<i>Оновлено о 14:25</i>"""

    # завтра нічого не закреслено, підсумок — «Завтра без світла»
    tomorrow_text = format_schedule(title="Дім", subtitle=subtitle, day=day, now=now - timedelta(days=1))
    assert "<s>" not in tomorrow_text and "⚡️ Завтра без світла: <b>6 год 30 хв</b>" in tomorrow_text
    assert tomorrow_text.count(SEPARATOR) == 2 and "Зараз" not in tomorrow_text
    # графік ще не опубліковано — лише два блоки
    waiting = format_schedule(title="Дім", subtitle=subtitle, day=None, now=now, target=D)
    assert waiting.count(SEPARATOR) == 1 and waiting.endswith("Я напишу, щойно він з'явиться.")

    # лише години без світла
    off_only = format_schedule(title="Дім", subtitle=subtitle, day=day, now=now, view="off")
    assert "🔴 <s>06:00–09:30  без світла</s>" in off_only and "09:30–16:00" not in off_only
    # лише години зі світлом
    on_only = format_schedule(title="Дім", subtitle=subtitle, day=day, now=now, view="on")
    assert "🟢 <b>09:30–16:00</b>  зі світлом" in on_only and "🔴 <b>06:00" not in on_only
    assert "💡 Сьогодні зі світлом: <b>17 год 30 хв</b>" in on_only
    full_off = DaySchedule(D, DayStatus.SCHEDULE, (Interval(0, 1440),))
    assert "Світла не буде цілу добу" in format_schedule(
        title="Дім", subtitle=subtitle, day=full_off, now=now, view="on"
    )

    old = DaySchedule(D, DayStatus.SCHEDULE, (Interval(360, 570),))
    msg = format_change("Дім", subtitle, compare(old, day), now)
    print("\n" + msg)
    assert "Графік змінився" in msg and "➕ Додано відключення: 16:00–19:00" in msg
    assert msg.count(SEPARATOR) == 2 and msg.endswith("⚡️ Сьогодні без світла: <b>6 год 30 хв</b>")


def test_period_now():
    """Режим «зараз і далі»: минулі періоди приховано, усі наступні — показано."""
    day = DaySchedule(D, DayStatus.SCHEDULE, (Interval(360, 570), Interval(960, 1140)))
    at = lambda h, m=0: datetime(2026, 11, 9, h, m, tzinfo=TZ)  # noqa: E731

    def periods(text: str) -> list[str]:
        return [ln for ln in text.split("\n") if ln.startswith(("🟢 ", "🔴 ", "✅ ")) and "Зараз" not in ln]

    fmt = lambda now, **kw: format_schedule(title="Дім", subtitle="s", day=day, now=now, period="now", **kw)  # noqa: E731
    assert periods(fmt(at(14, 30))) == ["🟢 09:30–16:00", "🔴 <b>16:00–19:00</b>  без світла", "🟢 19:00–24:00"]
    assert periods(fmt(at(20))) == ["🟢 19:00–24:00"]               # останній період доби
    assert periods(fmt(at(7), view="off")) == [
        "🔴 <b>06:00–09:30</b>  без світла", "🔴 <b>16:00–19:00</b>  без світла",
    ]
    assert periods(fmt(at(20), view="off")) == ["✅ Сьогодні відключень більше немає"]
    assert "⚡️ Сьогодні без світла: <b>6 год 30 хв</b>" in fmt(at(20))           # підсумок за добу лишається
    # графік на завтра — завжди повний
    assert len(periods(fmt(at(14) - timedelta(days=1)))) == 5
    # у повідомленні про зміну — так само
    old = DaySchedule(D, DayStatus.SCHEDULE, (Interval(360, 570),))
    msg = format_change("Дім", "s", compare(old, day), at(14, 30), period="now")
    assert periods(msg) == ["🟢 09:30–16:00", "🔴 <b>16:00–19:00</b>  без світла", "🟢 19:00–24:00"]


def test_period_now_keeps_all_future_outages():
    """Регресія: три відключення за день — у режимі «зараз і далі» не можна губити третє."""
    day = DaySchedule(D, DayStatus.SCHEDULE, (Interval(660, 990), Interval(1110, 1200), Interval(1320, 1440)))
    now = datetime(2026, 11, 9, 10, 56, tzinfo=TZ)
    msg = format_schedule(title="Дім", subtitle="s", day=day, now=now, view="off", period="now")
    for it in ("11:00–16:30", "18:30–20:00", "22:00–24:00"):
        assert it in msg


def test_minutes_left_rounds_to_five():
    from datetime import datetime, timedelta
    from app.core.formatter import minutes_left
    t = datetime(2026, 1, 1, 12, 0)
    m = lambda mins: minutes_left(t + timedelta(minutes=mins), t)
    assert [m(9), m(9.3), m(14), m(29), m(31), m(12), m(13), m(60)] == [10, 10, 15, 30, 30, 10, 15, 60]
    assert [m(3), m(4.6), m(0.2)] == [3, 5, 1]
