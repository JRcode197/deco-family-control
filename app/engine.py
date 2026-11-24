"""Network polling and cautious idempotent policy enforcement."""
from datetime import datetime
import logging
from threading import RLock
from time import monotonic
from zoneinfo import ZoneInfo
from app.deco import Adapter, Client, HomeAssistantGuardActuator
from app.diagnostics import Diagnostics, PROBE_INTERVAL_SECONDS, ProbeCooldown
from app.policy import device_reason, local_day, pending, policy_reason, profile_reason

log = logging.getLogger(__name__)


class Engine:
    def __init__(self, store, adapter, settings):
        self.store = store
        self.adapter = adapter
        self.settings = settings
        self.lock = RLock()
        self.last_poll = None
        self.last_sample_minute = None
        self.last_error = ""
        self.diagnostics = Diagnostics(mode=settings.mode)
        self._next_probe = 0.0
        self._last_probe_used_server_settings = True
        self.presence_quota_simulation = settings.mode == "mock" and settings.quota_enforcement
        self._retention_day = None

    def probe_diagnostics(self, adapter: Adapter | None = None) -> Diagnostics:
        with self.lock:
            if monotonic() < self._next_probe:
                if adapter is None and self._last_probe_used_server_settings:
                    return self.diagnostics
                raise ProbeCooldown
            report = (self.adapter if adapter is None else adapter).diagnostics()
            report.checked_at = datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds")
            self.diagnostics = report
            self._last_probe_used_server_settings = adapter is None
            self._next_probe = monotonic() + PROBE_INTERVAL_SECONDS
            return report

    def poll(self, now: datetime | None = None) -> dict:
        with self.lock:
            try:
                zone = ZoneInfo(self.settings.timezone)
                day = local_day(now or datetime.now(zone), self.settings.timezone)
                if self.settings.retention_days and day != self._retention_day:
                    self.store.prune_samples(self.settings.retention_days, day)
                    self._retention_day = day
                clients = self.adapter.clients()
                now = now or datetime.now(zone)
                day = local_day(now, self.settings.timezone)
                minute = now.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%MZ")
                with self.store.lock:
                    self.store.record_inventory(clients, now.isoformat(), day, minute)
                    self.last_poll = now.isoformat()
                    self.last_sample_minute = minute
                    self.last_error = ""
                actions = [] if self.settings.ha_ingress and self.settings.mode == "deco" else self.enforce(now)
                return {"clients": len(clients), "actions": actions}
            except Exception:
                with self.store.lock:
                    self.last_error = "Interrogazione non riuscita. Controlla la diagnostica."
                    self.store.log("poll", "errore", detail=self.last_error)
                raise

    def enforce(self, now: datetime | None = None) -> list[dict]:
        with self.lock:
            now = now or datetime.now(ZoneInfo(self.settings.timezone))
            now = now.astimezone(ZoneInfo(self.settings.timezone))
            with self.store.lock:
                users = self.store.users()
                windows = self.store.windows()
                devices = self.store.devices()
                usage = self.store.usage(local_day(now, self.settings.timezone)) if self.presence_quota_simulation else {}
            reasons = {}
            for user in users:
                user_id = user["id"]
                rules = [w for w in windows if w["user_id"] == user_id]
                reason = profile_reason(now, user, rules)
                if not reason and self.presence_quota_simulation and not pending(now, user["override_until"]):
                    reason = policy_reason(
                        manually_blocked=False, scheduled=False, quota_enforcement=True,
                        observed_minutes=usage["users"].get(user_id, 0), quota_minutes=user["quota_minutes"],
                    )
                reasons[user_id] = reason
            mock_blocks = (self.adapter.blocked_macs()
                           if self.settings.mode == "mock" and self.settings.writes_enabled else None)
            actions = []
            for device in devices:
                mac = device["mac"]
                if mac in self.settings.protected_macs:
                    continue
                reason = device_reason(device, reasons.get(device["user_id"]))
                if not reason and self.presence_quota_simulation:
                    reason = policy_reason(
                        manually_blocked=False, scheduled=False, quota_enforcement=True,
                        observed_minutes=usage["devices"].get(mac, 0), quota_minutes=device["quota_minutes"],
                    )
                currently_blocked = mac in mock_blocks if mock_blocks is not None else bool(device["managed_block"])
                if bool(reason) != currently_blocked:
                    actions.append(self._act(device, bool(reason), reason or "revoca_regola"))
            return actions

    def _act(self, device: dict, should_block: bool, reason: str) -> dict:
        mac = device["mac"]
        if self.settings.ha_ingress or not self.settings.writes_enabled:
            return {"mac": mac, "action": "block" if should_block else "unblock",
                    "status": "anteprima_senza_modifiche", "reason": reason}
        c = Client(mac, device["name"], device["ip"], bool(device["online"]), device["client_type"])
        try:
            if self.settings.mode == "deco" and self.settings.actuator == "ha_guard":
                service = HomeAssistantGuardActuator(self.settings.ha_url, self.settings.ha_token)
                status = service.call("block_device" if should_block else "unblock_device", c)
            else:
                status = self.adapter.block(c) if should_block else self.adapter.unblock(c)
            self.store.execute("""UPDATE devices SET managed_block=?, last_action=?, last_error=''
              WHERE mac=?""", (int(should_block), status, mac))
            self.store.log("block" if should_block else "unblock", status, mac, reason)
            return {"mac": mac, "status": status, "reason": reason}
        except Exception:
            msg = "Comando fallito. Esito del blocco da verificare."
            log.warning("Comando di controllo rete fallito")
            self.store.execute("UPDATE devices SET last_error=? WHERE mac=?", (msg, mac))
            self.store.log("block" if should_block else "unblock", "errore", mac, msg)
            return {"mac": mac, "status": "errore", "detail": msg}
