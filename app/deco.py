"""Thin adapter over external MIT-licensed tplink-deco-api. No vendor code copied.

SDK block-list confirmation is not proof that traffic is stopped.
See https://github.com/leewsimpson/ha-deco-guard.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock
from time import monotonic
from typing import Protocol
import json
import logging
import re
from urllib.request import Request, urlopen

from app.diagnostics import Capability, DecoAccessError, Diagnostics, PROBE_INTERVAL_SECONDS


# Discard arbitrary router strings rather than trying to redact their contents.
FIRMWARE_VERSION = re.compile(
    r"[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{1,2}(?: Build [0-9]{6,8}(?: Rel\. ?[0-9]{1,6})?)?",
    re.IGNORECASE,
)


@dataclass
class Client:
    mac: str
    name: str
    ip: str
    online: bool
    client_type: str = "unknown"
    up_kbs: int | None = None
    down_kbs: int | None = None


def normalize_mac(raw: str) -> str:
    compact = raw.replace(":", "").replace("-", "").upper()
    if len(compact) != 12 or any(c not in "0123456789ABCDEF" for c in compact):
        raise ValueError("Invalid MAC")
    return ":".join(compact[i:i + 2] for i in range(0, 12, 2))


class Adapter(Protocol):
    def clients(self) -> list[Client]: ...
    def blocked_macs(self) -> set[str]: ...
    def diagnostics(self) -> Diagnostics: ...
    def block(self, client: Client) -> str: ...
    def unblock(self, client: Client) -> str: ...


class MockAdapter:
    """Simulator: verify policy behavior without accessing the home router."""
    def __init__(self) -> None:
        self._clients = [
            Client("02:00:00:00:00:01", "iPhone di prova", "192.168.21.101", True, "phone"),
            Client("02:00:00:00:00:02", "iPad di prova", "192.168.21.102", True, "tablet"),
        ]
        self._blocked: set[str] = set()

    def clients(self) -> list[Client]:
        return list(self._clients)

    def blocked_macs(self) -> set[str]:
        return set(self._blocked)

    def diagnostics(self) -> Diagnostics:
        return Diagnostics(
            mode="mock", connection="simulated",
            inventory=Capability("simulated", len(self.clients())),
            blocklist=Capability("simulated", len(self.blocked_macs())),
            firmware=Capability("not_supported"), speed_samples="not_reported",
        )

    def block(self, client: Client) -> str:
        self._blocked.add(client.mac)
        return "simulato"

    def unblock(self, client: Client) -> str:
        self._blocked.discard(client.mac)
        return "simulato"


class DecoSdkAdapter:
    """Real local API with separate short admin sessions."""
    def __init__(self, host: str, username: str, password: str, *, direct: bool = False) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.direct = direct
        self._session_lock = Lock()
        self._retry_at = 0.0
        self._last_failure = ""

    def _connect(self):
        from tplink_deco_api import DecoClient

        deco = DecoClient(self.host, self.username, self.password)
        if self.direct:
            from app.deco_transport import DirectDecoTransport

            # SDK 2.2.0 creates its own transport and has no public injection hook.
            deco._transport = DirectDecoTransport()
        return deco

    @contextmanager
    def _session(self):
        with self._session_lock:
            if monotonic() < self._retry_at:
                raise DecoAccessError(self._last_failure)

            # SDK 2.2.0 logs the host and token-bearing URLs. Keep these off at all levels.
            logging.getLogger("tplink_deco_api.client").disabled = True
            logging.getLogger("tplink_deco_api.transport").disabled = True
            try:
                from tplink_deco_api import ApiError, AuthenticationError, TransportError
            except (ImportError, SyntaxError):
                raise DecoAccessError("sdk_unavailable") from None

            authenticated = False
            try:
                with self._connect() as deco:
                    authenticated = True
                    yield deco
                return
            except AuthenticationError:
                status = "authentication_failed"
            except ApiError:
                status = "read_error" if authenticated else "authentication_failed"
            except TransportError as exc:
                if exc.status_code in (401, 403):
                    status = "authentication_failed"
                elif exc.status_code in (404, 501):
                    status = "not_supported"
                else:
                    status = "temporary_error"
            except DecoAccessError as exc:
                status = str(exc)
            except Exception:
                status = "read_error"

            if status != "not_supported":
                self._last_failure = status
                self._retry_at = (float("inf") if status == "authentication_failed"
                                  else monotonic() + PROBE_INTERVAL_SECONDS)
            raise DecoAccessError(status)

    @staticmethod
    def _read_rows(
        deco, path: str, form: str, data: dict, key: str,
        *, empty_object_is_empty: bool = False,
    ) -> list[dict]:
        from tplink_deco_api import TransportError

        try:
            result = deco.request(path, form, data)
        except TransportError as exc:
            if exc.status_code in (404, 501):
                raise DecoAccessError("not_supported") from None
            raise
        rows = result.get(key)
        # The SDK documents {"client_list": {}} for an empty blacklist.
        # Do not relax device-inventory parsing or accept arbitrary dictionaries.
        if empty_object_is_empty and isinstance(rows, dict) and not rows:
            return []
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise DecoAccessError("read_error")
        return rows

    def _read_clients(self, deco) -> list[Client]:
        from tplink_deco_api import ClientDevice

        rows = self._read_rows(deco, "admin/client", "client_list",
                               {"operation": "read", "params": {"device_mac": "default"}}, "client_list")
        clients = []
        for row in rows:
            c = ClientDevice.from_api(row)
            clients.append(Client(
                mac=normalize_mac(c.mac), name=c.name or c.mac, ip=c.ip or "",
                online=c.online, client_type=c.client_type or "unknown",
                # The SDK defaults omitted/invalid speed fields to zero.
                up_kbs=_as_speed(row.get("up_speed")), down_kbs=_as_speed(row.get("down_speed")),
            ))
        return clients

    def clients(self) -> list[Client]:
        with self._session() as deco:
            return self._read_clients(deco)

    def diagnostics(self) -> Diagnostics:
        report = Diagnostics(mode="deco")
        check = None
        try:
            with self._session() as deco:
                report.connection = "read_only_detected"
                for name, read in (("inventory", self._probe_inventory),
                                   ("firmware", self._probe_firmware),
                                   ("blocklist", self._probe_blocklist)):
                    check = getattr(report, name)
                    try:
                        read(deco, report)
                    except DecoAccessError as exc:
                        if str(exc) != "not_supported":
                            raise
                        check.status = "not_supported"
        except DecoAccessError as exc:
            if check is None or str(exc) == "authentication_failed":
                report.connection = str(exc)
            if check is not None:
                check.status = str(exc)
        return report

    def _probe_inventory(self, deco, report: Diagnostics):
        clients = self._read_clients(deco)
        report.inventory.status = "read_only_detected"
        report.inventory.count = len(clients)
        report.speed_samples = ("read_only_detected" if any(
            c.up_kbs is not None or c.down_kbs is not None for c in clients
        ) else "not_reported")

    def _probe_firmware(self, deco, report: Diagnostics):
        nodes = self._read_rows(deco, "admin/device", "device_list",
                                {"operation": "read"}, "device_list")
        gateway = next((node for node in nodes if node.get("device_ip") == self.host), {})
        model = gateway.get("device_model")
        if isinstance(model, str) and model.strip().upper() in ("X50", "DECO X50"):
            report.model = "Deco X50"
        version = gateway.get("software_ver")
        if isinstance(version, str) and FIRMWARE_VERSION.fullmatch(version.strip()):
            report.firmware_version = version.strip()
        report.firmware.status = "read_only_detected" if report.firmware_version else "not_reported"

    def _probe_blocklist(self, deco, report: Diagnostics):
        rows = self._read_rows(
            deco, "admin/client", "black_list", {"operation": "list"},
            "client_list", empty_object_is_empty=True,
        )
        report.blocklist.status = "read_only_detected"
        report.blocklist.count = len(rows)

    def blocked_macs(self) -> set[str]:
        with self._session() as deco:
            return {normalize_mac(c.mac) for c in deco.clients.get_blocked()}

    def block(self, client: Client) -> str:
        with self._session() as deco:
            deco.clients.block(client.mac, client.name, client.client_type)
        return "inviato_non_verificato"

    def unblock(self, client: Client) -> str:
        with self._session() as deco:
            deco.clients.unblock(client.mac)
        return "inviato_non_verificato"


def _as_speed(value: object) -> int | None:
    if type(value) is int and 0 <= value <= 9_999_999_999:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,10}", value):
        return int(value)
    return None


class HomeAssistantGuardActuator:
    """Optional separate Deco Guard installation exposed through HA services."""
    def __init__(self, url: str, token: str) -> None:
        self.url = url.rstrip("/")
        self.token = token

    def call(self, action: str, client: Client) -> str:
        if action not in ("block_device", "unblock_device"):
            raise ValueError("Unsupported Home Assistant action")
        data = json.dumps({"mac": client.mac}).encode()
        req = Request(
            f"{self.url}/api/services/deco_guard/{action}",
            data=data,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=12) as response:
            if response.status not in (200, 201):
                raise RuntimeError(f"HA HTTP {response.status}")
        return "accettato_da_ha_non_verificato"
