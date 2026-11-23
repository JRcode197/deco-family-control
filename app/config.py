from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Network
import json
from os import environ
from pathlib import Path
from zoneinfo import ZoneInfo


HA_OPTIONS_PATH = Path("/data/options.json")
HA_DATABASE_PATH = "/data/inventory/family.sqlite3"
PRIVATE_LAN_RANGES = tuple(IPv4Network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def private_router_ip(value: IPv4Address) -> IPv4Address:
    if not any(value in network for network in PRIVATE_LAN_RANGES):
        raise ValueError("Private LAN IPv4 address required")
    return value


@dataclass(frozen=True)
class Settings:
    mode: str = "mock"
    router_ip: str = "192.168.21.1"
    router_user: str = "admin"
    router_password: str = field(default="", repr=False)
    admin_user: str = "admin"
    admin_password: str = field(default="", repr=False)
    database_path: str = "/data/family.sqlite3"
    timezone: str = "Europe/Rome"
    poll_seconds: int = 60
    writes_enabled: bool = False
    quota_enforcement: bool = False
    actuator: str = "sdk"
    ha_url: str = ""
    ha_token: str = field(default="", repr=False)
    protected_macs: tuple[str, ...] = ()
    ha_ingress: bool = False


def ha_settings() -> Settings:
    try:
        options = json.loads(HA_OPTIONS_PATH.read_bytes())
    except FileNotFoundError:
        options = {}
    except (OSError, ValueError):
        raise ValueError("Impossibile leggere le opzioni Home Assistant.") from None
    if not isinstance(options, dict):
        raise ValueError("Formato delle opzioni Home Assistant non valido.")
    if options.get("inventory_enabled") is not True:
        return Settings(ha_ingress=True, database_path=":memory:")

    ip = options.get("deco_ip", "192.168.21.1")
    username = options.get("deco_username", "admin")
    password = options.get("deco_password")
    try:
        router_ip = str(private_router_ip(IPv4Address(ip.strip())))
    except (AttributeError, ValueError):
        raise ValueError("Imposta un IPv4 privato del Deco nelle opzioni Home Assistant.") from None
    if not isinstance(username, str) or not username.strip() or not isinstance(password, str) or not password:
        raise ValueError("Imposta l'utente API e la password del Deco nelle opzioni Home Assistant.")
    return Settings(
        mode="deco", ha_ingress=True, database_path=HA_DATABASE_PATH,
        router_ip=router_ip, router_user=username.strip(), router_password=password,
    )


def from_env() -> Settings:
    def enabled(name: str) -> bool:
        return environ.get(name, "false").lower() == "true"

    if enabled("DECO_HA_INGRESS"):
        return ha_settings()

    values = Settings(
        mode=environ.get("DECO_MODE", "mock"),
        router_ip=environ.get("DECO_ROUTER_IP", "192.168.21.1"),
        router_user=environ.get("DECO_USERNAME", "admin"),
        router_password=environ.get("DECO_PASSWORD", ""),
        admin_user=environ.get("ADMIN_USER", "admin"),
        admin_password=environ.get("ADMIN_PASSWORD", ""),
        database_path=environ.get("DATABASE_PATH", "/data/family.sqlite3"),
        timezone=environ.get("TZ", "Europe/Rome"),
        poll_seconds=int(environ.get("POLL_SECONDS", "60")),
        writes_enabled=enabled("DECO_WRITE_ENABLED"),
        quota_enforcement=enabled("QUOTA_ENFORCEMENT"),
        actuator=environ.get("BLOCK_ACTUATOR", "sdk"),
        ha_url=environ.get("HA_URL", ""),
        ha_token=environ.get("HA_TOKEN", ""),
        protected_macs=tuple(x.strip().upper().replace("-", ":") for x in environ.get("PROTECTED_MACS", "").split(",") if x.strip()),
    )
    if values.mode not in ("mock", "deco"):
        raise ValueError("DECO_MODE must be mock or deco")
    if values.actuator not in ("sdk", "ha_guard"):
        raise ValueError("BLOCK_ACTUATOR must be sdk or ha_guard")
    if values.poll_seconds < 30:
        raise ValueError("Polling interval must be at least 30 seconds")
    ZoneInfo(values.timezone)
    if len(values.admin_password) < 12 or values.admin_password in ("change-me", "password"):
        raise ValueError("Configure a strong ADMIN_PASSWORD; app refuses to start without it")
    if values.writes_enabled and values.mode == "deco" and not values.protected_macs:
        raise ValueError("Set PROTECTED_MACS for the admin/automation host before enabling router writes")
    if values.mode == "deco" and not values.router_password:
        raise ValueError("DECO_PASSWORD required in deco mode")
    if values.actuator == "ha_guard" and values.writes_enabled and (not values.ha_url or not values.ha_token):
        raise ValueError("HA_URL and HA_TOKEN required for ha_guard writes")
    return values
