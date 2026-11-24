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


def test_overlapping_weekend_and_school_rules_do_not_release_early():
    from app.policy import next_transitions, profile_reason

    user = {"manually_blocked": False}
    windows = [
        {"days_mask": 0b11111, "start_time": "20:30", "end_time": "07:00", "kind": "bedtime"},
        {"days_mask": 0b1100000, "start_time": "21:30", "end_time": "08:00", "kind": "bedtime"},
        {"weekday": 5, "start_time": "06:00", "end_time": "07:30", "kind": "offtime"},
    ]
    friday = dt(2026, 11, 27, 21)
    assert profile_reason(dt(2026, 11, 28, 6, 30), user, windows) == "bedtime"
    assert profile_reason(dt(2026, 11, 28, 7, 10), user, windows) == "offtime"
    assert next_transitions(friday, user, windows) == {
        "next_release": "2026-11-28T07:30:00+01:00",
        "next_block": "2026-11-28T21:30:00+01:00",
    }


def test_dst_missing_hour_and_repeated_hour():
    from app.policy import next_transitions, schedule_active

    user = {"manually_blocked": False}
    spring_window = [{"weekday": 6, "start_time": "01:30", "end_time": "02:45"}]
    assert next_transitions(dt(2026, 3, 29, 1, 50), user, spring_window)["next_release"] == "2026-03-29T03:00:00+02:00"

    fall_window = [{"weekday": 6, "start_time": "02:15", "end_time": "02:45"}]
    early = dt(2026, 10, 25, 2, 30)
    late = early.replace(fold=1)
    assert schedule_active(early, fall_window) and schedule_active(late, fall_window)
    changes = next_transitions(early, user, fall_window)
    assert changes["next_release"] == "2026-10-25T02:45:00+02:00"
    assert changes["next_block"] == "2026-10-25T02:15:00+01:00"
