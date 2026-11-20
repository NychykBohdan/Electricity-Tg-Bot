from datetime import datetime, timedelta

from app.config import TZ
from app.core.formatter import format_schedule
from app.db.models import Subscription
from app.db.repo import Database, snapshot_to_day


async def render_schedule(db: Database, sub: Subscription, day_idx: int) -> str:
    now = datetime.now(TZ)
    today = now.date()
    rows = await db.get_days(sub.source_key, sub.group, [today, today + timedelta(days=1)])
    target = today + timedelta(days=day_idx)
    row = rows.get(target)
    return format_schedule(
        title=sub.title,
        group=sub.group,
        day=snapshot_to_day(row) if row else None,
        now=now,
        target=target,
        context_days=[snapshot_to_day(r) for r in rows.values()],
        view=sub.schedule_view,
    )
