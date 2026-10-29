"""Settings and model constants, defined in one place."""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

AGENT_NAME = "elder-companion"
STT_MODEL = "assemblyai/universal-3-6-pro"
LLM_MODEL = "google/gemma-4-31b-it"
TTS_MODEL = "fishaudio/s2.1-pro"
TTS_VOICE = "fa4c9eb3dccc4806b382b40d61c6b10a"
MEMORY_SIZE = 5
USER_AWAY_SECONDS = 120
MAX_CALL_SECONDS = 45 * 60
MAX_ANALYSIS_ATTEMPTS = 3

_REQUIRED = ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET",
             "RESEND_API_KEY", "EMAIL_FROM")


class ConfigError(RuntimeError):
    """Raised when required configuration is missing."""


@dataclass(frozen=True)
class Settings:
    livekit_url: str
    livekit_api_key: str
    livekit_api_secret: str
    resend_api_key: str
    email_from: str
    db_path: Path = Path("data/companion.db")


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    if env is None:
        load_dotenv(".env.local")
        env = os.environ
    missing = [name for name in _REQUIRED if not env.get(name)]
    if missing:
        raise ConfigError("Missing required environment variables: " + ", ".join(missing))
    kwargs = {}
    if env.get("DB_PATH"):
        kwargs["db_path"] = Path(env["DB_PATH"])
    return Settings(
        livekit_url=env["LIVEKIT_URL"],
        livekit_api_key=env["LIVEKIT_API_KEY"],
        livekit_api_secret=env["LIVEKIT_API_SECRET"],
        resend_api_key=env["RESEND_API_KEY"],
        email_from=env["EMAIL_FROM"],
        **kwargs,
    )
