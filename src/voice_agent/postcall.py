"""Post-call processing: analyze a finished conversation, save, send urgent email."""
import logging

from voice_agent.analysis import AnalysisError, TextLLM, analyze
from voice_agent.config import MAX_ANALYSIS_ATTEMPTS
from voice_agent.db import Database
from voice_agent.notify import EmailSender, send_urgent

log = logging.getLogger(__name__)


async def process_conversation(db: Database, conv_id: int, llm: TextLLM,
                               sender: EmailSender) -> None:
    """Never raises: every failure is logged and swallowed."""
    try:
        conv = db.get_conversation(conv_id)
        if conv is None:
            log.warning("conversation %s not found", conv_id)
            return
        try:
            result = await analyze(conv.transcript, llm)
        except AnalysisError as exc:
            log.warning("analysis failed for conversation %s: %s", conv_id, exc)
            status = db.record_analysis_failure(conv_id, MAX_ANALYSIS_ATTEMPTS)
            log.info("conversation %s status now %s", conv_id, status)
            return
        db.save_analysis(conv_id, result)
        if any(w.level == "urgent" for w in result.warnings):
            profile = db.get_profile()
            if profile is not None:
                await send_urgent(db, profile, db.get_conversation(conv_id), sender)
    except Exception:
        log.exception("post-call processing failed for conversation %s", conv_id)
