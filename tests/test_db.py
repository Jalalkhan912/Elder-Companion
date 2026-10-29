from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from voice_agent.models import AnalysisResult, Profile, Turn, WarningItem

T0 = datetime(2026, 10, 29, 10, 0, tzinfo=timezone.utc)


def _turns():
    return [Turn(role="user", text="hi", at=T0),
            Turn(role="assistant", text="hello", at=T0)]


def _analyzed(db, n, started=None):
    cid = db.create_conversation(f"room{n}", started or T0 + timedelta(hours=n))
    db.finish_conversation(cid, T0 + timedelta(hours=n, minutes=5), "user_ended", _turns())
    db.save_analysis(cid, AnalysisResult(summary=f"s{n}", mood="good"))
    return cid


def test_get_profile_none_when_empty(db):
    assert db.get_profile() is None
    assert db.get_pin_hash() is None


def test_profile_roundtrip_and_pin_kept(db, profile):
    db.save_profile(profile, "h1")
    db.save_profile(profile, None)
    assert db.get_pin_hash() == "h1"
    assert db.get_profile() == profile


def test_init_schema_idempotent(db):
    db.init_schema()


@pytest.mark.parametrize("kw", [
    {"timezone": "Not/AZone"}, {"timezone": "../etc"}, {"summary_time": "25:00"},
    {"child_emails": []}, {"child_emails": ["nope"]}, {"parent_name": ""},
])
def test_profile_validation(profile, kw):
    with pytest.raises(ValidationError):
        Profile(**{**profile.model_dump(), **kw})


def test_conversation_lifecycle(db):
    cid = db.create_conversation("r", T0)
    assert db.get_conversation(cid).status == "in_progress"
    db.finish_conversation(cid, T0 + timedelta(minutes=3), "user_ended", _turns())
    assert db.get_conversation(cid).status == "pending_analysis"
    db.save_analysis(cid, AnalysisResult(
        summary="ok", mood="low",
        warnings=[WarningItem(level="urgent", reason="fell", quote="I fell")]))
    c = db.get_conversation(cid)
    assert c.status == "analyzed"
    assert len(c.transcript) == 2
    assert c.warnings[0].level == "urgent"
    assert c.started_at == T0 and c.started_at.tzinfo is not None
    assert db.get_conversation(999) is None


def test_record_analysis_failure_marks_failed_after_max(db):
    cid = db.create_conversation("r", T0)
    db.finish_conversation(cid, T0, "x", _turns())
    assert [db.record_analysis_failure(cid, 3) for _ in range(3)] == [
        "pending_analysis", "pending_analysis", "analysis_failed"]
    assert db.pending_analysis(3) == []


def test_pending_analysis_lists_pending(db):
    cid = db.create_conversation("r", T0)
    db.finish_conversation(cid, T0, "x", _turns())
    assert [c.id for c in db.pending_analysis(3)] == [cid]


def test_recent_analyzed_newest_first_and_limited(db):
    for n in range(7):
        _analyzed(db, n)
    got = db.recent_analyzed(5)
    assert len(got) == 5
    starts = [c.started_at for c in got]
    assert starts == sorted(starts, reverse=True)


def test_ended_since_filters(db):
    a = _analyzed(db, 1)
    b = _analyzed(db, 2)
    db.create_conversation("open", T0)  # never ended
    assert [c.id for c in db.ended_since(None)] == [a, b]
    since = T0 + timedelta(hours=1, minutes=5)
    assert [c.id for c in db.ended_since(since)] == [b]
    # non-UTC input is normalised
    tz = timezone(timedelta(hours=5))
    assert [c.id for c in db.ended_since(since.astimezone(tz))] == [b]


def test_claim_email_once_per_key(db):
    r = ["a@example.com"]
    assert isinstance(db.claim_email("urgent", r, conversation_id=1), int)
    assert db.claim_email("urgent", r, conversation_id=1) is None
    assert isinstance(db.claim_email("urgent", r, conversation_id=2), int)
    d = date(2026, 10, 29)
    assert isinstance(db.claim_email("daily", r, period_date=d), int)
    assert db.claim_email("daily", r, period_date=d) is None
    assert isinstance(db.claim_email("daily", r, period_date=date(2026, 10, 30)), int)


def test_last_daily_sent_at(db):
    assert db.last_daily_sent_at() is None
    eid = db.claim_email("daily", ["a@example.com"], period_date=date(2026, 10, 29))
    assert db.last_daily_sent_at() is None
    db.mark_email(eid, "failed", "boom")
    assert db.last_daily_sent_at() is None
    db.mark_email(eid, "sent")
    got = db.last_daily_sent_at()
    assert got.tzinfo is not None
    assert abs(datetime.now(timezone.utc) - got) < timedelta(seconds=10)
