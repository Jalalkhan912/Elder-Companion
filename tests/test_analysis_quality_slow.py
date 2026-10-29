"""Analysis quality checks against real LiveKit Inference (slow)."""
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from voice_agent.analysis import InferenceTextLLM, analyze
from voice_agent.config import load_settings
from voice_agent.models import Turn

pytestmark = pytest.mark.slow

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"


@pytest.fixture(scope="module")
def llm():
    load_settings()  # loads .env.local into os.environ for LiveKit Inference
    return InferenceTextLLM()


def _load(name: str) -> list[Turn]:
    return TypeAdapter(list[Turn]).validate_json((FIXTURES / f"{name}.json").read_text())


def _levels(result) -> list[str]:
    return [w.level for w in result.warnings]


async def test_happy_has_no_warnings(llm):
    result = await analyze(_load("happy"), llm)
    assert result.warnings == []
    assert result.mood in ("good", "okay")


async def test_fall_is_urgent(llm):
    result = await analyze(_load("fall"), llm)
    assert "urgent" in _levels(result)


async def test_sad_is_mild_not_urgent(llm):
    result = await analyze(_load("sad"), llm)
    assert "urgent" not in _levels(result)
    assert "mild" in _levels(result)
    assert result.mood == "low"


async def test_suicidal_is_urgent(llm):
    result = await analyze(_load("suicidal"), llm)
    assert "urgent" in _levels(result)
