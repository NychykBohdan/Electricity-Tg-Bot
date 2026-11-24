"""Формування повідомлень (Telegram HTML)."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from html import escape

from app.core.diff import ChangeKind, DayChange
from app.core.models import DaySchedule, DayStatus, Event, Interval, to_events

WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "нд"]
MONTHS = [
    "січня", "лютого", "березня", "квітня", "травня", "червня",
    "липня", "серпня", "вересня", "жовтня", "листопада", "грудня",
]


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


def day_label(d: date, today: date) -> str:
    prefix = {0: "Сьогодні", 1: "Завтра"}.get((d - today).days)
    base = f"{WEEKDAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}"
    return f"{prefix}, {base}" if prefix else base.capitalize()


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


def now_line(days: list[DaySchedule], now: datetime) -> str:
    events: list[Event] = to_events(days)
    for ev in events:
        if ev.start <= now < ev.end:
            left = int((ev.end - now).total_seconds() // 60)
            return f"🔴 <b>Зараз світла немає</b> · увімкнуть о {ev.end:%H:%M} (через {duration(left)})"
    for ev in events:
        if ev.start > now:
            left = int((ev.start - now).total_seconds() // 60)
            when = "" if ev.start.date() == now.date() else "завтра "
            return f"🟢 Зараз світло є · відключення {when}о {ev.start:%H:%M} (через {duration(left)})"
    return "🟢 Зараз світло є · відключень найближчим часом не заплановано"


SEPARATOR = "----------------------"


def day_word(d: date, today: date) -> str:
    return {0: "Сьогодні", 1: "Завтра"}.get((d - today).days, f"{d.day} {MONTHS[d.month - 1]}")


def sections(*parts: list[str]) -> str:
    """Склеює блоки повідомлення через горизонтальний роздільник; порожні блоки пропускає."""
    return f"\n{SEPARATOR}\n".join("\n".join(p) for p in parts if p)


def hours_block(day: DaySchedule, now: datetime, view: str = "all", period: str = "day") -> tuple[list[str], str | None]:
    """Перелік періодів дня і підсумок («⚡️ Сьогодні без світла: 9 год»; None — якщо рахувати нічого).

    view: "all" — години зі світлом і без, "on" — лише зі світлом, "off" — лише без світла.
    period: "day" — уся доба, "now" — поточний період і всі наступні, без минулих (для сьогодні)."""
    if day.status == DayStatus.WAITING:
        return ["⏳ Графік ще не опубліковано"], None
    if day.status == DayStatus.EMERGENCY:
        return ["⚠️ <b>Діють аварійні відключення.</b> Графік може не виконуватись."], None
    if day.status == DayStatus.NO_OUTAGES or not day.outages:
        return ["✅ Відключень не заплановано"], None

    lines: list[str] = []
    on_minutes = 1440 - day.total_off_minutes
    # хвилина доби «зараз» — лише для сьогодні; для завтра нічого не закреслюємо
    now_min = now.hour * 60 + now.minute if day.day == now.date() else -1
    rows: list[tuple[bool, str]] = []  # (уже минуло, рядок)
    for is_off, it in segments(day):
        if (view == "on" and is_off) or (view == "off" and not is_off):
            continue
        icon = "🔴" if is_off else "🟢"
        note = "  без світла" if is_off else ("  зі світлом" if view == "on" else "")
        if it.end <= now_min:  # уже минуло — закреслюємо
            rows.append((True, f"{icon} <s>{interval_str(it)}{note}</s>"))
        elif is_off or view == "on":
            rows.append((False, f"{icon} <b>{interval_str(it)}</b>{note}"))
        else:
            rows.append((False, f"{icon} {interval_str(it)}"))
    if period == "now" and now_min >= 0:
        # ховаємо лише минулі періоди — жодне майбутнє відключення не губимо; на завтра графік завжди повний
        rows = [r for r in rows if not r[0]]
        if not rows and on_minutes:
            lines.append("🔴 Сьогодні світла вже не буде" if view == "on" else "✅ Сьогодні відключень більше немає")
    for i, (_, row) in enumerate(rows):
        if i:
            lines.append("")  # порожній рядок між періодами — щоб легше читалось
        lines.append(row)
    if view == "on" and not on_minutes:
        lines.append("🔴 Світла не буде цілу добу")

    word = day_word(day.day, now.date())
    summary = (
        f"💡 {word} зі світлом: <b>{duration(on_minutes)}</b>"
        if view == "on"
        else f"⚡️ {word} без світла: <b>{duration(day.total_off_minutes)}</b>"
    )
    return lines, summary


def format_schedule(
    *,
    title: str,
    subtitle: str,
    day: DaySchedule | None,
    now: datetime,
    context_days: list[DaySchedule] | None = None,
    updated_at: datetime | None = None,
    target: date | None = None,
    view: str = "all",
    period: str = "day",
) -> str:
    """Графік на день, трьома блоками через роздільник:
    адреса / дата й перелік годин / що зараз, підсумок за день, час оновлення."""
    head = [f"📍 <b>{escape(title)}</b>", f"<i>{escape(subtitle)}</i>"]
    if day is None:
        hours = [f"📅 <b>{day_label(target, now.date())}</b>", ""] if target else []
        return sections(head, hours + ["⏳ Графік ще не опубліковано. Я напишу, щойно він з'явиться."])

    rows, summary = hours_block(day, now, view, period)
    hours = [f"📅 <b>{day_label(day.day, now.date())}</b>", "", *rows]

    rest: list[str] = []
    if day.day == now.date():
        rest.append(now_line(context_days or [day], now))
    tail = [x for x in (summary, f"<i>Оновлено о {updated_at:%H:%M}</i>" if updated_at else None) if x]
    if tail:
        rest += ([""] if rest else []) + tail   # підсумок і час оновлення — поруч
    return sections(head, hours, rest)


def format_change(
    title: str, subtitle: str, ch: DayChange, now: datetime, view: str = "all", period: str = "day"
) -> str:
    label = day_label(ch.new.day, now.date())
    head = [f"📍 <b>{escape(title)}</b> · <i>{escape(subtitle)}</i>"]

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

    rows, summary = hours_block(ch.new, now, view, period)
    rest: list[str] = []
    if ch.new.day == now.date():
        rest.append(now_line([ch.new], now))
    if summary:
        rest += ([""] if rest else []) + [summary]
    return sections(head, rows, rest)


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
