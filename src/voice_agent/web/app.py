"""FastAPI backend: PIN-protected setup, LiveKit token, static pages."""
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from livekit.api import AccessToken, RoomAgentDispatch, RoomConfiguration, VideoGrants
from pydantic import BaseModel

from voice_agent.analysis import InferenceTextLLM, TextLLM
from voice_agent.config import AGENT_NAME, Settings, load_settings
from voice_agent.db import Database
from voice_agent.models import Profile
from voice_agent.notify import EmailSender, resend_sender
from voice_agent.schedule import build_scheduler
from voice_agent.security import PIN_PATTERN, PinLockout, hash_pin, verify_pin

STATIC_DIR = Path(__file__).resolve().parent / "static"


class UnlockBody(BaseModel):
    pin: str


class SetupBody(BaseModel):
    pin: str
    profile: Profile


def create_app(db: Database, settings: Settings, llm: TextLLM, sender: EmailSender,
               start_scheduler: bool = True) -> FastAPI:
    lockout = PinLockout()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        scheduler = build_scheduler(db, llm, sender) if start_scheduler else None
        if scheduler:
            scheduler.start()
        try:
            yield
        finally:
            if scheduler:
                scheduler.shutdown(wait=False)

    app = FastAPI(lifespan=lifespan)

    def check_pin(pin: str, pin_hash: str) -> None:
        if lockout.is_locked():
            raise HTTPException(423, "Too many wrong tries. Try again later.")
        if not verify_pin(pin, pin_hash):
            lockout.record_failure()
            raise HTTPException(401, "Wrong PIN.")
        lockout.reset()

    @app.get("/")
    def root():
        target = "/talk" if db.get_profile() else "/setup"
        return RedirectResponse(target, status_code=307)

    @app.get("/talk")
    def talk():
        if db.get_profile() is None:
            return RedirectResponse("/setup", status_code=307)
        return FileResponse(STATIC_DIR / "talk.html")

    @app.get("/setup")
    def setup_page():
        return FileResponse(STATIC_DIR / "setup.html")

    @app.get("/api/status")
    def status():
        p = db.get_profile()
        return {"configured": p is not None,
                "parent_nickname": p.parent_nickname if p else None,
                "agent_name": p.agent_name if p else None}

    @app.post("/api/setup/unlock")
    def unlock(body: UnlockBody):
        profile = db.get_profile()
        pin_hash = db.get_pin_hash()
        if profile is None or pin_hash is None:
            raise HTTPException(404, "Not set up yet.")
        check_pin(body.pin, pin_hash)
        return profile.model_dump(mode="json")

    @app.post("/api/setup")
    def setup(body: SetupBody):
        pin_hash = db.get_pin_hash()
        if pin_hash is None:
            if not re.fullmatch(PIN_PATTERN, body.pin):
                raise HTTPException(422, "PIN must be 4 to 8 digits.")
            db.save_profile(body.profile, hash_pin(body.pin))
        else:
            check_pin(body.pin, pin_hash)
            db.save_profile(body.profile, None)
        return {"ok": True}

    @app.post("/api/token")
    def token():
        if db.get_profile() is None:
            raise HTTPException(409, "Not set up yet.")
        room = f"companion-{uuid.uuid4().hex[:12]}"
        jwt = (AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
               .with_identity("parent")
               .with_grants(VideoGrants(room_join=True, room=room))
               .with_room_config(RoomConfiguration(
                   agents=[RoomAgentDispatch(agent_name=AGENT_NAME)]))
               .to_jwt())
        return {"url": settings.livekit_url, "token": jwt, "room": room}

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def create_app_from_env() -> FastAPI:
    settings = load_settings()  # loads .env.local before InferenceTextLLM needs LIVEKIT_*
    db = Database(settings.db_path)
    db.init_schema()
    return create_app(db, settings, InferenceTextLLM(),
                      resend_sender(settings.resend_api_key, settings.email_from))
