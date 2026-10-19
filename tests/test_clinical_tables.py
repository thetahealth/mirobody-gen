"""Clinical profile, complete tables: every result table a clinician reads has units, a reference range,
printed flags, subject fields above it and one script."""
from __future__ import annotations

import dataclasses
import random
from datetime import date

import pytest

from mirobody_gen import clinical, clinical_tables, corpus, document, hazards, layout, person, spec
from mirobody_gen.model import Person

DAY = date(2025, 3, 2)


@pytest.fixture
def clinical_on(monkeypatch):
    monkeypatch.setattr(clinical, "ENABLED", True)


def _complete_family(seed: int = 7) -> layout.Family:
    registry = layout.Registry(seed)
    return next(f for f in map(registry.family, registry.pools[("zh", "hospital")]) if clinical_tables.complete(f))


@pytest.mark.parametrize("change", [{"unit_at": "none"}, {"unit_at": "reference_amp"}, {"unit_at": "header"},
                                    {"flag_at": "none"}, {"subject_in_table": True}, {"language": "zh-Hant"}])
def test_an_incomplete_template_is_rejected(change):
    f = _complete_family()
    assert not clinical_tables.complete(dataclasses.replace(f, **change))


def test_a_template_without_ranges_or_with_traditional_headers_is_rejected():
    f = _complete_family()
    assert not clinical_tables.complete(dataclasses.replace(f, columns=tuple(c for c in f.columns if c != "reference")))
    assert not clinical_tables.complete(dataclasses.replace(f, headers={**f.headers, "name": "檢驗項目"}))
    assert not clinical_tables.complete(dataclasses.replace(f, headers={**f.headers, "name": "總項目"}))


def test_default_draws_keep_every_institution():
    process = corpus.Institutions(layout.Registry(7), 7)
    assert process.allowed is None and process.admits(process.registry.big["hospital"])


@pytest.mark.parametrize("seed", [7, 8])
def test_every_visit_is_drawn_from_a_template_complete_in_its_year(seed, clinical_on):
    registry = layout.Registry(seed)
    process = corpus.Institutions(registry, seed)
    assert all(process.admits(i) for order in process.fresh.values() for i in order)
    people = person.build_cohort(seed, 12)
    encounters = {p.person_id: person.encounters_for(p, seed) for p in people}
    homes, chosen, _ = corpus.assign_institutions(seed, people, encounters, process)
    assert all(process.admits(i) for _, home in homes.values() for i in home.values())
    for (pid, idx), index in chosen.items():
        year = encounters[pid][idx - 1].exam_date.year
        assert clinical_tables.complete(layout.revised(registry.family(index), year)[0])


def test_incidents_advance_the_stream_but_stay_off_the_page():
    p = Person("tables-contract", "female", 1975, 162, "dyslipidemia_statin", "test",
               weight_anchors=((date(2024, 1, 1), 60), (date(2026, 1, 1), 60)))
    enc = person.encounter_for(p, DAY, "followup", ["lipid", "cbc", "liver"], random.Random(7))
    groups = document.split_encounter(random.Random(1), enc)[0]

    def slip():
        return document.build_doc(random.Random(3), "tables-test", p, enc, groups, _complete_family(), {})

    injected = 0
    for s in range(40):
        drawn, kept = slip(), slip()
        a, b = random.Random(s), random.Random(s)
        hazards.inject(drawn, a)
        clinical_tables.skip_incidents(kept, b)
        assert a.random() == b.random()
        assert kept == slip() and not kept.hazards
        injected += bool(drawn.hazards)
    assert injected


def test_traditional_variants_are_dropped_from_simplified_pages_only():
    pool = ["身长", "身長", "收缩压", "收縮壓"]
    assert clinical_tables.same_script(pool, "zh-Hans") == ["身长", "收缩压"]
    assert clinical_tables.same_script(pool, "zh-Hant") == pool


def test_no_simplified_page_draws_a_traditional_name(clinical_on):
    for key, item in spec.indicators().items():
        for s in range(8):
            name = document._variant_for(item, "zh-Hans", random.Random(f"{key}:{s}"))
            assert name is None or not clinical_tables.traditional(name), (key, name)
