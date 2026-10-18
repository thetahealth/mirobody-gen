"""LDL is the modelled lipid state; total cholesterol is derived as LDL + HDL + TG/2.2.

Before, LDL was computed by Friedewald from a separately sampled TC that carried its own statin
effect, so the configured LDL -38% never reached the output: the noise-free change ran from -31% to
-52% by baseline (seed 7, p029 -51.6%). The configured magnitude is now the LDL change itself.
"""

import dataclasses
import random
from datetime import date, timedelta

import pytest

from mirobody_gen import person, physiology, spec

STATIN = "开始他汀治疗"


def statin_people():
    return [p for p in person.build_cohort(7) if any(e.name == STATIN for e in p.events)]


def configured_ldl(statin):
    """The configured LDL response at the course's starting dose (sourced dose table)."""
    response = spec.cohort()["medication_response"][statin.course.drug]
    return response["doses_mg"][f"{statin.course.steps[0].dose_mg:g}"]["ldl"]


def test_configured_statin_effect_is_the_ldl_change_for_every_baseline():
    people = statin_people()
    assert len(people) == 8
    for p in people:
        statin = next(e for e in p.events if e.name == STATIN)
        before, after = statin.start - timedelta(days=1), statin.start + timedelta(days=120)
        change = physiology.expected(p, "ldl", after) / physiology.expected(p, "ldl", before) - 1
        assert change == pytest.approx(configured_ldl(statin), abs=1e-12), p.person_id


def test_removing_or_moving_the_statin_moves_the_change():
    p = statin_people()[0]
    statin = next(e for e in p.events if e.name == STATIN)
    day = statin.start + timedelta(days=120)
    untreated = dataclasses.replace(p, events=tuple(e for e in p.events if e.name != STATIN))
    shift = timedelta(days=200)

    def moved(e):
        course = dataclasses.replace(e.course, steps=tuple(dataclasses.replace(s, start=s.start + shift)
                                                          for s in e.course.steps))
        return dataclasses.replace(e, start=e.start + shift, course=course)

    later = dataclasses.replace(p, events=tuple(moved(e) if e.name == STATIN else e for e in p.events))
    with pytest.raises(ValueError, match="must start on the event's start date"):
        dataclasses.replace(statin, start=statin.start + shift)
    assert physiology.expected(untreated, "ldl", day) == pytest.approx(
        physiology.expected(p, "ldl", day) / (1 + configured_ldl(statin)))
    assert physiology.expected(later, "ldl", day) == pytest.approx(physiology.expected(untreated, "ldl", day))


def test_no_configured_parameter_targets_the_derived_total_cholesterol():
    assert spec.indicators()["chol"]["derived_from"] and not spec.indicators()["ldl"]["derived_from"]
    assert "chol" not in person.SCRIPTS["statin"]["effects"]
    for drug in spec.cohort()["medication_response"].values():
        assert all("chol" not in response for response in drug["doses_mg"].values())
    for archetype in person.ARCHETYPES:
        assert "chol" not in archetype["shift"] and "chol" not in archetype["trend"]
    for incident in person.INCIDENTS:
        assert "chol" not in incident.get("effects", {})


def test_printed_lipids_keep_the_friedewald_identity():
    checked = 0
    for p in person.build_cohort(7):
        for e in person.encounters_for(p, 7):
            rows = {r.key: r.canonical_value for r in e.readings}
            if {"chol", "hdl", "tg", "ldl"} <= set(rows):
                checked += 1
                # printed LDL is the modelled LDL; printed TC is its rounded sum, so the recomputed
                # Friedewald value differs from the printed LDL by at most half a printed digit
                assert abs(rows["chol"] - rows["hdl"] - rows["tg"] / 2.2 - rows["ldl"]) <= 0.0051
            if {"chol", "hdl", "nonhdl"} <= set(rows):
                assert rows["nonhdl"] == pytest.approx(round(rows["chol"] - rows["hdl"], 2))
    assert checked > 200


def test_high_tg_keeps_tc_but_reports_no_ldl(monkeypatch):
    original = person.measure
    monkeypatch.setattr(person, "measure", lambda rng, who, key, when: 4.6 if key == "tg" else original(rng, who, key, when))
    p = statin_people()[0]
    e = person.encounter_for(p, date(2024, 1, 1), "follow-up", ["lipid"], random.Random(7))
    rows = {r.key: r.canonical_value for r in e.readings}
    assert "ldl" not in rows and {"chol", "hdl", "tg"} <= set(rows)
    missing = next(m for m in e.missing_results if m.key == "ldl")
    assert missing.reason_code == "tg_above_calculation_limit" and missing.details["tg_text"] == "4.60"
