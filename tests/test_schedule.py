from datetime import date, datetime, timedelta, timezone

from conftest import FakeLLM, FakeSender

from voice_agent.schedule import build_scheduler, daily_due, daily_job, retry_job

UTC = timezone.utc


def test_daily_due_timezone(profile):
    assert daily_due(datetime(2026, 10, 29, 14, 59, tzinfo=UTC), profile) is None
    assert daily_due(datetime(2026, 10, 29, 15, 0, tzinfo=UTC), profile) == date(2026, 10, 29)


async def test_daily_job_sends_once_after_late_start(db, profile):
    db.save_profile(profile, None)
    sender = FakeSender()
    now = lambda: datetime(2026, 10, 29, 18, 30, tzinfo=UTC)
    await daily_job(db, sender, now_fn=now)
    await daily_job(db, sender, now_fn=now)
    assert len(sender.sent) == 1


async def test_daily_job_before_time_or_no_profile_sends_nothing(db, profile):
    sender = FakeSender()
    await daily_job(db, sender, now_fn=lambda: datetime(2026, 10, 29, 18, 30, tzinfo=UTC))
    db.save_profile(profile, None)
    await daily_job(db, sender, now_fn=lambda: datetime(2026, 10, 29, 10, 0, tzinfo=UTC))
    assert sender.sent == []


async def test_retry_job_skips_recently_ended(db, profile):
    db.save_profile(profile, None)
    now = datetime.now(UTC)
    recent = db.create_conversation("a", now - timedelta(minutes=1))
    db.finish_conversation(recent, now - timedelta(seconds=10), "user_ended", [])
    old = db.create_conversation("b", now - timedelta(minutes=10))
    db.finish_conversation(old, now - timedelta(minutes=5), "user_ended", [])
    await retry_job(db, FakeLLM("unused"), FakeSender())
    assert db.get_conversation(recent).status == "pending_analysis"
    assert db.get_conversation(old).status == "analyzed"


def test_build_scheduler_jobs():
    sched = build_scheduler(None, FakeLLM("x"), FakeSender())
    jobs = {j.id: j for j in sched.get_jobs()}
    assert set(jobs) == {"daily", "retry"}
    assert jobs["daily"].trigger.interval == timedelta(minutes=1)
    assert jobs["retry"].trigger.interval == timedelta(minutes=5)
    assert jobs["daily"].max_instances == 1 and jobs["daily"].coalesce


from voice_agent import notify  # noqa: E402
from voice_agent.models import AnalysisResult, WarningItem  # noqa: E402

URGENT = WarningItem(level="urgent", reason="a fall", quote="I fell over")


def _missed_urgent(db, ended_ago):
    now = datetime.now(UTC)
    cid = db.create_conversation("u", now - ended_ago - timedelta(minutes=5))
    db.finish_conversation(cid, now - ended_ago, "user_ended", [])
    # worker died after saving the analysis but before claiming the email
    db.save_analysis(cid, AnalysisResult(summary="s", mood="low", warnings=[URGENT]))
    return cid


async def test_retry_job_sends_missed_urgent_email_once(db, profile):
    db.save_profile(profile, None)
    old = _missed_urgent(db, timedelta(minutes=5))
    _missed_urgent(db, timedelta(seconds=10))  # within grace: left to the agent
    sender = FakeSender()
    await retry_job(db, FakeLLM("unused"), sender)
    await retry_job(db, FakeLLM("unused"), sender)
    assert len(sender.sent) == 1
    assert "a fall" in sender.sent[0][1]
    rows = db._query("SELECT conversation_id, status FROM email_log")
    assert [(r["conversation_id"], r["status"]) for r in rows] == [(old, "sent")]


async def test_retry_job_resends_failed_urgent(db, profile, monkeypatch):
    monkeypatch.setattr(notify, "RETRY_DELAYS", (0, 0, 0))
    db.save_profile(profile, None)
    cid = _missed_urgent(db, timedelta(minutes=5))
    conv = db.get_conversation(cid)
    assert await notify.send_urgent(db, profile, conv, FakeSender(fail_times=3)) is False
    sender = FakeSender()
    await retry_job(db, FakeLLM("unused"), sender)
    assert len(sender.sent) == 1


async def test_retry_job_survives_urgent_unsent_failure(db, profile, monkeypatch):
    db.save_profile(profile, None)

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(db, "urgent_unsent", boom)
    await retry_job(db, FakeLLM("unused"), FakeSender())  # must not raise


async def test_daily_job_retries_failed_daily_up_to_cap(db, profile, monkeypatch):
    monkeypatch.setattr(notify, "RETRY_DELAYS", (0, 0, 0))
    db.save_profile(profile, None)
    now = lambda: datetime(2026, 10, 29, 18, 30, tzinfo=UTC)
    sender = FakeSender(fail_times=3)  # first job's three tries all fail
    await daily_job(db, sender, now_fn=now)
    assert db._query("SELECT status FROM email_log")[0]["status"] == "failed"
    await daily_job(db, sender, now_fn=now)
    await daily_job(db, sender, now_fn=now)
    assert len(sender.sent) == 1
    assert db._query("SELECT status FROM email_log")[0]["status"] == "sent"

    always = FakeSender(fail_times=10**6)
    later = lambda: datetime(2026, 10, 30, 18, 30, tzinfo=UTC)
    for _ in range(5):
        await daily_job(db, always, now_fn=later)
    assert always.calls == 9  # 3 claims x 3 tries, then capped
