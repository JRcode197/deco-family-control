"""Intervals without an inventory outcome are gaps in the retained observations."""
from datetime import date, datetime, time, timedelta, timezone


def coverage_slots(rows: list[dict], first_minute: str | None, start: date, end: date,
                   now: datetime) -> list[dict]:
    if first_minute is None:
        return []
    first = datetime.fromisoformat(first_minute.replace("Z", "+00:00"))
    recorded = {row["bucket"]: row for row in rows}
    if start == end:
        local_start = datetime.combine(start, time.min, now.tzinfo).astimezone(timezone.utc)
        local_end = datetime.combine(end + timedelta(days=1), time.min, now.tzinfo).astimezone(timezone.utc)
        cursor = max(local_start, first).replace(minute=0, second=0, microsecond=0)
        limit = min(local_end, now.astimezone(timezone.utc) + timedelta(microseconds=1))
        if first >= limit:
            return []
        keys = []
        while cursor < limit:
            keys.append(cursor.strftime("%Y-%m-%dT%H"))
            cursor += timedelta(hours=1)
    else:
        first_day = max(start, first.astimezone(now.tzinfo).date())
        keys = [(first_day + timedelta(days=offset)).isoformat()
                for offset in range((end - first_day).days + 1)]
    return [recorded.get(key, {"bucket": key, "successful_minutes": 0, "failure_only_minutes": 0})
            for key in keys]
