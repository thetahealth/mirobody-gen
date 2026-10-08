"""Diary sentences: symptoms and numbers in a person's own words, with split-and-code truth.

Material for mirobody's `POST /journal/sentence` and `POST /journal`. Its contract (per
`collect/sentence.py`, read 2026-09-29): a model splits one sentence into several items, each with
`kind in measurement / symptom / condition / medication / other`, `name` kept as the person's own
words, and `assertion in present / negated / hypothetical`; `symptom` items are then matched exactly
against the ICPC-3 symptom axis. Truth here has two layers: **which items the sentence should split
into** (kind plus surface wording), and **what each should resolve to** (an S-axis code, or an
expected abstention). Measurements also carry a LOINC.

Sentences are assembled from `resources/complaints.json` templates; symptoms are drawn from the same
archetype/event/background pools as clinic chief complaints, so "dizzy in the diary" and "dizzy as a
chief complaint" fall in the same window for the same person — material for longitudinal
reconciliation.
"""

from __future__ import annotations

import json
import pathlib
import random
from datetime import date, timedelta

from . import physiology, spec
from .model import Person
from .person import CORPUS_END
from .profile import complaint_surface

LOINC = {"sbp": "8480-6", "dbp": "8462-4", "pulse": "8867-4", "weight": "29463-7", "temp": "8310-5", "steps": "55423-8"}


def _sentence(rng: random.Random, lang: str, s1: str, s2: str | None, person: Person, when: date) -> tuple[str, list[dict]]:
    lang = spec.doc_lang(lang)   # diary wording is the document layer: the ja group writes in English (see spec.doc_lang)
    ph = spec.complaints()["phrasing"][lang]
    templates = [t for t in ph["journal"] if ("{s2}" in t) == (s2 is not None)]
    t = rng.choice(templates)
    # a diary has no institution: the wording varies entry by entry, from a stream of its own
    pool = spec.phrasings(f"complaints.phrasing.{lang}.journal.{ph['journal'].index(t)}", t)
    t = pool[0] if len(pool) == 1 else random.Random(f"jphr:{person.person_id}:{when}").choice(pool)
    text = t.format(s=s1, s_cap=s1[:1].upper() + s1[1:], s2=s2 or "", dur=rng.choice(ph["durations"]),
                    cause=rng.choice(ph["causes"]))
    measurements: list[dict] = []
    if rng.random() < 0.3:
        r = random.Random(f"jm:{person.person_id}:{when}")
        which = rng.choice(ph["measure"])
        sbp = int(round(physiology.measure(r, person, "sbp", when)))
        dbp = int(round(physiology.measure(r, person, "dbp", when)))
        pulse = int(round(physiology.measure(r, person, "pulse", when)))
        weight = round(physiology.weight_at(person, when) + r.gauss(0, 0.3), 1)
        temp = round(37.9 + r.random() * 1.2, 1) if s1 in ("发烧", "发热", "低烧", "feverish", "temperature") else round(36.3 + r.random() * 0.6, 1)
        steps = int(r.gauss(6500, 1800))
        text = text.rstrip("。.") + ("，" if lang == "zh" else ", ") + which.format(sbp=sbp, dbp=dbp, pulse=pulse, weight=weight, temp=temp, steps=steps) + ("。" if lang == "zh" else ".")
        for key, val, unit in (("sbp", sbp, "mmHg"), ("dbp", dbp, "mmHg"), ("pulse", pulse, "/min"),
                               ("weight", weight, "kg"), ("temp", temp, "Cel"), ("steps", steps, "count")):
            if ("{" + key + "}") in which:
                measurements.append({"kind": "measurement", "key": key, "value": val, "unit": unit, "loinc": LOINC[key]})
    return text, measurements


def entries_for(person: Person, seed: int, lang: str) -> list[dict]:
    rng = random.Random(f"journal:{seed}:{person.person_id}")
    comp = spec.complaints()
    pools = comp["pools"]
    arch = pools["archetype"].get(person.archetype, pools["archetype"]["healthy"])
    scripted = [e for e in person.events if e.note == "原型剧本"]
    start = person.weight_anchors[0][0]
    out: list[dict] = []
    day = start
    diligence = rng.choice([0.3, 0.6, 1.0, 1.6])          # some people write daily, some write thrice a year
    while day <= CORPUS_END:
        candidates: list[str] = []
        # background symptoms
        for sid, rate in pools["background"].items():
            if rng.random() < rate * diligence / 30:
                candidates.append(sid)
        # archetype: more symptoms before the intervention, fewer after
        after = bool(scripted) and scripted[0].start <= day
        pool = arch["after"] if after else arch["before"]
        if pool and rng.random() < (0.02 if after else 0.05) * diligence:
            candidates.append(rng.choice(pool))
        # during an event
        for e in person.events:
            if e.note == "随机事件" and 0 <= (day - e.start).days <= min(e.duration_days, 21):
                inc = pools["incident"].get(e.name, [])
                if inc and rng.random() < 0.25 * diligence:
                    candidates.append(rng.choice(inc))
        if candidates:
            picked = list(dict.fromkeys(candidates))[:2]
            c1 = complaint_surface(picked[0], lang, rng)
            c2 = complaint_surface(picked[1], lang, rng) if len(picked) > 1 else None
            text, measurements = _sentence(rng, lang, c1.text, c2.text if c2 else None, person, day)
            expected = [{"kind": "symptom", "name": c.text, "symptom_id": c.symptom_id, "icpc3": c.icpc3, "expect": c.expect}
                        for c in (c1, c2) if c] + measurements
            out.append({"person_id": person.person_id, "synthetic": True, "date": day.isoformat(), "lang": lang,
                        "text": text, "expected": expected})
        day += timedelta(days=1)
    return out


def write_all(out_dir: pathlib.Path, people: list[Person], seed: int, langs: dict[str, str]) -> int:
    n = 0
    with (out_dir / "journal.jsonl").open("w", encoding="utf-8") as fh:
        for person in people:
            for entry in entries_for(person, seed, langs.get(person.person_id, "zh")):
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                n += 1
    return n
