"""Pydantic types shared by every module."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, EmailStr, Field, field_validator

AgentRole = Literal["son", "daughter", "friend", "companion"]
ConversationStatus = Literal["in_progress", "pending_analysis", "analyzed", "analysis_failed"]


class Profile(BaseModel):
    parent_name: str = Field(min_length=1)
    parent_nickname: str = Field(min_length=1)
    agent_role: AgentRole
    agent_name: str = Field(min_length=1)
    personal_details: str = ""
    language: str = "en"
    child_emails: list[EmailStr] = Field(min_length=1)
    summary_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    timezone: str

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
            raise ValueError(f"unknown IANA time zone: {v!r}") from exc
        return v


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    text: str
    at: datetime


class WarningItem(BaseModel):
    level: Literal["urgent", "mild"]
    reason: str
    quote: str


class AnalysisResult(BaseModel):
    summary: str
    mood: Literal["good", "okay", "low"]
    warnings: list[WarningItem] = Field(default_factory=list)


class Conversation(BaseModel):
    id: int
    room_name: str
    started_at: datetime
    ended_at: datetime | None = None
    end_reason: str | None = None
    transcript: list[Turn] = Field(default_factory=list)
    status: ConversationStatus
    analysis_attempts: int = 0
    summary: str | None = None
    mood: str | None = None
    warnings: list[WarningItem] = Field(default_factory=list)
