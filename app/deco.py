"""Thin adapter over external MIT-licensed tplink-deco-api. No vendor code copied.

SDK block-list confirmation is not proof that traffic is stopped.
See https://github.com/leewsimpson/ha-deco-guard.
"""
from dataclasses import dataclass
from typing import Protocol
import json
from urllib.request import Request, urlopen


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

    def block(self, client: Client) -> str:
        self._blocked.add(client.mac)
        return "simulato"

    def unblock(self, client: Client) -> str:
        self._blocked.discard(client.mac)
        return "simulato"


class DecoSdkAdapter:
    """Real local API with separate short admin sessions."""
    def __init__(self, host: str, username: str, password: str) -> None:
        self.host = host
        self.username = username
        self.password = password

    def _connect(self):
        # Requires tplink-deco-api >=2.2 and Python >=3.14.
        from tplink_deco_api import DecoClient
        return DecoClient(self.host, self.username, self.password)

    def clients(self) -> list[Client]:
        with self._connect() as deco:
            return [Client(
                mac=normalize_mac(c.mac), name=c.name or c.mac, ip=c.ip or "",
                online=bool(c.online),
                client_type=getattr(c, "client_type", None) or "unknown",
                up_kbs=c.up_speed, down_kbs=c.down_speed,
            ) for c in deco.get_client_list()]

    def blocked_macs(self) -> set[str]:
        with self._connect() as deco:
            return {normalize_mac(c.mac) for c in deco.clients.get_blocked()}

    def block(self, client: Client) -> str:
        with self._connect() as deco:
            deco.clients.block(client.mac, client.name, client.client_type)
        return "inviato_non_verificato"

    def unblock(self, client: Client) -> str:
        with self._connect() as deco:
            deco.clients.unblock(client.mac)
        return "inviato_non_verificato"


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
