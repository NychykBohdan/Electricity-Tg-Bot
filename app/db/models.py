from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import (
    JSON, BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint, func, true,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Налаштування сповіщень і вигляду графіка. Загальні значення — у User (діють для всіх адрес),
# у Subscription ті самі поля — необов'язкові перевизначення: None = «як у загальних».
PREF_FIELDS = ("schedule_view", "schedule_period", "remind_minutes", "restore_remind_minutes", "notify_changes")
DEFAULT_PREFS = {
    "schedule_view": "all",       # "all" — години зі світлом і без, "on" — лише зі світлом, "off" — лише без
    "schedule_period": "day",     # "day" — уся доба, "now" — поточний і всі наступні періоди
    "remind_minutes": 30,         # за скільки хв до відключення; 0 = вимкнено
    "restore_remind_minutes": 0,  # за скільки хв до появи світла; 0 = вимкнено
    "notify_changes": True,       # повідомляти про зміни графіка
}


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # telegram user id
    created_at: Mapped[datetime] = mapped_column(DateTime(), server_default=func.now())
    # загальні налаштування (див. DEFAULT_PREFS); server_default — щоб міграція заповнила наявні рядки
    schedule_view: Mapped[str] = mapped_column(String(8), default="all", server_default="all")
    schedule_period: Mapped[str] = mapped_column(String(8), default="day", server_default="day")
    remind_minutes: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    restore_remind_minutes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    notify_changes: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())

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
    # перевизначення загальних налаштувань для цієї адреси; None = як у загальних
    schedule_view: Mapped[str | None] = mapped_column(String(8), nullable=True)
    schedule_period: Mapped[str | None] = mapped_column(String(8), nullable=True)
    remind_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    restore_remind_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notify_changes: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    user: Mapped[User] = relationship(back_populates="subscriptions")

    def overridden(self) -> list[str]:
        """Які налаштування задані саме для цієї адреси."""
        return [f for f in PREF_FIELDS if getattr(self, f) is not None]


@dataclass(frozen=True)
class Prefs:
    """Налаштування, що фактично діють для адреси."""
    schedule_view: str
    schedule_period: str
    remind_minutes: int
    restore_remind_minutes: int
    notify_changes: bool


def effective_prefs(user: "User | None", sub: Subscription | None = None) -> Prefs:
    """Значення адреси, якщо задане, інакше загальне значення користувача, інакше типове."""
    values = {}
    for f in PREF_FIELDS:
        value = getattr(sub, f) if sub is not None else None
        if value is None:
            value = getattr(user, f) if user is not None else DEFAULT_PREFS[f]
        values[f] = value
    return Prefs(**values)


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
    """Щоб не нагадувати двічі про одну подію (вимкнення чи появу світла)."""

    __tablename__ = "sent_reminders"

    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(8), primary_key=True)        # "off" | "on"
    event_at: Mapped[datetime] = mapped_column(DateTime(), primary_key=True)  # початок або кінець відключення


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
