from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton as B
from aiogram.types import InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from app.regions import GROUPS, City

BTN_SCHEDULE = "📅 Графік"
BTN_SUBS = "📍 Мої адреси"
BTN_ADD = "➕ Додати адресу"
BTN_SETTINGS = "⚙️ Налаштування"
BTN_HELP = "ℹ️ Допомога"

TITLE_PRESETS = ["🏠 Дім", "💼 Робота"]
OVERRIDE_MARK = "✏️"   # позначка «своє налаштування для цієї адреси»


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_SCHEDULE), KeyboardButton(text=BTN_SUBS)],
            [KeyboardButton(text=BTN_ADD), KeyboardButton(text=BTN_SETTINGS)],
            [KeyboardButton(text=BTN_HELP)],
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


# ---------- налаштування (загальні й для окремої адреси) ----------
@dataclass(frozen=True)
class Option:
    value: object
    short: str   # на кнопці налаштування: «⏰ До відключення: 30 хв»
    long: str    # у виборі значення


@dataclass(frozen=True)
class SettingDef:
    field: str       # поле в User (загальне) і Subscription (перевизначення)
    label: str
    question: str
    options: tuple[Option, ...]
    per_row: int = 1


_MINUTES = tuple(Option(m, f"{m} хв", f"{m} хв") for m in (5, 10, 15, 30)) + (Option(0, "вимк", "🔕 Вимкнено"),)

# ключ (у callback_data) → опис. Порядок = порядок кнопок.
SETTINGS: dict[str, SettingDef] = {
    "vw": SettingDef("schedule_view", "🗓 Показ графіка", "Які години показувати в графіку?", (
        Option("all", "усі години", "🟢🔴 Години зі світлом і без"),
        Option("on", "лише зі світлом", "🟢 Лише години зі світлом"),
        Option("off", "лише без світла", "🔴 Лише години без світла"),
    )),
    "pd": SettingDef("schedule_period", "🕐 Період", "Що показувати в графіку на сьогодні?", (
        Option("day", "уся доба", "🕛 Усі години доби"),
        Option("now", "зараз і далі", "⏭ Поточний і наступний період"),
    )),
    "off": SettingDef("remind_minutes", "⏰ До відключення",
                      "За скільки попередити, що світло <b>вимкнеться</b>?", _MINUTES, per_row=4),
    "on": SettingDef("restore_remind_minutes", "💡 До появи світла",
                     "За скільки попередити, що світло <b>з'явиться</b>?", _MINUTES, per_row=4),
    "nc": SettingDef("notify_changes", "🔔 Зміни графіка",
                     "Повідомляти, коли графік змінюється або публікують графік на завтра?", (
                         Option(True, "увімк", "🔔 Повідомляти"),
                         Option(False, "вимк", "🔕 Не повідомляти"),
                     )),
}


def option_label(sdef: SettingDef, value: object) -> str:
    for o in sdef.options:
        if o.value == value and type(o.value) is type(value):
            return o.short
    return f"{value} хв" if isinstance(value, int) and not isinstance(value, bool) else str(value)


def _setting_button(scope: str, key: str, value: object, overridden: bool = False) -> list[B]:
    sdef = SETTINGS[key]
    mark = f" {OVERRIDE_MARK}" if overridden else ""
    return [B(text=f"{sdef.label}: {option_label(sdef, value)}{mark}", callback_data=f"st:{scope}:{key}")]


def global_settings_kb(user, has_overrides: bool) -> InlineKeyboardMarkup:
    rows = [_setting_button("u", key, getattr(user, sdef.field)) for key, sdef in SETTINGS.items()]
    if has_overrides:
        rows.append([B(text="♻️ Скинути власні налаштування адрес", callback_data="rst")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_reset_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[B(text="Так, скинути", callback_data="rstok"), B(text="Ні", callback_data="settings")]]
    )


def sub_settings_kb(sub, prefs) -> InlineKeyboardMarkup:
    """prefs — налаштування, що фактично діють для адреси (models.effective_prefs)."""
    rows = [
        _setting_button(str(sub.id), key, getattr(prefs, sdef.field), getattr(sub, sdef.field) is not None)
        for key, sdef in SETTINGS.items()
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [B(text="📅 Показати графік", callback_data=f"sv:{sub.id}:0")],
            *rows,
            [B(text="🗑 Видалити", callback_data=f"del:{sub.id}")],
            [B(text="« До списку", callback_data="subs")],
        ]
    )


def setting_picker_kb(scope: str, key: str, current: object, inherited: object = None) -> InlineKeyboardMarkup:
    """Вибір значення. scope: "u" — загальні, інакше id адреси.
    Для адреси current=None означає «як у загальних», inherited — яке це значення."""
    sdef = SETTINGS[key]

    def mark(chosen: bool, text: str) -> str:
        return f"✅ {text}" if chosen else text

    btns = [
        B(text=mark(current is not None and o.value == current, o.long), callback_data=f"ss:{scope}:{key}:{i}")
        for i, o in enumerate(sdef.options)
    ]
    # кнопки з однаковою шириною в ряд, «🔕 Вимкнено» — окремим рядком
    narrow = [b for b, o in zip(btns, sdef.options) if o.long == o.short]
    wide = [b for b, o in zip(btns, sdef.options) if o.long != o.short]
    rows = _rows(narrow, sdef.per_row) + [[b] for b in wide]
    if scope == "u":
        back = "settings"
    else:
        rows.append([B(
            text=mark(current is None, f"↩️ Як у загальних ({option_label(sdef, inherited)})"),
            callback_data=f"ss:{scope}:{key}:-",
        )])
        back = f"sub:{scope}"
    return InlineKeyboardMarkup(inline_keyboard=rows + [[B(text="« Назад", callback_data=back)]])


def confirm_delete_kb(sub_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            B(text="Так, видалити", callback_data=f"delok:{sub_id}"),
            B(text="Ні", callback_data=f"sub:{sub_id}"),
        ]]
    )
