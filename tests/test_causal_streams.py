"""C: future schedule/event edits must not choose past encounter measurement noise."""
from dataclasses import asdict, replace
from datetime import date
import hashlib
import json
import random

import pytest

from mirobody_gen import corpus, person
from mirobody_gen.model import Event, Person

CUTOFF = date(2023, 3, 1)


def fixed_person():
    return next(p for p in person.build_cohort(7) if p.person_id == "p035")


def past(p, cutoff=CUTOFF):
    return [asdict(e) for e in person.encounters_for(p, 7) if e.exam_date <= cutoff]


@pytest.mark.parametrize("change", ["delete", "edit", "reorder"])
def test_original_p035_future_event_counterfactual(change):
    p = fixed_person()
    future = [e for e in p.events if e.start > CUTOFF]
    assert len(future) == 3
    if change == "delete":
        q = replace(p, events=tuple(e for e in p.events if e.start <= CUTOFF))
    elif change == "edit":
        q = replace(p, events=tuple(replace(e, start=date(2025, 7, 1), duration_days=1, effects={})
                                    if e.start > CUTOFF else e for e in p.events))
    else:
        q = replace(p, events=tuple(reversed(p.events)))
    assert len(past(p)) == 2
    assert past(p) == past(q)


@pytest.mark.parametrize("change", ["delete", "extra", "orders"])
def test_future_visit_schedule_does_not_select_past_noise(monkeypatch, change):
    p = fixed_person()
    expected = past(p)
    original = person.schedule
    def changed_schedule(p, rng):
        slots = original(p, rng)
        if change == "delete": return [s for s in slots if s[0] <= CUTOFF]
        if change == "extra": return slots + [(date(2026, 12, 31), "specialty", ["lipid"], None)]
        return [(s[0], s[1], ["cbc"], s[3]) if s[0] > CUTOFF else s for s in slots]
    monkeypatch.setattr(person, "schedule", changed_schedule)
    assert past(p) == expected


def test_future_incident_before_past_incident_in_input_does_not_shift_its_visit():
    p = Person("incident-order", "female", 1980, 165, "healthy", "华东",
               weight_anchors=((date(2024, 1, 1), 65), (date(2026, 1, 1), 65)))
    early = Event("急性上呼吸道感染", "health_event", date(2024, 4, 1), 7, "negative", "low", note="随机事件")
    later = replace(early, start=date(2025, 4, 1))
    a = replace(p, events=(early, later))
    b = replace(p, events=(later, early))
    cutoff = date(2024, 7, 1)
    def slots(p):
        return [s for s in person.schedule(p, random.Random(7)) if s[0] <= cutoff]
    assert slots(a) == slots(b) == slots(replace(p, events=(early,)))
    assert past(a, cutoff) == past(b, cutoff) == past(replace(p, events=(early,)), cutoff)


def test_duplicate_visit_noise_is_distinct_and_repeatable(monkeypatch):
    p = fixed_person()
    slot = (date(2023, 1, 1), "follow-up", ["lipid"], None)
    monkeypatch.setattr(person, "schedule", lambda p, rng: [slot, slot])
    a, b = person.encounters_for(p, 7), person.encounters_for(p, 7)
    assert [asdict(e) for e in a] == [asdict(e) for e in b]
    assert [r.value for r in a[0].readings] != [r.value for r in a[1].readings]


def test_incident_decision_is_independent_of_later_schedule_horizon(monkeypatch):
    p = Person("incident-horizon", "female", 1980, 165, "healthy", "华东",
               weight_anchors=((date(2024, 1, 1), 65), (date(2026, 1, 1), 65)))
    event = Event("急性上呼吸道感染", "health_event", date(2024, 4, 1), 7, "negative", "low", note="随机事件")
    p = replace(p, events=(event,))
    included = 0
    # All16 seeds are fixed; no selection of a seed that happens to agree.
    for seed in range(16):
        got = []
        for horizon in (date(2026, 9, 1), date(2024, 7, 1)):
            monkeypatch.setattr(person, "CORPUS_END", horizon)
            slots = person.schedule(p, random.Random(seed))
            got.append([s for s in slots if date(2024, 4, 2) <= s[0] <= date(2024, 4, 4)
                        and s[2] == ["vitals_clinic", "cbc", "inflammation"]])
        assert got[0] == got[1]
        included += bool(got[0])
    assert 0 < included < 16


def test_real_corpus_encounter_materials_hold_under_future_event_deletion(tmp_path):
    """Actual corpus renderer/delivery, not predictions synthesized from manifest truth."""
    p = fixed_person()
    q = replace(p, events=tuple(e for e in p.events if e.start <= CUTOFF))
    # Keep the original complete timelines supplied to the ordinary corpus consumer.
    # Bound this test to its encounter document family; home logs/genetics are
    # separate consumers and are not silently counted as historical material.
    records = []
    for label, who in (("a", p), ("b", q)):
        out = tmp_path / label
        rs = corpus.render_corpus(7, [who], {who.person_id: person.encounters_for(who, 7)}, out)
        selected = [r for r in rs if len(r.get("encounter_dates", [])) == 1
                    and r["encounter_dates"][0] <= CUTOFF.isoformat()
                    and r.get("exam_type") in ("routine", "follow-up", "clinic", "specialty")]
        assert selected
        records.append({r["file"]: ((out / r["file"]).read_bytes(), r) for r in selected})
    assert records[0] == records[1]
    # Record actual delivered bytes without using truth to manufacture predictions.
    (tmp_path / "historical-material-evidence.json").write_text(json.dumps({
        "policy": person.ENCOUNTER_STREAM_POLICY,
        "cutoff": CUTOFF.isoformat(),
        "scope": "all delivered encounter files through cutoff for this case; other sources excluded",
        "files": [{"file": name, "sha256": hashlib.sha256(blob).hexdigest(),
                   "page_count": rec["page_count"], "format": rec["format"],
                   "scene": rec["scene"], "earlier_view_metadata_identical": True}
                  for name, (blob, rec) in records[0].items()],
        "byte_identical": True,
        "clinical_answers_accuracy_measured": False,
        "independent_ocr_performed": False,
    }, ensure_ascii=False, indent=2))
