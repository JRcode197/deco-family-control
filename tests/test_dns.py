from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.deco import MockAdapter
from app.dns import DeviceDns, ServicePolicy, WebPolicy, export_rules
from app.main import create_app


AUTH = ("admin", "test-password-long-enough")
ACTION = {"X-Family-Action": "1"}


def test_adguard_compiler_only_emits_scoped_static_rules_and_dns_rewrites():
    web = WebPolicy(blocked_domains={"EXAMPLE.test.", "youtube.com"},
                    allowed_domains={"studio.example.test"}, categories={"threats"},
                    safe_search=True, youtube_mode="strict")
    client = DeviceDns(client_name="alice-phone", blocked_domains={"Extra.test"},
                       allowed_domains={"consentito.threat.test"})
    services = ServicePolicy(blocked_services={"roblox"}, allowed_services={"instagram"}, windows=[
        {"service": "tiktok", "weekdays": [0, 1], "start_time": "16:00", "end_time": "17:30"},
    ])
    rules = export_rules(client, web, services, {"threats": ["threat.test"]})
    assert all("$client='alice-phone'" in rule for rule in rules)
    assert "||example.test^$client='alice-phone'" in rules
    assert "@@||studio.example.test^$client='alice-phone'" in rules
    assert "||extra.test^$client='alice-phone'" in rules
    assert "||threat.test^$client='alice-phone'" in rules
    assert "@@||consentito.threat.test^$client='alice-phone'" in rules
    assert "||roblox.com^$client='alice-phone'" in rules
    assert "@@||instagram.com^$client='alice-phone'" in rules
    assert "||www.google.it^$client='alice-phone',dnsrewrite=NOERROR;CNAME;forcesafesearch.google.com" in rules
    assert "||youtubei.googleapis.com^$client='alice-phone',dnsrewrite=NOERROR;CNAME;restrict.youtube.com" in rules
    assert not any(rule.startswith("||www.youtube.com^") or "tiktok.com" in rule for rule in rules)


@pytest.mark.parametrize("invalid", ["https://example.com", "192.168.0.1", "*.example.com",
                                     "example.com^$client=", "x\n@@||test.com", "localhost"])
def test_reject_dns_rule_injection_and_urls(invalid):
    with pytest.raises(ValueError):
        WebPolicy(blocked_domains={invalid})


def test_dns_import_export_without_provider_and_profile_copy(tmp_path):
    adapter = MockAdapter()
    first, protected = adapter.clients()
    cfg = Settings(admin_password=AUTH[1], database_path=str(tmp_path / "dns.sqlite"),
                   protected_macs=(protected.mac,))
    with TestClient(create_app(cfg, adapter=adapter), headers=ACTION) as client:
        client.auth = AUTH
        client.post("/api/refresh").raise_for_status()
        alice = client.post("/api/users", json={"name": "Alice"}).json()["id"]
        anna = client.post("/api/users", json={"name": "Anna"}).json()["id"]
        for device in (first, protected):
            client.post("/api/assign", json={"mac": device.mac, "user_id": alice}).raise_for_status()
        empty = client.get("/api/dns/export").raise_for_status().json()
        assert empty["status"] == "applicazione_manuale"
        assert empty["devices"] == [
            {"mac": first.mac, "status": "client_non_collegato"},
            {"mac": protected.mac, "status": "protetto"},
        ]
        assert empty["rules"].splitlines() == ["! BEGIN Deco Family Control", "! END Deco Family Control"]
        path = f"/api/dns/users/{alice}"
        assert client.put(path + "/devices/" + protected.mac, json={"client_name": "ha"}).status_code == 403
        assert client.put(path + "/devices/" + first.mac, json={"client_name": "all|~parent"}).status_code == 422
        client.put(path + "/devices/" + first.mac, json={"client_name": "alice"}).raise_for_status()
        assert client.put(path + "/web", headers={"X-Family-Action": ""}, json={"safe_search": True}).status_code == 403
        client.put(path + "/web", json={"safe_search": True, "youtube_mode": "moderate", "categories": ["threats"]}).raise_for_status()
        client.put("/api/dns/categories/threats", json={
            "source": "Lista locale di prova", "domains": ["malevolo.test"]
        }).raise_for_status()
        import_rule = {"user_ids": [alice, anna], "blocked_domains": ["SOCIAL.test", "social.test."],
                       "allowed_domains": ["studio.social.test"]}
        client.post("/api/dns/import", json=import_rule).raise_for_status()
        result = client.get(path).raise_for_status().json()
        assert result["sources"]["threats"]["count"] == 1 and result["web"]["blocked_domains"] == ["social.test"]
        assert result["verification"] == "da_verificare_sul_client"
        rules = client.get("/api/dns/export").raise_for_status().json()["rules"]
        assert "malevolo.test^$client='alice'" in rules
        assert "@@||studio.social.test^$client='alice'" in rules
        assert "CNAME;restrictmoderate.youtube.com" in rules
        assert protected.mac not in rules

        client.put(f"/api/devices/{first.mac}/profiles", json={"user_ids": [alice, anna]}).raise_for_status()
        client.put(f"/api/dns/users/{anna}/web", json={"youtube_mode": "strict", "blocked_domains": ["shared.test"]}).raise_for_status()
        merged = client.get("/api/dns/export").raise_for_status().json()["rules"]
        assert merged.count("||shared.test^$client='alice'") == 1
        assert "CNAME;restrict.youtube.com" in merged and "restrictmoderate.youtube.com" not in merged

        target = f"/api/users/{anna}/copy"
        payload = {"source_id": alice, "groups": ["web"]}
        preview = client.post(target + "/preview", json=payload).raise_for_status().json()
        assert preview["before"]["web"]["safe_search"] is False
        copy_id = client.post(target, json={**payload, "revision": preview["revision"]}).raise_for_status().json()["id"]
        assert client.get(f"/api/dns/users/{anna}").json()["web"]["safe_search"] is True
        client.post(target + f"/{copy_id}/restore").raise_for_status()
        assert client.get(f"/api/dns/users/{anna}").json()["web"]["safe_search"] is False
