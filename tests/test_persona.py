from datetime import datetime, timezone

from voice_agent.models import Conversation
from voice_agent.persona import GOODBYE_INSTRUCTIONS, build_instructions, greeting_instructions


def _conv(i, when, summary):
    return Conversation(
        id=i, room_name=f"r{i}", started_at=when, status="analyzed", summary=summary
    )


def test_identity_and_details(profile):
    text = build_instructions(profile, [])
    for word in ("Ayesha", "Abbu", "daughter", "Loves cricket"):
        assert word in text


def test_memory_included_newest_first(profile):
    older = _conv(1, datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc), "Talked about Ali's exam.")
    newer = _conv(2, datetime(2026, 10, 28, 20, 0, tzinfo=timezone.utc), "Talked about knee pain.")
    text = build_instructions(profile, [older, newer])
    assert "Things you remember from recent chats:" in text
    assert text.index("knee pain") < text.index("Ali's exam")
    # 20:00 UTC is 01:00 on the 29th in Asia/Karachi
    assert "2026-10-29: Talked about knee pain." in text
    assert "2026-10-27: Talked about Ali's exam." in text


def test_blank_summaries_skipped(profile):
    c = _conv(1, datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc), "  ")
    assert "This is your first conversation" in build_instructions(profile, [c])


def test_no_memory_section_when_empty(profile):
    text = build_instructions(profile, [])
    assert "This is your first conversation" in text
    assert "Things you remember" not in text


def test_safety_and_honesty_rules_present(profile):
    text = build_instructions(profile, [])
    for phrase in ("call button", "staff", "real person", "medical advice"):
        assert phrase in text


def test_style_rules(profile):
    text = build_instructions(profile, [])
    assert "short" in text
    assert "one question at a time" in text
    assert "emojis" in text


def test_greeting_uses_nickname(profile):
    assert "Abbu" in greeting_instructions(profile)


def test_goodbye():
    assert "goodbye" in GOODBYE_INSTRUCTIONS.lower()
