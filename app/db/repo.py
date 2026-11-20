"""Доступ до БД. Усі datetime у БД — naive UTC."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Connection, delete, inspect, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.models import DaySchedule
from app.db.models import (
    DEFAULT_PREFS, PREF_FIELDS, Base, FsmRecord, KeyValue, ScheduleSnapshot, SentReminder, Subscription, User,
)


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


# Колонки, яких могло не бути в старих базах. Лише константи — жодних даних від користувача.
_NEW_COLUMNS = {
    "users": {
        "schedule_view": "VARCHAR(8) NOT NULL DEFAULT 'all'",
        "schedule_period": "VARCHAR(8) NOT NULL DEFAULT 'day'",
        "remind_minutes": "INTEGER NOT NULL DEFAULT 30",
        "restore_remind_minutes": "INTEGER NOT NULL DEFAULT 0",
        "notify_changes": "BOOLEAN NOT NULL DEFAULT TRUE",
    },
    # у адресах це перевизначення: NULL = як у загальних налаштуваннях
    "subscriptions": {
        "restore_remind_minutes": "INTEGER",
        "schedule_view": "VARCHAR(8)",
        "schedule_period": "VARCHAR(8)",
    },
}


def _add_missing_columns(conn: Connection, table: str) -> None:
    cols = {c["name"] for c in inspect(conn).get_columns(table)}
    if_not_exists = "IF NOT EXISTS " if conn.dialect.name == "postgresql" else ""
    for name, ddl in _NEW_COLUMNS[table].items():
        if name not in cols:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {if_not_exists}{name} {ddl}"))


def _rebuild_sqlite_subscriptions(conn: Connection) -> None:
    """SQLite не вміє ALTER COLUMN: створюємо таблицю за поточною моделлю і переносимо рядки."""
    insp = inspect(conn)
    old_cols = {c["name"] for c in insp.get_columns("subscriptions")}
    old_indexes = [i["name"] for i in insp.get_indexes("subscriptions")]
    # legacy_alter_table: щоб зовнішній ключ sent_reminders і далі вказував на "subscriptions"
    conn.exec_driver_sql("PRAGMA legacy_alter_table=ON")
    conn.exec_driver_sql("ALTER TABLE subscriptions RENAME TO _subscriptions_old")
    for name in old_indexes:  # імена індексів у SQLite глобальні — звільняємо їх для нової таблиці
        conn.exec_driver_sql(f'DROP INDEX IF EXISTS "{name}"')
    Subscription.__table__.create(conn)
    cols = ", ".join(f'"{c}"' for c in Subscription.__table__.columns.keys() if c in old_cols)
    conn.exec_driver_sql(f"INSERT INTO subscriptions ({cols}) SELECT {cols} FROM _subscriptions_old")
    conn.exec_driver_sql("DROP TABLE _subscriptions_old")
    conn.exec_driver_sql("PRAGMA legacy_alter_table=OFF")


def _make_overrides_nullable(conn: Connection) -> None:
    """Раніше налаштування жили лише в адресі (NOT NULL). Тепер це перевизначення загальних:
    робимо колонки nullable і скидаємо в NULL значення, що збігаються з типовими, —
    тоді для таких адрес почнуть діяти загальні налаштування користувача."""
    cols = {c["name"]: c for c in inspect(conn).get_columns("subscriptions")}
    if all(cols[f]["nullable"] for f in PREF_FIELDS):
        return  # уже зроблено
    if conn.dialect.name == "postgresql":
        for f in PREF_FIELDS:
            conn.execute(text(f"ALTER TABLE subscriptions ALTER COLUMN {f} DROP NOT NULL"))
            conn.execute(text(f"ALTER TABLE subscriptions ALTER COLUMN {f} DROP DEFAULT"))
    else:
        _rebuild_sqlite_subscriptions(conn)
    for f in PREF_FIELDS:
        conn.execute(text(f"UPDATE subscriptions SET {f} = NULL WHERE {f} = :v"), {"v": DEFAULT_PREFS[f]})


def _migrate(conn: Connection) -> None:
    """Доводить старі таблиці до поточної схеми (create_all сам колонок не додає)."""
    insp = inspect(conn)
    tables = set(insp.get_table_names())
    if "users" in tables:
        _add_missing_columns(conn, "users")
    if "subscriptions" in tables:
        _add_missing_columns(conn, "subscriptions")
        _make_overrides_nullable(conn)
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
    async def ensure_user(self, user_id: int) -> User:
        async with self.session() as s:
            user = await s.get(User, user_id)
            if user is None:
                user = User(id=user_id)
                s.add(user)
                await s.commit()
            return user

    async def users_by_ids(self, user_ids: set[int]) -> dict[int, User]:
        if not user_ids:
            return {}
        async with self.session() as s:
            res = await s.scalars(select(User).where(User.id.in_(user_ids)))
            return {u.id: u for u in res.all()}

    async def update_user(self, user_id: int, **fields) -> None:
        """Змінює загальні налаштування. Лише поля з PREF_FIELDS."""
        assert set(fields) <= set(PREF_FIELDS), fields
        async with self.session() as s:
            user = await s.get(User, user_id)
            if user:
                for k, v in fields.items():
                    setattr(user, k, v)
                await s.commit()

    async def reset_overrides(self, user_id: int) -> None:
        """Прибирає власні налаштування всіх адрес користувача — далі діють загальні."""
        async with self.session() as s:
            await s.execute(
                update(Subscription)
                .where(Subscription.user_id == user_id)
                .values({f: None for f in PREF_FIELDS})
            )
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
