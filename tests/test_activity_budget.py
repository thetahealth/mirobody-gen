"""Shared activity must conserve the synthetic day's step total without rescaling exports."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from mirobody_gen import continuous as C, devices, person
from mirobody_gen.model import Event, Person

DAY = date(2025, 3, 2)


def life_for(steps, *, observed=True):
    p = Person("activity-budget", "male", 1980, 175.0, "healthy", "test",
               events=(Event(C.RUN, "exercise_change", date(2024, 1, 1), 400, "positive", "medium",
                             effects={"pulse": (-0.10, 60, None)}),),
               weight_anchors=((date(2024, 1, 1), 70.0), (date(2026, 1, 1), 70.0)))
    records = [{"_metric": "rhr", "value": 52, "time": f"{DAY}T08:00:00+00:00"}]
    if observed:
        records.append({"_metric": "steps", "value": steps, "time": f"{DAY}T23:59:00+00:00"})
    series = {"records": records, "vendor": "apple", "source": "apple_health",
              "baseline": {"rhr": 52.0, "steps": steps, "tz": "+00:00", "sleep_min": 430}}
    life = C.Life(7, p, series)
    life.run_rate = 1.0
    return life


@pytest.mark.parametrize("steps", [0, 160, 3999, 4000, 6159, 6400, 20000])
def test_run_and_walk_bouts_fit_daily_total_without_changing_resting_hr(steps):
    life = life_for(steps)
    plan = life.plan(DAY)
    reference = life_for(20000).plan(DAY)
    run_steps = plan.run[1]*C.RUN_SPM if plan.run else 0
    walk_steps = sum(m for _, m in plan.walks)*C.WALK_SPM
    assert plan.steps == steps
    assert run_steps + walk_steps <= steps
    assert plan.rhr == 52  # Do not clamp low resting rates to make activity tests pass.
    assert (plan.wake, plan.bed, plan.meals) == (reference.wake, reference.bed, reference.meals)
    if steps < 25*C.RUN_SPM:
        assert plan.run is None
    else:
        assert plan.run is not None and 25 <= plan.run[1] <= 50
        if steps >= reference.run[1]*C.RUN_SPM:
            assert plan.run == reference.run
    facts = plan.describe()
    assert facts["activity_policy_version"] == "daily-activity-v3"
    assert facts["steps_source"] == "phone_store"
    assert sum(facts["step_allocation"].values()) == steps
    assert all(n >= 0 for n in facts["step_allocation"].values())


def test_missing_steps_keep_model_provenance_instead_of_claiming_a_measurement():
    plan = life_for(5000, observed=False).plan(DAY)
    assert plan.steps == 5000
    assert plan.describe()["steps_source"] == "baseline_model"


class ExportCapture:
    def __init__(self):
        self.rows = []

    def add(self, kind, start, end, **kwargs):
        self.rows.append({"kind": kind, "start": start, "end": end, **kwargs})

    def _version(self, *args):
        return "test"

    def device(self, *args):
        return None


def test_export_rejects_inconsistent_overridden_plan_instead_of_shrinking_cadence():
    life = life_for(1000)
    plan = life.plan(DAY)
    life._plans[DAY] = replace(plan, run=(datetime(2025, 3, 2, 19), 50), walks=())
    with pytest.raises(ValueError, match="step budget"):
        C._apple_daily(ExportCapture(), life, life.series)


def test_valid_export_and_heart_curve_share_the_corrected_activity():
    life = life_for(5000)
    plan = life.plan(DAY)
    export = ExportCapture()
    C._apple_daily(export, life, life.series)
    exported = [x for x in export.rows if x["kind"] == "HKQuantityTypeIdentifierStepCount"]
    assert sum(int(x["value"]) for x in exported) == 5000
    run = next(x for x in exported if x["start"] == plan.run[0])
    assert int(run["value"]) == plan.run[1]*C.RUN_SPM
    assert run["end"]-run["start"] == timedelta(minutes=plan.run[1])
    curve = C.heart_minutes(life, datetime.combine(DAY, datetime.min.time()), 1440)
    assert sum(state == "run" for _, state in curve) == plan.run[1]


def test_canceled_run_reallocation_does_not_add_a_walk_overlap():
    p = next(p for p in person.build_cohort(7) if p.person_id == "p050")
    series = devices.series_for(p, 7, person.person_lang(7, p.person_id))
    plan = C.Life(7, p, series).plan(date(2024, 6, 9))
    assert plan.steps == 3616 and plan.run is None
    assert len(plan.walks) > 1
    for (start, minutes), (next_start, _) in zip(plan.walks, plan.walks[1:]):
        assert start + timedelta(minutes=minutes) <= next_start
