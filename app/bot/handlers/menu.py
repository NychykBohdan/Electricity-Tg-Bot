"""Головне меню: графік, мої адреси, налаштування."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as kb
from app.bot.views import render_schedule
from app.db.repo import Database
from app.services.poller import sub_subtitle

router = Router(name="menu")

HELP = (
    "<b>Як користуватись</b>\n\n"
    "📅 <b>Графік</b> — відключення на сьогодні й завтра\n"
    "📍 <b>Мої адреси</b> — нагадування, сповіщення, видалення\n"
    "➕ <b>Додати адресу</b> — можна стежити за кількома (дім, робота…)\n\n"
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
    text = f"📍 <b>{sub.title}</b>\n{sub_subtitle(sub.city_key, sub.group)}"
    if sub.address:
        text += f"\n🏠 {sub.address}"
    return text


@router.callback_query(F.data.startswith("sub:"))
async def cb_sub(cb: CallbackQuery, db: Database) -> None:
    await cb.answer()
    sub = await db.get_subscription(int(cb.data.split(":")[1]), cb.from_user.id)
    if sub:
        await cb.message.edit_text(sub_card(sub), reply_markup=kb.sub_settings_kb(sub))


@router.callback_query(F.data.startswith("rm:"))
async def cb_remind(cb: CallbackQuery, db: Database) -> None:
    sub = await db.get_subscription(int(cb.data.split(":")[1]), cb.from_user.id)
    if not sub:
        return await cb.answer()
    steps = kb.REMIND_STEPS
    nxt = steps[(steps.index(sub.remind_minutes) + 1) % len(steps)] if sub.remind_minutes in steps else 30
    await db.update_subscription(sub.id, remind_minutes=nxt)
    sub.remind_minutes = nxt
    await cb.answer(f"Нагадування: {nxt} хв" if nxt else "Нагадування вимкнено")
    await cb.message.edit_reply_markup(reply_markup=kb.sub_settings_kb(sub))


@router.callback_query(F.data.startswith("nc:"))
async def cb_notify(cb: CallbackQuery, db: Database) -> None:
    sub = await db.get_subscription(int(cb.data.split(":")[1]), cb.from_user.id)
    if not sub:
        return await cb.answer()
    sub.notify_changes = not sub.notify_changes
    await db.update_subscription(sub.id, notify_changes=sub.notify_changes)
    await cb.answer("Сповіщення увімкнено" if sub.notify_changes else "Сповіщення вимкнено")
    await cb.message.edit_reply_markup(reply_markup=kb.sub_settings_kb(sub))


@router.callback_query(F.data.startswith("del:"))
async def cb_delete(cb: CallbackQuery, db: Database) -> None:
    await cb.answer()
    sub = await db.get_subscription(int(cb.data.split(":")[1]), cb.from_user.id)
    if sub:
        await cb.message.edit_text(f"Видалити «{sub.title}»?", reply_markup=kb.confirm_delete_kb(sub.id))


@router.callback_query(F.data.startswith("delok:"))
async def cb_delete_ok(cb: CallbackQuery, db: Database) -> None:
    await db.delete_subscription(int(cb.data.split(":")[1]), cb.from_user.id)
    await cb.answer("Видалено")
    await show_subs(cb, db)
