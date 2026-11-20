"""FSM-сховище aiogram у нашій БД.

На Vercel кожен запит може обслуговувати новий процес, тож MemoryStorage губив би діалог
(наприклад, посеред додавання адреси). Тут стан лежить у таблиці fsm_states.

Database береться з ContextVar `current_db`: її виставляє той, хто обробляє запит
(app/runtime.py або app/main.py), — так одночасні запити не плутають свої з'єднання.
"""
from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from typing import Any

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey

from app.db.repo import Database

current_db: ContextVar[Database] = ContextVar("current_db")


def _key(k: StorageKey) -> str:
    return ":".join(
        str(x) if x is not None else ""
        for x in (k.bot_id, k.chat_id, k.user_id, k.thread_id, k.business_connection_id, k.destiny)
    )


class SqlStorage(BaseStorage):
    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        value = state.state if isinstance(state, State) else state
        await current_db.get().fsm_put(_key(key), state=value)

    async def get_state(self, key: StorageKey) -> str | None:
        return (await current_db.get().fsm_get(_key(key)))[0]

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        await current_db.get().fsm_put(_key(key), data=dict(data))

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        return (await current_db.get().fsm_get(_key(key)))[1]

    async def close(self) -> None:
        pass
