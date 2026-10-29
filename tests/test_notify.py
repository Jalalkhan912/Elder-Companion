import asyncio
from datetime import date, datetime, timedelta, timezone

from conftest import FakeSender

from voice_agent.models import AnalysisResult, WarningItem
from voice_agent.notify import daily_email, send_daily, send_urgent, urgent_email


async def _nosleep(_):
    return None


def _conv(db, ended_at, result=None, fail=False, minutes=10):
    start = ended_at - timedelta(minutes=minutes)
    cid = db.create_conversation("r", start)
    db.finish_conversation(cid, ended_at, "user_ended", [])
    if result:
        db.save_analysis(cid, result)
    if fail:
        for _ in range(3):
            db.record_analysis_failure(cid, 3)
    return db.get_conversation(cid)


URGENT = WarningItem(level="urgent", reason="chest pain", quote="my chest hurts badly")


def _urgent_conv(db):
    now = datetime.now(timezone.utc)
    return _conv(db, now, AnalysisResult(summary="s", mood="low", warnings=[URGENT]))


def test_urgent_subject_and_body(db, profile):
    conv = _urgent_conv(db)
    subject, html = urgent_email(profile, conv)
    assert subject == "⚠️ Urgent: Abbu mentioned chest pain"
    assert "my chest hurts badly" in html
    assert "care home" in html


def test_daily_no_chats_body(profile):
    _, html = daily_email(profile, [], date(2026, 10, 29))
    assert "Abbu didn't talk with Ayesha today." in html


def test_daily_lists_chats_mild_warnings_and_failures(db, profile):
    now = datetime.now(timezone.utc)
    a = _conv(db, now - timedelta(hours=3),
              AnalysisResult(summary="Talked about cricket", mood="good"))
    b = _conv(db, now - timedelta(hours=2), AnalysisResult(
        summary="Talked about garden", mood="low",
        warnings=[WarningItem(level="mild", reason="poor sleep", quote="q")]))
    c = _conv(db, now - timedelta(hours=1), fail=True)
    subject, html = daily_email(profile, [a, b, c], date(2026, 10, 29))
    assert subject == "Daily summary: Abbu's chats on Thursday 29 October"
    assert "Talked about cricket" in html and "Talked about garden" in html
    assert "poor sleep" in html
    assert "1 chat(s) couldn't be summarized" in html
    assert "Overall mood: low" in html


def test_daily_escapes_html(db, profile):
    now = datetime.now(timezone.utc)
    a = _conv(db, now, AnalysisResult(summary="<script>x</script>", mood="good"))
    _, html = daily_email(profile, [a], date(2026, 10, 29))
    assert "<script>" not in html


def test_send_urgent_sends_once_to_all_children(db, profile):
    conv = _urgent_conv(db)
    sender = FakeSender()
    assert asyncio.run(send_urgent(db, profile, conv, sender, _nosleep)) is True
    assert asyncio.run(send_urgent(db, profile, conv, sender, _nosleep)) is False
    assert len(sender.sent) == 1
    assert sender.sent[0][0] == ["ayesha@example.com", "omar@example.com"]


def _status(db, kind="urgent"):
    row = db._query("SELECT status, error FROM email_log WHERE kind = ?", (kind,))[0]
    return row["status"], row["error"]


def test_send_retries_then_succeeds(db, profile):
    conv = _urgent_conv(db)
    sender = FakeSender(fail_times=2)
    delays = []

    async def sleep(d):
        delays.append(d)

    assert asyncio.run(send_urgent(db, profile, conv, sender, sleep)) is True
    assert _status(db)[0] == "sent"
    assert delays == [1, 2]


def test_send_gives_up_after_three(db, profile):
    conv = _urgent_conv(db)
    sender = FakeSender(fail_times=3)
    assert asyncio.run(send_urgent(db, profile, conv, sender, _nosleep)) is False
    status, error = _status(db)
    assert status == "failed" and "boom" in error


def test_daily_covers_since_last_daily(db, profile):
    now = datetime.now(timezone.utc)
    _conv(db, now - timedelta(hours=1),
          AnalysisResult(summary="FIRST chat", mood="good"))
    sender = FakeSender()
    assert asyncio.run(send_daily(db, profile, date(2026, 10, 28), sender, _nosleep))
    assert "FIRST chat" in sender.sent[0][2]
    _conv(db, now + timedelta(hours=1),
          AnalysisResult(summary="SECOND chat", mood="good"))
    assert asyncio.run(send_daily(db, profile, date(2026, 10, 29), sender, _nosleep))
    html = sender.sent[1][2]
    assert "SECOND chat" in html and "FIRST chat" not in html
