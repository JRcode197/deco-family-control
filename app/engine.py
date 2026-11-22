"""Network polling and cautious idempotent policy enforcement."""
from datetime import datetime
from threading import RLock
from zoneinfo import ZoneInfo
from app.deco import Client, HomeAssistantGuardActuator
from app.policy import local_day, policy_reason, schedule_active


class Engine:
    def __init__(self, store, adapter, settings):
        self.store = store
        self.adapter = adapter
        self.settings = settings
        self.lock = RLock()
        self.last_poll = None
        self.last_error = ""

    def poll(self, now: datetime | None = None) -> dict:
        with self.lock:
            now = now or datetime.now(ZoneInfo(self.settings.timezone))
            try:
                clients = self.adapter.clients()
                known = [c.mac for c in clients]
                for c in clients:
                    self.store.upsert_client(c, now.isoformat())
                    if c.online:
                        # A snapshot of connected Wi-Fi presence, not browsing or screen time.
                        self.store.observe(local_day(now, self.settings.timezone),
                                           now.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%MZ"),
                                           c.mac, c.up_kbs, c.down_kbs)
                self.store.seen_offline_except(known)
                self.last_poll = now.isoformat()
                self.last_error = ""
                actions = self.enforce(now)
                return {"clients": len(clients), "actions": actions}
            except Exception as exc:
                self.last_error = f"Interrogazione non riuscita ({type(exc).__name__}); controlla i log locali"
                self.store.log("poll", "errore", detail=self.last_error)
                raise

    def enforce(self, now: datetime | None = None) -> list[dict]:
        with self.lock:
            now = now or datetime.now(ZoneInfo(self.settings.timezone))
            users = {u["id"]: u for u in self.store.users()}
            windows = self.store.windows()
            usage = self.store.usage(local_day(now, self.settings.timezone))
            actions = []
            for d in self.store.devices():
                if d["mac"] in self.settings.protected_macs:
                    continue
                u = users.get(d["user_id"])
                user_windows = [w for w in windows if w["user_id"] == d["user_id"]] if u else []
                quota_hit = bool(u and self.settings.quota_enforcement and
                                 u["quota_minutes"] is not None and
                                 usage["users"].get(u["id"], 0) >= u["quota_minutes"])
                device_quota_hit = bool(self.settings.quota_enforcement and
                                        d["quota_minutes"] is not None and
                                        usage["devices"].get(d["mac"], 0) >= d["quota_minutes"])
                reason = policy_reason(
                    manually_blocked=bool(d["manually_blocked"] or (u and u["manually_blocked"])),
                    scheduled=bool(u and schedule_active(now, user_windows)),
                    observed_minutes=1 if quota_hit or device_quota_hit else 0,
                    quota_minutes=1 if quota_hit or device_quota_hit else None,
                    quota_enforcement=self.settings.quota_enforcement,
                )
                if reason and not d["managed_block"]:
                    actions.append(self._act(d, True, reason))
                elif not reason and d["managed_block"]:
                    actions.append(self._act(d, False, "revoca_regola"))
            return actions

    def _act(self, device: dict, should_block: bool, reason: str) -> dict:
        mac = device["mac"]
        if not self.settings.writes_enabled:
            return {"mac": mac, "action": "block" if should_block else "unblock",
                    "status": "anteprima_senza_modifiche", "reason": reason}
        c = Client(mac, device["name"], device["ip"], bool(device["online"]), device["client_type"])
        try:
            if self.settings.actuator == "ha_guard":
                service = HomeAssistantGuardActuator(self.settings.ha_url, self.settings.ha_token)
                status = service.call("block_device" if should_block else "unblock_device", c)
            else:
                status = self.adapter.block(c) if should_block else self.adapter.unblock(c)
            self.store.execute("""UPDATE devices SET managed_block=?, last_action=?, last_error=''
              WHERE mac=?""", (int(should_block), status, mac))
            self.store.log("block" if should_block else "unblock", status, mac, reason)
            return {"mac": mac, "status": status, "reason": reason}
        except Exception as exc:
            msg = f"Comando fallito ({type(exc).__name__}); dettagli nei log locali"
            self.store.execute("UPDATE devices SET last_error=? WHERE mac=?", (msg, mac))
            self.store.log("block" if should_block else "unblock", "errore", mac, msg)
            return {"mac": mac, "status": "errore", "detail": msg}
