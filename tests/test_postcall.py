import json
from datetime import datetime, timezone

from conftest import FakeLLM, FakeSender

from voice_agent.analysis import SHORT_CHAT_SUMMARY
from voice_agent.models import Turn
from voice_agent.postcall import process_conversation

NOW = datetime(2026, 10, 29, 10, 0, tzinfo=timezone.utc)


def _make(db, turns):
    cid = db.create_conversation("r", NOW)
    db.finish_conversation(cid, NOW, "user_ended", turns)
    return cid


def _talk():
    return [Turn(role="user", text="hello there", at=NOW)]


def _reply(level):
    return json.dumps({"summary": "Chatted.", "mood": "low",
                       "warnings": [{"level": level, "reason": "r", "quote": "q"}]})


async def test_process_success_with_urgent_sends_email(db, profile):
    db.save_profile(profile, None)
    cid = _make(db, _talk())
    sender = FakeSender()
    await process_conversation(db, cid, FakeLLM(_reply("urgent")), sender)
    assert db.get_conversation(cid).status == "analyzed"
    assert len(sender.sent) == 1
    assert sender.sent[0][0] == ["ayesha@example.com", "omar@example.com"]


async def test_process_mild_only_sends_nothing(db, profile):
    db.save_profile(profile, None)
    cid = _make(db, _talk())
    sender = FakeSender()
    await process_conversation(db, cid, FakeLLM(_reply("mild")), sender)
    assert db.get_conversation(cid).status == "analyzed"
    assert sender.sent == []


async def test_process_failure_records_attempt_and_never_raises(db, profile):
    db.save_profile(profile, None)
    cid = _make(db, _talk())
    await process_conversation(db, cid, FakeLLM(RuntimeError()), FakeSender())
    conv = db.get_conversation(cid)
    assert conv.status == "pending_analysis"
    assert conv.analysis_attempts == 1


async def test_process_empty_chat_no_llm_no_email(db, profile):
    db.save_profile(profile, None)
    cid = _make(db, [])
    llm, sender = FakeLLM("unused"), FakeSender()
    await process_conversation(db, cid, llm, sender)
    conv = db.get_conversation(cid)
    assert conv.status == "analyzed"
    assert conv.summary == SHORT_CHAT_SUMMARY
    assert llm.calls == []
    assert sender.sent == []


async def test_process_swallows_db_errors(db):
    await process_conversation(db, 999, FakeLLM("x"), FakeSender())
