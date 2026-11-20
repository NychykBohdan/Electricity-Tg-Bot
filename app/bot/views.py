from datetime import datetime, timedelta

from app.config import TZ
from app.core.formatter import format_schedule
from app.db.models import Subscription, effective_prefs
from app.db.repo import Database, from_db, snapshot_to_day
from app.services.poller import sub_subtitle


async def render_schedule(db: Database, sub: Subscription, day_idx: int) -> str:
    now = datetime.now(TZ)
    today = now.date()
    rows = await db.get_days(sub.source_key, sub.group, [today, today + timedelta(days=1)])
    target = today + timedelta(days=day_idx)
    row = rows.get(target)
    prefs = effective_prefs((await db.users_by_ids({sub.user_id})).get(sub.user_id), sub)
    subtitle = sub_subtitle(sub.city_key, sub.group)
    if sub.address:
        subtitle += f"\n{sub.address}"
    return format_schedule(
        title=sub.title,
        subtitle=subtitle,
        day=snapshot_to_day(row) if row else None,
        now=now,
        target=target,
        context_days=[snapshot_to_day(r) for r in rows.values()],
        updated_at=from_db(row.updated_at).astimezone(TZ) if row else None,
        view=prefs.schedule_view,
        period=prefs.schedule_period,
    )
