"""Shared pytest fixtures (extended by later tasks)."""
import pytest

from voice_agent.config import load_settings
from voice_agent.db import Database
from voice_agent.models import Profile


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "t.db")
    database.init_schema()
    return database


@pytest.fixture
def settings():
    return load_settings({
        "LIVEKIT_URL": "wss://x.livekit.cloud", "LIVEKIT_API_KEY": "k",
        "LIVEKIT_API_SECRET": "s" * 32, "RESEND_API_KEY": "r", "EMAIL_FROM": "a@b.com",
    })


@pytest.fixture
def profile():
    return Profile(
        parent_name="Rashid Khan",
        parent_nickname="Abbu",
        agent_role="daughter",
        agent_name="Ayesha",
        personal_details="Loves cricket and gardening. Grandson is Ali.",
        child_emails=["ayesha@example.com", "omar@example.com"],
        summary_time="20:00",
        timezone="Asia/Karachi",
    )


class FakeLLM:
    """TextLLM test double. Tests construct it directly: `from conftest import FakeLLM`."""

    def __init__(self, reply: "str | Exception"):
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class FakeSender:
    """EmailSender test double: fails the first `fail_times` calls, then records."""

    def __init__(self, fail_times: int = 0):
        self.fail_times = fail_times
        self.calls = 0
        self.sent: list[tuple[list[str], str, str]] = []

    async def __call__(self, to: list[str], subject: str, html: str) -> None:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError(f"boom {self.calls}")
        self.sent.append((to, subject, html))
