from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict

TZ = ZoneInfo("Europe/Kyiv")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    database_url: str = "sqlite+aiosqlite:///data/bot.db"
    poll_interval_sec: int = 300        # як часто опитувати джерела
    reminder_check_sec: int = 60        # як часто перевіряти нагадування
    admin_id: int | None = None         # кому слати алерти, якщо джерело "мовчить"
    log_level: str = "INFO"

    # Лише для режиму webhook (Vercel). Обидва — довгі випадкові рядки з [A-Za-z0-9_-].
    webhook_secret: str | None = None   # Telegram додає його в заголовок кожного запиту
    cron_secret: str | None = None      # зовнішній cron передає його як ?key=...


settings = Settings()  # type: ignore[call-arg]
