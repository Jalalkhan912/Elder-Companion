from datetime import UTC, datetime, timedelta

from voice_agent.transcript import TranscriptRecorder


def test_recorder_keeps_user_and_assistant_only():
    rec = TranscriptRecorder()
    rec.add("system", "you are a companion")
    rec.add("developer", "secret")
    rec.add("user", "Hello?")
    rec.add("assistant", "Hi Abbu!")
    rec.add("user", "   ")
    rec.add("assistant", None)
    rec.add("user", "")
    assert [(t.role, t.text) for t in rec.turns] == [("user", "Hello?"), ("assistant", "Hi Abbu!")]


def test_recorder_timestamps_from_clock():
    start = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)
    ticks = iter([start, start + timedelta(seconds=5)])
    rec = TranscriptRecorder(clock=lambda: next(ticks))
    rec.add("user", "Hello?")
    rec.add("assistant", "Hi!")
    assert [t.at for t in rec.turns] == [start, start + timedelta(seconds=5)]


def test_recorder_turns_is_a_copy():
    rec = TranscriptRecorder()
    rec.add("user", "Hello?")
    rec.turns.clear()
    assert len(rec.turns) == 1
