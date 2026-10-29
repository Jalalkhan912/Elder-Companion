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


def test_two_connections_send_once_and_no_lock_left_behind(tmp_path):
    from voice_agent.db import Database
    a = Database(tmp_path / "shared.db")
    a.init_schema()
    b = Database(tmp_path / "shared.db")
    r = ["a@example.com"]
    assert isinstance(a.claim_email("urgent", r, conversation_id=1), int)
    assert b.claim_email("urgent", r, conversation_id=1) is None
    assert not b._conn.in_transaction
    # the other process can still write after the duplicate claim
    assert isinstance(a.claim_email("urgent", r, conversation_id=2), int)
    assert isinstance(b.claim_email("daily", r, period_date=date(2026, 10, 29)), int)
    assert a.claim_email("daily", r, period_date=date(2026, 10, 29)) is None
    assert isinstance(b.create_conversation("r", T0), int)


# ---- final-review fixes: email re-claim (R12), short-chat memory, failure rollback ----

URGENT_W = WarningItem(level="urgent", reason="fell", quote="I fell")


def _email_row(db, kind="urgent"):
    return db._query("SELECT * FROM email_log WHERE kind = ?", (kind,))[0]


def _age_claim(db, seconds):
    old = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(
        timespec="microseconds")
    db._exec("UPDATE email_log SET claimed_at = ?", (old,))


def test_claim_email_reclaims_failed_row_capped_at_three(db):
    from voice_agent.config import EMAIL_MAX_CLAIMS
    assert EMAIL_MAX_CLAIMS == 3
    r = ["a@example.com"]
    first = db.claim_email("urgent", r, conversation_id=1)
    assert _email_row(db)["attempts"] == 1 and _email_row(db)["claimed_at"]
    db.mark_email(first, "failed", "boom")
    again = db.claim_email("urgent", r, conversation_id=1)
    assert again == first
    row = _email_row(db)
    assert (row["status"], row["error"], row["attempts"]) == ("pending", None, 2)
    db.mark_email(first, "failed", "boom")
    assert db.claim_email("urgent", r, conversation_id=1) == first
    db.mark_email(first, "failed", "boom")
    assert db.claim_email("urgent", r, conversation_id=1) is None  # capped at 3 claims
    assert _email_row(db)["attempts"] == 3
    assert not db._conn.in_transaction


def test_claim_email_never_reclaims_sent_row(db):
    r = ["a@example.com"]
    eid = db.claim_email("daily", r, period_date=date(2026, 10, 29))
    db.mark_email(eid, "sent")
    _age_claim(db, 3600)
    assert db.claim_email("daily", r, period_date=date(2026, 10, 29)) is None


def test_claim_email_stale_pending_reclaimable_fresh_not(db):
    from voice_agent.config import STALE_CLAIM_SECONDS
    assert STALE_CLAIM_SECONDS == 300
    r = ["a@example.com"]
    eid = db.claim_email("urgent", r, conversation_id=1)
    _age_claim(db, 240)  # 4 minutes: still fresh
    assert db.claim_email("urgent", r, conversation_id=1) is None
    _age_claim(db, 301)
    assert db.claim_email("urgent", r, conversation_id=1) == eid
    assert _email_row(db)["attempts"] == 2


def _shared_db(tmp_path):
    from voice_agent.db import Database
    d = Database(tmp_path / "shared.db")
    d.init_schema()
    return d


def test_two_databases_cannot_both_reclaim(tmp_path):
    a = _shared_db(tmp_path)
    b = _shared_db(tmp_path)
    r = ["a@example.com"]
    eid = a.claim_email("urgent", r, conversation_id=1)
    a.mark_email(eid, "failed", "boom")
    assert b.claim_email("urgent", r, conversation_id=1) == eid
    assert a.claim_email("urgent", r, conversation_id=1) is None
    assert not a._conn.in_transaction and not b._conn.in_transaction


def _urgent_analyzed(db, n):
    cid = db.create_conversation(f"u{n}", T0 + timedelta(hours=n))
    db.finish_conversation(cid, T0 + timedelta(hours=n, minutes=5), "user_ended", _turns())
    db.save_analysis(cid, AnalysisResult(summary="s", mood="low", warnings=[URGENT_W]))
    return cid


def test_urgent_unsent_lists_claimable_urgent_conversations(db):
    r = ["a@example.com"]
    _analyzed(db, 1)  # no warnings
    mild = db.create_conversation("m", T0)
    db.finish_conversation(mild, T0, "x", _turns())
    db.save_analysis(mild, AnalysisResult(summary="s", mood="okay", warnings=[
        WarningItem(level="mild", reason="sleep", quote="q")]))
    no_row = _urgent_analyzed(db, 2)
    failed = _urgent_analyzed(db, 3)
    db.mark_email(db.claim_email("urgent", r, conversation_id=failed), "failed", "x")
    sent = _urgent_analyzed(db, 4)
    db.mark_email(db.claim_email("urgent", r, conversation_id=sent), "sent")
    fresh = _urgent_analyzed(db, 5)
    db.claim_email("urgent", r, conversation_id=fresh)
    assert [c.id for c in db.urgent_unsent()] == [no_row, failed]
    # stale pending becomes claimable; exhausted failed rows drop out
    _age_claim(db, 400)
    for _ in range(2):
        eid = db.claim_email("urgent", r, conversation_id=failed)
        db.mark_email(eid, "failed", "x")
    assert [c.id for c in db.urgent_unsent()] == [no_row, fresh]


def test_recent_analyzed_skips_short_chats(db):
    from voice_agent.models import SHORT_CHAT_SUMMARY
    real = [_analyzed(db, n) for n in range(5)]
    for n in range(5, 8):  # three accidental Talk->End taps, newest of all
        cid = db.create_conversation(f"short{n}", T0 + timedelta(hours=n))
        db.finish_conversation(cid, T0 + timedelta(hours=n), "user_ended", [])
        db.save_analysis(cid, AnalysisResult(summary=SHORT_CHAT_SUMMARY, mood="okay"))
    assert sorted(c.id for c in db.recent_analyzed(5)) == sorted(real)


def test_short_chat_summary_shared_with_analysis():
    from voice_agent import analysis, models
    assert analysis.SHORT_CHAT_SUMMARY == models.SHORT_CHAT_SUMMARY


def test_record_analysis_failure_rolls_back_on_error(db):
    import sqlite3
    cid = db.create_conversation("r", T0)
    db.finish_conversation(cid, T0, "x", _turns())
    real = db._conn

    class FailingCommit:
        def __getattr__(self, name):
            return getattr(real, name)

        def commit(self):
            raise sqlite3.OperationalError("disk I/O error")

    db._conn = FailingCommit()
    with pytest.raises(sqlite3.OperationalError):
        db.record_analysis_failure(cid, 3)
    db._conn = real
    assert not real.in_transaction
    assert db.get_conversation(cid).analysis_attempts == 0
