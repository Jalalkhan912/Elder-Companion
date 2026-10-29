from datetime import datetime, timezone

import pytest
from conftest import FakeLLM

from voice_agent.analysis import SHORT_CHAT_SUMMARY, AnalysisError, analyze
from voice_agent.models import Turn

NOW = datetime(2026, 10, 29, 10, 0, tzinfo=timezone.utc)
JSON = (
    '{"summary": "He felt unwell.", "mood": "low", "warnings": '
    '[{"level": "urgent", "reason": "chest pain", "quote": "my chest hurts"}]}'
)


def convo():
    return [Turn(role="assistant", text="Hello", at=NOW), Turn(role="user", text="I fell", at=NOW)]


async def test_parses_clean_json():
    result = await analyze(convo(), FakeLLM(JSON))
    assert result.warnings[0].reason == "chest pain"
    assert result.mood == "low"


async def test_parses_fenced_json_with_prose():
    reply = f"Sure!\n```json\n{JSON}\n```\nHope this helps"
    result = await analyze(convo(), FakeLLM(reply))
    assert result.warnings[0].level == "urgent"


async def test_invalid_json_raises():
    with pytest.raises(AnalysisError):
        await analyze(convo(), FakeLLM("I can't do that"))


async def test_bad_enum_raises():
    with pytest.raises(AnalysisError):
        await analyze(convo(), FakeLLM('{"summary": "x", "mood": "great", "warnings": []}'))


async def test_llm_exception_wrapped():
    with pytest.raises(AnalysisError):
        await analyze(convo(), FakeLLM(RuntimeError("boom")))


@pytest.mark.parametrize(
    "turns",
    [[], [Turn(role="assistant", text="Hello", at=NOW)], [Turn(role="user", text="  ", at=NOW)]],
)
async def test_empty_transcript_skips_llm(turns):
    fake = FakeLLM(JSON)
    result = await analyze(turns, fake)
    assert result.summary == SHORT_CHAT_SUMMARY
    assert result.mood == "okay" and result.warnings == []
    assert fake.calls == []


async def test_transcript_sent_with_speaker_labels():
    fake = FakeLLM(JSON)
    await analyze(convo(), fake)
    assert "Parent: I fell" in fake.calls[0][1]
    assert "Companion: Hello" in fake.calls[0][1]
