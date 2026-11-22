from datetime import datetime
from zoneinfo import ZoneInfo
from app.config import Settings
from app.deco import MockAdapter
from app.engine import Engine
from app.store import Store


def test_two_devices_same_user_minutes_count_once(tmp_path):
    store = Store(str(tmp_path / "t.sqlite"))
    adapter = MockAdapter()
    cfg = Settings(database_path=str(tmp_path / "t.sqlite"), writes_enabled=False)
    engine = Engine(store, adapter, cfg)
    now = datetime(2026, 11, 23, 18, 0, tzinfo=ZoneInfo("Europe/Rome"))
    engine.poll(now)
    uid = store.execute("INSERT INTO users(name) VALUES (?)", ("Alice",))
    for c in adapter.clients():
        store.execute("UPDATE devices SET user_id=? WHERE mac=?", (uid, c.mac))
    engine.poll(now)
    used = store.usage("2026-11-23")
    assert used["users"][uid] == 1
    assert set(used["devices"].values()) == {1}
    store.close()


def test_default_dry_run_never_blocks(tmp_path):
    store = Store(str(tmp_path / "d.sqlite"))
    adapter = MockAdapter()
    engine = Engine(store, adapter, Settings(writes_enabled=False))
    now = datetime(2026, 11, 23, 18, 0, tzinfo=ZoneInfo("Europe/Rome"))
    engine.poll(now)
    mac = adapter.clients()[0].mac
    store.execute("UPDATE devices SET manually_blocked=1 WHERE mac=?", (mac,))
    actions = engine.enforce(now)
    assert actions[0]["status"] == "anteprima_senza_modifiche"
    assert adapter.blocked_macs() == set()
    assert store.query("SELECT managed_block FROM devices WHERE mac=?", (mac,))[0]["managed_block"] == 0
    store.close()


def test_mock_block_unblock_lifecycle(tmp_path):
    store = Store(str(tmp_path / "e.sqlite"))
    adapter = MockAdapter()
    engine = Engine(store, adapter, Settings(writes_enabled=True))
    now = datetime(2026, 11, 23, 18, 0, tzinfo=ZoneInfo("Europe/Rome"))
    engine.poll(now)
    mac = adapter.clients()[0].mac
    store.execute("UPDATE devices SET manually_blocked=1 WHERE mac=?", (mac,))
    engine.enforce(now)
    assert mac in adapter.blocked_macs()
    store.execute("UPDATE devices SET manually_blocked=0 WHERE mac=?", (mac,))
    engine.enforce(now)
    assert mac not in adapter.blocked_macs()
    store.close()


def test_protected_device_not_blocked(tmp_path):
    store = Store(str(tmp_path / "protected.sqlite"))
    adapter = MockAdapter()
    mac = adapter.clients()[0].mac
    engine = Engine(store, adapter, Settings(writes_enabled=True, protected_macs=(mac,)))
    now = datetime(2026, 11, 23, 18, 0, tzinfo=ZoneInfo("Europe/Rome"))
    engine.poll(now)
    store.execute("UPDATE devices SET manually_blocked=1 WHERE mac=?", (mac,))
    engine.enforce(now)
    assert mac not in adapter.blocked_macs()
    store.close()
