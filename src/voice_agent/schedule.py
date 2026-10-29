"""Scheduler jobs: daily summary email and analysis retries."""
import logging
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from voice_agent.analysis import TextLLM
from voice_agent.config import MAX_ANALYSIS_ATTEMPTS
from voice_agent.db import Database
from voice_agent.models import Profile
from voice_agent.notify import EmailSender, send_daily
from voice_agent.postcall import process_conversation

log = logging.getLogger(__name__)

RETRY_GRACE = timedelta(seconds=60)


def daily_due(now_utc: datetime, profile: Profile) -> date | None:
    """Local date if local time is at or after summary_time, else None."""
    local = now_utc.astimezone(ZoneInfo(profile.timezone))
    hh, mm = (int(x) for x in profile.summary_time.split(":"))
    if (local.hour, local.minute) >= (hh, mm):
        return local.date()
    return None


async def daily_job(db: Database, sender: EmailSender,
                    now_fn=lambda: datetime.now(UTC)) -> None:
    try:
        profile = db.get_profile()
        if profile is None:
            return
        period = daily_due(now_fn(), profile)
        if period is not None:
            await send_daily(db, profile, period, sender)
    except Exception:
        log.exception("daily job failed")


async def retry_job(db: Database, llm: TextLLM, sender: EmailSender) -> None:
    try:
        pending = db.pending_analysis(MAX_ANALYSIS_ATTEMPTS)
    except Exception:
        log.exception("retry job failed to list pending conversations")
        return
    cutoff = datetime.now(UTC) - RETRY_GRACE
    for conv in pending:
        if conv.ended_at is None or conv.ended_at > cutoff:
            continue
        try:
            await process_conversation(db, conv.id, llm, sender)
        except Exception:
            log.exception("retry job failed for conversation %s", conv.id)


def build_scheduler(db: Database, llm: TextLLM, sender: EmailSender) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    opts = {"max_instances": 1, "coalesce": True}
    scheduler.add_job(daily_job, "interval", minutes=1, args=[db, sender],
                      id="daily", **opts)
    scheduler.add_job(retry_job, "interval", minutes=5, args=[db, llm, sender],
                      id="retry", **opts)
    return scheduler
