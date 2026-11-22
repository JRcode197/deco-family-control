import logging
import sys
from types import ModuleType, SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.deco import DecoSdkAdapter, MockAdapter
from app.diagnostics import DecoAccessError
from app.main import create_app


GATEWAY = "192.0.2.9"
PRIVATE_DETAIL = "test-secret;stok=private-token"
READS = [
    ("admin/client", "client_list", "read"),
    ("admin/device", "device_list", "read"),
    ("admin/client", "black_list", "list"),
]


@pytest.fixture
def sdk(monkeypatch):
    module = ModuleType("tplink_deco_api")

    class TransportError(Exception):
        def __init__(self, status_code=None):
            super().__init__(PRIVATE_DETAIL)
            self.status_code = status_code

    module.TransportError = TransportError
    module.AuthenticationError = type("AuthenticationError", (Exception,), {})
    module.ApiError = type("ApiError", (Exception,), {})
    module.ClientDevice = SimpleNamespace(from_api=lambda row: SimpleNamespace(**row))
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return module


class FakeSession:
    def __init__(self):
        self.logins = 0
        self.calls = []
        self.login_error = None
        private = {"mac": "02:00:00:00:00:01", "ip": "192.0.2.10", "name": PRIVATE_DETAIL}
        self.responses = {
            "client_list": {"client_list": [
                dict(private, client_type="phone", online=True, up_speed=0, down_speed="5")
            ]},
            "device_list": {"device_list": [
                {"device_ip": "192.0.2.11", "software_ver": "2.0.1"},
                {"device_ip": GATEWAY, "device_model": "Deco X50", "nickname": PRIVATE_DETAIL,
                 "software_ver": "1.11.1 Build 26081714 Rel. 32981"},
            ]},
            "black_list": {"client_list": [private]},
        }

    def __enter__(self):
        self.logins += 1
        logging.getLogger("tplink_deco_api.client").info(PRIVATE_DETAIL)
        if self.login_error:
            raise self.login_error
        return self

    def __exit__(self, *_):
        pass

    def request(self, path, form, data):
        logging.getLogger("tplink_deco_api.transport").debug(PRIVATE_DETAIL)
        self.calls.append((path, form, data["operation"]))
        response = self.responses[form]
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def fake_deco(sdk, monkeypatch):
    for name in ("tplink_deco_api.client", "tplink_deco_api.transport"):
        monkeypatch.setattr(logging.getLogger(name), "disabled", False)
    session = FakeSession()
    adapter = DecoSdkAdapter(GATEWAY, "fixture-user", "fixture-password")
    monkeypatch.setattr(adapter, "_connect", lambda: session)
    return adapter, session


def test_sdk_probe_uses_only_allowlisted_reads_and_redacts(fake_deco, caplog):
    adapter, session = fake_deco
    with caplog.at_level(logging.DEBUG):
        report = adapter.diagnostics()

    assert session.logins == 1
    assert session.calls == READS
    assert report.inventory.count == report.blocklist.count == 1
    assert report.model == "Deco X50"
    assert report.firmware_version == "1.11.1 Build 26081714 Rel. 32981"
    assert report.speed_samples == "read_only_detected"
    assert report.enforcement == "not_verified"
    for private in (PRIVATE_DETAIL, GATEWAY, "02:00:00:00:00:01", "fixture-password"):
        assert private not in repr(report)
        assert private not in caplog.text


def test_unsupported_read_and_empty_list_are_distinct(fake_deco, sdk):
    adapter, session = fake_deco
    session.responses["client_list"] = {"client_list": []}
    session.responses["black_list"] = sdk.TransportError(404)
    report = adapter.diagnostics()
    assert session.calls == READS
    assert report.inventory.status == "read_only_detected"
    assert report.inventory.count == 0
    assert report.blocklist.status == "not_supported"
    assert report.blocklist.count is None
    assert report.speed_samples == "not_reported"


def test_hostile_firmware_content_and_missing_speeds(fake_deco):
    adapter, session = fake_deco
    gateway = session.responses["device_list"]["device_list"][1]
    gateway.update(device_model=PRIVATE_DETAIL, software_ver="1.1.1 " + PRIVATE_DETAIL)
    row = session.responses["client_list"]["client_list"][0]
    del row["up_speed"]
    row["down_speed"] = "invalid"

    report = adapter.diagnostics()
    assert report.firmware.status == "not_reported"
    assert report.model is None
    assert report.firmware_version is None
    assert report.speed_samples == "not_reported"
    client = adapter.clients()[0]
    assert client.up_kbs is client.down_kbs is None


def test_temporary_failure_stops_remaining_reads_and_backs_off(fake_deco, sdk, monkeypatch):
    adapter, session = fake_deco
    clock = iter((100.0, 100.0, 159.0, 160.0))
    monkeypatch.setattr("app.deco.monotonic", lambda: next(clock))
    session.responses["device_list"] = sdk.TransportError(503)

    report = adapter.diagnostics()
    assert report.inventory.status == "read_only_detected"
    assert report.firmware.status == "temporary_error"
    assert report.blocklist.status == "not_checked"
    assert session.calls == READS[:2]
    with pytest.raises(DecoAccessError, match="temporary_error"):
        adapter.clients()
    assert session.logins == 1

    assert adapter.clients()[0].down_kbs == 5
    assert session.logins == 2


@pytest.mark.parametrize("rejection", ["AuthenticationError", "ApiError"])
def test_login_rejection_stops_polling_and_probe_attempts(fake_deco, sdk, rejection, monkeypatch):
    adapter, session = fake_deco
    session.login_error = getattr(sdk, rejection)(PRIVATE_DETAIL)

    with pytest.raises(DecoAccessError, match="authentication_failed"):
        adapter.clients()
    monkeypatch.setattr("app.deco.monotonic", lambda: 10 ** 12)
    session.login_error = None
    assert adapter.diagnostics().connection == "authentication_failed"
    assert adapter.diagnostics().connection == "authentication_failed"
    assert session.logins == 1
    assert session.calls == []


def test_malformed_response_is_not_an_empty_inventory(fake_deco):
    adapter, session = fake_deco
    session.responses["client_list"] = {}
    report = adapter.diagnostics()
    assert report.inventory.status == "read_error"
    assert report.inventory.count is None


def test_diagnostics_requires_auth_and_csrf_and_caches_result(tmp_path, monkeypatch):
    settings = Settings(admin_password="fixture-admin-password",
                        database_path=str(tmp_path / "diagnostics.sqlite"))
    adapter = MockAdapter()
    calls = []
    original = adapter.diagnostics

    def probe():
        calls.append(True)
        return original()

    monkeypatch.setattr(adapter, "diagnostics", probe)
    app = create_app(settings, adapter=adapter)
    auth = ("admin", settings.admin_password)
    with TestClient(app) as client:
        assert client.get("/api/diagnostics").status_code == 401
        assert client.post("/api/diagnostics", headers={"X-Family-Action": "1"}).status_code == 401
        assert client.post("/api/diagnostics", auth=auth).status_code == 403
        assert client.get("/api/diagnostics", auth=auth).json()["checked_at"] is None
        assert calls == []

        response = client.post("/api/diagnostics", auth=auth, headers={"X-Family-Action": "1"})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["inventory"] == {"count": 2, "status": "simulated"}
        assert response.json()["enforcement"] == "not_verified"
        assert client.get("/api/diagnostics", auth=auth).json() == response.json()
        assert client.post("/api/diagnostics", auth=auth, headers={"X-Family-Action": "1"}).json() == response.json()
        assert calls == [True]


def test_sdk_failures_stay_out_of_api_audit_and_logs(tmp_path, fake_deco, sdk, caplog):
    adapter, session = fake_deco
    session.login_error = sdk.AuthenticationError(PRIVATE_DETAIL)
    settings = Settings(mode="deco", admin_password="fixture-admin-password",
                        database_path=str(tmp_path / "failure.sqlite"))
    app = create_app(settings, adapter=adapter)
    auth = ("admin", settings.admin_password)
    headers = {"X-Family-Action": "1"}

    with TestClient(app) as client:
        refresh = client.post("/api/refresh", auth=auth, headers=headers)
        report = client.post("/api/diagnostics", auth=auth, headers=headers)
        state = client.get("/api/state", auth=auth)
        assert refresh.status_code == 503
        assert report.json()["connection"] == "authentication_failed"
        assert state.json()["audit"][0]["status"] == "errore"
        assert all(PRIVATE_DETAIL not in item for item in
                   [refresh.text, report.text, state.text, caplog.text])
    assert session.logins == 1
