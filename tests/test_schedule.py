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
