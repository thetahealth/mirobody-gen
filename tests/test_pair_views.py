"""Ordinary table controls are a selectable subset of the full stress pairs."""
from __future__ import annotations

import sys

import pytest

from mirobody_gen import build, pairs, person, spec


def _render_stub(doc, out_root, rel, *, delivery=None):
    delivery = delivery or {"tier": "T0", "scene": None, "severity": None,
                            "ops": [], "container": "pdf", "annotations": []}
    return out_root / (rel + ".pdf"), 1, delivery


def _current(record):
    return sorted((r["key"], r["loinc"] or "", r["canonical_value"], r["value_text"],
                   r["unit_ucum"], r["observed"], r["value_kind"], r["status"])
                  for r in record["readings"] if r["role"] == "current")


@pytest.mark.parametrize("seed", [7, 8])
def test_ordinary_is_same_control_with_complete_evidence_and_retained_stress(seed, tmp_path, monkeypatch):
    cohort = person.build_cohort(seed, 8)
    encounters = {p.person_id: person.encounters_for(p, seed) for p in cohort}
    monkeypatch.setattr(pairs, "render_doc", _render_stub)
    full = pairs.render_pairs(seed, cohort, encounters, tmp_path, 2)
    ordinary = pairs.render_pairs(seed, cohort, encounters, tmp_path, 2, views="ordinary")
    bases = {r["pair"]["pair_id"]: r for r in full if r["pair"]["variant"] == "base"}
    assert ordinary == list(bases.values())
    assert len(ordinary) == 2
    assert {r["presentation"]["purpose"] for r in ordinary} == {"ordinary"}
    seen = set()
    abnormal = 0
    for record in full:
        variant = record["pair"]["variant"]
        assert _current(record) == _current(bases[record["pair"]["pair_id"]]), variant
        if variant != "base":
            assert record["presentation"]["purpose"] == "stress"
            seen.add(variant)
    # Selection must leave difficult, missing-evidence cases available in full mode.
    assert {"unit.missing", "reference.absent", "value.missing", "view:phone_creased"} <= seen
    for record in ordinary:
        assert not record["distractors"]
        for row in record["printed_rows"]:
            current = [record["readings"][i] for i in row["readings"]
                       if record["readings"][i]["role"] == "current"]
            quantitative = [r for r in current if r["value_kind"] == "quantitative"]
            if quantitative:
                assert row["readable"]
                if any(r["unit_ucum"] for r in quantitative):
                    assert row["item_unit"]
                if any(spec.indicators()[r["key"]]["reference"] for r in quantitative):
                    assert row["item_range"]
                if any(r["status"] != "normal" for r in quantitative):
                    assert row["is_abnormal"] == "1"
                    abnormal += 1
    assert abnormal > 0


def test_unknown_view_rejected_without_output(tmp_path):
    out = tmp_path / "not-created"
    with pytest.raises(ValueError, match="views"):
        pairs.render_pairs(7, [], {}, out, 1, views="typo")
    assert not out.exists()


def test_view_option_without_pair_request_fails_before_generation(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["build", "--pair-views", "ordinary", "--out", str(tmp_path)])
    monkeypatch.setattr(person, "build_cohort", lambda *a: pytest.fail("must reject before generating"))
    with pytest.raises(SystemExit) as exc:
        build.main()
    assert exc.value.code == 2
    assert not list(tmp_path.iterdir())
