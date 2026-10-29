"""SQLite repository shared by the LiveKit worker and the web backend."""
from __future__ import annotations

import sqlite3
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import TypeAdapter

from .models import (AnalysisResult, Conversation, ConversationStatus, Profile,
                     Turn, WarningItem)

_TURNS = TypeAdapter(list[Turn])
_WARNINGS = TypeAdapter(list[WarningItem])

_SCHEMA = """
CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    data TEXT NOT NULL,
    pin_hash TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_name TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    end_reason TEXT,
    transcript TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'in_progress',
    analysis_attempts INTEGER NOT NULL DEFAULT 0,
    summary TEXT,
    mood TEXT,
    warnings TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_conv_ended ON conversations(ended_at);
CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('urgent', 'daily')),
    conversation_id INTEGER,
    period_date TEXT,
    recipients TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    created_at TEXT NOT NULL,
    sent_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_email_urgent
    ON emails(conversation_id) WHERE kind = 'urgent';
CREATE UNIQUE INDEX IF NOT EXISTS uq_email_daily
    ON emails(period_date) WHERE kind = 'daily';
"""


def _iso(dt: datetime) -> str:
    """Normalise to UTC ISO-8601 with a fixed format so strings sort correctly."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _now() -> str:
    return _iso(datetime.now(timezone.utc))


def _parse(s: str | None) -> datetime | None:
    if s is None:
        return None
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class Database:
    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=10000")

    def init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # ---- profile ----
    def get_profile(self) -> Profile | None:
        rows = self._query("SELECT data FROM profile WHERE id = 1")
        return Profile.model_validate_json(rows[0]["data"]) if rows else None

    def get_pin_hash(self) -> str | None:
        rows = self._query("SELECT pin_hash FROM profile WHERE id = 1")
        return rows[0]["pin_hash"] if rows else None

    def save_profile(self, profile: Profile, pin_hash: str | None) -> None:
        now = _now()
        self._exec(
            """INSERT INTO profile (id, data, pin_hash, created_at, updated_at)
               VALUES (1, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 data = excluded.data,
                 pin_hash = COALESCE(excluded.pin_hash, profile.pin_hash),
                 updated_at = excluded.updated_at""",
            (profile.model_dump_json(), pin_hash, now, now),
        )

    # ---- conversations ----
    @staticmethod
    def _conv(row: sqlite3.Row) -> Conversation:
        return Conversation(
            id=row["id"],
            room_name=row["room_name"],
            started_at=_parse(row["started_at"]),
            ended_at=_parse(row["ended_at"]),
            end_reason=row["end_reason"],
            transcript=_TURNS.validate_json(row["transcript"]),
            status=row["status"],
            analysis_attempts=row["analysis_attempts"],
            summary=row["summary"],
            mood=row["mood"],
            warnings=_WARNINGS.validate_json(row["warnings"]),
        )

    def create_conversation(self, room_name: str, started_at: datetime) -> int:
        cur = self._exec(
            "INSERT INTO conversations (room_name, started_at, status) VALUES (?, ?, 'in_progress')",
            (room_name, _iso(started_at)),
        )
        return int(cur.lastrowid)

    def finish_conversation(self, conv_id: int, ended_at: datetime, end_reason: str,
                            transcript: list[Turn]) -> None:
        self._exec(
            """UPDATE conversations SET ended_at = ?, end_reason = ?, transcript = ?,
               status = 'pending_analysis' WHERE id = ?""",
            (_iso(ended_at), end_reason, _TURNS.dump_json(transcript).decode(), conv_id),
        )

    def save_analysis(self, conv_id: int, result: AnalysisResult) -> None:
        self._exec(
            """UPDATE conversations SET summary = ?, mood = ?, warnings = ?,
               status = 'analyzed' WHERE id = ?""",
            (result.summary, result.mood,
             _WARNINGS.dump_json(result.warnings).decode(), conv_id),
        )

    def record_analysis_failure(self, conv_id: int, max_attempts: int) -> ConversationStatus:
        with self._lock:
            self._conn.execute(
                """UPDATE conversations SET analysis_attempts = analysis_attempts + 1,
                   status = CASE WHEN analysis_attempts + 1 >= ? THEN 'analysis_failed'
                                 ELSE status END
                   WHERE id = ?""",
                (max_attempts, conv_id),
            )
            self._conn.commit()
            rows = self._conn.execute(
                "SELECT status FROM conversations WHERE id = ?", (conv_id,)).fetchall()
        return rows[0]["status"]

    def get_conversation(self, conv_id: int) -> Conversation | None:
        rows = self._query("SELECT * FROM conversations WHERE id = ?", (conv_id,))
        return self._conv(rows[0]) if rows else None

    def recent_analyzed(self, limit: int) -> list[Conversation]:
        rows = self._query(
            "SELECT * FROM conversations WHERE status = 'analyzed' "
            "ORDER BY started_at DESC, id DESC LIMIT ?", (limit,))
        return [self._conv(r) for r in rows]

    def pending_analysis(self, max_attempts: int) -> list[Conversation]:
        rows = self._query(
            "SELECT * FROM conversations WHERE status = 'pending_analysis' "
            "AND analysis_attempts < ? ORDER BY id", (max_attempts,))
        return [self._conv(r) for r in rows]

    def ended_since(self, since: datetime | None) -> list[Conversation]:
        if since is None:
            rows = self._query(
                "SELECT * FROM conversations WHERE ended_at IS NOT NULL "
                "ORDER BY ended_at, id")
        else:
            rows = self._query(
                "SELECT * FROM conversations WHERE ended_at IS NOT NULL AND ended_at > ? "
                "ORDER BY ended_at, id", (_iso(since),))
        return [self._conv(r) for r in rows]

    # ---- emails ----
    def claim_email(self, kind: Literal["urgent", "daily"], recipients: list[str],
                    conversation_id: int | None = None,
                    period_date: date | None = None) -> int | None:
        try:
            cur = self._exec(
                """INSERT INTO emails (kind, conversation_id, period_date, recipients,
                   status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)""",
                (kind, conversation_id,
                 period_date.isoformat() if period_date else None,
                 ",".join(recipients), _now()),
            )
        except sqlite3.IntegrityError:
            return None
        return int(cur.lastrowid)

    def mark_email(self, email_id: int, status: Literal["sent", "failed"],
                   error: str | None = None) -> None:
        sent_at = _now() if status == "sent" else None
        self._exec("UPDATE emails SET status = ?, error = ?, sent_at = ? WHERE id = ?",
                   (status, error, sent_at, email_id))

    def last_daily_sent_at(self) -> datetime | None:
        rows = self._query(
            "SELECT MAX(sent_at) AS m FROM emails WHERE kind = 'daily' AND status = 'sent'")
        return _parse(rows[0]["m"])
