"""Додавання адреси: область → місто → постачальник → черга (за рахунком або вручну) → назва."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as kb
from app.bot.views import render_schedule
from app.collectors.base import CollectorError
from app.collectors.prykarpattya import PrykarpattyaClient
from app.config import settings
from app.db.repo import Database
from app.regions import cities_in, get_city, get_provider, oblasts, source_key
from app.services.poller import Poller

log = logging.getLogger(__name__)
router = Router(name="onboarding")


class AddSub(StatesGroup):
    oblast = State()
    city = State()
    provider = State()
    method = State()
    group = State()
    account = State()
    title = State()


WELCOME = (
    "👋 Привіт! Я попереджаю про відключення світла.\n\n"
    "• показую графік на сьогодні й завтра\n"
    "• пишу, коли графік змінюється\n"
    "• нагадую перед відключенням\n\n"
    "Додамо твою адресу. Обери область:"
)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    await db.ensure_user(message.from_user.id)
    if await db.user_subscriptions(message.from_user.id):
        await message.answer("З поверненням! 👋", reply_markup=kb.main_menu())
        return
    await message.answer(WELCOME, reply_markup=kb.main_menu())
    await ask_oblast(message, state)


@router.message(F.text == kb.BTN_ADD)
async def btn_add(message: Message, state: FSMContext, db: Database) -> None:
    await db.ensure_user(message.from_user.id)
    await state.clear()
    await ask_oblast(message, state)


async def ask_oblast(message: Message, state: FSMContext) -> None:
    await state.set_state(AddSub.oblast)
    await message.answer("🗺 Обери область:", reply_markup=kb.oblasts_kb(oblasts()))


@router.callback_query(F.data == "cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.message.edit_text("Скасовано.")
    await cb.answer()


@router.callback_query(AddSub.oblast, F.data.startswith("ob:"))
async def cb_oblast(cb: CallbackQuery, state: FSMContext) -> None:
    oblast = oblasts()[int(cb.data.split(":")[1])]
    cities = cities_in(oblast)
    await cb.answer()
    if len(cities) == 1:
        return await select_city(cb, state, cities[0].key)
    await state.set_state(AddSub.city)
    await cb.message.edit_text(f"🗺 {oblast}\n\n🏙 Обери місто:", reply_markup=kb.cities_kb(cities))


@router.callback_query(AddSub.city, F.data.startswith("ct:"))
async def cb_city(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await select_city(cb, state, cb.data.split(":")[1])


async def select_city(cb: CallbackQuery, state: FSMContext, city_key: str) -> None:
    city = get_city(city_key)
    await state.update_data(city=city.key)
    if len(city.providers) == 1:
        return await select_provider(cb, state, city.providers[0].id)
    await state.set_state(AddSub.provider)
    await cb.message.edit_text(
        f"🏙 {city.title}\n\n⚡ Обери постачальника електроенергії\n"
        "<i>(його назва є в рахунку за світло)</i>",
        reply_markup=kb.providers_kb(city),
    )


@router.callback_query(AddSub.provider, F.data.startswith("pv:"))
async def cb_provider(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await select_provider(cb, state, int(cb.data.split(":")[1]))


async def select_provider(cb: CallbackQuery, state: FSMContext, provider_id: int) -> None:
    data = await state.update_data(provider=provider_id)
    city = get_city(data["city"])
    await state.set_state(AddSub.method)
    await cb.message.edit_text(
        f"🏙 {city.title} · {get_provider(city, provider_id).name}\n\nЯк визначимо твою чергу?",
        reply_markup=kb.method_kb(),
    )


@router.callback_query(AddSub.method, F.data == "m:group")
async def cb_method_group(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.set_state(AddSub.group)
    await cb.message.edit_text("🔢 Обери чергу (підчергу):", reply_markup=kb.groups_kb())


@router.callback_query(AddSub.method, F.data == "m:account")
async def cb_method_account(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.set_state(AddSub.account)
    await cb.message.edit_text(
        "🧾 Напиши номер особового рахунку (його можна знайти в квитанції за електроенергію):"
    )


# ---------- пошук черги за особовим рахунком ----------
@router.message(AddSub.account, F.text)
async def msg_account(message: Message, state: FSMContext, prk: PrykarpattyaClient) -> None:
    account = message.text.strip()
    try:
        group = await prk.resolve_queue_by_account(account)
    except CollectorError:
        log.exception("account lookup failed")
        await state.set_state(AddSub.group)
        return await message.answer("😕 Сервіс пошуку недоступний. Обери чергу вручну:", reply_markup=kb.groups_kb())
    if not group:
        await state.set_state(AddSub.group)
        return await message.answer(
            "Не знайшов такого рахунку або для нього не визначено чергу 🤔 Обери чергу вручну:",
            reply_markup=kb.groups_kb(),
        )
    await state.update_data(group=group, address=f"Особовий рахунок №{account}")
    await ask_title(message, state, edit=False, intro=f"✅ Твоя черга: <b>{group}</b>")


# ---------- черга та назва ----------
@router.callback_query(AddSub.group, F.data.startswith("g:"))
async def cb_group(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    group = cb.data.split(":")[1]
    await state.update_data(group=group)
    await ask_title(cb.message, state, edit=True, intro=f"✅ Черга <b>{group}</b>")


async def ask_title(message: Message, state: FSMContext, edit: bool, intro: str) -> None:
    await state.set_state(AddSub.title)
    text = f"{intro}\n\nЯк назвемо цю адресу? Обери або напиши свою назву:"
    if edit:
        await message.edit_text(text, reply_markup=kb.title_kb())
    else:
        await message.answer(text, reply_markup=kb.title_kb())


@router.callback_query(AddSub.title, F.data.startswith("tt:"))
async def cb_title(cb: CallbackQuery, state: FSMContext, db: Database, poller: Poller) -> None:
    await cb.answer()
    title = kb.TITLE_PRESETS[int(cb.data.split(":")[1])]
    await cb.message.edit_reply_markup(reply_markup=None)
    await finish(cb.message, cb.from_user.id, title, state, db, poller)


@router.message(AddSub.title, F.text)
async def msg_title(message: Message, state: FSMContext, db: Database, poller: Poller) -> None:
    await finish(message, message.from_user.id, message.text.strip()[:40], state, db, poller)


async def finish(message: Message, user_id: int, title: str, state: FSMContext, db: Database, poller: Poller) -> None:
    data = await state.get_data()
    await state.clear()
    city = get_city(data["city"])
    key = source_key(city, data["provider"])
    sub = await db.add_subscription(
        user_id=user_id,
        title=title,
        city_key=city.key,
        provider_id=data["provider"],
        source_key=key,
        group=data["group"],
        address=data.get("address"),
        remind_minutes=settings.default_remind_minutes,
        notify_changes=True,
    )
    if not await db.has_source(key):  # даних ще немає — тягнемо одразу
        try:
            await poller.poll_source(key, city, data["provider"])
        except CollectorError:
            log.exception("initial fetch failed")
    await message.answer(
        f"✅ Збережено! Я напишу, якщо графік зміниться, і нагадаю за "
        f"{settings.default_remind_minutes} хв до відключення.\n"
        "Налаштувати можна в «📍 Мої адреси».",
        reply_markup=kb.main_menu(),
    )
    await message.answer(await render_schedule(db, sub, 0), reply_markup=kb.schedule_kb(sub.id, 0))


@router.message(StateFilter(AddSub), ~F.text)
async def non_text(message: Message) -> None:
    await message.answer("Скористайся кнопками вище або напиши текстом 🙂")
