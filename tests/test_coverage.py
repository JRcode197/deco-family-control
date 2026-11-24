from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.coverage import coverage_slots


@pytest.mark.parametrize(("month", "day", "hours"), [(3, 29, 23), (10, 25, 25)])
def test_one_day_coverage_uses_distinct_utc_hours_across_dst(month, day, hours):
    now = datetime(2026, month, day, 23, 59, tzinfo=ZoneInfo("Europe/Rome"))
    rows = coverage_slots([], "2026-01-01T00:00Z", now.date(), now.date(), now)
    assert len(rows) == hours
    assert len({row["bucket"] for row in rows}) == hours
