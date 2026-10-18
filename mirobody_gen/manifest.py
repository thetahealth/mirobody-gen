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
import copy
import math
import pathlib
from collections import defaultdict
from dataclasses import asdict
from datetime import date

from . import spec
from .model import Encounter, Person


DIAGNOSTIC_POLICY = "observed-diagnosis-time-v1"
CONDITION_KNOWLEDGE_POLICY = "dated-diagnosis-knowledge-v1"


def _known_on(condition: dict, as_of: date | None) -> bool:
    """A source and recorded date establish when a condition became known, not onset."""
    if not isinstance(condition, dict):
        raise ValueError("condition must be a source record")
    days = {}
    for key in ("recorded_date", "onset_date"):
        text = condition.get(key)
        if text is not None:
            if not isinstance(text, str) or len(text) != 10:
                raise ValueError(f"{key} must be a calendar date or null")
            days[key] = date.fromisoformat(text)
    source = condition.get("source")
    if "recorded_date" not in days or not isinstance(source, str) or not source.strip():
        return False
    return as_of is None or all(day <= as_of for day in days.values())


def _predicate(readings: list, criterion: dict) -> bool | None:
    candidates = [r for r in readings if r.key == criterion["key"]]
    if not candidates:
        return None
    signatures = {(r.canonical_value, r.unit_ucum, r.ref_low, r.ref_high) for r in candidates}
    if len(signatures) != 1:
        return None
    row = candidates[0]
    value = row.canonical_value
    threshold = getattr(row, criterion["reference"], None) if "reference" in criterion else criterion["value"]
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in (value, threshold)):
        return None
    if criterion.get("unit") and row.unit_ucum != criterion["unit"]:
        return None
    return _meets(value, criterion["op"], threshold)


def _sample_result(encounter: Encounter, rule: dict) -> bool | None:
    primary = [_predicate(encounter.readings, c) for c in rule["any_of"]]
    any_met = True if True in primary else None if None in primary else False
    required = [_predicate(encounter.readings, c) for c in rule.get("all_of", [])]
    if any_met is False or False in required:
        return False
    return True if any_met is True and all(v is True for v in required) else None


def diagnostic_assessments(encounters: list[Encounter], as_of: date | None = None) -> list[dict]:
    """Evidence checks only; absent tests are unknown, not negative or implied diagnoses.

    Requirements in one sample are never joined across different encounters.
    Same-day repeats count once. A conflicting day resets the run conservatively;
    an unmeasured day does not masquerade as a normal measurement.
    """
    assessments = []
    for rule in spec.cohort().get("diagnostic_criteria", []):
        days = defaultdict(list)
        for encounter in encounters:
            if as_of is None or encounter.exam_date <= as_of:
                days[encounter.exam_date].append(_sample_result(encounter, rule))
        positives, unknown, conflicts = [], [], []
        confirmed = None
        for day, states in sorted(days.items()):
            assessed = {s for s in states if s is not None}
            if assessed == {True, False}:
                positives = []
                conflicts.append(day.isoformat())
            elif assessed == {False}:
                positives = []
            elif assessed == {True}:
                positives.append(day)
                enough_time = ("minimum_elapsed_days_exclusive" not in rule or
                               (day - positives[0]).days > rule["minimum_elapsed_days_exclusive"])
                if len(positives) >= rule["persistence"] and enough_time:
                    confirmed = day
                    break
            else:
                unknown.append(day.isoformat())
        status = ("met" if confirmed else "pending_confirmation" if positives else
                  "not_assessable" if unknown or conflicts else "not_met")
        assessments.append({"code": rule["condition"]["code"], "status": status,
                            "recorded_date": confirmed.isoformat() if confirmed else None,
                            "evidence_dates": [d.isoformat() for d in positives],
                            "unmeasured_dates": unknown, "conflicting_dates": conflicts,
                            "source": rule["source"], "policy": DIAGNOSTIC_POLICY})
    return assessments


def _conditions_of(person: Person, encounters: list[Encounter] | None = None,
                   as_of: date | None = None) -> list[dict]:
    # An archetype is latent simulation design, not a dated clinical source.
    # Preserve it separately in the full person archive, never as an early diagnosis.
    base = []
    for c in person.diagnosis_history:
        if not isinstance(c, dict) or any(not isinstance(c.get(k), str) or not c[k].strip() for k in ("code", "display")):
            raise ValueError("diagnosis history requires code/display")
        if _known_on(c, as_of):
            base.append(copy.deepcopy(c))
    have = {c["code"] for c in base}
    rules = {r["condition"]["code"]: r for r in spec.cohort().get("diagnostic_criteria", [])}
    for assessment in diagnostic_assessments(encounters or [], as_of):
        code = assessment["code"]
        if assessment["status"] == "met" and code not in have:
            base.append({**rules[code]["condition"], "onset_date": None,
                         "recorded_date": assessment["recorded_date"],
                         "source": "synthetic_numeric_rule", "evidence_dates": assessment["evidence_dates"],
                         "policy": DIAGNOSTIC_POLICY})
            have.add(code)
    return base


def conditions_for_encounter(person: Person, encounter: Encounter) -> list[dict]:
    """Same dated facts for standalone rendering and the longitudinal generator."""
    known = {c["code"]: copy.deepcopy(c) for c in (encounter.conditions or [])
             if _known_on(c, encounter.exam_date)}
    # Explicit current source history remains available to a standalone consumer;
    # historical numeric confirmations come from the encounter's timeline context.
    known.update({c["code"]: c for c in _conditions_of(person, [encounter], encounter.exam_date)})
    return list(known.values())


def _meets(value, op: str, threshold: float) -> bool:
    if value is None:
        return False
    return {">=": value >= threshold, ">": value > threshold,
            "<=": value <= threshold, "<": value < threshold}.get(op, False)


def encounter_record(person: Person, encounter: Encounter, file: str | None = None,
                     index: int = 0, conditions: list[dict] | None = None,
                     assessments: list[dict] | None = None) -> dict:
    encounter.validate_order_results()
    if assessments is not None:
        for assessment in assessments:
            days = [assessment.get("recorded_date")]
            for key in ("evidence_dates", "unmeasured_dates", "conflicting_dates"):
                days.extend(assessment.get(key, []))
            if any(day and date.fromisoformat(day[:10]) > encounter.exam_date for day in days):
                raise ValueError("diagnostic assessment contains future evidence")
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
            "conditions": [copy.deepcopy(c) for c in conditions if _known_on(c, encounter.exam_date)]
                          if conditions is not None else conditions_for_encounter(person, encounter),
        },
        "collected": encounter.exam_date.isoformat(),
        "knowledge_as_of": encounter.exam_date.isoformat(),
        "condition_knowledge_policy": CONDITION_KNOWLEDGE_POLICY,
        "diagnostic_assessments": assessments if assessments is not None else diagnostic_assessments([encounter], encounter.exam_date),
        "exam_type": encounter.exam_type,
        "exam_location": encounter.exam_location,
        "panels": list(encounter.panels),
        "ordered_keys": list(encounter.ordered_keys) if encounter.ordered_keys is not None else None,
        "missing_results": [asdict(m) for m in encounter.missing_results],
        "package": encounter.package,
        "department": encounter.department,
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
        "truth_scope": "full_simulation_timeline_not_point_in_time",
        "person_id": person.person_id,
        "sex": person.sex,
        "birth_year": person.birth_year,
        "height_cm": person.height_cm,
        "archetype": person.archetype,
        "region": person.region,
        "conditions": conditions if conditions is not None else _conditions_of(person),
        "archetype_conditions": copy.deepcopy(spec.cohort().get("archetype_conditions", {}).get(person.archetype, [])),
        "diagnosis_history": copy.deepcopy(list(person.diagnosis_history)),
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

    # Full simulated truth is retained for people.jsonl, never copied into earlier visits.
    conditions = {pid: _conditions_of(by_person[pid], items)
                  for pid, items in encounters.items()}

    rows = 0
    with (out_dir / "manifest.jsonl").open("w", encoding="utf-8") as fh:
        for person_id, items in encounters.items():
            for index, encounter in enumerate(sorted(items, key=lambda e: (e.exam_date, e.exam_type, e.panels)), start=1):
                record = encounter_record(by_person[person_id], encounter, index=index,
                                          conditions=_conditions_of(by_person[person_id], items, encounter.exam_date),
                                          assessments=diagnostic_assessments(items, encounter.exam_date))
                rows += len(record["rows"])
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    with (out_dir / "people.jsonl").open("w", encoding="utf-8") as fh:
        for person in people:
            fh.write(json.dumps(person_record(person, conditions.get(person.person_id)),
                                ensure_ascii=False) + "\n")

    return sum(len(v) for v in encounters.values()), rows
