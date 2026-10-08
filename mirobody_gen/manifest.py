"""Write the truth manifest (manifest.jsonl, people.jsonl) in the contract the clinical audit checks.

The contract is fixed by `audit/clinical.py`; this module only fills it in.

One record = one encounter. After rendering to files (`corpus.py`), an encounter splits into one or
several files, each with its own record, layout fingerprint and injected hazards — **the record's
shape never changes**, only `file` turns from a placeholder into a real path and `rows` narrows to
the subset actually printed on that file.

Fields fall into two groups:
* named the same as mirobody's extraction contract (`original_indicator / value / unit /
  reference_range / status / detection_method`) — compared field by field when scoring;
* scoring-only, never printed (`key / canonical_value / loinc / ref_low / ref_high /
  expect_resolvable / readable`).

`readable` gates the recall denominator: when an injected hazard makes a row unreadable, it drops
out of the denominator and into the "must abstain" set. **Scoring a row that cannot be read measures
a tendency to hallucinate, not extraction ability.**
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import asdict

from . import spec
from .model import Encounter, Person


def _conditions_of(person: Person, encounters: list[Encounter] | None = None) -> list[dict]:
    """This person's diagnosis set = the archetype's own + **anything inferred from the values**.

    The second part is a feedback loop: three consecutive blood-pressure readings >=140/90 make
    someone hypertensive on paper, whatever archetype the cohort originally assigned them. Without
    this loop, a generated record could show values that meet a diagnostic threshold with no
    matching diagnosis — which a clinical audit would (correctly) flag as a missed diagnosis.

    Measured on the public ESL-Bench dataset: of 20 users, values met a diagnostic threshold for 7
    consecutive readings with no matching entry in that person's chronic-disease list in 2 cases
    (both hypertension). Their pipeline is top-down (profile -> events -> values), so values
    drifting into a diagnostic range never flows back into the profile. This function adds that
    feedback.

    "Undiagnosed hypertension" certainly exists in the real world, so this is not claiming that
    pattern is wrong in general. But a benchmark meant to test attribution needs this written into
    the truth, not left to be inferred.
    """
    base = list(spec.cohort().get("archetype_conditions", {}).get(person.archetype, []))
    if not encounters:
        return base
    have = {c["code"] for c in base}
    for rule in spec.cohort().get("diagnostic_criteria", []):
        code = rule["condition"]["code"]
        if code in have:
            continue
        run = 0
        for encounter in sorted(encounters, key=lambda e: e.exam_date):
            values = {r.key: r.canonical_value for r in encounter.readings
                      if r.canonical_value is not None}
            met = any(_meets(values.get(c["key"]), c["op"], c["value"]) for c in rule["any_of"])
            run = run + 1 if met else 0
            if run >= rule["persistence"]:
                base.append({**rule["condition"], "onset_date": encounter.exam_date.isoformat(),
                             "source": "由数值推出（" + rule["source"].split("：")[0] + "）"})
                have.add(code)
                break
    return base


def _meets(value, op: str, threshold: float) -> bool:
    if value is None:
        return False
    return {">=": value >= threshold, ">": value > threshold,
            "<=": value <= threshold, "<": value < threshold}.get(op, False)


def encounter_record(person: Person, encounter: Encounter, file: str | None = None,
                     index: int = 0, conditions: list[dict] | None = None) -> dict:
    # The placeholder id uses the encounter index, not the concatenated order names: normalising
    # `vitals+inflammation` gives `vitalsinflammation`, which happens to fall inside a 12-character
    # window of the reference corpus and would make the privacy gate report a verbatim copy that
    # isn't there. The file layer (`corpus.record`) replaces this with a real file path.
    return {
        "file": file or f"{person.person_id}/{encounter.exam_date}/e{index:02d}",
        "synthetic": True,
        "person_id": person.person_id,
        "person": {
            "sex": person.sex,
            "age": person.age_at(encounter.exam_date),
            "birth_year": person.birth_year,
            "height_cm": person.height_cm,
            "archetype": person.archetype,
            "region": person.region,
            # Diagnosis set. Currently looked up from the archetype; once PySynthea is wired in,
            # this would read directly from its Condition resource — this line is that seam.
            "conditions": conditions if conditions is not None else _conditions_of(person),
        },
        "collected": encounter.exam_date.isoformat(),
        "exam_type": encounter.exam_type,
        "exam_location": encounter.exam_location,
        "panels": list(encounter.panels),
        "package": encounter.package,
        "events_since_previous": list(encounter.events_since_previous),
        # Chief complaints (expected symptom-axis code) and named findings (expected diagnosis-axis code).
        "complaints": [{"text": c.text, "symptom_id": c.symptom_id, "icpc3": c.icpc3, "expect": c.expect,
                        "duration": c.duration} for c in encounter.complaints],
        "findings": [{"id": x.id, "where": x.where, "item": x.item, "surface": x.surface, "icpc3": x.icpc3,
                      "severity": x.severity, "since": x.since.isoformat() if x.since else None}
                     for x in encounter.findings],
        # Filled in by the file layer: layout fingerprint, injected hazards, file format and difficulty tier.
        "layout_fingerprint": None,
        "hazards": [],
        "tier": None,
        "format": None,
        "language": None,
        "rows": [asdict(r) for r in encounter.readings],
    }


def person_record(person: Person, conditions: list[dict] | None = None) -> dict:
    return {
        "person_id": person.person_id,
        "sex": person.sex,
        "birth_year": person.birth_year,
        "height_cm": person.height_cm,
        "archetype": person.archetype,
        "region": person.region,
        "conditions": conditions if conditions is not None else _conditions_of(person),
        "events": [
            {
                "name": e.name,
                "event_type": e.event_type,
                "start_date": e.start.isoformat(),
                "duration_days": e.duration_days,
                "health_effect": e.health_effect,
                "impact_level": e.impact_level,
                # The decidable answer to an attribution question lives here: which event, which
                # indicators, how large an effect, and how long it takes to kick in.
                "affected_indicators": sorted(e.effects),
                "effects": {k: {"magnitude": m, "onset_days": o, "half_life_days": d}
                            for k, (m, o, d) in e.effects.items()},
            }
            for e in person.events
        ],
    }


def write(out_dir: pathlib.Path, people: list[Person],
          encounters: dict[str, list[Encounter]]) -> tuple[int, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    by_person = {p.person_id: p for p in people}

    # Computed once per person (needs the full timeline), then handed to every one of their records.
    conditions = {pid: _conditions_of(by_person[pid], items)
                  for pid, items in encounters.items()}

    rows = 0
    with (out_dir / "manifest.jsonl").open("w", encoding="utf-8") as fh:
        for person_id, items in encounters.items():
            for index, encounter in enumerate(items, start=1):
                record = encounter_record(by_person[person_id], encounter, index=index,
                                          conditions=conditions[person_id])
                rows += len(record["rows"])
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    with (out_dir / "people.jsonl").open("w", encoding="utf-8") as fh:
        for person in people:
            fh.write(json.dumps(person_record(person, conditions.get(person.person_id)),
                                ensure_ascii=False) + "\n")

    return sum(len(v) for v in encounters.values()), rows
