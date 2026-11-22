"""Pure policy rules; schedule weekdays mean day when block STARTS."""
from datetime import datetime
from zoneinfo import ZoneInfo


def schedule_active(now: datetime, windows: list[dict]) -> bool:
    if now.tzinfo is None:
        raise ValueError("Timezone-aware datetime required")
    minute = now.hour * 60 + now.minute
    weekday = now.weekday()
    for w in windows:
        dow = int(w["weekday"])
        start = to_minutes(w["start_time"])
        end = to_minutes(w["end_time"])
        if start == end:
            continue
        if start < end and dow == weekday and start <= minute < end:
            return True
        if start > end and (
            (dow == weekday and minute >= start)
            or ((dow + 1) % 7 == weekday and minute < end)
        ):
            return True
    return False


def to_minutes(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("Invalid HH:MM")
    return h * 60 + m


def policy_reason(*, manually_blocked: bool, scheduled: bool,
                  observed_minutes: int, quota_minutes: int | None,
                  quota_enforcement: bool) -> str | None:
    if manually_blocked:
        return "manuale"
    if scheduled:
        return "orario"
    if (quota_enforcement and quota_minutes is not None
            and observed_minutes >= quota_minutes):
        return "limite_presenza"
    return None


def local_day(now: datetime, tz: str) -> str:
    return now.astimezone(ZoneInfo(tz)).date().isoformat()
