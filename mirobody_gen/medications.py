"""Medication plans: `medications.jsonl`, one line per person who has any.

The corpus otherwise has no medication records at all -- a chronic condition only ever shows up as
an *event* on the timeline ("开始他汀治疗", "开始二甲双胍", ...), which is enough to explain a value
jump but leaves nothing for an agent's medication tool to find. This module turns each archetype's
scripted intervention event into a first-line drug and starting dose (`resources/cohort.json`'s
`medication_plans`, hand-authored in `scripts/build_cohort.py`), plus an optional short prn course
during a random infection incident.

A plan's `start` is always its triggering event's own start date, and its drug name matches the one
`scripts/build_profile.py`'s clinic-note narrative already says the person is "currently taking" --
two sources naming two different drugs for the same archetype is exactly the kind of inconsistency
this generator exists to catch, not reproduce.
"""

from __future__ import annotations

import json
import pathlib
import random
from datetime import timedelta

from . import spec
from .model import Person

_PLANS: dict[str, dict] = spec.cohort()["medication_plans"]
#: Script event name -> (lo, hi) days after start a metformin uptitration may land, chosen so there
#: is a follow-up visit on both sides (the archetype's own follow-up interval is 6 months).
_METFORMIN_UPTITRATE_DAYS = (120, 270)
#: Levothyroxine titration happens every 6-8 weeks per TSH, absent cohort-specific titration dates.
_LEVO_STEP_DAYS = (42, 56)
_IBUPROFEN_COURSE_DAYS = (3, 5)
#: Chance a person already on a scripted medication also gets a short prn course for a cold.
_IBUPROFEN_RATE = 0.35


def plans_for(person: Person, seed: int) -> list[dict]:
    """This person's medication plans, or `[]`. Deterministic from `seed` and `person_id`."""
    rng = random.Random(f"meds:{seed}:{person.person_id}")
    scripted = [e for e in person.events if e.note == "原型剧本" and e.name in _PLANS]
    plans: list[dict] = []
    for e in scripted:
        drug = _PLANS[e.name]
        plan = {
            "plan_id": f"{person.person_id}-m{len(plans) + 1}",
            "generic": drug["generic"], "name_zh": drug["name_zh"], "name_en": drug["name_en"],
            "strength": drug["strength"], "route": drug["route"], "frequency": drug["frequency"],
            "dose_text_zh": drug["dose_text_zh"], "dose_text_en": drug["dose_text_en"],
            # Scripted treatment remains active throughout follow-up. A stop date needs a
            # shared stop event; inventing one here contradicts later clinic narratives.
            "start": e.start.isoformat(), "end": None, "reason_event": e.name, "changes": [],
        }
        if e.course is not None:
            # The prescription follows the same exposure course the lab values read: strength from
            # the first step, a change per later dose step, the last day taken before a stop.
            # Adherence is the person's behaviour, not part of a prescription.
            plan.update(_course_plan(drug, e.course))
        elif e.name == "开始二甲双胍" and rng.random() < 0.5:
            plan["changes"].append(_metformin_uptitration(rng, e.start))
        elif e.name == "左甲状腺素滴定":
            plan["changes"] = _levothyroxine_titration(rng, e.start)
        plans.append(plan)

    # An optional short prn course on top of existing treatment, not a reason by itself to create a
    # medications record: a "healthy" archetype person who happens to catch a cold still has no
    # treatment events and so gets no medications.jsonl line at all.
    if scripted:
        infection = _PLANS["急性上呼吸道感染"]
        for e in person.events:
            if e.note == "随机事件" and e.name == "急性上呼吸道感染" and rng.random() < _IBUPROFEN_RATE:
                days = rng.randint(*_IBUPROFEN_COURSE_DAYS)
                plans.append({
                    "plan_id": f"{person.person_id}-m{len(plans) + 1}",
                    "generic": infection["generic"], "name_zh": infection["name_zh"],
                    "name_en": infection["name_en"], "strength": infection["strength"],
                    "route": infection["route"], "frequency": infection["frequency"],
                    "dose_text_zh": infection["dose_text_zh"], "dose_text_en": infection["dose_text_en"],
                    "start": e.start.isoformat(), "end": (e.start + timedelta(days=days - 1)).isoformat(),
                    "reason_event": e.name, "changes": [],
                })
    return plans


def _course_plan(drug: dict, course) -> dict:
    def dose_fields(step) -> dict:
        dose = f"{step.dose_mg:g}"
        return {"strength": f"{dose} mg", "dose_text_zh": drug["dose_text_template_zh"].format(dose=dose),
                "dose_text_en": drug["dose_text_template_en"].format(dose=dose)}

    first = dose_fields(course.steps[0])
    changes, current = [], first["strength"]
    for step in course.steps[1:]:
        fields = dose_fields(step)
        if fields["strength"] != current:  # an adherence-only step changes no prescription
            changes.append({"date": step.start.isoformat(), **fields})
            current = fields["strength"]
    end = (course.stop - timedelta(days=1)).isoformat() if course.stop is not None else None
    return {**first, "changes": changes, "end": end}


def _metformin_uptitration(rng: random.Random, start) -> dict:
    """In some people, metformin steps up to 500mg tid or 850mg bid a few months in."""
    at = start + timedelta(days=rng.randint(*_METFORMIN_UPTITRATE_DAYS))
    if rng.random() < 0.5:
        return {"date": at.isoformat(), "strength": "500 mg", "frequency": "tid",
                "dose_text_zh": "每日3次，每次500mg，随餐服用",
                "dose_text_en": "500 mg three times daily with meals"}
    return {"date": at.isoformat(), "strength": "850 mg",
            "dose_text_zh": "每日2次，每次850mg，随餐服用",
            "dose_text_en": "850 mg twice daily with meals"}


def _levothyroxine_titration(rng: random.Random, start) -> list[dict]:
    """25 -> 50 -> 75 ug qd, each step 6-8 weeks after the last."""
    first = start + timedelta(days=rng.randint(*_LEVO_STEP_DAYS))
    second = first + timedelta(days=rng.randint(*_LEVO_STEP_DAYS))
    return [
        {"date": first.isoformat(), "strength": "50 ug",
         "dose_text_zh": "每日1次，每次50微克，晨起空腹服用",
         "dose_text_en": "50 mcg once daily on an empty stomach"},
        {"date": second.isoformat(), "strength": "75 ug",
         "dose_text_zh": "每日1次，每次75微克，晨起空腹服用",
         "dose_text_en": "75 mcg once daily on an empty stomach"},
    ]


def write_all(out_dir: pathlib.Path, people: list[Person], seed: int) -> int:
    n = 0
    with (out_dir / "medications.jsonl").open("w", encoding="utf-8") as fh:
        for person in people:
            plans = plans_for(person, seed)
            if not plans:
                continue
            fh.write(json.dumps({"person_id": person.person_id, "synthetic": True, "plans": plans},
                                ensure_ascii=False) + "\n")
            n += 1
    return n
