"""Continuous-stream scheduling never depends on anything after the scheduled day.

Found by the cut-off matrix: adding a cold after 2024-03-01 moved p021's ordinary heart-rate week,
which lay before it (one stream drew the cold's coin flip and the week's position), and an overlap
could drop an earlier window in favour of a later one. The baseline glucose sensor sat 14-40 days
before the metformin start, so deleting a future treatment changed a past sensor session (8/8
metformin users).
"""

import dataclasses
from datetime import date, timedelta

import pytest

from mirobody_gen import continuous, devices, person
from mirobody_gen.model import Event

SEED = 7


def schedule(p, cutoff):
    lang = person.person_lang(SEED, p.person_id)
    sessions = continuous.cgm_plan(SEED, p, lang)
    windows = continuous.hr_windows(SEED, p, devices.series_for(p, SEED, lang), sessions)
    return ([(i, s["device"], s["start"], s["end"], s["setting"]) for i, s in enumerate(sessions, 1) if s["end"].date() <= cutoff],
            [(i, w["start"], w["end"], w["reason"]) for i, w in enumerate(windows, 1) if w["end"] <= cutoff])


def future_cold(p, cutoff):
    cold = Event(name=continuous.URI, event_type="health_event", start=cutoff + timedelta(days=45), duration_days=10,
                 health_effect="negative", impact_level="medium", effects={"wbc": (0.55, 1, 14)}, note="随机事件")
    return dataclasses.replace(p, events=tuple(sorted(p.events + (cold,), key=lambda e: e.start)))


@pytest.mark.parametrize("cutoff", [date(2023, 3, 1), date(2024, 3, 1), date(2025, 3, 1)])
def test_future_events_never_reschedule_the_past(cutoff):
    for p in person.build_cohort(SEED):
        base = schedule(p, cutoff)
        assert schedule(future_cold(p, cutoff), cutoff) == base, p.person_id
        trimmed = dataclasses.replace(p, events=tuple(e for e in p.events if e.start <= cutoff))
        assert schedule(trimmed, cutoff) == base, p.person_id


def test_baseline_sensor_precedes_treatment_without_depending_on_it():
    checked = 0
    for p in person.build_cohort(SEED):
        drug = continuous._metformin(p)
        sessions = continuous.cgm_plan(SEED, p, person.person_lang(SEED, p.person_id))
        if drug is None or not sessions:
            continue
        checked += 1
        baseline = sessions[0]
        assert baseline["end"].date() < drug.start and baseline["setting"] in ("clinic", "home")
        cutoff = baseline["end"].date() + timedelta(days=1)   # sensor done, treatment still ahead
        untreated = dataclasses.replace(p, events=tuple(e for e in p.events if e.start <= cutoff))
        assert schedule(untreated, cutoff)[0] == schedule(p, cutoff)[0]
    assert checked >= 5


def test_windows_are_in_calendar_order():
    for p in person.build_cohort(SEED):
        lang = person.person_lang(SEED, p.person_id)
        windows = continuous.hr_windows(SEED, p, devices.series_for(p, SEED, lang), continuous.cgm_plan(SEED, p, lang))
        assert [w["start"] for w in windows] == sorted(w["start"] for w in windows)
        assert all(a["end"] < b["start"] for a, b in zip(windows, windows[1:]))
