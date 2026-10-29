"""TranscriptRecorder: collects user/assistant turns from session events."""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from voice_agent.models import Turn

_KEPT_ROLES = ("user", "assistant")


class TranscriptRecorder:
    def __init__(self, clock: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self._clock = clock
        self._turns: list[Turn] = []

    def add(self, role: str, text: str | None) -> None:
        """Record a turn; other roles and blank text are ignored."""
        if role not in _KEPT_ROLES or text is None or not text.strip():
            return
        self._turns.append(Turn(role=role, text=text.strip(), at=self._clock()))

    @property
    def turns(self) -> list[Turn]:
        return list(self._turns)
