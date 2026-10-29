"""Post-call analysis: one LLM call turning a transcript into a validated result."""
from __future__ import annotations

from typing import Protocol

from pydantic import ValidationError

from voice_agent.config import LLM_MODEL
from voice_agent.models import AnalysisResult, Turn

SHORT_CHAT_SUMMARY = "A very short chat with nothing much said."

ANALYSIS_SYSTEM_PROMPT = """\
You review a phone conversation between an elderly parent ("Parent") and a friendly \
AI companion ("Companion"). Write a report for the parent's grown children.

Reply with JSON only, no other text, in exactly this shape:
{"summary": "...", "mood": "good | okay | low", "warnings": [{"level": "urgent | mild", "reason": "...", "quote": "..."}]}

summary: 3-5 plain sentences about what the Parent talked about and how they seemed.
mood: the Parent's overall mood. "good" = cheerful or content, "okay" = neutral or mixed, \
"low" = sad, worried, distressed or unwell.
warnings: a list, empty if there is nothing to flag. Each item has:
- level: "urgent" or "mild".
- reason: a short noun phrase, for example "chest pain".
- quote: the Parent's own words that show it.

Urgent: fall or injury, chest pain, breathing trouble, self-harm or suicidal talk, \
severe confusion, abuse or mistreatment.
Mild: low mood, loneliness, poor sleep or appetite, ongoing pain, medication worries, \
complaints about care.

Only flag what the Parent actually said. Do not invent concerns.\
"""


class AnalysisError(Exception):
    """Analysis failed: LLM error, unparseable output, or invalid structure."""


class TextLLM(Protocol):
    async def complete(self, system: str, user: str) -> str: ...


def _format_transcript(transcript: list[Turn]) -> str:
    return "\n".join(
        f"{'Parent' if t.role == 'user' else 'Companion'}: {t.text}" for t in transcript
    )


async def analyze(transcript: list[Turn], llm: TextLLM) -> AnalysisResult:
    if not any(t.role == "user" and t.text.strip() for t in transcript):
        return AnalysisResult(summary=SHORT_CHAT_SUMMARY, mood="okay", warnings=[])
    try:
        reply = await llm.complete(ANALYSIS_SYSTEM_PROMPT, _format_transcript(transcript))
    except Exception as exc:
        raise AnalysisError(f"LLM call failed: {exc}") from exc
    start, end = reply.find("{"), reply.rfind("}")
    if start == -1 or end < start:
        raise AnalysisError("no JSON object in LLM reply")
    try:
        return AnalysisResult.model_validate_json(reply[start : end + 1])
    except ValidationError as exc:
        raise AnalysisError(f"invalid analysis JSON: {exc}") from exc


class InferenceTextLLM:
    """TextLLM backed by LiveKit Inference. Covered by slow tests only."""

    def __init__(self, model: str = LLM_MODEL):
        from livekit.agents import inference

        self._llm = inference.LLM(model=model)

    async def complete(self, system: str, user: str) -> str:
        from livekit.agents.llm import ChatContext

        ctx = ChatContext.empty()
        ctx.add_message(role="system", content=system)
        ctx.add_message(role="user", content=user)
        parts: list[str] = []
        async with self._llm.chat(chat_ctx=ctx) as stream:
            async for chunk in stream:
                if chunk.delta and chunk.delta.content:
                    parts.append(chunk.delta.content)
        return "".join(parts)
