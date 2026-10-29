"""Shared pytest fixtures (extended by later tasks)."""
import pytest

from voice_agent.db import Database
from voice_agent.models import Profile


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "t.db")
    database.init_schema()
    return database


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
