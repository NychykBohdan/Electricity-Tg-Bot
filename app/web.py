"""Вхідна точка для Vercel (режим webhook).

POST /telegram   — Telegram надсилає сюди кожне оновлення (повідомлення, натискання кнопок).
GET  /cron?key=… — зовнішній cron (cron-job.org) смикає щохвилини: нагадування + опитування джерел.
GET  /           — перевірка, що функція жива.
"""
from __future__ import annotations

import hmac
import logging

from fastapi import FastAPI, Header, HTTPException, Request

from app.config import settings
from app.runtime import get_dispatcher, runtime
from app.services.reminders import check_reminders

logging.basicConfig(level=settings.log_level, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def _check(given: str | None, expected: str | None, name: str) -> None:
    if not expected:
        # без секрету будь-хто міг би слати боту підроблені оновлення
        raise HTTPException(500, f"{name} не задано в змінних середовища")
    if not given or not hmac.compare_digest(given, expected):
        raise HTTPException(403, "forbidden")


@app.get("/")
async def health() -> dict:
    return {"ok": True}


@app.post("/telegram")
async def telegram(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    _check(x_telegram_bot_api_secret_token, settings.webhook_secret, "WEBHOOK_SECRET")
    data = await request.json()
    async with runtime() as rt:
        try:
            await get_dispatcher().feed_raw_update(rt.bot, data, **rt.handler_data)
        except Exception:
            # відповідаємо 200 навіть на помилку: інакше Telegram повторюватиме те саме оновлення
            log.exception("Update handling failed")
    return {"ok": True}


@app.api_route("/cron", methods=["GET", "POST"])
async def cron(key: str | None = None, authorization: str | None = Header(default=None)) -> dict:
    if key is None and authorization and authorization.startswith("Bearer "):
        key = authorization.removeprefix("Bearer ")
    _check(key, settings.cron_secret, "CRON_SECRET")
    result: dict = {"ok": True}
    async with runtime() as rt:
        # нагадування першими — вони чутливі до часу
        try:
            result["reminders"] = await check_reminders(rt.db, rt.notifier)
        except Exception:
            log.exception("Reminder check failed")
            result["ok"] = False
        try:
            result["polled"] = await rt.poller.tick_if_due(settings.poll_interval_sec)
        except Exception:
            log.exception("Poller tick failed")
            result["ok"] = False
    return result
