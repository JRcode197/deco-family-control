"""Evaluate recurring windows in local wall time and temporary exceptions in UTC."""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def schedule_active(now: datetime, windows: list[dict]) -> bool:
    if now.tzinfo is None:
        raise ValueError("Timezone-aware datetime required")
    minute = now.hour * 60 + now.minute
    weekday = now.weekday()
    for window in windows:
        if not window.get("enabled", True) or pending(now, window.get("paused_until")):
            continue
        days = weekdays_mask(window)
        today = days & (1 << weekday)
        yesterday = days & (1 << ((weekday - 1) % 7))
        start = to_minutes(window["start_time"])
        end = to_minutes(window["end_time"])
        if start < end and today and start <= minute < end:
            return True
        if start > end and ((today and minute >= start) or (yesterday and minute < end)):
            return True
    return False


def weekdays_mask(window: dict) -> int:
    mask = window.get("days_mask")
    return 1 << int(window["weekday"]) if mask is None else mask


def to_minutes(hhmm: str) -> int:
    hour, minute = map(int, hhmm.split(":"))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Invalid HH:MM")
    return hour * 60 + minute


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


def pending(now: datetime, expiry: str | None) -> bool:
    return bool(expiry and now.astimezone(timezone.utc) < datetime.fromisoformat(expiry))


def profile_reason(now: datetime, user: dict, windows: list[dict]) -> str | None:
    if pending(now, user.get("override_until")):
        return None
    if user["manually_blocked"]:
        return "manuale"
    for kind in ("bedtime", "offtime"):
        if schedule_active(now, [w for w in windows if w.get("kind", "offtime") == kind]):
            return kind
    return None


def device_reason(device: dict, inherited_reason: str | None) -> str | None:
    if device["manually_blocked"]:
        return "manuale_dispositivo"
    return None if device.get("profile_exempt") else inherited_reason


def next_transitions(now: datetime, user: dict, windows: list[dict]) -> dict:
    """Find changes in effective profile availability, including DST clock jumps."""
    zone = now.tzinfo
    current = now.astimezone(timezone.utc)
    result = {"next_block": None, "next_release": None}
    candidates = set()
    windows = [w for w in windows if w.get("enabled", True)]
    for item, key in [(user, "override_until"), *((w, "paused_until") for w in windows)]:
        if pending(now, item.get(key)):
            candidates.add(datetime.fromisoformat(item[key]))
    if not windows and not candidates:
        return result

    # Start on yesterday to include the ending boundary of overnight rules.
    for offset in range(-1, 9):
        day = now.date() + timedelta(days=offset)
        for window in windows:
            if not weekdays_mask(window) & (1 << day.weekday()):
                continue
            start = time(*divmod(to_minutes(window["start_time"]), 60))
            end = time(*divmod(to_minutes(window["end_time"]), 60))
            end_day = day + timedelta(days=end < start)
            for wall in (datetime.combine(day, start), datetime.combine(end_day, end)):
                for fold in (0, 1):
                    candidates.add(wall.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc))

    # The local clock can cross a rule boundary without ever reaching its wall time.
    hour = current.replace(minute=0, second=0, microsecond=0)
    for _ in range(9 * 24):
        following = hour + timedelta(hours=1)
        old_offset = hour.astimezone(zone).utcoffset()
        if following.astimezone(zone).utcoffset() != old_offset:
            for minute in range(1, 61):
                instant = hour + timedelta(minutes=minute)
                if instant.astimezone(zone).utcoffset() != old_offset:
                    candidates.add(instant)
                    break
        hour = following

    for instant in sorted(t for t in candidates if t > current):
        before = (instant - timedelta(microseconds=1)).astimezone(zone)
        after = instant.astimezone(zone)
        was_blocked = bool(profile_reason(before, user, windows))
        is_blocked = bool(profile_reason(after, user, windows))
        if is_blocked != was_blocked:
            key = "next_block" if is_blocked else "next_release"
            if result[key] is None:
                result[key] = after.isoformat()
            if all(result.values()):
                break
    return result
