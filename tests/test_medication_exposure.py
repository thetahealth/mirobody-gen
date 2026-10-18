"""One drug's exposure drives its physiology and its text from one shared state.

Atorvastatin: the course (dose steps with adherence, an optional stop) is the single state that the
LDL/TG values, medications.jsonl and the clinic note read. The per-dose response is the LIPITOR label's
Table 8 (sourced); the onset shape, washout, adherence scaling and population transfer are declared
assumptions. These tests check the program's response to the state, not the drug's real efficacy.
"""

import json
import random
from datetime import date, timedelta

import pytest

from mirobody_gen import book, layout, manifest, medications, person, physiology, spec
from mirobody_gen.model import DoseStep, MedicationCourse

STATIN = "开始他汀治疗"
TABLE = spec.cohort()["medication_response"]["atorvastatin"]


def subject():
    p = next(x for x in person.build_cohort(7) if x.person_id == "p029")
    return p, next(e for e in p.events if e.name == STATIN)


def ldl_ratio(p, untreated_ref, day):
    return physiology.expected(p, "ldl", day) / physiology.expected(untreated_ref, "ldl", day)


def untreated(p):
    import dataclasses
    return dataclasses.replace(p, events=tuple(e for e in p.events if e.name != STATIN))


@pytest.mark.parametrize("dose", [10, 20, 40, 80])
def test_steady_state_follows_the_sourced_dose_table(dose):
    p, statin = subject()
    q = person.with_course(p, STATIN, [DoseStep(statin.start, dose)])
    day = statin.start + timedelta(days=120)
    assert ldl_ratio(q, untreated(p), day) == pytest.approx(1 + TABLE["doses_mg"][str(dose)]["ldl"])
    assert physiology.expected(q, "tg", day) / physiology.expected(untreated(p), "tg", day) == \
        pytest.approx(1 + TABLE["doses_mg"][str(dose)]["tg"])


def test_unsourced_dose_is_rejected():
    p, statin = subject()
    q = person.with_course(p, STATIN, [DoseStep(statin.start, 30)])
    with pytest.raises(ValueError, match="no sourced response for atorvastatin 30 mg"):
        physiology.expected(q, "ldl", statin.start + timedelta(days=60))


def test_onset_titration_adherence_and_stop_on_one_person():
    p, statin = subject()
    s = statin.start
    up, half, stop = s + timedelta(days=90), s + timedelta(days=180), s + timedelta(days=270)
    q = person.with_course(p, STATIN, [DoseStep(s, 20), DoseStep(up, 40), DoseStep(half, 40, 0.5)], stop)
    base = untreated(p)
    r = {d: ldl_ratio(q, base, s + timedelta(days=d)) for d in (-1, 14, 28, 89, 104, 118, 179, 208, 269, 284, 298, 400)}
    e20, e40 = TABLE["doses_mg"]["20"]["ldl"], TABLE["doses_mg"]["40"]["ldl"]
    assert r[-1] == pytest.approx(1)                                     # before the start
    assert r[14] == pytest.approx(1 + e20 / 2)                           # half-way through onset
    assert r[28] == pytest.approx(1 + e20) == pytest.approx(r[89])       # 20 mg steady state
    assert r[104] == pytest.approx(1 + e20 + (e40 - e20) / 2)            # ramping to 40 mg
    assert r[118] == pytest.approx(1 + e40) == pytest.approx(r[179])     # 40 mg steady state
    assert r[208] == pytest.approx(1 + e40 * 0.5)                        # half the doses taken
    assert r[284] == pytest.approx(1 + e40 * 0.5 / 2)                    # half-way through the washout
    assert r[298] == pytest.approx(1) == pytest.approx(r[400])           # back to untreated


def test_plan_note_and_people_record_read_the_same_course():
    p, statin = subject()
    s = statin.start
    up, half, stop = s + timedelta(days=90), s + timedelta(days=180), s + timedelta(days=270)
    q = person.with_course(p, STATIN, [DoseStep(s, 20), DoseStep(up, 40), DoseStep(half, 40, 0.5)], stop)

    plan = next(x for x in medications.plans_for(q, 7) if x["generic"] == "atorvastatin")
    assert (plan["start"], plan["strength"], plan["end"]) == (s.isoformat(), "20 mg", (stop - timedelta(days=1)).isoformat())
    assert [(c["date"], c["strength"]) for c in plan["changes"]] == [(up.isoformat(), "40 mg")]  # adherence is not a prescription

    record = manifest.person_record(q) if hasattr(manifest, "person_record") else None
    if record is None:
        import pathlib
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            manifest.write(pathlib.Path(tmp), [q], {q.person_id: person.encounters_for(q, 7)})
            record = json.loads((pathlib.Path(tmp) / "people.jsonl").read_text(encoding="utf-8").splitlines()[0])
    event = next(e for e in record["events"] if e["name"] == STATIN)
    assert [(x["dose_mg"], x["adherence"]) for x in event["course"]["steps"]] == [(20, 1.0), (40, 1.0), (40, 0.5)]
    assert event["course"]["stop"] == stop.isoformat() and "ldl" in event["affected_indicators"]
    assert event["course"]["steps"][2]["steady_state"]["ldl"] == pytest.approx(TABLE["doses_mg"]["40"]["ldl"] * 0.5)

    registry = layout.build_registry(7)
    family = registry.family(registry.pools[("zh", "hospital")][0])

    def note(day):
        e = person.encounter_for(q, day, "clinic", ["vitals_clinic", "lipid"], random.Random(7))
        doc = book.build_outpatient(random.Random(7), "n", q, e, family)
        return "\n".join(v for block in doc.blocks for _, v in block.rows if v)

    assert "阿托伐他汀 20mg 每日1次" in note(s + timedelta(days=60))
    assert "阿托伐他汀 40mg 每日1次" in note(up + timedelta(days=30))
    late = note(half + timedelta(days=30))
    assert "40mg" in late and "自述约50%的剂量按时服用" in late
    assert f"已于{stop.isoformat()}停用" in note(stop + timedelta(days=30))


def test_visits_after_a_dose_change_name_the_treatment():
    p, statin = subject()
    s = statin.start
    up = s + timedelta(days=200)
    q = person.with_course(p, STATIN, [DoseStep(s, 20), DoseStep(up, 80)])
    encounters = person.encounters_for(q, 7)
    first_after = next(e for e in encounters if e.exam_date > up)
    assert STATIN in first_after.events_since_previous
    steady = [e for e in encounters if s + timedelta(days=60) < e.exam_date < up]
    assert steady and all(STATIN not in e.events_since_previous for e in steady[1:])


def test_default_course_is_the_unchanged_prescription():
    p, statin = subject()
    assert statin.course == MedicationCourse("atorvastatin", (DoseStep(statin.start, 20),))
    plan = next(x for x in medications.plans_for(p, 7) if x["generic"] == "atorvastatin")
    assert (plan["strength"], plan["changes"], plan["end"]) == ("20 mg", [], None)


@pytest.mark.parametrize("steps,stop", [
    ([DoseStep(date(2022, 6, 19), 20), DoseStep(date(2022, 6, 19), 40)], None),
    ([DoseStep(date(2022, 6, 19), 20, 1.5)], None),
    ([DoseStep(date(2022, 6, 19), 20)], date(2022, 6, 19)),
])
def test_invalid_courses_are_rejected(steps, stop):
    with pytest.raises(ValueError):
        MedicationCourse("atorvastatin", tuple(steps), stop)
