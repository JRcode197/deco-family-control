from dataclasses import dataclass, field, replace
from ipaddress import IPv4Address, IPv4Network
import json
from os import environ
from pathlib import Path
from zoneinfo import ZoneInfo

from app.deco import normalize_mac


HA_OPTIONS_PATH = Path("/data/options.json")
HA_DATABASE_PATH = "/data/inventory/family.sqlite3"
HA_SIMULATION_DATABASE_PATH = "/data/simulation/family.sqlite3"
PRIVATE_LAN_RANGES = tuple(IPv4Network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def release_version() -> str:
    manifest = Path(__file__).resolve().parents[1] / "config.yaml"
    if manifest.is_file():
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if line.startswith("version:"):
                return line.split(":", 1)[1].strip().strip("\"'")
    return "sviluppo"


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
    retention_days: int = 0
    writes_enabled: bool = False
    quota_enforcement: bool = False
    actuator: str = "sdk"
    ha_url: str = ""
    ha_token: str = field(default="", repr=False)
    protected_macs: tuple[str, ...] = ()
    ha_ingress: bool = False
    inventory_setup_error: str = ""


def protected_macs(value: str) -> tuple[str, ...]:
    return tuple(normalize_mac(item.strip()) for item in value.split(",") if item.strip())


def ha_settings() -> Settings:
    try:
        options = json.loads(HA_OPTIONS_PATH.read_bytes())
    except FileNotFoundError:
        options = {}
    except (OSError, ValueError):
        raise ValueError("Impossibile leggere le opzioni Home Assistant.") from None
    if not isinstance(options, dict):
        raise ValueError("Formato delle opzioni Home Assistant non valido.")
    inventory_enabled = options.get("inventory_enabled") is True
    settings = Settings(ha_ingress=True, mode="deco" if inventory_enabled else "mock",
                        database_path=HA_DATABASE_PATH if inventory_enabled else HA_SIMULATION_DATABASE_PATH)
    try:
        exempt_macs = protected_macs(options.get("protected_macs", ""))
        retention = options.get("retention_days", 0)
        if type(retention) is not int or not 0 <= retention <= 3650:
            raise ValueError
    except (ValueError, AttributeError):
        return replace(settings, inventory_setup_error="Controlla retention_days e protected_macs.")
    settings = replace(settings, retention_days=retention, protected_macs=exempt_macs)
    if not inventory_enabled:
        return settings
    ip = options.get("deco_ip", settings.router_ip)
    username = options.get("deco_username", settings.router_user)
    password = options.get("deco_password")
    try:
        router_ip = str(private_router_ip(IPv4Address(ip.strip())))
    except (AttributeError, ValueError):
        return replace(settings, inventory_setup_error="Inserisci un IPv4 privato del Deco in deco_ip.")
    if not isinstance(username, str) or not username.strip():
        return replace(settings, inventory_setup_error="Compila deco_username con l'utente API del Deco.")
    if not isinstance(password, str) or not password:
        return replace(settings, inventory_setup_error="Compila deco_password con la password usata nella diagnostica.")
    return replace(settings, router_ip=router_ip, router_user=username.strip(), router_password=password)


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
        retention_days=int(environ.get("RETENTION_DAYS", "0")),
        writes_enabled=enabled("DECO_WRITE_ENABLED"),
        quota_enforcement=enabled("QUOTA_ENFORCEMENT"),
        actuator=environ.get("BLOCK_ACTUATOR", "sdk"),
        ha_url=environ.get("HA_URL", ""),
        ha_token=environ.get("HA_TOKEN", ""),
        protected_macs=protected_macs(environ.get("PROTECTED_MACS", "")),
    )
    if values.mode not in ("mock", "deco"):
        raise ValueError("DECO_MODE must be mock or deco")
    if values.actuator not in ("sdk", "ha_guard"):
        raise ValueError("BLOCK_ACTUATOR must be sdk or ha_guard")
    if values.poll_seconds < 30:
        raise ValueError("Polling interval must be at least 30 seconds")
    if not 0 <= values.retention_days <= 3650:
        raise ValueError("RETENTION_DAYS must be between 0 and 3650")
    ZoneInfo(values.timezone)
    if len(values.admin_password) < 12 or values.admin_password in ("change-me", "password"):
        raise ValueError("Configure a strong ADMIN_PASSWORD; app refuses to start without it")
    if values.writes_enabled and values.mode == "deco" and not values.protected_macs:
        raise ValueError("Set PROTECTED_MACS for the admin/automation host before enabling router writes")
    if values.mode == "deco" and not values.router_password:
        raise ValueError("DECO_PASSWORD required in deco mode")
    if (values.mode == "deco" and values.actuator == "ha_guard" and values.writes_enabled
            and (not values.ha_url or not values.ha_token)):
        raise ValueError("HA_URL and HA_TOKEN required for ha_guard writes")
    return values
