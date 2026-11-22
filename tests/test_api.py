from fastapi.testclient import TestClient
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
