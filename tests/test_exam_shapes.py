"""Exam shapes: the heading a row prints under, one sentence for both kidneys, sentence-valued findings."""
from __future__ import annotations

import dataclasses
import random

from mirobody_gen import book, layout, person, spec
from mirobody_gen.corpus import record
from mirobody_gen.document import split_encounter
from mirobody_gen.model import Finding

SEED = 7


def _routine():
    for p in person.build_cohort(SEED, 12):
        for enc in person.encounters_for(p, SEED):
            if enc.exam_type == "routine" and enc.package == "premium":
                return p, enc
    raise AssertionError("no premium checkup")


def _zh_family():
    registry = layout.build_registry(SEED)
    return next(f for f in map(registry.family, registry.pools[("zh", "checkup_center")]) if f.language == "zh-Hans")


def _book(enc_override=None):
    p, enc = _routine()
    enc = enc_override(enc) if enc_override else enc
    groups = [g for s in split_encounter(random.Random("split"), enc) for g in s]
    return p, enc, book.build_book(random.Random("book"), "b", p, enc, groups, _zh_family(), {}, banner=False)


def test_every_printed_row_names_the_heading_it_prints_under(tmp_path):
    p, enc, doc = _book()
    captions = {t.caption for t in doc.tables}
    table_rows = [r for r in doc.printed if r.section in captions]
    assert table_rows and all(r.section for r in table_rows)
    out = record(doc, tmp_path / "b.pdf", tmp_path, 1, [enc])
    assert all(row["section"] for row in out["printed_rows"])
    ecg = book.build_ecg_report(random.Random(0), "e", p, enc, _zh_family(), {}, banner=False)
    rows = record(ecg, tmp_path / "e.pdf", tmp_path, 1, [enc])["printed_rows"]
    assert rows and {r["section"] for r in rows} == {ecg.title}


def test_a_bilateral_kidney_finding_is_one_sentence_and_one_finding():
    fdef = spec.narratives()["findings"]["kidney_crystals"]
    assert fdef["bilateral"] and "{side}" not in fdef["finding"]["zh"]
    crystals = Finding(id="kidney_crystals", where="abd_us", item="kidneys", surface=fdef["surface"], icpc3=None,
                       params={"side": "左", "side_en": "left"})

    def with_crystals(enc):
        return dataclasses.replace(enc, findings=[x for x in enc.findings if x.where != "abd_us"] + [crystals])

    _, enc, doc = _book(with_crystals)
    block = next(b for b in doc.blocks if b.section_id == "abd_us")
    impressions = [t for t in block.truth if t["finding"] == "kidney_crystals"]
    assert len(impressions) == 1 and impressions[0]["value"] == fdef["impression"]["zh"]
    text = "\n".join(value for _, value in block.rows)
    assert text.count(fdef["finding"]["zh"]) == 1 and "左肾" not in text and "右肾" not in text
    truth = [x for x in doc.findings_truth if x["id"] == "kidney_crystals"]
    assert len(truth) == 1 and truth[0]["bilateral"]


def test_sentence_valued_findings_are_marked_apart_from_measurements():
    _, _, doc = _book()
    entries = [t for b in doc.blocks if b.kind in ("kv", "narrative") for t in b.truth]
    assert {t["value_kind"] for t in entries} == {"measurement", "narrative"}
    for t in entries:
        if t.get("measurements"):
            assert t["value_kind"] == "measurement"
        if t["detection_method"] == "Physiological" and t["value_kind"] == "narrative":
            assert not t["value"].replace(".", "", 1).isdigit()


def test_an_export_row_has_no_heading_to_carry(tmp_path):
    from mirobody_gen.export import build_export

    p, _ = _routine()
    encounters = person.encounters_for(p, SEED)
    names = [f"f{i}" for i in range(len(encounters))]
    doc = build_export(random.Random(0), "x", p, encounters, names, names, _zh_family(), banner=False)
    rows = record(doc, tmp_path / "x.csv", tmp_path, None, encounters)["printed_rows"]
    assert rows and {r["section"] for r in rows} == {""}


def test_the_advice_for_renal_crystals_names_crystals():
    n = spec.narratives()
    advice = n["advice"][n["findings"]["kidney_crystals"]["advice"]]
    assert advice["zh"].startswith("双肾结晶") and advice["en"].startswith("Renal crystals")
