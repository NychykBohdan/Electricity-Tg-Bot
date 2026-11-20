"""Доступ до БД. Усі datetime у БД — naive UTC."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Connection, delete, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.models import DaySchedule
from app.db.models import Base, FsmRecord, KeyValue, ScheduleSnapshot, SentReminder, Subscription, User


def to_db(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(tzinfo=None)


def from_db(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def make_engine(url: str) -> AsyncEngine:
    """SQLite локально; Postgres (Supabase) на Vercel.

    Рядок підключення з Supabase ("postgresql://...") сам переводимо на драйвер asyncpg.
    Пулер Supabase у transaction-режимі (порт 6543) не підтримує кешовані prepared statements,
    тому вимикаємо кеш і даємо кожному statement унікальне ім'я.
    """
    u = make_url(url)
    if u.drivername in ("postgres", "postgresql"):
        u = u.set(drivername="postgresql+asyncpg")
    if u.drivername == "postgresql+asyncpg":
        u = u.update_query_dict({"prepared_statement_cache_size": "0"})
        return create_async_engine(
            u,
            pool_pre_ping=True,
            connect_args={
                "statement_cache_size": 0,
                "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
            },
        )
    return create_async_engine(u)


_NEW_SUBSCRIPTION_COLUMNS = {
    "restore_remind_minutes": "INTEGER NOT NULL DEFAULT 0",
    "schedule_view": "VARCHAR(8) NOT NULL DEFAULT 'all'",
}


def _migrate(conn: Connection) -> None:
    """Доводить старі таблиці до поточної схеми (create_all сам колонок не додає)."""
    insp = inspect(conn)
    tables = set(insp.get_table_names())
    if "subscriptions" in tables:
        cols = {c["name"] for c in insp.get_columns("subscriptions")}
        for name, ddl in _NEW_SUBSCRIPTION_COLUMNS.items():
            if name not in cols:
                conn.execute(text(f"ALTER TABLE subscriptions ADD COLUMN {name} {ddl}"))
    if "sent_reminders" in tables:
        cols = {c["name"] for c in insp.get_columns("sent_reminders")}
        if "kind" not in cols:
            # лише журнал «що вже надіслано» на сьогодні/завтра — простіше створити заново
            SentReminder.__table__.drop(conn)


class Database:
    def __init__(self, url: str):
        self.engine: AsyncEngine = make_engine(url)
        self.session: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine, expire_on_commit=False
        )

    async def create_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(_migrate)
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()

    # ---------- службові значення ----------
    async def kv_get(self, key: str) -> str | None:
        async with self.session() as s:
            row = await s.get(KeyValue, key)
            return row.value if row else None

    async def kv_set(self, key: str, value: str) -> None:
        async with self.session() as s:
            row = await s.get(KeyValue, key)
            if row is None:
                s.add(KeyValue(key=key, value=value))
            else:
                row.value = value
            await s.commit()

    # ---------- стан діалогу (FSM) ----------
    async def fsm_get(self, key: str) -> tuple[str | None, dict[str, Any]]:
        async with self.session() as s:
            row = await s.get(FsmRecord, key)
            return (row.state, dict(row.data or {})) if row else (None, {})

    async def fsm_put(self, key: str, **fields: Any) -> None:
        """Оновлює state та/або data; порожній запис видаляє."""
        async with self.session() as s:
            row = await s.get(FsmRecord, key)
            if row is None:
                row = FsmRecord(key=key, state=None, data={})
                s.add(row)
            for k, v in fields.items():
                setattr(row, k, v)
            if row.state is None and not row.data:
                if row in s.new:
                    s.expunge(row)
                else:
                    await s.delete(row)
            await s.commit()

    # ---------- users / subscriptions ----------
    async def ensure_user(self, user_id: int) -> None:
        async with self.session() as s:
            if await s.get(User, user_id) is None:
                s.add(User(id=user_id))
                await s.commit()

    async def add_subscription(self, **fields) -> Subscription:
        async with self.session() as s:
            sub = Subscription(**fields)
            s.add(sub)
            await s.commit()
            return sub

    async def user_subscriptions(self, user_id: int) -> Sequence[Subscription]:
        async with self.session() as s:
            res = await s.scalars(
                select(Subscription).where(Subscription.user_id == user_id).order_by(Subscription.id)
            )
            return res.all()

    async def get_subscription(self, sub_id: int, user_id: int) -> Subscription | None:
        async with self.session() as s:
            sub = await s.get(Subscription, sub_id)
            return sub if sub and sub.user_id == user_id else None

    async def update_subscription(self, sub_id: int, **fields) -> None:
        async with self.session() as s:
            sub = await s.get(Subscription, sub_id)
            if sub:
                for k, v in fields.items():
                    setattr(sub, k, v)
                await s.commit()

    async def delete_subscription(self, sub_id: int, user_id: int) -> None:
        async with self.session() as s:
            await s.execute(
                delete(Subscription).where(Subscription.id == sub_id, Subscription.user_id == user_id)
            )
            await s.commit()

    async def delete_user(self, user_id: int) -> None:
        async with self.session() as s:
            await s.execute(delete(Subscription).where(Subscription.user_id == user_id))
            await s.execute(delete(User).where(User.id == user_id))
            await s.commit()

    async def subscriptions_for(self, source_key: str, group: str) -> Sequence[Subscription]:
        async with self.session() as s:
            res = await s.scalars(
                select(Subscription).where(Subscription.source_key == source_key, Subscription.group == group)
            )
            return res.all()

    async def all_subscriptions(self) -> Sequence[Subscription]:
        async with self.session() as s:
            return (await s.scalars(select(Subscription))).all()

    # ---------- schedules ----------
    async def has_source(self, source_key: str) -> bool:
        async with self.session() as s:
            res = await s.scalar(
                select(ScheduleSnapshot.id).where(ScheduleSnapshot.source_key == source_key).limit(1)
            )
            return res is not None

    async def get_snapshot(self, source_key: str, group: str, day: date) -> ScheduleSnapshot | None:
        async with self.session() as s:
            return await s.scalar(
                select(ScheduleSnapshot).where(
                    ScheduleSnapshot.source_key == source_key,
                    ScheduleSnapshot.group == group,
                    ScheduleSnapshot.day == day,
                )
            )

    async def get_days(self, source_key: str, group: str, days: list[date]) -> dict[date, ScheduleSnapshot]:
        async with self.session() as s:
            res = await s.scalars(
                select(ScheduleSnapshot).where(
                    ScheduleSnapshot.source_key == source_key,
                    ScheduleSnapshot.group == group,
                    ScheduleSnapshot.day.in_(days),
                )
            )
            return {r.day: r for r in res.all()}

    async def save_snapshot(self, source_key: str, group: str, day: DaySchedule, now: datetime) -> None:
        async with self.session() as s:
            row = await s.scalar(
                select(ScheduleSnapshot).where(
                    ScheduleSnapshot.source_key == source_key,
                    ScheduleSnapshot.group == group,
                    ScheduleSnapshot.day == day.day,
                )
            )
            if row is None:
                row = ScheduleSnapshot(source_key=source_key, group=group, day=day.day)
                s.add(row)
            row.status = day.status.value
            row.outages = day.outages_json()
            row.fingerprint = day.fingerprint()
            row.updated_at = to_db(now)
            await s.commit()

    async def cleanup(self, before: date) -> None:
        async with self.session() as s:
            await s.execute(delete(ScheduleSnapshot).where(ScheduleSnapshot.day < before))
            await s.execute(
                delete(SentReminder).where(SentReminder.event_at < datetime.combine(before, datetime.min.time()))
            )
            await s.commit()

    # ---------- reminders ----------
    async def try_mark_reminder(self, sub_id: int, kind: str, event_at: datetime) -> bool:
        """True, якщо нагадування ще не надсилали (і позначає його надісланим)."""
        async with self.session() as s:
            key = (sub_id, kind, to_db(event_at))
            if await s.get(SentReminder, key):
                return False
            s.add(SentReminder(subscription_id=sub_id, kind=kind, event_at=key[2]))
            await s.commit()
            return True


def snapshot_to_day(row: ScheduleSnapshot) -> DaySchedule:
    return DaySchedule.from_stored(row.day, row.status, row.outages)
