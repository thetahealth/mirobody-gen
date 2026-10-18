"""Resting input provenance must distinguish a source record from model completion."""
from datetime import date, datetime

import pytest

from mirobody_gen import continuous as C
from mirobody_gen.model import Event, Person

DAY = date(2025, 3, 2)


def _life(records=(), *, events=(), vendor="apple"):
    p = Person("rhr-provenance", "male", 1980, 175, "healthy", "test", events=events,
               weight_anchors=((date(2024, 1, 1), 70), (date(2026, 1, 1), 70)))
    return C.Life(7, p, {"records": list(records), "vendor": vendor,
                         "baseline": {"rhr": 52.0, "steps": 5000, "tz": "+09:00", "sleep_min": 430}})


def _expected(source, context, stamp=None):
    return {"policy_version": "resting-hr-provenance-v1", "source": source,
            "context": context, "record_time": stamp}


def test_record_value_and_original_local_timestamp_are_preserved():
    stamp = f"{DAY}T00:12:34+09:00"
    life = _life([{"_metric": "rhr", "value": 51.25, "time": stamp}])
    plan = life.plan(DAY)
    assert plan.rhr == 51.25
    assert plan.describe()["rhr"] == round(51.25, 1)  # Existing truth precision, not a new clamp.
    assert plan.describe()["rhr_provenance"] == _expected("phone_store", "daily_resting_summary", stamp)


@pytest.mark.parametrize("vendor", ["apple", "xiaomi"])
def test_missing_daily_record_stays_a_model_value_even_when_vendor_can_have_rhr(vendor):
    events = (Event("configured-pulse", "health_event", date(2025, 1, 1), 365, "neutral", "low",
                    effects={"pulse": (-0.1, 0, None)}),
              Event(C.URI, "health_event", DAY, 10, "negative", "low"))
    life = _life([{"_metric": "rhr", "value": 54, "time": "2025-03-01T07:00:00+09:00"}],
                 events=events, vendor=vendor)
    plan = life.plan(DAY)
    assert plan.rhr == pytest.approx(52*0.9*1.1)
    assert plan.describe()["rhr_provenance"] == _expected("baseline_model", "resting_model_expectation")
    assert life.plan(date(2025, 3, 1)).describe()["rhr_provenance"]["source"] == "phone_store"


def test_record_presence_takes_priority_over_vendor_habits_or_fallback_effects():
    # The current reader keys actual supplied records, not a brand/adoption assumption.
    life = _life([{"_metric": "rhr", "value": 49, "time": f"{DAY}T06:00:00+09:00"}], vendor="xiaomi",
                 events=(Event(C.URI, "health_event", DAY, 10, "negative", "low"),))
    assert life.plan(DAY).rhr == 49  # Do not add URI again or raise a low supplied value.
    assert life.plan(DAY).describe()["rhr_provenance"] == _expected(
        "phone_store", "daily_resting_summary", f"{DAY}T06:00:00+09:00")


def test_existing_last_record_selection_keeps_the_matching_timestamp():
    life = _life([{"_metric": "rhr", "value": 49, "time": f"{DAY}T06:00:00+09:00"},
                  {"_metric": "rhr", "value": 53, "time": f"{DAY}T08:20:00+09:00"}])
    assert life.plan(DAY).rhr == 53
    assert life.plan(DAY).describe()["rhr_provenance"] == _expected(
        "phone_store", "daily_resting_summary", f"{DAY}T08:20:00+09:00")


def test_manual_plan_does_not_invent_a_record_or_model_authority():
    plan = C.DayPlan(DAY, datetime(2025, 3, 2, 7), datetime(2025, 3, 2, 23), (), None, (), 0, 47)
    assert plan.describe()["rhr_provenance"] == _expected("unspecified", "unspecified")
