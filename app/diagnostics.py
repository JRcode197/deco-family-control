"""Shareable capability report. Only counts, status codes and firmware metadata."""
from dataclasses import dataclass, field


PROBE_INTERVAL_SECONDS = 60


@dataclass
class Capability:
    status: str = "not_checked"
    count: int | None = None


@dataclass
class Diagnostics:
    mode: str
    checked_at: str | None = None
    connection: str = "not_checked"
    inventory: Capability = field(default_factory=Capability)
    firmware: Capability = field(default_factory=Capability)
    blocklist: Capability = field(default_factory=Capability)
    model: str | None = None
    firmware_version: str | None = None
    speed_samples: str = "not_checked"
    enforcement: str = "not_verified"


class DecoAccessError(Exception):
    """A safe status independent of the SDK's exception text and request URL."""
