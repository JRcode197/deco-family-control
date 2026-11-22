from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from app.policy import schedule_active, policy_reason, to_minutes

Z = ZoneInfo("Europe/Rome")


def dt(y, m, d, hr, mn=0):
    return datetime(y, m, d, hr, mn, tzinfo=Z)


def test_overnight_weekday_schedule():
    # 2026-11-23 Monday; block begins Monday and spills into Tuesday.
    ws = [{"weekday": 0, "start_time": "20:30", "end_time": "07:00"}]
    assert schedule_active(dt(2026, 11, 23, 21), ws)
    assert schedule_active(dt(2026, 11, 24, 6, 59), ws)
    assert not schedule_active(dt(2026, 11, 24, 7), ws)
    assert not schedule_active(dt(2026, 11, 23, 19), ws)


def test_same_day_schedule():
    ws = [{"weekday": 0, "start_time": "14:00", "end_time": "15:30"}]
    assert schedule_active(dt(2026, 11, 23, 15), ws)
    assert not schedule_active(dt(2026, 11, 23, 15, 30), ws)


def test_policy_priorities_and_quota_optin():
    kw = dict(manually_blocked=False, scheduled=False, observed_minutes=91, quota_minutes=90)
    assert policy_reason(**kw, quota_enforcement=False) is None
    assert policy_reason(**kw, quota_enforcement=True) == "limite_presenza"
    assert policy_reason(**{**kw, "manually_blocked": True}, quota_enforcement=True) == "manuale"
    assert policy_reason(**{**kw, "scheduled": True}, quota_enforcement=True) == "orario"


def test_invalid_time():
    with pytest.raises(ValueError):
        to_minutes("25:01")
