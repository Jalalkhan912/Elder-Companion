"""Email content builders and send-once delivery via Resend."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import date
from html import escape
from zoneinfo import ZoneInfo

from .db import Database
from .models import Conversation, Profile

log = logging.getLogger(__name__)

EmailSender = Callable[[list[str], str, str], Awaitable[None]]

RETRY_DELAYS = (1, 2, 4)
MAX_ATTEMPTS = 3
_MOOD_ORDER = {"low": 0, "okay": 1, "good": 2}


def resend_sender(api_key: str, from_addr: str) -> EmailSender:
    import resend

    resend.api_key = api_key

    async def send(to: list[str], subject: str, html: str) -> None:
        await asyncio.to_thread(
            resend.Emails.send,
            {"from": from_addr, "to": to, "subject": subject, "html": html},
        )

    return send


def _local(profile: Profile, conv: Conversation) -> str:
    return conv.started_at.astimezone(ZoneInfo(profile.timezone)).strftime("%a %d %b, %H:%M")


def urgent_email(profile: Profile, conv: Conversation) -> tuple[str, str]:
    urgent = [w for w in conv.warnings if w.level == "urgent"]
    reason = urgent[0].reason if urgent else "something concerning"
    subject = f"⚠️ Urgent: {profile.parent_nickname} mentioned {reason}"
    items = "".join(
        f"<li><b>{escape(w.reason)}</b>: &ldquo;{escape(w.quote)}&rdquo;</li>" for w in urgent
    )
    html = (
        f"<p>During a chat with {escape(profile.agent_name)} on {escape(_local(profile, conv))}, "
        f"{escape(profile.parent_nickname)} said something that may need attention:</p>"
        f"<ul>{items}</ul>"
        "<p>Please consider calling the care home to check on them.</p>"
    )
    return subject, html


def daily_email(profile: Profile, convs: list[Conversation], period_date: date) -> tuple[str, str]:
    nick = escape(profile.parent_nickname)
    subject = f"Daily summary: {profile.parent_nickname}'s chats on {period_date:%A %d %B}"
    if not convs:
        return subject, f"<p>{nick} didn't talk with {escape(profile.agent_name)} today.</p>"

    analyzed = [c for c in convs if c.status == "analyzed"]
    failed = len(convs) - len(analyzed)
    parts: list[str] = []
    for c in analyzed:
        minutes = max(1, round((c.ended_at - c.started_at).total_seconds() / 60)) if c.ended_at else 0
        parts.append(
            f"<p><b>{escape(_local(profile, c))}</b> ({minutes} min)<br>{escape(c.summary or '')}</p>"
        )
    moods = [c.mood for c in analyzed if c.mood in _MOOD_ORDER]
    if moods:
        parts.append(f"<p>Overall mood: {escape(min(moods, key=_MOOD_ORDER.__getitem__))}</p>")
    mild = [w for c in analyzed for w in c.warnings if w.level == "mild"]
    if mild:
        items = "".join(
            f"<li>{escape(w.reason)}: &ldquo;{escape(w.quote)}&rdquo;</li>" for w in mild
        )
        parts.append(f"<p>Things to keep an eye on:</p><ul>{items}</ul>")
    if failed:
        parts.append(f"<p>{failed} chat(s) couldn't be summarized.</p>")
    return subject, "".join(parts)


async def _deliver(db: Database, email_id: int, recipients: list[str], subject: str,
                   html: str, sender: EmailSender, sleep) -> bool:
    last_error = ""
    for attempt in range(MAX_ATTEMPTS):
        try:
            await sender(recipients, subject, html)
        except Exception as exc:  # noqa: BLE001 - email must never crash callers
            last_error = f"{type(exc).__name__}: {exc}"
            log.warning("email attempt %d failed: %s", attempt + 1, last_error)
            if attempt < MAX_ATTEMPTS - 1:
                await sleep(RETRY_DELAYS[attempt])
        else:
            db.mark_email(email_id, "sent")
            return True
    db.mark_email(email_id, "failed", last_error)
    return False


async def send_urgent(db: Database, profile: Profile, conv: Conversation,
                      sender: EmailSender, sleep=asyncio.sleep) -> bool:
    recipients = [str(e) for e in profile.child_emails]
    email_id = db.claim_email("urgent", recipients, conversation_id=conv.id)
    if email_id is None:
        return False
    subject, html = urgent_email(profile, conv)
    return await _deliver(db, email_id, recipients, subject, html, sender, sleep)


async def send_daily(db: Database, profile: Profile, period_date: date,
                     sender: EmailSender, sleep=asyncio.sleep) -> bool:
    convs = db.ended_since(db.last_daily_sent_at())
    recipients = [str(e) for e in profile.child_emails]
    email_id = db.claim_email("daily", recipients, period_date=period_date)
    if email_id is None:
        return False
    subject, html = daily_email(profile, convs, period_date)
    return await _deliver(db, email_id, recipients, subject, html, sender, sleep)
