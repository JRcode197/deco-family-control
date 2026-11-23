import asyncio
import sqlite3
from threading import Event

from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.deco import MockAdapter
from app.main import create_app
from app.store import Store

AUTH = ("admin", "strong_password_only_testing_123")


def test_auth_and_assign(tmp_path):
    store = Store(str(tmp_path / "a.sqlite"))
    cfg = Settings(admin_password=AUTH[1], database_path=str(tmp_path / "a.sqlite"))
    app = create_app(cfg, store=store, adapter=MockAdapter())
    with TestClient(app) as client:
        assert client.get("/api/state").status_code == 401
        r = client.get("/api/state", auth=AUTH)
        assert r.status_code == 200
        mac = r.json()["devices"][0]["mac"]
        assert client.post("/api/users", auth=AUTH, json={"name": "Anna"}).status_code == 403
        hdr = {"X-Family-Action": "1"}
        uid = client.post("/api/users", auth=AUTH, headers=hdr, json={"name": "Anna"}).json()["id"]
        assert client.post("/api/assign", auth=AUTH, headers=hdr,
                           json={"mac": mac, "user_id": uid}).status_code == 200
        users = client.get("/api/state", auth=AUTH).json()["devices"]
        assert next(d for d in users if d["mac"] == mac)["user_id"] == uid


def test_schedule_validation(tmp_path):
    cfg = Settings(admin_password=AUTH[1], database_path=str(tmp_path / "b.sqlite"))
    app = create_app(cfg, store=Store(cfg.database_path), adapter=MockAdapter())
    hdr = {"X-Family-Action": "1"}
    with TestClient(app) as client:
        uid = client.post("/api/users", auth=AUTH, headers=hdr, json={"name": "Alice"}).json()["id"]
        r = client.post("/api/windows", auth=AUTH, headers=hdr, json={
            "user_id": uid, "weekday": 0, "start_time": "20:30", "end_time": "07:00"
        })
        assert r.status_code == 200
        assert client.get("/api/state", auth=AUTH).json()["windows"][0]["user_id"] == uid


def test_shutdown_waits_for_poll_before_closing_store(tmp_path):
    cfg = Settings(database_path=str(tmp_path / "shutdown.sqlite"))
    store = Store(cfg.database_path)
    polling = Event()
    finish_poll = Event()

    class PendingPoll(MockAdapter):
        def clients(self):
            polling.set()
            assert finish_poll.wait(timeout=5)
            return super().clients()

    app = create_app(cfg, store=store, adapter=PendingPoll())

    async def exercise_lifespan():
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        try:
            assert await asyncio.to_thread(polling.wait, 5)
        except BaseException:
            finish_poll.set()
            await lifespan.__aexit__(None, None, None)
            raise
        shutdown = asyncio.create_task(lifespan.__aexit__(None, None, None))
        try:
            await asyncio.sleep(0)
            assert not shutdown.done()
            assert store.users() == []
        finally:
            finish_poll.set()
            await shutdown

    asyncio.run(exercise_lifespan())
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        store.users()


def test_home_assistant_ingress_requires_supervisor_and_action_header(monkeypatch):
    from app.config import from_env
    from app.main import SUPERVISOR_IP

    monkeypatch.setenv("DECO_HA_INGRESS", "true")
    monkeypatch.setenv("DECO_MODE", "deco")
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    settings = from_env()
    assert settings.mode == "mock"
    assert settings.writes_enabled is False
    assert settings.database_path == ":memory:"

    with TestClient(create_app(settings), client=("192.168.1.15", 9000)) as direct:
        spoofed = {"X-Forwarded-For": SUPERVISOR_IP, "X-Ingress-Path": "/ingress"}
        for path in ("/", "/api/diagnostics", "/api/state"):
            assert direct.get(path, headers=spoofed).status_code == 403
        assert direct.post("/api/diagnostics/deco", headers=spoofed).status_code == 403

    with TestClient(create_app(settings), client=(SUPERVISOR_IP, 9000)) as via_ha:
        assert via_ha.get("/").status_code == 200
        assert via_ha.get("/api/state").json()["ha_ingress"] is True
        assert via_ha.post("/api/diagnostics").status_code == 403
        report = via_ha.post("/api/diagnostics", headers={"X-Family-Action": "1"})
        assert report.status_code == 200
        assert report.json()["inventory"]["status"] == "simulated"


def test_deco_login_form_defaults_to_documented_local_api_user():
    """Do not mislead users into entering a personal name as the Deco API username."""
    from pathlib import Path

    html = (Path(__file__).resolve().parents[1] / "app" / "static" / "index.html").read_text()
    assert 'id="deco-username" value="admin"' in html
    assert 'password.value=""' in html
    assert "password del TP-Link ID" in html
    assert "potrebbe esserci un problema di protocollo" in html
