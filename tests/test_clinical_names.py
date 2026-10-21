"""Clinical profile, names: a patient's name reads as the person's sex and is theirs alone; no printed
name mixes a feminine and a masculine character."""
from __future__ import annotations

import random
from datetime import date

import pytest

from mirobody_gen import clinical, clinical_names, continuous, document, layout, person, spec


@pytest.fixture
def clinical_on(monkeypatch):
    monkeypatch.setattr(clinical, "ENABLED", True)


def _subject_name(p, lang="zh"):
    registry = layout.Registry(7)
    family = registry.family(registry.pools[(lang, "hospital")][0])
    fields = document.subject_fields(random.Random(3), p, family, date(2025, 3, 2), "", "")
    return fields[0][1]


def test_every_pool_name_is_classified():
    pools = spec.fiction()
    assert {clinical_names.name_sex(n) for n in pools["person_names_zh"]} <= {"female", "male", "neutral", "mixed"}
    assert any(clinical_names.name_sex(n) == "mixed" for n in pools["person_names_zh"])


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_patients_get_own_sex_unique_names_across_documents_and_exports(lang, clinical_on):
    people = person.build_cohort(7)
    names = {p.person_id: _subject_name(p, lang) for p in people}
    assert len(set(names.values())) == len(people)
    for p in people:
        assert clinical_names.name_sex(names[p.person_id]) in (p.sex, "neutral")
        first, last = continuous._name(p, lang)
        assert names[p.person_id] in (f"{first} {last}", f"{last}{first}")


def test_default_names_include_the_cases_clinical_mode_removes():
    people = person.build_cohort(7)
    names = [_subject_name(p) for p in people]
    assert len(set(names)) < len(names) or any(clinical_names.name_sex(n) == "mixed" for n in names)


def test_a_signing_name_never_mixes():
    pool = spec.fiction()["person_names_zh"]
    mixed = next(n for n in pool if clinical_names.name_sex(n) == "mixed")
    assert clinical_names.name_sex(clinical_names.staff_name(mixed, pool)) != "mixed"
    plain = next(n for n in pool if clinical_names.name_sex(n) != "mixed")
    assert clinical_names.staff_name(plain, pool) == plain
    assert clinical_names.signatures([("报告医师", mixed), ("时间", "2025-03-02 10:00")], english=False)[1][1] \
        == "2025-03-02 10:00"
