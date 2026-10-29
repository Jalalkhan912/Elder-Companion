import pytest
from fastapi.testclient import TestClient
from livekit.api import TokenVerifier

from conftest import FakeLLM, FakeSender
from voice_agent.web.app import create_app


@pytest.fixture
def client(db, settings):
    app = create_app(db, settings, FakeLLM("{}"), FakeSender(), start_scheduler=False)
    with TestClient(app, follow_redirects=False) as c:
        yield c


def body(profile, pin="1234", **over):
    p = profile.model_dump()
    p.update(over)
    return {"pin": pin, "profile": p}


def test_root_redirects_to_setup_then_talk(client, profile):
    r = client.get("/")
    assert r.status_code == 307 and r.headers["location"].endswith("/setup")
    assert client.get("/talk").status_code == 307
    assert client.get("/setup").status_code == 200
    assert client.get("/api/status").json() == {
        "configured": False, "parent_nickname": None, "agent_name": None}
    assert client.post("/api/setup", json=body(profile)).status_code == 200
    r = client.get("/")
    assert r.status_code == 307 and r.headers["location"].endswith("/talk")
    assert client.get("/talk").status_code == 200
    assert client.get("/static/talk.html").status_code == 200
    assert client.get("/api/status").json() == {
        "configured": True, "parent_nickname": "Abbu", "agent_name": "Ayesha"}


def test_first_setup_sets_pin_and_saves(client, db, profile):
    r = client.post("/api/setup", json=body(profile))
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert db.get_profile() == profile
    assert db.get_pin_hash().startswith("scrypt$")
    assert "1234" not in db.get_pin_hash()


@pytest.mark.parametrize("over", [
    {"child_emails": ["not-an-email"]},
    {"child_emails": []},
    {"timezone": "Mars/Base"},
    {"summary_time": "25:00"},
    {"pin": "12ab"},
])
def test_setup_validation_rejects_and_saves_nothing(client, db, profile, over):
    pin = over.pop("pin", "1234")
    r = client.post("/api/setup", json=body(profile, pin=pin, **over))
    assert r.status_code == 422
    assert db.get_profile() is None


def test_update_requires_correct_pin(client, db, profile):
    client.post("/api/setup", json=body(profile))
    changed = body(profile, pin="0000", parent_nickname="Dad")
    for _ in range(5):
        assert client.post("/api/setup", json=changed).status_code == 401
    assert db.get_profile().parent_nickname == "Abbu"
    good = body(profile, parent_nickname="Dad")
    assert client.post("/api/setup", json=good).status_code == 423
    assert client.post("/api/setup/unlock", json={"pin": "1234"}).status_code == 423


def test_update_with_correct_pin_keeps_hash(client, db, profile):
    client.post("/api/setup", json=body(profile))
    h = db.get_pin_hash()
    r = client.post("/api/setup", json=body(profile, parent_nickname="Dad"))
    assert r.status_code == 200
    assert db.get_profile().parent_nickname == "Dad"
    assert db.get_pin_hash() == h
    assert client.post("/api/setup", json=body(profile, pin="12ab")).status_code == 401


def test_unlock_returns_profile(client, profile):
    assert client.post("/api/setup/unlock", json={"pin": "1234"}).status_code == 404
    client.post("/api/setup", json=body(profile))
    assert client.post("/api/setup/unlock", json={"pin": "9999"}).status_code == 401
    r = client.post("/api/setup/unlock", json={"pin": "1234"})
    assert r.status_code == 200
    assert r.json() == profile.model_dump(mode="json")


def test_token_requires_profile(client):
    assert client.post("/api/token").status_code == 409


def test_token_contains_agent_dispatch(client, profile, settings):
    client.post("/api/setup", json=body(profile))
    r = client.post("/api/token")
    assert r.status_code == 200
    data = r.json()
    assert data["url"] == settings.livekit_url
    assert data["room"].startswith("companion-")
    claims = TokenVerifier(settings.livekit_api_key, settings.livekit_api_secret).verify(data["token"])
    assert claims.identity == "parent"
    assert claims.video.room_join and claims.video.room == data["room"]
    assert claims.room_config.agents[0].agent_name == "elder-companion"


def test_scheduler_starts_and_stops_with_app(db, settings):
    app = create_app(db, settings, FakeLLM("{}"), FakeSender(), start_scheduler=True)
    with TestClient(app) as c:
        assert c.get("/api/status").status_code == 200
