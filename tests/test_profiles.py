from datetime import datetime, timedelta
import sqlite3
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.deco import Client, MockAdapter
from app.main import SUPERVISOR_IP, create_app
from app.store import Store


ROME = ZoneInfo("Europe/Rome")
DEVICE_1 = "02:00:00:00:00:01"
DEVICE_2 = "02:00:00:00:00:02"
ADMIN = "02:00:00:00:00:03"
NIGHT = {"weekdays": [0, 1, 2, 3, 4], "start_time": "20:30", "end_time": "07:00", "kind": "bedtime"}
PAYLOAD = {"name": "Alice", "avatar": "A", "age_band": "10–12", "description": "Profilo di prova"}


@pytest.fixture
def ha_client(tmp_path, monkeypatch):
    class Clock(datetime):
        instant = datetime(2026, 11, 23, 21, 0, tzinfo=ROME)

        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return cls.instant.replace(tzinfo=None)
            return cls.instant.astimezone(tz)

    for module in ("main", "engine", "profiles"):
        monkeypatch.setattr(f"app.{module}.datetime", Clock)
    snapshots = [
        Client(DEVICE_1, "iPhone", "192.168.1.2", True, "phone", up_kbs=0, down_kbs=40),
        Client(DEVICE_2, "iPad", "192.168.1.3", True, "tablet"),
        Client(ADMIN, "Home Assistant", "192.168.1.4", True),
    ]

    class ReadOnlyDeco(MockAdapter):
        def clients(self):
            return list(snapshots)

        def block(self, *_):
            pytest.fail("L'anteprima ha tentato di scrivere sul router")

        unblock = block

    settings = Settings(mode="deco", ha_ingress=True, database_path=str(tmp_path / "family.sqlite"),
                        protected_macs=(ADMIN,))

    def connect():
        return TestClient(create_app(settings, adapter=ReadOnlyDeco()), client=(SUPERVISOR_IP, 9000),
                          headers={"X-Family-Action": "1"})

    return connect, snapshots, Clock


def test_profile_rules_restart_expiry_and_mac_identity(ha_client):
    connect, snapshots, clock = ha_client
    with connect() as client:
        client.post("/api/refresh").raise_for_status()
        alice = client.post("/api/users", json=PAYLOAD).raise_for_status().json()["id"]
        anna = client.post("/api/users", json={"name": "Anna"}).raise_for_status().json()["id"]
        for mac in (DEVICE_1, DEVICE_2, ADMIN):
            client.post("/api/assign", json={"mac": mac, "user_id": alice}).raise_for_status()
        path = f"/api/users/{alice}"
        assert client.post(path + "/schedules", json={**NIGHT, "weekdays": []}).status_code == 422
        client.post(path + "/schedules", json=NIGHT).raise_for_status()
        preview = client.get(path + "/preview").raise_for_status().json()
        assert preview["reason"] == "bedtime" and preview["next_release"] == "2026-11-24T07:00:00+01:00"
        assert {row["mac"]: row["reason"] for row in preview["devices"]} == {
            DEVICE_1: "bedtime", DEVICE_2: "bedtime", ADMIN: None,
        }
        client.put(path + f"/devices/{DEVICE_2}/exception", json={"exempt": True}).raise_for_status()
        assert {row["mac"]: row["reason"] for row in client.get(path + "/preview").json()["devices"]}[DEVICE_2] is None

        client.post(path + "/action", json={"action": "block"}).raise_for_status()
        assert client.get(path + "/preview").json()["reason"] == "manuale"
        client.post(path + "/action", json={"action": "pause", "minutes": 15}).raise_for_status()
        preview = client.get(path + "/preview").json()
        assert preview["reason"] is None
        assert datetime.fromisoformat(preview["override_until"]) == clock.instant + timedelta(minutes=15)
        assert client.get("/api/state").json()["writes_enabled"] is False

    clock.instant += timedelta(minutes=14)
    snapshots[0].ip = "192.168.1.9"
    with connect() as restarted:
        restarted.post("/api/refresh").raise_for_status()
        state = restarted.get("/api/state").json()
        user = next(person for person in state["users"] if person["id"] == alice)
        assert user["avatar"] == "A" and user["description"] == PAYLOAD["description"]
        assert any(row["user_id"] == alice and row["ip"] == "192.168.1.9" for row in state["devices"])
        assert restarted.get(path + "/preview").json()["reason"] is None
        clock.instant += timedelta(minutes=1)
        assert restarted.get(path + "/preview").json()["reason"] == "manuale"
        restarted.post(path + "/action", json={"action": "unblock"}).raise_for_status()
        assert restarted.get(path + "/preview").json()["next_block"] == "2026-11-24T20:30:00+01:00"
        restarted.post(path + "/action", json={"action": "resume"}).raise_for_status()
        assert restarted.get(path + "/preview").json()["reason"] == "bedtime"
        restarted.post("/api/assign", json={"mac": DEVICE_2, "user_id": anna}).raise_for_status()
        restarted.put(path, json={**PAYLOAD, "name": "Alice A."}).raise_for_status()
        devices = restarted.get(path + "/preview").json()["devices"]
        assert {row["mac"]: (row["protected"], row["reason"]) for row in devices} == {
            DEVICE_1: (False, "bedtime"), ADMIN: (True, None),
        }
        restarted.delete(path).raise_for_status()
        state = restarted.get("/api/state").json()
        assert {row["mac"]: (row["user_id"], row["profile_exempt"]) for row in state["devices"]} == {
            DEVICE_1: (None, 0), DEVICE_2: (anna, 0), ADMIN: (None, 0),
        }
        assert state["windows"] == []
        assert any(row["mac"] == DEVICE_1 for row in restarted.get("/api/usage").json()["data"])


def test_copy_is_selective_atomic_stale_checked_and_recoverable(ha_client):
    connect, _, _ = ha_client
    with connect() as client:
        alice = client.post("/api/users", json=PAYLOAD).json()["id"]
        anna = client.post("/api/users", json={"name": "Anna"}).json()["id"]
        source = f"/api/users/{alice}"
        target = f"/api/users/{anna}"
        client.post(source + "/schedules", json={**NIGHT, "label": "Notti scolastiche"}).raise_for_status()
        original = client.post(target + "/schedules", json={**NIGHT, "label": "Prima della copia"}).json()["id"]
        client.put(target + "/weekly-quotas", json={"days": {"0": 60, "6": 120}}).raise_for_status()
        payload = {"source_id": alice, "groups": ["bedtime"]}
        old = client.post(target + "/copy/preview", json=payload).raise_for_status().json()
        assert old["before"]["bedtime"][0]["label"] == "Prima della copia"
        assert old["after"]["bedtime"][0]["label"] == "Notti scolastiche"
        client.put(target + f"/schedules/{original}", json={**NIGHT, "label": "Aggiornata"}).raise_for_status()
        assert client.post(target + "/copy", json={**payload, "revision": old["revision"]}).status_code == 409
        current = client.post(target + "/copy/preview", json=payload).json()
        copy_id = client.post(target + "/copy", json={**payload, "revision": current["revision"]}).raise_for_status().json()["id"]
        state = client.get("/api/state").json()
        assert [row["label"] for row in state["windows"] if row["user_id"] == anna] == ["Notti scolastiche"]
        assert sorted(row["minutes"] for row in state["weekly_quotas"]) == [60, 120]

    with connect() as restarted:
        history = restarted.get(target + "/copy").raise_for_status().json()
        assert history[0]["id"] == copy_id and history[0]["groups"] == ["bedtime"]
        restore = target + f"/copy/{copy_id}/restore"
        restarted.post(restore).raise_for_status()
        state = restarted.get("/api/state").json()
        assert [row["label"] for row in state["windows"] if row["user_id"] == anna] == ["Aggiornata"]
        assert restarted.post(restore).status_code == 409


def test_schedule_pause_enable_and_mutation_guard(ha_client):
    connect, _, clock = ha_client
    with connect() as client:
        alice = client.post("/api/users", json=PAYLOAD).json()["id"]
        path = f"/api/users/{alice}"
        assert client.get("/dashboard.js").headers["cache-control"] == "no-store"
        assert client.post(path + "/action", headers={"X-Family-Action": "0"},
                           json={"action": "block"}).status_code == 403
        rule_id = client.post(path + "/schedules", json=NIGHT).json()["id"]
        rule_uri = path + f"/schedules/{rule_id}"
        client.post(rule_uri + "/pause", json={"minutes": 30}).raise_for_status()
        assert client.get(path + "/preview").json()["schedules"][str(rule_id)] == {"active": False, "paused": True}
        clock.instant += timedelta(minutes=30)
        assert client.get(path + "/preview").json()["reason"] == "bedtime"
        client.put(rule_uri, json={**NIGHT, "enabled": False}).raise_for_status()
        assert client.get(path + "/preview").json()["reason"] is None
        client.delete(rule_uri).raise_for_status()
        assert client.get("/api/state").json()["windows"] == []


def test_usage_report_does_not_invent_missing_speed_data(ha_client):
    connect, _, _ = ha_client
    with connect() as client:
        client.post("/api/refresh").raise_for_status()
        alice = client.post("/api/users", json=PAYLOAD).json()["id"]
        for mac in (DEVICE_1, DEVICE_2):
            client.post("/api/assign", json={"mac": mac, "user_id": alice}).raise_for_status()
        result = client.get(f"/api/usage?user_id={alice}&days=1").raise_for_status().json()
        assert result["profiles"] == [{"day": "2026-11-23", "user_id": alice, "minutes": 1}]
        assert len(result["data"]) == 2
        missing = next(row for row in result["data"] if row["mac"] == DEVICE_2)
        assert missing["up_samples"] == 0 and missing["average_up_kbs"] is None
        zero = next(row for row in result["data"] if row["mac"] == DEVICE_1)
        assert zero["up_samples"] == 1 and zero["average_up_kbs"] == 0
        custom = client.get(f"/api/usage?start=2026-11-23&end=2026-11-23&mac={DEVICE_2}").json()
        assert custom["data"] == [missing]
        assert client.get("/api/usage?start=2026-11-23").status_code == 422
        assert client.delete("/api/usage?through=2026-11-23").json()["removed"] == 3
        assert client.get("/api/usage").json()["data"] == []


def test_migrate_legacy_sqlite_and_prune_observations(tmp_path):
    path = str(tmp_path / "legacy.sqlite")
    with sqlite3.connect(path) as legacy:
        legacy.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, quota_minutes INTEGER, manually_blocked INTEGER);
        CREATE TABLE devices (mac TEXT PRIMARY KEY, name TEXT, ip TEXT, online INTEGER, client_type TEXT,
            user_id INTEGER, last_seen TEXT, managed_block INTEGER, manually_blocked INTEGER, quota_minutes INTEGER,
            last_action TEXT, last_error TEXT);
        CREATE TABLE windows (id INTEGER PRIMARY KEY, user_id INTEGER, weekday INTEGER,
            start_time TEXT, end_time TEXT);
        INSERT INTO users VALUES (1,'Alice',30,0);
        INSERT INTO devices VALUES ('02:00:00:00:00:01','iPhone','192.168.1.2',1,'phone',1,NULL,0,0,NULL,'','');
        INSERT INTO windows VALUES (1,1,0,'20:30','07:00');
        CREATE TABLE minute_samples (day TEXT, minute TEXT, mac TEXT, up_kbs INTEGER, down_kbs INTEGER,
            PRIMARY KEY (minute,mac));
        INSERT INTO minute_samples VALUES ('2026-11-23','2026-11-23T19:05Z','02:00:00:00:00:01',NULL,4);
        """)
    for _ in range(2):
        store = Store(path)
        assert store.users()[0]["avatar"] == ""
        assert store.windows()[0]["kind"] == "offtime"
        assert store.devices()[0]["last_online"] == "2026-11-23T19:05:00+00:00"
        assert store.usage("2026-11-23")["users"] == {1: 1}
        store.close()
    store = Store(path)
    store.prune_samples(1, "2026-11-24")
    assert store.usage("2026-11-23")["users"] == {}
    assert store.devices()[0]["user_id"] == 1
    store.close()


def test_sample_retention_saved_pruned_and_applied_after_restart(ha_client):
    connect, snapshots, clock = ha_client
    with connect() as client:
        client.post("/api/refresh").raise_for_status()
        store = client.app.state.store
        for age in (7, 6):
            moment = clock.instant - timedelta(days=age)
            store.record_inventory(snapshots, moment.isoformat(), moment.date().isoformat(),
                                   moment.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%MZ"))
        uri = "/api/usage/retention"
        assert client.put(uri, json={"days": 8}).status_code == 422
        assert client.put(uri, headers={"X-Family-Action": "0"}, json={"days": 7}).status_code == 403
        assert client.put(uri, json={"days": 7}).raise_for_status().json()["removed"] == len(snapshots)
        report = client.get("/api/usage?days=30").raise_for_status().json()
        assert report["retention_override"] == report["retention_days"] == 7
        assert {row["day"] for row in report["data"]} == {"2026-11-17", "2026-11-23"}

    clock.instant += timedelta(days=1)
    with connect() as restarted:
        restarted.post("/api/refresh").raise_for_status()
        report = restarted.get("/api/usage?days=30").raise_for_status().json()
        assert report["retention_override"] == report["retention_days"] == 7
        assert {row["day"] for row in report["data"]} == {"2026-11-23", "2026-11-24"}
        restarted.put(uri, json={"days": None}).raise_for_status()
        report = restarted.get("/api/usage").json()
        assert report["retention_override"] is None and report["retention_days"] == 0
