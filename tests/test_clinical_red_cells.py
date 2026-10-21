"""Clinical profile, red cells: haemoglobin varies as haemoglobin does, and the red cell count follows."""
from __future__ import annotations

import random
from datetime import date

import pytest

from mirobody_gen import clinical, person, spec

RED_CELLS = {"rbc", "hgb", "hct", "mcv", "mch", "mchc"}


def _blood_counts(monkeypatch, enabled):
    monkeypatch.setattr(clinical, "ENABLED", enabled)
    people = person.build_cohort(7)
    out = []
    for p in people:
        for enc in person.encounters_for(p, 7):
            vals = {r.key: r.canonical_value for r in enc.readings if r.value_kind == "quantitative"}
            if {"hgb", "rbc", "mcv", "mchc"} <= set(vals):
                out.append((p, vals))
    return out


def test_haemoglobin_and_mchc_tails_stay_within_their_own_variation(monkeypatch):
    default, kept = _blood_counts(monkeypatch, False), _blood_counts(monkeypatch, True)
    assert len(default) == len(kept)

    def high_mchc(rows):
        return sum(v["mchc"] > 360 for _, v in rows)

    assert high_mchc(kept) < high_mchc(default)
    men = [v["hgb"] for p, v in kept if p.sex == "male" and p.archetype != "iron_deficiency_anemia"]
    assert max(men) < max(v["hgb"] for p, v in default if p.sex == "male")
    assert max(men) <= spec.reference_bounds("hgb", "male")[1] + 10


def test_the_printed_indices_still_multiply_out(monkeypatch):
    for _, v in _blood_counts(monkeypatch, True):
        assert v["hgb"] == pytest.approx(v["rbc"] * v["mchc"] * v["mcv"] / 1000, rel=0.01)


def test_iron_deficiency_still_lowers_haemoglobin(monkeypatch):
    rows = _blood_counts(monkeypatch, True)
    women = {a: [v["hgb"] for p, v in rows if p.sex == "female" and p.archetype == a]
             for a in ("healthy", "iron_deficiency_anemia")}
    assert sum(women["iron_deficiency_anemia"]) / len(women["iron_deficiency_anemia"]) \
        < sum(women["healthy"]) / len(women["healthy"]) - 10


def test_other_results_of_the_visit_are_unchanged(monkeypatch):
    p = person.build_cohort(7)[0]

    def visit(enabled):
        monkeypatch.setattr(clinical, "ENABLED", enabled)
        enc = person.encounter_for(p, date(2025, 3, 2), "followup", ["cbc", "liver", "lipid"], random.Random(7))
        return {r.key: r.value for r in enc.readings}

    default, kept = visit(False), visit(True)
    assert default.keys() == kept.keys()
    assert {k: v for k, v in default.items() if k not in RED_CELLS} == {k: v for k, v in kept.items() if k not in RED_CELLS}
    assert default["rbc"] != kept["rbc"]
