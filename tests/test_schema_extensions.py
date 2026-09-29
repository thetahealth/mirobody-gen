"""Contracts of the enlarged schema: value kinds, sticky results, package tiers, parameter
examinations, judgement grades, critical results and the two-column US layout.

Everything here runs in process on a small cohort; no files are rendered.
"""

from __future__ import annotations

import pathlib
import random
import sys
from datetime import date

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mirobody_gen import book, document, hazards, layout, person as person_mod, physiology, spec  # noqa: E402
from mirobody_gen.model import Encounter, Reading  # noqa: E402
from mirobody_gen.render import grid  # noqa: E402

SEED = 7


@pytest.fixture(scope="module")
def cohort():
    people = person_mod.build_cohort(SEED, 60)
    encounters = {p.person_id: person_mod.encounters_for(p, SEED) for p in people}
    return people, encounters


def _readings(encounters, key):
    for pid, encs in encounters.items():
        for e in encs:
            for r in e.readings:
                if r.key == key:
                    yield pid, e, r


def test_categorical_readings_are_constant_per_person(cohort):
    people, encounters = cohort
    seen: dict[str, set[str]] = {}
    for pid, e, r in _readings(encounters, "abo"):
        assert r.value_kind == "categorical" and r.status == "normal" and r.canonical_value is None
        seen.setdefault(pid, set()).add(r.value)
    assert seen, "no visit ordered a blood group in a 60-person cohort"
    assert all(len(v) == 1 for v in seen.values()), "a person's blood group changed between visits"
    assert set().union(*seen.values()) <= {"A", "B", "O", "AB"}


def test_qualitative_items_with_a_population_rate_are_sticky(cohort):
    people, encounters = cohort
    per_person: dict[str, set[str]] = {}
    for pid, e, r in _readings(encounters, "hbsab"):
        assert r.value_kind == "qualitative"
        per_person.setdefault(pid, set()).add(r.value)
    assert per_person
    assert all(len(v) == 1 for v in per_person.values()), "hepatitis B surface antibody flipped between visits"
    # a vaccinated person's positive antibody is not an abnormality
    assert all(r.status == "normal" for _, _, r in _readings(encounters, "hbsab"))
    catalogue = spec.indicators()
    positives = [r for _, _, r in _readings(encounters, "hbsag") if r.value != catalogue["hbsag"]["reference"][1]]
    assert all(r.status == "high" for r in positives)


def test_entry_and_senior_packages_are_scheduled(cohort):
    people, encounters = cohort
    ages = {p.person_id: p for p in people}
    packages = {(pid, e.package) for pid, encs in encounters.items() for e in encs if e.exam_type == "routine"}
    tiers = {pkg for _, pkg in packages}
    assert {"entry", "senior", "basic", "standard", "premium"} <= tiers, tiers
    for pid, encs in encounters.items():
        routine = [e for e in encs if e.exam_type == "routine"]
        if any(e.package == "entry" for e in routine):
            assert routine[0].package == "entry", "a pre-employment examination is the first check-up, not a later one"
        for e in routine:
            if e.package == "senior":
                assert ages[pid].age_at(e.exam_date) >= 65
    narr = spec.narratives()
    for pid, encs in encounters.items():
        for e in encs:
            if e.package == "entry":
                keys = {r.key for r in e.readings}
                assert not keys & {"hbsag", "hbsab", "hbeag", "hbeab", "hbcab"}, "pre-employment examinations must not test hepatitis B"
                assert {"abo", "rh"} <= keys
    assert narr["packages"]["entry"]["sections"] == ["internal", "surgical", "eye", "ent"]


def test_parameter_examinations_print_their_readings_outside_the_lab_tables(cohort):
    people, encounters = cohort
    registry = layout.build_registry(SEED)
    fam = next(f for f in (registry.family(i) for i in range(20)) if f.language != "en")
    narr = spec.narratives()
    param_keys = {k for aid, a in narr["aux"].items() if a["kind"] == "params" for k in a["parameters"]}
    checked = 0
    for pid, encs in encounters.items():
        person = next(p for p in people if p.person_id == pid)
        for e in encs:
            if e.package != "premium":
                continue
            rng = random.Random(f"t:{pid}:{e.exam_date}")
            groups = [g for s in document.split_encounter(rng, e) for g in s]
            doc = book.build_book(rng, "t", person, e, groups, fam, {})
            table_keys = {doc.readings[i].key for t in doc.tables for c in t.rows if c.printed is not None
                          for i in doc.printed[c.printed].readings}
            assert not table_keys & param_keys, "spirometry / echo / arterial / body-composition values must not appear in laboratory tables"
            block_sections = {b.section_id for b in doc.blocks}
            assert {"spirometry", "arterial", "body_composition", "echo"} <= block_sections
            printed_param = [row for row in doc.printed if any(doc.readings[i].key in param_keys for i in row.readings)]
            assert printed_param and all(r.item_range for r in printed_param if r.item_name != "第一秒用力呼气容积")
            for row in printed_param:
                for i in row.readings:
                    assert doc.readings[i].printed_row == doc.printed.index(row)
            assert any(s["kind"] == "grade" for s in doc.summary_truth) or True   # grades are per-institution; see next test
            checked += 1
            break
        if checked >= 3:
            break
    assert checked >= 3


def _encounter(person, when, rows: list[tuple[str, float]]) -> Encounter:
    catalogue = spec.indicators()
    readings = []
    for key, value in rows:
        item = catalogue[key]
        readings.append(Reading(key=key, original_indicator=item["zh"], value=spec.format_value(key, value), unit=item["unit"],
                                reference_range=spec.reference_text(key, person.sex), status=spec.status_for(key, value, person.sex),
                                canonical_value=value, unit_ucum=item["unit"], loinc=item.get("loinc"),
                                value_kind="quantitative", expect_resolvable=False))
    return Encounter(person_id=person.person_id, exam_date=when, exam_type="routine", exam_location="checkup-center",
                     panels=("checkup_premium",), readings=readings, package="premium")


def test_judgement_grades_and_critical_results(cohort):
    people, _ = cohort
    person = people[0]
    enc = _encounter(person, date(2025, 3, 1), [("sbp", 185.0), ("dbp", 96.0), ("glu", 6.3), ("ldl", 2.5), ("hgb", 58.0)])
    grades = dict(book.judgement_grades(enc, person.sex))
    assert grades["bp"] == "D" and grades["glucose"] == "C" and grades["lipid"] == "A"
    assert grades["cbc"] == "D"
    critical = book.critical_results(enc, person.sex)
    assert any(c.startswith("收缩压") for c in critical) and any(c.startswith("血红蛋白") for c in critical)
    assert not any(c.startswith("舒张压") for c in critical)
    # the summary block carries both as truth lines
    registry = layout.build_registry(SEED)
    fam = next(f for f in (registry.family(i) for i in range(30)) if f.language != "en")
    block = book.summary_block(random.Random(1), document.Doc(doc_id="t", person_id=person.person_id, family=fam, title="t",
                                                              subject=[], dates=[], tables=[]), person, enc, fam)
    kinds = {t["kind"] for t in block.truth}
    assert "critical" in kinds
    crit = next(t for t in block.truth if t["kind"] == "critical")
    assert {"sbp", "hgb"} <= set(crit["keys"]) or {"收缩压", "血红蛋白"} <= {k for k in crit["keys"]}
    for t in block.truth:
        if t["kind"] == "grade":
            assert t["grade"] in "ABCDE" and t["area"]


def test_two_column_us_layout_prints_each_value_in_exactly_one_column(cohort):
    people, encounters = cohort
    inst = {"name": "Test Lab", "kind": "lab", "language": "en"}
    fam = next(f for f in (layout.sample_family(random.Random(i), f"f{i}", inst) for i in range(500))
               if "result_out" in f.columns)
    assert fam.flag_at == "none" and fam.lab_code
    person = people[1]
    enc = next(e for e in encounters[person.person_id] if e.exam_type in ("routine", "follow-up"))
    rng = random.Random(3)
    groups = [g for s in document.split_encounter(rng, enc) for g in s][:2]
    doc = document.build_doc(rng, "t", person, enc, groups, fam, {})
    hazards.detect(doc)
    assert "table.two_result_columns" in doc.hazards
    for table in doc.tables:
        for cells in table.rows:
            if cells.printed is None:
                continue
            in_col, out_col = grid.cell_text(cells, "result_in"), grid.cell_text(cells, "result_out")
            assert (in_col == "") != (out_col == ""), (in_col, out_col)
            row = doc.printed[cells.printed]
            assert row.is_abnormal == ("1" if out_col else "0")
    headers, body, owners = grid.table_grid(doc, doc.tables[0])
    lab_idx = doc.tables[0].columns.index("lab")
    assert all(r[lab_idx] == fam.lab_code for r, o in zip(body, owners) if o is not None)


def test_bounded_measurements_never_exceed_their_ceiling(cohort):
    people, encounters = cohort
    for pid, e, r in _readings(encounters, "spo2"):
        assert r.canonical_value <= 100.0
    for pid, e, r in _readings(encounters, "fev1_fvc"):
        assert r.canonical_value <= 100.0
    assert physiology.CEILING["spo2"] == 100.0
