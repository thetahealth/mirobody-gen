"""Full checkup controls freeze clinical content before introducing extraction stress."""
from __future__ import annotations

import copy
import sys

import pytest

from mirobody_gen import build, pairs, person


@pytest.mark.parametrize("seed", [7, 8])
def test_checkup_pairs_freeze_whole_document_and_source_measurements(seed, tmp_path, monkeypatch):
    people = person.build_cohort(seed, 8)
    encounters = {p.person_id: person.encounters_for(p, seed) for p in people}
    captures = {}

    def capture(doc, out_root, rel, *, delivery=None):
        captures[doc.doc_id] = [copy.deepcopy(b.image) for b in doc.blocks]
        return out_root / (rel + ".pdf"), 1, delivery

    monkeypatch.setattr(pairs, "render_doc", capture)
    full = pairs.render_pairs(seed, people, encounters, tmp_path, 2, scope="checkup")
    bases = {r["pair"]["pair_id"]: r for r in full if r["pair"]["variant"] == "base"}
    ordinary = pairs.render_pairs(seed, people, encounters, tmp_path, 2, scope="checkup", views="ordinary")
    assert len(ordinary) == 2
    assert ordinary == list(bases.values())
    assert all(r["kind"] == "checkup_book" and r["exam_type"] == "routine" for r in full)
    assert {r["pair"]["variant"] for r in full} >= {"base", "value.missing", "view:phone_creased"}
    for record in full:
        base = bases[record["pair"]["pair_id"]]
        assert record["presentation"]["scope"] == "checkup_document"
        assert record["presentation"]["policy_version"] == "paired-checkup-v1"
        assert record["presentation"]["purpose"] == ("ordinary" if record is base else "stress")
        for field in ("readings", "blocks", "findings", "summary", "cover", "complaints", "diagnoses", "subject", "dates"):
            assert record[field] == base[field], (record["pair"]["variant"], field)
        assert captures[record["doc_id"]] == captures[base["doc_id"]]
    for base in ordinary:
        assert base["cover"] and base["summary"] and base["blocks"]
        source = next(e for e in encounters[base["person_id"]]
                      if e.exam_date.isoformat() == base["encounter_dates"][0] and e.exam_type == "routine")
        original = {(r.key, r.canonical_value, r.unit_ucum, r.loinc, r.value, r.status)
                    for r in source.readings}
        observed = {(r["key"], r["canonical_value"], r["unit_ucum"], r["loinc"], r["value_text"], r["status"])
                    for r in base["readings"] if r["role"] == "current"}
        assert observed == original


def test_unknown_scope_fails_without_output(tmp_path):
    out = tmp_path / "absent"
    with pytest.raises(ValueError, match="scope"):
        pairs.render_pairs(7, [], {}, out, 1, scope="typo")
    assert not out.exists()


def test_scope_option_requires_pair_request_before_generation(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["build", "--pair-scope", "checkup", "--out", str(tmp_path)])
    monkeypatch.setattr(person, "build_cohort", lambda *a: pytest.fail("must reject before generation"))
    with pytest.raises(SystemExit) as exc:
        build.main()
    assert exc.value.code == 2
    assert not list(tmp_path.iterdir())


def test_real_checkup_capture_retains_every_page(tmp_path, monkeypatch):
    fitz = pytest.importorskip("fitz")
    people = person.build_cohort(7, 8)
    encounters = {p.person_id: person.encounters_for(p, 7) for p in people}
    real_render = pairs.render_doc

    def selected(doc, out_root, rel, *, delivery=None):
        if doc.doc_id.endswith(("_base", "_view_screenshot")):
            return real_render(doc, out_root, rel, delivery=delivery)
        return out_root / (rel + ".pdf"), 1, delivery

    monkeypatch.setattr(pairs, "render_doc", selected)
    records = pairs.render_pairs(7, people, encounters, tmp_path, 1, scope="checkup")
    base = next(r for r in records if r["pair"]["variant"] == "base")
    capture = next(r for r in records if r["pair"]["variant"] == "view:screenshot")
    with fitz.open(tmp_path / base["file"]) as original, fitz.open(tmp_path / capture["file"]) as delivered:
        assert len(original) > 1
        assert len(delivered) == len(original) == capture["page_count"]
        assert all(not page.get_text().strip() for page in delivered)
