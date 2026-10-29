from pathlib import Path

import pytest

from voice_agent.config import ConfigError, load_settings

FULL = {"LIVEKIT_URL": "wss://x.livekit.cloud", "LIVEKIT_API_KEY": "k",
        "LIVEKIT_API_SECRET": "s", "RESEND_API_KEY": "r", "EMAIL_FROM": "a@b.com"}


def test_load_settings_reads_values_and_default_db_path():
    s = load_settings(FULL)
    assert s.livekit_url == "wss://x.livekit.cloud"
    assert s.db_path == Path("data/companion.db")


def test_load_settings_lists_all_missing():
    with pytest.raises(ConfigError) as e:
        load_settings({"LIVEKIT_URL": "wss://x"})
    for name in ["LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "RESEND_API_KEY", "EMAIL_FROM"]:
        assert name in str(e.value)
