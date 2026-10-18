"""A supplied encounter department is a fact; a hospital name is not its source."""
from dataclasses import replace
from datetime import date
from pathlib import Path
import random

import pytest

from mirobody_gen import book, corpus, layout, manifest, person
from mirobody_gen.model import Person
from mirobody_gen.document import build_doc

DAY = date(2025, 3, 2)


def _case(lang="zh", department=None):
    p = Person("department-contract", "male", 1970, 175, "hypertension", "test",
               weight_anchors=((date(2024, 1, 1), 70), (date(2026, 1, 1), 70)))
    enc = person.encounter_for(p, DAY, "clinic", ["vitals_clinic"], random.Random(7))
    enc.department = department
    registry = layout.Registry(7)
    family = registry.family(registry.pools[(lang, "hospital")][0])
    return p, enc, family


@pytest.mark.parametrize("lang,department", [("zh", "心内科"), ("en", "Internal Medicine")])
@pytest.mark.parametrize("subject_in_table", [False, True])
def test_supplied_department_is_in_body_and_real_pdf_in_every_subject_layout(lang, department, subject_in_table):
    import pymupdf
    from mirobody_gen.render import pdf

    p, enc, family = _case(lang, department)
    family = replace(family, subject_in_table=subject_in_table)
    doc = book.build_outpatient(random.Random(8), "dept-test", p, enc, family)
    rows = [pair for block in doc.blocks for pair in block.rows]
    assert ("Department" if lang == "en" else "科室", department) in rows
    payload, _ = pdf.render(doc, DAY.isoformat())
    with pymupdf.open(stream=payload, filetype="pdf") as rendered:
        text = "".join(page.get_text() for page in rendered)
    assert department.replace(" ", "") in "".join(text.split())
    record = corpus.record(doc, Path("/out/probe.pdf"), Path("/out"), 1, [enc])
    assert record["care_context"] == {"policy_version": "outpatient-department-v1",
                                       "department": department, "department_source": "encounter"}


@pytest.mark.parametrize("department", [None, "", "   "])
def test_unknown_department_is_not_inferred_from_maternal_or_tcm_institution(department):
    p, enc, family = _case(department=department)
    for institution in ("樾沅妇幼保健院", "霁璟中医医院"):
        doc = book.build_outpatient(random.Random(8), "dept-test", p, enc,
                                    replace(family, institution=institution))
        assert all(label != "科室" for block in doc.blocks for label, _ in block.rows)
        record = corpus.record(doc, Path("/out/probe.pdf"), Path("/out"), 1, [enc])
        assert record["care_context"] == {"policy_version": "outpatient-department-v1",
                                           "department": None, "department_source": "unknown"}


def test_department_does_not_change_random_state_clinical_rows_or_language_of_source():
    p, enc, family = _case("en", None)
    a_rng, b_rng = random.Random(8), random.Random(8)
    control = book.build_outpatient(a_rng, "dept-test", p, enc, family)
    enc.department = "心内科 / Cardiology"
    stated = book.build_outpatient(b_rng, "dept-test", p, enc, family)
    assert a_rng.getstate() == b_rng.getstate()
    assert stated.readings == control.readings and stated.printed == control.printed
    assert stated.subject == control.subject and stated.dates == control.dates
    assert stated.diagnoses_truth == control.diagnoses_truth
    assert stated.complaints_truth == control.complaints_truth
    assert stated.blocks[0].rows[0] == ("Department", "心内科 / Cardiology")
    assert stated.blocks[0].rows[1:] == control.blocks[0].rows


def test_encounter_constructor_and_manifest_preserve_supplied_department():
    p, enc, _ = _case()
    supplied = replace(enc, department="General Medicine / 诊室2")
    assert manifest.encounter_record(p, supplied)["department"] == "General Medicine / 诊室2"
    assert manifest.encounter_record(p, replace(enc, department=None))["department"] is None


def test_same_visit_lab_uses_supplied_department_without_resampling_layout():
    p, enc, family = _case("zh", "源科室 / Clinic 2")
    witnessed = False
    for seed in range(10):
        a_rng, b_rng = random.Random(seed), random.Random(seed)
        explicit = build_doc(a_rng, "same-visit", p, enc, ["vitals"], family, {})
        control = build_doc(b_rng, "same-visit", p, replace(enc, department=None), ["vitals"], family, {})
        assert a_rng.getstate() == b_rng.getstate()
        assert explicit.readings == control.readings and explicit.printed == control.printed
        if "科室" in dict(explicit.subject):
            witnessed = True
            assert dict(explicit.subject)["科室"] == "源科室 / Clinic 2"
    assert witnessed
