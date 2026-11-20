from aiogram.types import InlineKeyboardButton as B
from aiogram.types import InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from app.regions import GROUPS, City

BTN_SCHEDULE = "📅 Графік"
BTN_SUBS = "📍 Мої адреси"
BTN_ADD = "➕ Додати адресу"
BTN_HELP = "ℹ️ Допомога"

REMIND_STEPS = [5, 10, 15, 30]  # хвилини; 0 = вимкнено
# вид нагадування → (поле Subscription, заголовок кнопки, питання у виборі)
REMIND_KINDS = {
    "off": ("remind_minutes", "⏰ До відключення", "За скільки попередити, що світло <b>вимкнеться</b>?"),
    "on": ("restore_remind_minutes", "💡 До появи світла", "За скільки попередити, що світло <b>з'явиться</b>?"),
}
TITLE_PRESETS = ["🏠 Дім", "💼 Робота"]


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_SCHEDULE), KeyboardButton(text=BTN_SUBS)],
            [KeyboardButton(text=BTN_ADD), KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
    )


def _rows(buttons: list[B], per_row: int) -> list[list[B]]:
    return [buttons[i : i + per_row] for i in range(0, len(buttons), per_row)]


def cancel_row() -> list[B]:
    return [B(text="✖️ Скасувати", callback_data="cancel")]


def oblasts_kb(oblasts: list[str]) -> InlineKeyboardMarkup:
    btns = [B(text=o, callback_data=f"ob:{i}") for i, o in enumerate(oblasts)]
    return InlineKeyboardMarkup(inline_keyboard=_rows(btns, 2) + [cancel_row()])


def cities_kb(cities: list[City]) -> InlineKeyboardMarkup:
    btns = [B(text=c.title, callback_data=f"ct:{c.key}") for c in cities]
    return InlineKeyboardMarkup(inline_keyboard=_rows(btns, 2) + [cancel_row()])


def providers_kb(city: City) -> InlineKeyboardMarkup:
    btns = [[B(text=p.name, callback_data=f"pv:{p.id}")] for p in city.providers]
    return InlineKeyboardMarkup(inline_keyboard=btns + [cancel_row()])


def method_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [B(text="🧾 Знайти за особовим рахунком", callback_data="m:account")],
            [B(text="🔢 Я знаю свою чергу", callback_data="m:group")],
            cancel_row(),
        ]
    )


def groups_kb() -> InlineKeyboardMarkup:
    btns = [B(text=g, callback_data=f"g:{g}") for g in GROUPS]
    return InlineKeyboardMarkup(inline_keyboard=_rows(btns, 4) + [cancel_row()])


def title_kb() -> InlineKeyboardMarkup:
    btns = [B(text=t, callback_data=f"tt:{i}") for i, t in enumerate(TITLE_PRESETS)]
    return InlineKeyboardMarkup(inline_keyboard=[btns])


def schedule_kb(sub_id: int, day: int = 0) -> InlineKeyboardMarkup:
    mark = lambda d, t: f"• {t} •" if d == day else t  # noqa: E731
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                B(text=mark(0, "Сьогодні"), callback_data=f"sv:{sub_id}:0"),
                B(text=mark(1, "Завтра"), callback_data=f"sv:{sub_id}:1"),
                B(text="🔄", callback_data=f"sv:{sub_id}:{day}"),
            ]
        ]
    )


def subs_list_kb(subs, prefix: str) -> InlineKeyboardMarkup:
    btns = [[B(text=f"{s.title} · {s.group}", callback_data=f"{prefix}:{s.id}")] for s in subs]
    return InlineKeyboardMarkup(inline_keyboard=btns)


# режим показу графіка → (кнопка вибору, коротко для картки адреси)
SCHEDULE_VIEWS = {
    "all": ("🟢🔴 Години зі світлом і без", "усі години"),
    "on": ("🟢 Лише години зі світлом", "лише зі світлом"),
    "off": ("🔴 Лише години без світла", "лише без світла"),
}


def minutes_label(m: int) -> str:
    return f"{m} хв" if m else "вимк"


def sub_settings_kb(sub) -> InlineKeyboardMarkup:
    changes = "увімк" if sub.notify_changes else "вимк"
    remind_rows = [
        [B(text=f"{label}: {minutes_label(getattr(sub, field))}", callback_data=f"rm:{kind}:{sub.id}")]
        for kind, (field, label, _) in REMIND_KINDS.items()
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [B(text="📅 Показати графік", callback_data=f"sv:{sub.id}:0")],
            [B(text=f"🗓 Показ графіка: {view_label(sub.schedule_view)}", callback_data=f"vw:{sub.id}")],
            *remind_rows,
            [B(text=f"🔔 Сповіщення про зміни: {changes}", callback_data=f"nc:{sub.id}")],
            [B(text="🗑 Видалити", callback_data=f"del:{sub.id}")],
            [B(text="« До списку", callback_data="subs")],
        ]
    )


def view_label(view: str) -> str:
    return SCHEDULE_VIEWS.get(view, SCHEDULE_VIEWS["all"])[1]


def view_picker_kb(sub) -> InlineKeyboardMarkup:
    rows = [
        [B(text=f"✅ {text}" if view == sub.schedule_view else text, callback_data=f"vs:{sub.id}:{view}")]
        for view, (text, _) in SCHEDULE_VIEWS.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows + [[B(text="« Назад", callback_data=f"sub:{sub.id}")]])


def remind_picker_kb(sub, kind: str) -> InlineKeyboardMarkup:
    current = getattr(sub, REMIND_KINDS[kind][0])
    mark = lambda m, t: f"✅ {t}" if m == current else t  # noqa: E731
    steps = [B(text=mark(m, f"{m} хв"), callback_data=f"rs:{kind}:{sub.id}:{m}") for m in REMIND_STEPS]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            steps,
            [B(text=mark(0, "🔕 Вимкнено"), callback_data=f"rs:{kind}:{sub.id}:0")],
            [B(text="« Назад", callback_data=f"sub:{sub.id}")],
        ]
    )


def confirm_delete_kb(sub_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            B(text="Так, видалити", callback_data=f"delok:{sub_id}"),
            B(text="Ні", callback_data=f"sub:{sub_id}"),
        ]]
    )
