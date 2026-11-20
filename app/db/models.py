from datetime import date, datetime

from sqlalchemy import JSON, BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # telegram user id
    created_at: Mapped[datetime] = mapped_column(DateTime(), server_default=func.now())

    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )


class Subscription(Base):
    """Адреса/черга, за якою стежить користувач (можна кілька: дім, робота...)."""

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(64))
    city_key: Mapped[str] = mapped_column(String(32))
    provider_id: Mapped[int] = mapped_column(Integer)
    source_key: Mapped[str] = mapped_column(String(64), index=True)
    group: Mapped[str] = mapped_column(String(8))
    address: Mapped[str | None] = mapped_column(String(200), nullable=True)
    remind_minutes: Mapped[int] = mapped_column(Integer, default=30)     # 0 = вимкнено
    notify_changes: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped[User] = relationship(back_populates="subscriptions")


class ScheduleSnapshot(Base):
    """Остання відома версія графіка для (джерело, черга, дата)."""

    __tablename__ = "schedule_snapshots"
    __table_args__ = (UniqueConstraint("source_key", "group", "day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_key: Mapped[str] = mapped_column(String(64), index=True)
    group: Mapped[str] = mapped_column(String(8))
    day: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16))
    outages: Mapped[list] = mapped_column(JSON)
    fingerprint: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[datetime] = mapped_column(DateTime())


class SentReminder(Base):
    """Щоб не нагадувати двічі про одне відключення."""

    __tablename__ = "sent_reminders"

    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"), primary_key=True
    )
    event_start: Mapped[datetime] = mapped_column(DateTime(), primary_key=True)


class FsmRecord(Base):
    """Стан діалогу aiogram (FSM). У БД, бо на Vercel пам'ять між запитами не зберігається."""

    __tablename__ = "fsm_states"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class KeyValue(Base):
    """Службові значення між запусками: час останнього опитування, лічильник збоїв джерела."""

    __tablename__ = "kv"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))
