"""Головне меню: графік, мої адреси, налаштування."""
from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.bot import keyboards as kb
from app.bot.views import render_schedule
from app.db.models import effective_prefs
from app.db.repo import Database
from app.services.poller import sub_subtitle

router = Router(name="menu")

HELP = (
    "<b>Як користуватись</b>\n\n"
    "📅 <b>Графік</b> — відключення на сьогодні й завтра\n"
    "📍 <b>Мої адреси</b> — свої налаштування для окремої адреси, видалення\n"
    "➕ <b>Додати адресу</b> — можна стежити за кількома (дім, робота…)\n"
    "⚙️ <b>Налаштування</b> — спільні для всіх адрес: вигляд графіка, нагадування, сповіщення\n\n"
    "Я перевіряю графіки кожні кілька хвилин і пишу, щойно щось змінюється.\n\n"
    "⚠️ Це тестовий проєкт (так і задумано). Не орієнтуйся на нього як на надійне "
    "джерело — завжди перевіряй офіційну інформацію на svitlo.oe.if.ua або сайті "
    "Прикарпаттяобленерго."
)


@router.message(Command("help"))
@router.message(F.text == kb.BTN_HELP)
async def help_(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(HELP, reply_markup=kb.main_menu())


@router.message(Command("schedule"))
@router.message(F.text == kb.BTN_SCHEDULE)
async def schedule(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    subs = await db.user_subscriptions(message.from_user.id)
    if not subs:
        return await message.answer("Ще немає адрес. Натисни «➕ Додати адресу».", reply_markup=kb.main_menu())
    if len(subs) == 1:
        sub = subs[0]
        return await message.answer(await render_schedule(db, sub, 0), reply_markup=kb.schedule_kb(sub.id, 0))
    await message.answer("Для якої адреси показати графік?", reply_markup=kb.subs_list_kb(subs, "svp"))


@router.callback_query(F.data.startswith("svp:"))
async def cb_schedule_pick(cb: CallbackQuery, db: Database) -> None:
    sub = await db.get_subscription(int(cb.data.split(":")[1]), cb.from_user.id)
    await cb.answer()
    if sub:
        await cb.message.edit_text(await render_schedule(db, sub, 0), reply_markup=kb.schedule_kb(sub.id, 0))


@router.callback_query(F.data.startswith("sv:"))
async def cb_schedule_view(cb: CallbackQuery, db: Database) -> None:
    _, sub_id, day = cb.data.split(":")
    sub = await db.get_subscription(int(sub_id), cb.from_user.id)
    if not sub:
        return await cb.answer("Адресу видалено", show_alert=True)
    try:
        await cb.message.edit_text(
            await render_schedule(db, sub, int(day)), reply_markup=kb.schedule_kb(sub.id, int(day))
        )
        await cb.answer()
    except TelegramBadRequest as e:
        if "not modified" in str(e):
            await cb.answer("Без змін ✅")
        else:
            raise


# ---------- мої адреси ----------
@router.message(Command("addresses"))
@router.message(F.text == kb.BTN_SUBS)
async def subs_list(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    subs = await db.user_subscriptions(message.from_user.id)
    if not subs:
        return await message.answer("Ще немає адрес. Натисни «➕ Додати адресу».", reply_markup=kb.main_menu())
    await message.answer("📍 Твої адреси:", reply_markup=kb.subs_list_kb(subs, "sub"))


@router.callback_query(F.data == "subs")
async def cb_subs_list(cb: CallbackQuery, db: Database) -> None:
    await cb.answer()
    await show_subs(cb, db)


async def show_subs(cb: CallbackQuery, db: Database) -> None:
    subs = await db.user_subscriptions(cb.from_user.id)
    if not subs:
        return await cb.message.edit_text("Адрес не залишилось. Натисни «➕ Додати адресу».")
    await cb.message.edit_text("📍 Твої адреси:", reply_markup=kb.subs_list_kb(subs, "sub"))


def sub_card(sub) -> str:
    text = f"📍 <b>{escape(sub.title)}</b>\n{escape(sub_subtitle(sub.city_key, sub.group))}"
    if sub.address:
        text += f"\n🏠 {escape(sub.address)}"
    return text


# ---------- налаштування: загальні (scope "u") і для окремої адреси (scope = id адреси) ----------
GLOBAL_TITLE = (
    "⚙️ <b>Загальні налаштування</b>\n"
    "Діють для всіх адрес. Для окремої адреси можна задати своє в «📍 Мої адреси» — "
    "воно матиме перевагу над загальним."
)
SUB_LEGEND = (
    f"\n\n<i>{kb.OVERRIDE_MARK} — своє налаштування цієї адреси, решта — із загальних "
    f"«{kb.BTN_SETTINGS}».</i>"
)


async def global_screen(db: Database, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    user = await db.ensure_user(user_id)
    custom = [s for s in await db.user_subscriptions(user_id) if s.overridden()]
    text = GLOBAL_TITLE
    if custom:
        text += f"\n\n{kb.OVERRIDE_MARK} Свої налаштування мають: " + ", ".join(escape(s.title) for s in custom)
    return text, kb.global_settings_kb(user, has_overrides=bool(custom))


async def sub_screen(db: Database, sub) -> tuple[str, InlineKeyboardMarkup]:
    user = await db.ensure_user(sub.user_id)
    return sub_card(sub) + SUB_LEGEND, kb.sub_settings_kb(sub, effective_prefs(user, sub))


async def own_sub(db: Database, scope: str, user_id: int):
    """Адреса з callback_data — лише якщо вона належить цьому користувачу."""
    return await db.get_subscription(int(scope), user_id) if scope.isdigit() else None


@router.message(Command("settings"))
@router.message(F.text == kb.BTN_SETTINGS)
async def settings_menu(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    text, markup = await global_screen(db, message.from_user.id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "settings")
async def cb_settings(cb: CallbackQuery, db: Database) -> None:
    await cb.answer()
    text, markup = await global_screen(db, cb.from_user.id)
    await cb.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith("sub:"))
async def cb_sub(cb: CallbackQuery, db: Database) -> None:
    await cb.answer()
    sub = await own_sub(db, cb.data.split(":")[1], cb.from_user.id)
    if sub:
        text, markup = await sub_screen(db, sub)
        await cb.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith("st:"))
async def cb_setting_open(cb: CallbackQuery, db: Database) -> None:
    """Відкриває вибір значення: st:<scope>:<ключ>."""
    await cb.answer()
    parts = cb.data.split(":")
    if len(parts) != 3 or parts[2] not in kb.SETTINGS:
        return
    _, scope, key = parts
    sdef = kb.SETTINGS[key]
    user = await db.ensure_user(cb.from_user.id)
    if scope == "u":
        text = f"{GLOBAL_TITLE}\n\n{sdef.question}"
        markup = kb.setting_picker_kb("u", key, getattr(user, sdef.field))
    else:
        sub = await own_sub(db, scope, cb.from_user.id)
        if not sub:
            return
        text = f"{sub_card(sub)}\n\n{sdef.question}"
        markup = kb.setting_picker_kb(scope, key, getattr(sub, sdef.field), getattr(user, sdef.field))
    await cb.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith("ss:"))
async def cb_setting_set(cb: CallbackQuery, db: Database) -> None:
    """Зберігає вибір: ss:<scope>:<ключ>:<номер варіанта> або «-» = як у загальних."""
    parts = cb.data.split(":")
    if len(parts) != 4 or parts[2] not in kb.SETTINGS:
        return await cb.answer()
    _, scope, key, choice = parts
    sdef = kb.SETTINGS[key]
    if choice == "-" and scope != "u":
        value = None
    elif choice.isdigit() and int(choice) < len(sdef.options):
        value = sdef.options[int(choice)].value
    else:
        return await cb.answer()

    if scope == "u":
        await db.ensure_user(cb.from_user.id)
        await db.update_user(cb.from_user.id, **{sdef.field: value})
        await cb.answer(f"{sdef.label}: {kb.option_label(sdef, value)} — для всіх адрес")
        text, markup = await global_screen(db, cb.from_user.id)
    else:
        sub = await own_sub(db, scope, cb.from_user.id)
        if not sub:
            return await cb.answer()
        await db.update_subscription(sub.id, **{sdef.field: value})
        setattr(sub, sdef.field, value)
        shown = "як у загальних" if value is None else kb.option_label(sdef, value)
        await cb.answer(f"{sdef.label}: {shown}")
        text, markup = await sub_screen(db, sub)
    await cb.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "rst")
async def cb_reset(cb: CallbackQuery) -> None:
    await cb.answer()
    await cb.message.edit_text(
        "Скинути власні налаштування всіх адрес? Далі для них діятимуть загальні.",
        reply_markup=kb.confirm_reset_kb(),
    )


@router.callback_query(F.data == "rstok")
async def cb_reset_ok(cb: CallbackQuery, db: Database) -> None:
    await db.reset_overrides(cb.from_user.id)
    await cb.answer("Готово: усі адреси на загальних налаштуваннях")
    text, markup = await global_screen(db, cb.from_user.id)
    await cb.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith("del:"))
async def cb_delete(cb: CallbackQuery, db: Database) -> None:
    await cb.answer()
    sub = await db.get_subscription(int(cb.data.split(":")[1]), cb.from_user.id)
    if sub:
        await cb.message.edit_text(f"Видалити «{escape(sub.title)}»?", reply_markup=kb.confirm_delete_kb(sub.id))


@router.callback_query(F.data.startswith("delok:"))
async def cb_delete_ok(cb: CallbackQuery, db: Database) -> None:
    await db.delete_subscription(int(cb.data.split(":")[1]), cb.from_user.id)
    await cb.answer("Видалено")
    await show_subs(cb, db)
