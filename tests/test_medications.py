"""Tests for medications.jsonl: a new channel, no prior behaviour to regress against.

Checks the contract an external loader will be written against: the schema, that `start` always
equals the triggering event's own start date, that a person without a treatment event gets no line
at all, and that output is deterministic.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mirobody_gen import medications, person as person_mod  # noqa: E402

SEED = 20261014
PEOPLE = 60
FREQUENCIES = {"qd", "bid", "tid", "qn", "prn"}


def _cohort():
    return person_mod.build_cohort(SEED, PEOPLE)


def test_schema_of_every_plan():
    required = {"plan_id", "generic", "name_zh", "name_en", "strength", "route", "frequency",
                "dose_text_zh", "dose_text_en", "start", "end", "reason_event", "changes"}
    seen_any = False
    for p in _cohort():
        for i, plan in enumerate(medications.plans_for(p, SEED), start=1):
            seen_any = True
            assert set(plan) == required, plan
            assert plan["plan_id"] == f"{p.person_id}-m{i}"
            assert plan["frequency"] in FREQUENCIES, plan
            assert plan["end"] is None or plan["end"] >= plan["start"]
            for change in plan["changes"]:
                assert {"date", "strength", "dose_text_zh", "dose_text_en"} <= set(change)
                assert change["date"] >= plan["start"]
    assert seen_any, "no plan generated in a 60-person cohort"


def test_start_dates_equal_their_triggering_events_start():
    # A person can have more than one event of the same name (two colds), so a plan's start must
    # match *some* event of that name, not be collapsed into a single name -> date mapping.
    for p in _cohort():
        starts_by_name: dict[str, set[str]] = {}
        for e in p.events:
            starts_by_name.setdefault(e.name, set()).add(e.start.isoformat())
        for plan in medications.plans_for(p, SEED):
            assert plan["start"] in starts_by_name[plan["reason_event"]], (p.person_id, plan)


def test_no_plan_for_a_person_with_no_treatment_event():
    for p in _cohort():
        scripted_drugs = [e for e in p.events if e.note == "原型剧本" and e.name in medications._PLANS]
        plans = medications.plans_for(p, SEED)
        if not scripted_drugs:
            assert plans == [], (p.person_id, plans)
        else:
            assert plans, (p.person_id, "has a scripted drug event but no plan")


def test_lifestyle_intervention_is_not_a_medication():
    """fatty_liver's archetype script is a lifestyle intervention, not a drug: it must not surface
    as a medication plan even though it is a scripted, note == '原型剧本' event."""
    fatty_liver = [p for p in _cohort() if p.archetype == "fatty_liver"]
    assert fatty_liver
    for p in fatty_liver:
        names = {pl["reason_event"] for pl in medications.plans_for(p, SEED)}
        assert "生活方式干预（减重 + 戒酒）" not in names


def test_iron_has_no_independent_stop_date_without_a_stop_event():
    """Clinic notes and physiology still model ongoing iron therapy; the plan must agree.
    Previously p040's plan ended 2026-01-25 but its 2026-03-25 clinic note still said taking iron.
    """
    checked = 0
    for p in _cohort():
        for event in p.events:
            if event.name != "开始补铁治疗":
                continue
            assert event.duration_days >= (person_mod.CORPUS_END - event.start).days
            plan = next(plan for plan in medications.plans_for(p, SEED) if plan["reason_event"] == event.name)
            assert plan["end"] is None, (p.person_id, plan)
            checked += 1
    assert checked > 0


def test_deterministic_from_seed():
    people = _cohort()
    for p in people[:10]:
        assert medications.plans_for(p, SEED) == medications.plans_for(p, SEED)


def test_write_all_matches_plans_for_and_skips_empty_people():
    people = _cohort()
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp)
        n = medications.write_all(out, people, SEED)
        lines = [json.loads(line) for line in (out / "medications.jsonl").read_text(encoding="utf-8").splitlines()]
    assert n == len(lines)
    by_id = {rec["person_id"]: rec for rec in lines}
    for p in people:
        plans = medications.plans_for(p, SEED)
        if not plans:
            assert p.person_id not in by_id
        else:
            assert by_id[p.person_id]["plans"] == plans
            assert by_id[p.person_id]["synthetic"] is True


def test_future_treatment_never_changes_past_plans():
    """Deleting a treatment that starts after the cut-off leaves every earlier plan, and its id, as it
    was. Before, a cold's prn course depended on whether the person would ever be treated, and plans
    were numbered with the scripted ones first (seed 7, p020: deleting the 2025-07-26 metformin start
    removed or renumbered an earlier ibuprofen course)."""
    import dataclasses
    from datetime import timedelta

    checked = 0
    for p in person_mod.build_cohort(SEED):
        for e in [e for e in p.events if e.note == "原型剧本" and e.name in medications._PLANS]:
            cutoff = e.start - timedelta(days=1)
            trimmed = dataclasses.replace(p, events=tuple(x for x in p.events if x.start <= cutoff))
            before = [plan for plan in medications.plans_for(p, SEED) if plan["start"] <= cutoff.isoformat()]
            after = [plan for plan in medications.plans_for(trimmed, SEED) if plan["start"] <= cutoff.isoformat()]
            assert before == after, p.person_id
            checked += 1
    assert checked >= 20


def test_a_prn_course_needs_treatment_in_progress_on_the_day():
    for p in person_mod.build_cohort(SEED):
        scripted = [e for e in p.events if e.note == "原型剧本" and e.name in medications._PLANS]
        for plan in medications.plans_for(p, SEED):
            if plan["generic"] == "ibuprofen":
                assert any(s.start.isoformat() <= plan["start"] for s in scripted), (p.person_id, plan)
