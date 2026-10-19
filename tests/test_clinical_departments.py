"""Clinical profile, departments: a document prints the visit's own department, the checkup centre for a
checkup, or none; never one drawn at random."""
from __future__ import annotations

import dataclasses
import random
from datetime import date

import pytest

from mirobody_gen import clinical, clinical_departments, document, handwriting, layout, person, spec
from mirobody_gen.model import Person

DAY = date(2025, 3, 2)
LABEL = spec.templates()["subject_labels"]["zh"]["department"]


def _visit(exam_type="clinic", orders=("vitals_clinic", "renal"), department=None):
    p = Person("department-contract", "male", 1962, 172, "ckd_progression", "test")
    enc = person.encounter_for(p, DAY, exam_type, list(orders), random.Random(7))
    enc.department = department
    return p, enc


def _slip(p, enc, seed, monkeypatch, enabled):
    monkeypatch.setattr(clinical, "ENABLED", enabled)
    registry = layout.Registry(7)
    family = next(f for f in map(registry.family, registry.pools[("zh", "hospital")]) if f.language == "zh-Hans")
    rng = random.Random(seed)
    groups = document.split_encounter(random.Random(1), enc)[0]
    doc = document.build_doc(rng, "dept-test", p, enc, groups, family, {})
    return doc.subject, rng.random()


def test_the_printed_department_is_explicit_or_the_checkup_centre():
    _, enc = _visit()
    assert clinical_departments.printed(enc, "zh") == ""
    assert clinical_departments.printed(dataclasses.replace(enc, department="肾内科"), "zh") == "肾内科"
    assert clinical_departments.printed(dataclasses.replace(enc, exam_location="checkup-center"), "zh") == "体检中心"
    for lang, name in clinical_departments.CHECKUP_CENTRE.items():
        assert name in spec.templates()["departments"][lang]


@pytest.mark.parametrize("department", [None, "肾内科"])
def test_a_slip_prints_only_a_known_department_and_keeps_the_draws(department, monkeypatch):
    p, enc = _visit(department=department)
    shown = 0
    for seed in range(30):
        default, after_default = _slip(p, enc, seed, monkeypatch, False)
        kept, after_kept = _slip(p, enc, seed, monkeypatch, True)
        assert after_default == after_kept
        labels = [label for label, _ in default]
        assert [label for label, _ in kept] == ([x for x in labels if x != LABEL] if department is None else labels)
        assert dict(kept).get(LABEL) == (department if LABEL in labels else None)
        shown += LABEL in labels
    assert shown


def test_a_checkup_book_page_names_the_checkup_centre(monkeypatch):
    p, enc = _visit("routine", ("checkup_basic",))
    printed = {value for seed in range(30) for label, value in _slip(p, enc, seed, monkeypatch, True)[0]
               if label == LABEL}
    assert printed == {"体检中心"}


def test_a_handwritten_booklet_leaves_an_unknown_department_blank(monkeypatch):
    p, enc = _visit(orders=("vitals_clinic",))
    registry = layout.Registry(7)
    family = registry.family(registry.pools[("zh", "hospital")][0])
    label = spec.handwriting()["note"]["zh"]["fields"]["department"] + "："

    departments = tuple(spec.handwriting()["note"]["zh"]["departments"])

    def after_label(enabled):
        monkeypatch.setattr(clinical, "ENABLED", enabled)
        note = handwriting.build_note(7, p, enc, family, "zh", "dept-note", True)
        return next(x for x in note.transcript if label in x).split(label, 1)[1]

    assert after_label(False).startswith(departments)
    assert not after_label(True).startswith(departments)
