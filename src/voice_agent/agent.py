"""LiveKit worker: CompanionAgent and the rtc_session entrypoint.

Run with `uv run python -m voice_agent.agent dev` (or `console`, `start`).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from livekit import agents
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    RunContext,
    TurnHandlingOptions,
    function_tool,
    get_job_context,
    inference,
)
from livekit.plugins import silero

from voice_agent.analysis import InferenceTextLLM
from voice_agent.config import (
    AGENT_NAME,
    LLM_MODEL,
    MAX_CALL_SECONDS,
    MEMORY_SIZE,
    STT_MODEL,
    TTS_MODEL,
    TTS_VOICE,
    USER_AWAY_SECONDS,
    load_settings,
)
from voice_agent.db import Database
from voice_agent.notify import resend_sender
from voice_agent.persona import GOODBYE_INSTRUCTIONS, build_instructions, greeting_instructions
from voice_agent.postcall import process_conversation
from voice_agent.transcript import TranscriptRecorder

log = logging.getLogger(__name__)


class CompanionAgent(Agent):
    def __init__(self, instructions: str) -> None:
        super().__init__(instructions=instructions)
        # Why the call ended; read by the entrypoint's shutdown callback.
        self.end_reason: str | None = None

    @function_tool
    async def end_conversation(self, context: RunContext) -> None:
        """Call after you and the parent have said goodbye."""
        if self.end_reason is None:  # keep "silence"/"time_limit" if those started the goodbye
            self.end_reason = "goodbye"
        session = context.session

        def _shutdown_after_speech(_handle: object) -> None:
            job = get_job_context(required=False)
            if job is not None:
                job.shutdown(reason="goodbye")
            else:  # e.g. text-only test sessions with no job
                session.shutdown()

        # The speech handle completes once the current turn (incl. this tool) has played out.
        context.speech_handle.add_done_callback(_shutdown_after_speech)


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    try:
        settings = load_settings()
        db = Database(settings.db_path)
        db.init_schema()
        profile = db.get_profile()
    except Exception:
        log.exception("could not load settings or profile; not starting the call")
        return
    if profile is None:
        log.warning("no profile has been set up yet; not starting the call")
        return

    # A database failure here must not stop the call: continue without memory/recording.
    conv_id: int | None = None
    try:
        conv_id = db.create_conversation(ctx.room.name, datetime.now(UTC))
    except Exception:
        log.exception("could not create conversation row; this call will not be recorded")
    recent = []
    try:
        recent = db.recent_analyzed(MEMORY_SIZE)
    except Exception:
        log.exception("could not load recent conversations; continuing without memory")

    agent = CompanionAgent(build_instructions(profile, recent))
    recorder = TranscriptRecorder()
    session = AgentSession(
        stt=inference.STT(model=STT_MODEL, language=profile.language),
        llm=inference.LLM(model=LLM_MODEL),
        tts=inference.TTS(model=TTS_MODEL, voice=TTS_VOICE),
        vad=ctx.proc.userdata["vad"],
        turn_handling=TurnHandlingOptions(turn_detection=inference.TurnDetector()),
        user_away_timeout=USER_AWAY_SECONDS,
    )

    background: set[asyncio.Task] = set()
    timer: asyncio.Task | None = None
    ending = False

    def _spawn(coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        background.add(task)
        task.add_done_callback(background.discard)
        return task

    async def _goodbye_and_end(reason: str) -> None:
        nonlocal ending
        if ending or agent.end_reason is not None:
            return
        ending = True
        agent.end_reason = reason
        try:
            await session.generate_reply(instructions=GOODBYE_INSTRUCTIONS)
        except Exception:
            log.exception("goodbye reply failed")
        finally:
            ctx.shutdown(reason=reason)

    async def _time_limit() -> None:
        await asyncio.sleep(MAX_CALL_SECONDS)
        await _goodbye_and_end("time_limit")

    @session.on("conversation_item_added")
    def _on_item(ev) -> None:
        try:
            role = getattr(ev.item, "role", None)
            if role is not None:
                recorder.add(role, ev.item.text_content)
        except Exception:
            log.exception("failed to record conversation item")

    @session.on("user_state_changed")
    def _on_user_state(ev) -> None:
        if ev.new_state == "away":
            _spawn(_goodbye_and_end("silence"))

    @session.on("close")
    def _on_close(_ev) -> None:
        # The parent left (or the session ended otherwise): end the job too.
        ctx.shutdown(reason="session closed")

    async def _on_shutdown() -> None:
        if timer is not None:
            timer.cancel()
        if conv_id is None:
            return
        try:
            db.finish_conversation(conv_id, datetime.now(UTC),
                                   agent.end_reason or "parent_left", recorder.turns)
        except Exception:
            log.exception("could not save conversation %s", conv_id)
            return
        try:
            llm = InferenceTextLLM()
            sender = resend_sender(settings.resend_api_key, settings.email_from)
        except Exception:
            log.exception("could not set up post-call processing; the retry job will pick it up")
            return
        await process_conversation(db, conv_id, llm, sender)

    ctx.add_shutdown_callback(_on_shutdown)

    await session.start(room=ctx.room, agent=agent)
    timer = _spawn(_time_limit())
    try:
        await session.generate_reply(instructions=greeting_instructions(profile))
    except Exception:  # e.g. the parent already left and the session closed
        log.exception("greeting failed")


if __name__ == "__main__":
    agents.cli.run_app(server)
