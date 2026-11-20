"""Оновлення старих баз: налаштування переїжджають з адрес у загальні (users), а в адресах
стають необов'язковими перевизначеннями (NULL = як у загальних)."""
import sqlite3
from datetime import UTC, datetime

import pytest

from app.db.models import effective_prefs
from app.db.repo import Database

# Схема найстарішої версії (як у data/bot.db до появи нагадувань про світло)
V1 = """
CREATE TABLE users (id BIGINT NOT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, PRIMARY KEY (id));
CREATE TABLE subscriptions (
    id INTEGER NOT NULL, user_id BIGINT NOT NULL, title VARCHAR(64) NOT NULL, city_key VARCHAR(32) NOT NULL,
    provider_id INTEGER NOT NULL, source_key VARCHAR(64) NOT NULL, "group" VARCHAR(8) NOT NULL,
    address VARCHAR(200), remind_minutes INTEGER NOT NULL, notify_changes BOOLEAN NOT NULL, address_params JSON,
    PRIMARY KEY (id), FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE);
CREATE INDEX ix_subscriptions_user_id ON subscriptions (user_id);
CREATE INDEX ix_subscriptions_source_key ON subscriptions (source_key);
CREATE TABLE sent_reminders (subscription_id INTEGER NOT NULL, event_start DATETIME NOT NULL,
    PRIMARY KEY (subscription_id, event_start),
    FOREIGN KEY(subscription_id) REFERENCES subscriptions (id) ON DELETE CASCADE);
INSERT INTO users (id) VALUES (1);
INSERT INTO subscriptions VALUES (1, 1, 'Дім', 'if_oblast', 1, 'prk:if_oblast:1', '3.2', NULL, 30, 1, NULL);
INSERT INTO subscriptions VALUES (2, 1, 'Робота', 'if_oblast', 1, 'prk:if_oblast:1', '1.1', NULL, 15, 0, NULL);
"""

# Схема попередньої версії (нагадування про світло і вигляд графіка — у кожній адресі, NOT NULL)
V2 = """
CREATE TABLE users (id BIGINT NOT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, PRIMARY KEY (id));
CREATE TABLE subscriptions (
    id INTEGER NOT NULL, user_id BIGINT NOT NULL, title VARCHAR(64) NOT NULL, city_key VARCHAR(32) NOT NULL,
    provider_id INTEGER NOT NULL, source_key VARCHAR(64) NOT NULL, "group" VARCHAR(8) NOT NULL,
    address VARCHAR(200), remind_minutes INTEGER NOT NULL, notify_changes BOOLEAN NOT NULL,
    restore_remind_minutes INTEGER NOT NULL DEFAULT 0, schedule_view VARCHAR(8) NOT NULL DEFAULT 'all',
    PRIMARY KEY (id), FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE);
CREATE INDEX ix_subscriptions_user_id ON subscriptions (user_id);
CREATE INDEX ix_subscriptions_source_key ON subscriptions (source_key);
CREATE TABLE sent_reminders (subscription_id INTEGER NOT NULL, kind VARCHAR(8) NOT NULL,
    event_at DATETIME NOT NULL, PRIMARY KEY (subscription_id, kind, event_at),
    FOREIGN KEY(subscription_id) REFERENCES subscriptions (id) ON DELETE CASCADE);
INSERT INTO users (id) VALUES (1);
INSERT INTO subscriptions VALUES (1, 1, 'Дім', 'if_oblast', 1, 'prk:if_oblast:1', '3.2', NULL, 30, 1, 0, 'all');
INSERT INTO subscriptions VALUES (2, 1, 'Робота', 'if_oblast', 1, 'prk:if_oblast:1', '1.1', NULL, 15, 0, 10, 'off');
INSERT INTO sent_reminders VALUES (2, 'off', '2026-11-20 10:00:00.000000');
"""


@pytest.mark.parametrize("schema", [V1, V2], ids=["v1", "v2"])
async def test_old_db_upgrade(tmp_path, schema):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as c:
        c.executescript(schema)

    db = Database(f"sqlite+aiosqlite:///{path}")
    await db.create_all()
    await db.create_all()   # повторний запуск нічого не ламає
    user = (await db.users_by_ids({1}))[1]
    home, work = await db.user_subscriptions(1)

    # загальні — типові; «Дім» мав типові значення → тепер повністю на загальних
    assert (user.remind_minutes, user.notify_changes, user.schedule_view) == (30, True, "all")
    assert home.overridden() == []
    # «Робота» мала нестандартні значення → вони лишились її власними
    assert work.remind_minutes == 15 and work.notify_changes is False
    prefs = effective_prefs(user, work)
    assert prefs.remind_minutes == 15 and prefs.notify_changes is False and prefs.schedule_period == "day"
    if schema is V2:
        assert (work.restore_remind_minutes, work.schedule_view) == (10, "off")
        # журнал надісланих нагадувань пережив перебудову таблиці адрес
        assert await db.try_mark_reminder(2, "off", datetime(2026, 11, 20, 10, tzinfo=UTC)) is False

    # нові адреси без налаштувань і зміни загальних працюють
    new = await db.add_subscription(user_id=1, title="Дача", city_key="if_oblast", provider_id=1,
                                    source_key="prk:if_oblast:1", group="2.1")
    await db.update_user(1, remind_minutes=5)
    user = (await db.users_by_ids({1}))[1]
    assert effective_prefs(user, new).remind_minutes == 5 and effective_prefs(user, work).remind_minutes == 15
    await db.close()

    with sqlite3.connect(path) as c:
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        fk = c.execute("PRAGMA foreign_key_list(sent_reminders)").fetchall()
        assert fk[0][2] == "subscriptions"   # зовнішній ключ і далі на справжню таблицю
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "_subscriptions_old" not in tables
