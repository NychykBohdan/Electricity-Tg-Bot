"""Формування повідомлень (Telegram HTML)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from html import escape

from app.core.diff import ChangeKind, DayChange
from app.core.models import DaySchedule, DayStatus, Event, Interval, to_events

WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "нд"]
MONTHS = [
    "січня", "лютого", "березня", "квітня", "травня", "червня",
    "липня", "серпня", "вересня", "жовтня", "листопада", "грудня",
]

MARK_PAST = "✔︎"
MARK_NOW = "▶"
MARK_NEXT = "  "


def hm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def interval_str(it: Interval) -> str:
    return f"{hm(it.start)}–{hm(it.end)}"


def duration(minutes: int) -> str:
    h, m = divmod(max(0, minutes), 60)
    if h and m:
        return f"{h} год {m} хв"
    if h:
        return f"{h} год"
    return f"{m} хв"


def countdown(minutes: int) -> str:
    """Скільки лишилось: «1 год 03 хв», «2 год», «45 хв»."""
    h, m = divmod(max(0, minutes), 60)
    if h and m:
        return f"{h} год {m:02d} хв"
    if h:
        return f"{h} год"
    return f"{m} хв"


def length(minutes: int) -> str:
    """Тривалість періоду: «3 год», «3,5 год», «45 хв», «1 год 15 хв»."""
    h, m = divmod(max(0, minutes), 60)
    if h and m == 30:
        return f"{h},5 год"
    return countdown(minutes)


def day_label(d: date, today: date) -> str:
    prefix = {0: "Сьогодні", 1: "Завтра"}.get((d - today).days)
    base = f"{WEEKDAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}"
    return f"{prefix}, {base}" if prefix else base.capitalize()


def day_header(d: date, today: date) -> str:
    """«Пт, 20 листопада», для завтра — «Сб, 21 листопада (завтра)»."""
    text = f"{WEEKDAYS[d.weekday()].capitalize()}, {d.day} {MONTHS[d.month - 1]}"
    return text + " (завтра)" if (d - today).days == 1 else text


def segments(day: DaySchedule) -> list[tuple[bool, Interval]]:
    """Розбиває добу на (is_off, інтервал) — і години зі світлом, і без."""
    out: list[tuple[bool, Interval]] = []
    cur = 0
    for it in day.outages:
        if it.start > cur:
            out.append((False, Interval(cur, it.start)))
        out.append((True, it))
        cur = it.end
    if cur < 1440:
        out.append((False, Interval(cur, 1440)))
    return out


def _minutes(a: datetime, b: datetime) -> int:
    return int((b - a).total_seconds() // 60)


def _clock(dt: datetime, now: datetime) -> str:
    """«о 17:30» або «завтра о 02:00»."""
    return f"{'' if dt.date() == now.date() else 'завтра '}о {dt:%H:%M}"


def _event_range(ev: Event) -> str:
    end = "24:00" if ev.end.timetz().replace(tzinfo=None) == time(0) else f"{ev.end:%H:%M}"
    return f"{ev.start:%H:%M}–{end}"


def status_block(days: list[DaySchedule], now: datetime) -> list[str]:
    """Що зараз і що далі:

    🔴 Світла немає · ще 1 год 03 хв
       увімкнуть о 17:30

    ⏭ Наступне: 19:00–22:30 (3,5 год)
    """
    events = to_events(days)
    current = next((ev for ev in events if ev.start <= now < ev.end), None)
    upcoming = next((ev for ev in events if ev.start > now), None)

    if current:
        lines = [
            f"🔴 <b>Світла немає</b> · ще {countdown(_minutes(now, current.end))}",
            f"   увімкнуть {_clock(current.end, now)}",
        ]
    elif upcoming:
        lines = [
            f"🟢 <b>Світло є</b> · ще {countdown(_minutes(now, upcoming.start))}",
            f"   вимкнуть {_clock(upcoming.start, now)}",
        ]
    else:
        lines = ["🟢 <b>Світло є</b>", "   відключень найближчим часом не заплановано"]

    if upcoming:
        when = "" if upcoming.start.date() == now.date() else "завтра "
        span = length(_minutes(upcoming.start, upcoming.end))
        lines += ["", f"⏭ Наступне: {when}{_event_range(upcoming)} ({span})"]
    return lines


def day_block(day: DaySchedule | None, target: date, group: str, now: datetime, view: str = "all") -> list[str]:
    """Графік на день:

    📅 Пт, 20 листопада · група 6.2
    ✔︎ 08:30–12:00
    ▶ 15:30–17:30  ← зараз
      19:00–22:30
    Всього без світла: 9 год

    view: "all" — години зі світлом і без, "on" — лише зі світлом, "off" — лише без світла.
    """
    lines = [f"📅 <b>{day_header(target, now.date())}</b> · група {escape(group)}"]
    if day is None or day.status == DayStatus.WAITING:
        return lines + ["⏳ Графік ще не опубліковано. Я напишу, щойно він з'явиться."]
    if day.status == DayStatus.EMERGENCY:
        return lines + ["⚠️ <b>Діють аварійні відключення.</b> Графік може не виконуватись."]
    if day.status == DayStatus.NO_OUTAGES or not day.outages:
        return lines + ["✅ Відключень не заплановано"]

    is_today = day.day == now.date()
    now_min = now.hour * 60 + now.minute
    for is_off, it in segments(day):
        if (view == "on" and is_off) or (view == "off" and not is_off):
            continue
        dot = ("🔴 " if is_off else "🟢 ") if view == "all" else ""
        if not is_today:
            lines.append(f"{dot}{interval_str(it)}")
        elif it.end <= now_min:
            lines.append(f"{MARK_PAST} {dot}{interval_str(it)}")
        elif it.start <= now_min:
            lines.append(f"{MARK_NOW} {dot}<b>{interval_str(it)}</b>  ← зараз")
        else:
            lines.append(f"{MARK_NEXT}{dot}{interval_str(it)}")

    on_minutes = 1440 - day.total_off_minutes
    if view == "on":
        if not on_minutes:
            lines.append("🔴 Світла не буде цілу добу")
        lines.append(f"Всього зі світлом: <b>{length(on_minutes)}</b>")
    else:
        lines.append(f"Всього без світла: <b>{length(day.total_off_minutes)}</b>")
    return lines


def format_schedule(
    *,
    title: str,
    group: str,
    day: DaySchedule | None,
    now: datetime,
    context_days: list[DaySchedule] | None = None,
    target: date | None = None,
    view: str = "all",
) -> str:
    target = target or (day.day if day else now.date())
    lines = [f"📍 <b>{escape(title)}</b>", ""]
    # «що зараз» — лише для сьогодні і коли графік справді діє
    if target == now.date() and day and day.status in (DayStatus.SCHEDULE, DayStatus.NO_OUTAGES):
        lines += status_block(context_days or [day], now) + [""]
    lines += day_block(day, target, group, now, view)
    return "\n".join(lines)


def format_change(title: str, group: str, ch: DayChange, now: datetime, view: str = "all") -> str:
    label = day_label(ch.new.day, now.date())
    head = [f"📍 <b>{escape(title)}</b>"]

    if ch.kind == ChangeKind.PUBLISHED:
        head.append(f"📅 <b>Опубліковано графік: {label}</b>")
    else:
        head.append(f"🔄 <b>Графік змінився: {label}</b>")
        if ch.new.status == DayStatus.EMERGENCY:
            head.append("⚠️ Діють аварійні відключення!")
        elif ch.new.status == DayStatus.NO_OUTAGES and ch.status_changed:
            head.append("✅ Відключення скасовано")
        if ch.became_off:
            head.append("➕ Додано відключення: " + ", ".join(interval_str(i) for i in ch.became_off))
        if ch.became_on:
            head.append("➖ Скасовано: " + ", ".join(interval_str(i) for i in ch.became_on))

    return "\n".join(head + [""] + day_block(ch.new, ch.new.day, group, now, view))


def format_reminder(title: str, ev: Event, now: datetime) -> str:
    left = max(1, round((ev.start - now).total_seconds() / 60))
    return (
        f"⏰ <b>Відключення через {duration(left)}</b>\n"
        f"📍 {escape(title)}\n"
        f"🔴 {ev.start:%H:%M}–{ev.end:%H:%M} ({duration(int((ev.end - ev.start).total_seconds() // 60))})"
    )


def format_restore_reminder(title: str, ev: Event, now: datetime) -> str:
    left = max(1, round((ev.end - now).total_seconds() / 60))
    return (
        f"💡 <b>Світло з'явиться через {duration(left)}</b>\n"
        f"📍 {escape(title)}\n"
        f"🟢 о {ev.end:%H:%M} за графіком (відключення {ev.start:%H:%M}–{ev.end:%H:%M})"
    )


def tomorrow(d: date) -> date:
    return d + timedelta(days=1)
