"""Activity-clock constraints checked independently from the placement algorithm."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from mirobody_gen import continuous as C, devices, person
from mirobody_gen.model import Event, Person


def check_clock(walks, run, lower, upper):
    bouts = sorted(list(walks) + ([run] if run else []))
    for start, minutes in bouts:
        assert minutes > 0
        assert lower <= start < start+timedelta(minutes=minutes) <= upper
    for (start, minutes), (following, _) in zip(bouts, bouts[1:]):
        assert start+timedelta(minutes=minutes) <= following


@pytest.mark.parametrize("pid,day,durations", [
    ("p006", date(2024, 1, 21), [13, 10, 13, 12]),
    ("p002", date(2025, 10, 8), [23, 9]),
    ("p013", date(2026, 5, 24), [13, 21]),
])
def test_real_cases_keep_durations_and_daily_facts_while_fixing_clock(pid, day, durations):
    p = next(p for p in person.build_cohort(7) if p.person_id == pid)
    series = devices.series_for(p, 7, person.person_lang(7, pid))
    life = C.Life(7, p, series)
    plan = life.plan(day)
    check_clock(plan.walks, plan.run, max(plan.wake, datetime.combine(day, datetime.min.time())),
                min(plan.bed, datetime.combine(day+timedelta(days=1), datetime.min.time())))
    assert sorted(m for _, m in plan.walks) == sorted(durations)
    assert plan.steps == life._steps.get(day.isoformat(), int(series["baseline"]["steps"]))
    assert plan.rhr == life.resting_hr(day)
    described = plan.describe()
    assert described["walks"] == [{"start": t.isoformat(), "minutes": m} for t, m in plan.walks]


def test_valid_proposals_stay_at_their_original_times():
    lower, upper = datetime(2025, 1, 1, 7), datetime(2025, 1, 1, 23)
    walks = ((datetime(2025, 1, 1, 9), 10), (datetime(2025, 1, 1, 16), 20))
    run = (datetime(2025, 1, 1, 19), 30)
    assert C._schedule_walks(walks, run, lower, upper) == walks


def test_collisions_are_relocated_without_deleting_minutes():
    lower, upper = datetime(2025, 1, 1, 9), datetime(2025, 1, 1, 12)
    walks = ((datetime(2025, 1, 1, 10), 20), (datetime(2025, 1, 1, 10, 5), 15))
    run = (datetime(2025, 1, 1, 10, 25), 30)
    fixed = C._schedule_walks(walks, run, lower, upper)
    assert sorted(m for _, m in fixed) == [15, 20]
    check_clock(fixed, run, lower, upper)


def test_impossible_window_is_rejected_instead_of_truncating_activity():
    lower, upper = datetime(2025, 1, 1, 9), datetime(2025, 1, 1, 9, 10)
    with pytest.raises(ValueError, match="activity window"):
        C._schedule_walks(((lower, 5),), (lower, 10), lower, upper)


def test_export_rejects_overlapping_clock_even_with_plenty_of_steps():
    day = date(2025, 1, 1)
    p = Person("clock", "male", 1980, 175.0, "healthy", "test")
    series = {"records": [{"_metric": "steps", "time": "2025-01-01T23:59:00+00:00", "value": 20000}],
              "baseline": {"rhr": 52, "steps": 20000, "tz": "+00:00"}}
    life = C.Life(7, p, series)
    life._plans[day] = C.DayPlan(day, datetime(2025, 1, 1, 7), datetime(2025, 1, 1, 23), (),
                               (datetime(2025, 1, 1, 10), 30), ((datetime(2025, 1, 1, 10, 15), 10),),
                               20000, 52)

    class Export:
        def add(self, *a, **kw):
            pass

        def _version(self, *a):
            return "test"

        def device(self, *a):
            return None

    with pytest.raises(ValueError, match="activity clock"):
        C._apple_daily(Export(), life, series)


def test_heart_curve_counts_each_declared_walk_minute():
    p = next(p for p in person.build_cohort(7) if p.person_id == "p006")
    life = C.Life(7, p, devices.series_for(p, 7, person.person_lang(7, p.person_id)))
    plan = life.plan(date(2024, 1, 21))
    curve = C.heart_minutes(life, datetime(2024, 1, 21), 1440)
    assert sum(state == "walk" for _, state in curve) == sum(m for _, m in plan.walks)
    assert sum(state == "run" for _, state in curve) == plan.run[1]


def early_bed_life(bed_hour):
    day = date(2025, 3, 2)
    p = Person("clock-shift", "male", 1980, 175.0, "healthy", "test",
               events=(Event(C.RUN, "exercise_change", date(2024, 1, 1), 400, "positive", "medium"),))
    series = {"records": [], "baseline": {"rhr": 52, "steps": 20000, "tz": "+00:00"}}
    life = C.Life(7, p, series)
    life.run_rate, life.run_evening = 1.0, True
    life._sleep[day.isoformat()] = (datetime(2025, 3, 1, 23), datetime(2025, 3, 2, 7))
    life._sleep[(day+timedelta(days=1)).isoformat()] = (
        datetime(2025, 3, 2, bed_hour), datetime(2025, 3, 3, 7))
    return life, day


def test_running_time_is_relocated_without_shortening_or_rewriting_sleep():
    early, day = early_bed_life(18)
    wide, _ = early_bed_life(23)
    plan, reference = early.plan(day), wide.plan(day)
    assert plan.run[1] == reference.run[1]
    assert plan.bed == datetime(2025, 3, 2, 18)
    assert plan.steps == reference.steps == 20000
    assert plan.rhr == reference.rhr
    check_clock(plan.walks, plan.run, plan.wake, plan.bed)


def test_running_bout_cannot_silently_disappear_when_awake_window_is_impossible():
    life, day = early_bed_life(7)
    with pytest.raises(ValueError, match="activity window"):
        life.plan(day)
