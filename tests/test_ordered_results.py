"""B: every requested analyte has one result or an explicit non-result, never both."""
import copy
from dataclasses import asdict, replace
from datetime import date
import json
import random

import pytest

from mirobody_gen import manifest, person, spec, verify
from mirobody_gen.document import build_doc, split_encounter
from mirobody_gen.layout import build_registry
from mirobody_gen.model import Person, Reading
from mirobody_gen.pairs import clean_family
from mirobody_gen.render import pdf, sheet


def subject():
    return Person("ordered", "male", 1980, 175, "healthy", "上海",
                  weight_anchors=((date(2024, 1, 1), 70),))


def lipid(monkeypatch, tg=4.51, **kwargs):
    original = person.measure
    monkeypatch.setattr(person, "measure", lambda rng, p, key, when: {"chol": 6, "hdl": 1.2, "tg": tg}.get(key)
                        if key in {"chol", "hdl", "tg"} else original(rng, p, key, when))
    return person.encounter_for(subject(), date(2024, 1, 1), "follow-up", ["lipid"], random.Random(7), **kwargs)


@pytest.mark.parametrize("tg,available", [(4.49, True), (4.5, True), (4.51, False)])
def test_ldl_boundary_has_result_or_explicit_reason(monkeypatch, tg, available):
    e = lipid(monkeypatch, tg)
    results = {r.key: r for r in e.readings}
    missing = {r.key: r for r in e.missing_results}
    assert "ldl" in e.ordered_keys
    assert ("ldl" in results, "ldl" in missing) == (available, not available)
    if available:
        assert results["ldl"].result_method == "calculated_friedewald"
        assert results["ldl"].source == "synthetic_calculation"
    else:
        assert missing["ldl"].reason_code == "tg_above_calculation_limit"
        assert missing["ldl"].details == {"tg": tg, "tg_text": spec.format_value("tg", tg), "tg_unit": "mmol/L",
                                          "limit": 4.5, "direct_result_supplied": False}
    e.validate_order_results()


def original_high_tg_encounter(monkeypatch, p):
    """Retain the historical trigger as explicit input across RNG policy versions.

    The legacy seed7/p035/2023-02-11 trigger is TG=4.53. New stream versions
    intentionally draw a different value, which must not erase this regression.
    This is a generator contract fixture, never an extraction prediction.
    """
    original = person.measure
    monkeypatch.setattr(person, "measure", lambda rng, who, key, when:
                        4.53 if key == "tg" else original(rng, who, key, when))
    return person.encounter_for(p, date(2023, 2, 11), "routine", ["checkup_standard", "ecg"],
                                random.Random("historical-p035-trigger"), "standard")


def test_fixed_longitudinal_original_counterexample_is_not_silent(monkeypatch):
    p = next(p for p in person.build_cohort(7) if p.person_id == "p035")
    e = original_high_tg_encounter(monkeypatch, p)
    assert next(r.value for r in e.readings if r.key == "tg") == "4.53"
    assert "ldl" not in {r.key for r in e.readings}
    missing = next(m for m in e.missing_results if m.key == "ldl")
    assert missing.reason_code == "tg_above_calculation_limit" and missing.unit == "mmol/L"
    assert e.panels == ("checkup_standard", "ecg")


def test_missing_calculation_inputs_are_named(monkeypatch):
    monkeypatch.setitem(person.ORDERS, "ldl_only", ["ldl"])
    e = person.encounter_for(subject(), date(2024, 1, 1), "specialty", ["ldl_only"], random.Random(7))
    assert not e.readings
    m = e.missing_results[0]
    assert m.reason_code == "calculation_inputs_missing"
    assert m.details["missing_inputs"] == ["chol", "hdl", "tg"]
    assert split_encounter(random.Random(7), e) == [["lipid_full"]]


def test_unknown_panel_cannot_be_certified_as_empty_orders():
    with pytest.raises(ValueError, match="Unknown order panel"):
        person.encounter_for(subject(), date(2024, 1, 1), "specialty", ["misspelled-lipid"], random.Random(7))


@pytest.mark.parametrize("panel", ["ultrasound", "imaging"])
def test_existing_non_analyte_procedures_are_not_unknown_panels(panel):
    e = person.encounter_for(subject(), date(2024, 1, 1), "specialty", [panel], random.Random(7))
    assert e.panels == (panel,) and e.ordered_keys == () and not e.readings


def direct():
    return Reading("ldl", "直接LDL", "2.71", "mmol/L", "", "normal", canonical_value=2.71,
                   unit_ucum="mmol/L", result_method="direct_assay", source="authored-synthetic-direct-result:1")


@pytest.mark.parametrize("tg", [2, 4.51])
def test_explicit_direct_result_is_preserved_and_never_recalculated(monkeypatch, tg):
    supplied = direct()
    before = asdict(supplied)
    e = lipid(monkeypatch, tg, direct_ldl=supplied)
    got = next(r for r in e.readings if r.key == "ldl")
    assert asdict(got) == before and got is not supplied
    assert not any(m.key == "ldl" for m in e.missing_results)
    assert asdict(supplied) == before


@pytest.mark.parametrize("change", [{"source": ""}, {"unit": "mg/dL"}, {"canonical_value": float("nan")},
                                    {"result_method": "calculated_friedewald"}, {"value": "1.0"}])
def test_invalid_claimed_direct_result_rejected(monkeypatch, change):
    with pytest.raises(ValueError):
        lipid(monkeypatch, direct_ldl=replace(direct(), **change))


def test_all_default_orders_are_accounted_once_without_dropping_sex_exclusions():
    for p in person.build_cohort(7):
        for e in person.encounters_for(p, 7):
            expected = list(dict.fromkeys(k for panel in e.panels for k in person.ORDERS.get(panel, [])))
            assert list(e.ordered_keys) == expected
            counts = [r.key for r in e.readings] + [m.key for m in e.missing_results]
            assert sorted(counts) == sorted(expected) and len(counts) == len(set(counts))
            e.validate_order_results()


def test_manifest_gate_rejects_missing_and_double_outcomes(monkeypatch, tmp_path):
    e = lipid(monkeypatch)
    record = manifest.encounter_record(subject(), e)
    assert record["ordered_keys"] and record["missing_results"]
    bad = copy.deepcopy(record)
    bad["missing_results"] = []
    (tmp_path / "manifest.jsonl").write_text(json.dumps(bad) + "\n")
    result = verify.run(tmp_path, requested_people=1, expected_people=1, people=1,
                        rendered=False, pairs=False, continuous=False)
    assert next(c for c in result["checks"] if c["check"] == "order_results")["status"] == "failed"
    e.readings.append(replace(e.readings[0], key="ldl"))
    with pytest.raises(ValueError):
        manifest.encounter_record(subject(), e)


def test_missing_result_explanation_reaches_existing_pdf_csv_and_file_truth(monkeypatch, tmp_path):
    from mirobody_gen import corpus
    from mirobody_gen.audit import readability
    e = lipid(monkeypatch)
    registry = build_registry(7)
    f = clean_family(registry.family(registry.pools[("en", "hospital")][0]))
    groups = [g for chunk in split_encounter(random.Random(7), e) for g in chunk]
    d = build_doc(random.Random(7), "missing-ldl", subject(), e, groups, f, {})
    assert len(d.missing_results) == 1 and not any(r.key == "ldl" for r in d.readings)
    csv_text = sheet.to_csv(d, True).decode()
    assert "not resulted" in csv_text and "4.51" in csv_text and "no direct-assay result supplied" in csv_text
    import fitz
    data, _ = pdf.render(d, "2024-01-01T12:00:00")
    with fitz.open(stream=data, filetype="pdf") as doc:
        text = " ".join(page.get_text() for page in doc)
    assert "not resulted" in text and "4.51" in text
    rec = corpus.record(d, tmp_path / "d.pdf", tmp_path, 1, [e])
    assert rec["missing_results"][0]["reason_code"] == "tg_above_calculation_limit"
    assert not any(r["key"] == "ldl" for r in rec["readings"])
    (tmp_path / "d.pdf").write_bytes(data)
    (tmp_path / "files.jsonl").write_text(json.dumps(rec) + "\n")
    assert readability.audit(tmp_path / "files.jsonl") == 0
    lost = copy.deepcopy(d)
    lost.narratives = []
    lost_data, _ = pdf.render(lost, "2024-01-01T12:00:00")
    (tmp_path / "d.pdf").write_bytes(lost_data)
    assert readability.audit(tmp_path / "files.jsonl") > 0


def test_book_has_no_empty_table_for_a_non_result_only_group():
    """A male premium checkup orders cervical screening; the book explains it instead of printing a
    captioned table with headers and no rows."""
    p = next(x for x in person.build_cohort(7) if x.person_id == "p001")
    e = next(x for x in person.encounters_for(p, 7) if x.exam_date == date(2022, 11, 27))
    registry = build_registry(7)
    family = next(f for f in map(registry.family, registry.pools[("zh", "checkup_center")])
                  if f.columns.count("name") == 1)
    groups = [g for chunk in split_encounter(random.Random(7), e) for g in chunk]
    assert "cervical" in groups
    d = build_doc(random.Random(7), "book", p, e, groups, family, {}, book=True)
    assert d.tables and all(t.rows for t in d.tables)
    explained = {m["key"] for m in d.missing_results}
    assert {"hpv16", "hpv18", "hpv_other", "tct"} <= explained


def test_tg_in_the_note_matches_its_printed_precision(monkeypatch):
    e = lipid(monkeypatch, tg=4.6)
    note = next(m for m in e.missing_results if m.key == "ldl").explanation(False)
    assert next(r.value for r in e.readings if r.key == "tg") == "4.60" and "TG=4.60 mmol/L" in note


def test_export_note_names_the_analyte_not_its_key(monkeypatch):
    from mirobody_gen.export import build_export

    registry = build_registry(7)
    p = next(x for x in person.build_cohort(7) if x.person_id == "p035")
    encounters = person.encounters_for(p, 7)
    trigger = original_high_tg_encounter(monkeypatch, p)
    encounters = [trigger if e.exam_date == trigger.exam_date else e for e in encounters]
    base = registry.family(registry.pools[("zh", "checkup_center")][0])
    for seed in range(200):
        d = build_export(random.Random(seed), "export", p, encounters, [""] * len(encounters),
                         [""] * len(encounters), base, True)
        if d.missing_results:
            break
    else:
        pytest.fail("no export sampled the LDL column")
    text = d.missing_results[0]["printed_text"]
    assert spec.indicators()["ldl"]["zh"] in text and " ldl:" not in text


def test_vitals_and_auxiliary_parameters_carry_provenance(tmp_path):
    from mirobody_gen import build

    out = tmp_path / "b"
    build.main(["--seed", "7", "--people", "2", "--render", "--out", str(out)])
    readings = [r for line in (out / "files.jsonl").read_text(encoding="utf-8").splitlines()
                for r in json.loads(line).get("readings") or [] if r.get("role", "current") == "current"]
    assert readings and all(r.get("source") for r in readings)
