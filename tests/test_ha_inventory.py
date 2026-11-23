from dataclasses import replace
import json
from pathlib import Path
import re

from fastapi.testclient import TestClient
import pytest

from app.config import from_env
from app.deco import Client
from app.diagnostics import Capability, Diagnostics
from app.main import SUPERVISOR_IP, create_app


GATEWAY = "192.168.42.2"
FIXTURE_SECRET = "inventory-fixture-secret"
MAC = "0A:11:22:33:44:55"
ACTION = {"X-Family-Action": "1"}


def set_ha_options(tmp_path, monkeypatch, **overrides):
    options_path = tmp_path / "options.json"
    options_path.write_text(json.dumps({
        "inventory_enabled": True,
        "deco_ip": GATEWAY,
        "deco_username": " fixture-admin ",
        "deco_password": FIXTURE_SECRET,
        **overrides,
    }))
    monkeypatch.setenv("DECO_HA_INGRESS", "true")
    monkeypatch.setattr("app.config.HA_OPTIONS_PATH", options_path)


@pytest.mark.parametrize("invalid", [
    {"deco_ip": "https://192.168.42.2"},
    {"deco_ip": "127.0.0.1"},
    {"deco_password": ""},
    {"deco_username": "  "},
])
def test_enabled_inventory_requires_valid_local_options(tmp_path, monkeypatch, invalid):
    set_ha_options(tmp_path, monkeypatch, **invalid)
    with pytest.raises(ValueError) as failure:
        from_env()
    assert FIXTURE_SECRET not in str(failure.value)


def test_home_assistant_inventory_assignment_and_history_persist(tmp_path, monkeypatch):
    set_ha_options(tmp_path, monkeypatch)
    settings = from_env()
    assert settings.mode == "deco"
    assert settings.router_ip == GATEWAY
    assert not settings.writes_enabled
    assert settings.database_path == "/data/inventory/family.sqlite3"
    assert FIXTURE_SECRET not in repr(settings)
    database_path = tmp_path / "inventory.sqlite3"
    settings = replace(settings, database_path=str(database_path))
    snapshot = [Client(MAC, "iPhone di prova reale", "192.168.42.110", True, up_kbs=0, down_kbs=23)]
    failing = False

    class FakeDeco:
        def __init__(self, ip, username, password, *, direct):
            assert (ip, username, password, direct) == (GATEWAY, "fixture-admin", FIXTURE_SECRET, True)

        def clients(self):
            if failing:
                raise RuntimeError(FIXTURE_SECRET)
            return list(snapshot)

        def diagnostics(self):
            return Diagnostics(mode="deco", inventory=Capability("read_only_detected", len(snapshot)))

        def block(self, *_):
            pytest.fail("Read-only Home Assistant inventory attempted a router write")

        unblock = block

    monkeypatch.setattr("app.main.DecoSdkAdapter", FakeDeco)
    with TestClient(create_app(settings), client=(SUPERVISOR_IP, 9000)) as client:
        assert client.post("/api/refresh", headers=ACTION).status_code == 200
        state = client.get("/api/state")
        assert state.headers["cache-control"] == "no-store"
        manifest = Path(__file__).resolve().parents[1] / "config.yaml"
        assert re.search(r'^version: "([^"]+)"$', manifest.read_text(), re.M)[1] == state.json()["release"]
        [device] = state.json()["devices"]
        assert device["mac"] == MAC and device["online"] and device["in_latest_snapshot"]
        assert device["up_kbs"] == 0 and device["down_kbs"] == 23
        assert FIXTURE_SECRET not in state.text
        person = client.post("/api/users", headers=ACTION, json={"name": "Alice"}).json()["id"]
        assignment = {"mac": MAC.lower().replace(":", "-"), "user_id": person}
        assert client.post("/api/assign", json=assignment).status_code == 403
        assert client.post("/api/assign", headers=ACTION, json=assignment).status_code == 200

        for path, data in (
            ("/api/manual-block", {"mac": MAC, "blocked": True}),
            ("/api/quota", {"mac": MAC, "minutes": 5}),
            ("/api/windows", {"user_id": person, "weekday": 1, "start_time": "08:00", "end_time": "09:00"}),
        ):
            assert client.post(path, headers=ACTION, json=data).status_code == 403

        report = client.post("/api/diagnostics", headers=ACTION)
        assert report.json()["inventory"]["count"] == 1
        assert MAC not in report.text and FIXTURE_SECRET not in report.text
        failing = True
        failed = client.post("/api/refresh", headers=ACTION)
        assert failed.status_code == 503 and FIXTURE_SECRET not in failed.text
        assert client.get("/api/state").json()["devices"][0]["user_id"] == person
        failing = False
        snapshot.clear()
        assert client.post("/api/refresh", headers=ACTION).status_code == 200
        [offline] = client.get("/api/state").json()["devices"]
        assert not offline["online"] and not offline["in_latest_snapshot"]
        assert offline["user_id"] == person and offline["down_kbs"] is None

    with TestClient(create_app(settings), client=(SUPERVISOR_IP, 9000)) as restarted:
        assert restarted.post("/api/refresh", headers=ACTION).status_code == 200
        assert restarted.get("/api/state").json()["users"][0]["name"] == "Alice"
        [device] = restarted.get("/api/state").json()["devices"]
        assert device["user_id"] == person and not device["online"]
        [daily] = restarted.get("/api/usage").json()["data"]
        assert daily["mac"] == MAC and daily["minutes"] == 1 and daily["average_down_kbs"] == 23
        snapshot.append(Client(MAC, "iPhone rinominato", "192.168.42.111", True, down_kbs=7))
        assert restarted.post("/api/refresh", headers=ACTION).status_code == 200
        [renamed] = restarted.get("/api/state").json()["devices"]
        assert renamed["name"] == "iPhone rinominato" and renamed["user_id"] == person
        assert renamed["in_latest_snapshot"]
    assert FIXTURE_SECRET.encode() not in database_path.read_bytes()
