"""Configured pulse effects cross synthetic channels without equating their contexts."""
from dataclasses import replace
from datetime import date
import json
import random

import pytest

from mirobody_gen import continuous, devices, physiology
from mirobody_gen.model import Event, Person

SEED = 1
START = date(2025, 1, 10)


def _person(effects=()):
    events = tuple(Event(f"pulse-probe-{i}", "health_event", START, 14,
                         "neutral", "low", {"pulse": effect}) for i, effect in enumerate(effects))
    return Person("pulse-contract", "male", 1980, 170, "hypertension", "CN",
                  weight_anchors=((date(2025, 1, 1), 70), (date(2025, 4, 1), 70)), events=events)


def _without_pulse(p):
    return replace(p, events=tuple(replace(e, effects={}) for e in p.events))


@pytest.mark.parametrize("effects", [((0.12, 7, 7),), ((-0.10, 14, None),),
                                     ((0.12, 7, 7), (-0.10, 14, None))])
def test_cuff_pulse_follows_event_onset_sum_and_decay_with_same_noise(effects):
    p = _person(effects)
    actual = devices.series_for(p, SEED, "en")
    control = devices.series_for(_without_pulse(p), SEED, "en")
    cuffs = [(a, b) for a, b in zip(actual["records"], control["records"]) if a["_metric"] == "hr"]
    assert len(cuffs) > 50
    before = ramp = full = after = 0
    for a, b in cuffs:
        elapsed = (date.fromisoformat(a["time"][:10])-START).days
        factor = 1.0
        if elapsed >= 0:
            for magnitude, onset, half in effects:
                decay = 0.5**((elapsed-14)/half) if elapsed > 14 and half is not None else 1
                contribution = magnitude*min(1, elapsed/onset)*decay
                if abs(contribution) > 1e-6:
                    factor += contribution
        # Paired quantization can differ by at most one beat; the measurement noise cancels.
        delta = actual["baseline"]["rhr"]*1.08*(factor-1)
        assert abs((a["value"]-b["value"])-delta) <= 1.0
        before += elapsed < 0
        ramp += 0 < elapsed < 7
        full += elapsed == 14
        after += elapsed > 28
    assert all((before, ramp, full, after))


def test_pulse_event_does_not_resample_blood_pressure_weight_steps_or_sleep():
    p = _person(((0.12, 7, 7),))
    actual = devices.series_for(p, SEED, "en")
    control = devices.series_for(_without_pulse(p), SEED, "en")
    assert actual["habits"] == control["habits"] and actual["baseline"] == control["baseline"]
    assert len(actual["records"]) == len(control["records"])
    for a, b in zip(actual["records"], control["records"]):
        assert (a["time"], a["_metric"]) == (b["time"], b["_metric"])
        if a["_metric"] not in ("hr", "rhr"):
            assert a == b


def test_resting_summary_and_clinic_measurement_keep_their_own_baselines():
    p = _person(((0.12, 7, 7),))
    control = _without_pulse(p)
    series = devices.series_for(p, SEED, "en")
    life = continuous.Life(SEED, p, series)
    resting = [r for r in series["records"] if r["_metric"] == "rhr"]
    assert resting
    for record in resting:
        assert life.resting_hr(date.fromisoformat(record["time"][:10])) == record["value"]
    # Test the no-summary fallback separately; it is not a cuff or clinic observation.
    day = date(2025, 1, 24)
    fallback = continuous.Life(SEED, p, {**series, "records": []})
    assert fallback.resting_hr(day) == pytest.approx(series["baseline"]["rhr"]*1.12)
    actual = physiology.measure(random.Random(7), p, "pulse", day)
    null = physiology.measure(random.Random(7), control, "pulse", day)
    assert actual/null == pytest.approx(1.12)
    assert physiology.expected(p, "pulse", day) != fallback.resting_hr(day)


def test_exported_pulse_truth_records_context_and_reuses_home_cuff_values(tmp_path):
    p = _person(((0.12, 7, 7),))
    series = devices.series_for(p, SEED, "en")
    devices.write_all(tmp_path, [p], SEED, {p.person_id: "en"})
    truth = json.loads((tmp_path/"devices.jsonl").read_text())
    assert truth["pulse_model"] == series["pulse_model"] == {
        "policy_version": "device-pulse-v2", "authority": "synthetic_model",
        "contexts": {"rhr": "daily_resting_summary", "hr": "home_cuff"}}
    expected = {(r["time"], r["value"]) for r in series["records"] if r["_metric"] == "hr"}
    assert {(r["time"], r["value"]) for r in truth["records"] if r["metric"] == "hr"} == expected
    windows = devices.bp_log_windows(series, random.Random(7))
    assert windows
    assert all((r["time"], r["value"]) in expected for w in windows for day in w
               for r in day["records"] if r["_metric"] == "hr")
    for name in truth["files"]:
        records = json.loads((tmp_path/name).read_text())["records"]
        assert all(set(r) <= {"indicator", "value", "unit", "time", "source", "end_time"} for r in records)
        assert all(not any(k.startswith("_") for k in r) and "pulse_model" not in r for r in records)
