"""Render the truth layer into files and write one record per file to files.jsonl.

    mirobody-gen build --seed 7 --out out/p2 --render
    mirobody-gen build --seed 7 --out out/p2 --render --pairs 12

One visit becomes one or two lab slips, or one checkup report book; some people also get a transposed
export table. Each file gets one manifest record (`files.jsonl`), carrying both truth layers, a hazard
list (attributed row by row) and a layout summary.

## Minimal-contrast pairs (`--pairs`)

Same visit, same content: lay it out once in a **clean layout** (unit column, reference-range column,
arrow flags, standard separators, no watermark...), then once more per **single** hazard class turned
on. The two files differ only in that one hazard, so the difference in extraction results between them
is that hazard's **causal effect**, not a correlation tangled up with a dozen other things in the
layout.

This brings CheckList's [ribeiro2020beyond] invariance test (INV: apply a label-preserving
perturbation, expect the prediction to stay the same) to documents. One catch: **at the printed-truth
layer, some hazards legitimately change the correct answer** (after `unit.missing`, the unit should no
longer be extractable); what stays invariant is the semantic-truth layer (indicator, value, UCUM unit).
So contrast pairs are scored against the semantic layer — this is exactly what the two truth layers
are for.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import random
import re
from dataclasses import asdict
from datetime import date

from . import book, export, hazards, layout, spec
from . import person as person_mod
from .delivery import render_doc, upload_habit
from .document import Doc, build_doc, split_encounter
from .model import Encounter, Person

TIER = {"pdf": "T0", "xlsx": "T1", "csv": "T1"}
KIND_OF_LOCATION = {"checkup-center": "checkup_center", "hospital": "hospital",
                    "clinic": "clinic", "lab": "lab"}
#: Cap on hazard classes per file in the main split, taken from the reference corpus's measured p95
#: (docs/zh-CN/numbers.md). Files above it go into the stress split.
MAIN_CAP = spec.hazards()["per_document_count"]["p95"]


#: Institution-assignment parameters. All come from the real corpus's **aggregate counts**
#: (resources/numbers.json), not from any single record:
#: * long tail: the concentration alpha of the Chinese restaurant process (Ewens). Inverting the 491
#:   layout fingerprints seen across 627 documents gives alpha ~= 1041;
#: * big clients: the two largest real layout families account for 60/627 and 37/627 of documents. In
#:   a longitudinal cohort this shows up as "some people's checkup center / usual hospital just is one
#:   of these two"; the share is counted per person;
#: * stickiness: chronic-disease follow-ups mostly return to the same hospital (no real count backs
#:   this one; it's a structural assumption of the longitudinal cohort, stated as such).
CRP_ALPHA = 1041.0
BIG_CLIENT_SHARE = {"checkup_center": 0.30, "hospital": 0.20}
STICKY_FOLLOWUP = 0.6


class Institutions:
    """Which institution each person in a corpus went to. State accumulates across people: an
    institution many people have already visited is more likely to be visited again."""

    def __init__(self, registry: layout.Registry, seed: int):
        self.registry = registry
        self.counts: dict[int, int] = {}
        self.fresh = {key: random.Random(f"pool:{seed}:{key}").sample(pool, len(pool))
                      for key, pool in registry.pools.items()}

    def draw(self, rng: random.Random, kind: str, group: str) -> int:
        key = (group, kind) if (group, kind) in self.fresh else (group, "hospital")
        seen = [i for i in self.counts if self.registry.institutions[i]["kind"] == key[1]
                and ("en" if self.registry.institutions[i]["language"] == "en" else "zh") == group]
        total = sum(self.counts[i] for i in seen)
        if seen and rng.random() < total / (total + CRP_ALPHA):
            index = rng.choices(seen, weights=[self.counts[i] for i in seen])[0]
        else:
            index = next(i for i in self.fresh[key] if i not in self.counts and i not in self.registry.big.values())
        return index

    def use(self, index: int) -> None:
        self.counts[index] = self.counts.get(index, 0) + 1


def home_institutions(seed: int, person: Person, process: Institutions) -> tuple[str, dict[str, int]]:
    """This person's language group, checkup center and usual hospital. Big clients exist only among
    Chinese-language institutions.

    The first return value is the **demographic language group** (person_lang as-is, which may be
    `ja`) — it's what distinguishes populations for device time zone and gene-frequency draws. The
    institution pool is split only into document-wording groups (zh/en): a Japanese speaker's checkup
    slip is currently issued by an English-wording institution (the document-wording layer is
    `spec.doc_lang`), but their device time zone is still +09:00 and genes still drawn at East Asian
    frequencies — each of the three layers is kept separate."""
    rng = person_mod.home_stream(seed, person.person_id)
    group = person_mod.draw_lang(rng)
    doc_group = spec.doc_lang(group)
    home = {}
    for kind in ("checkup_center", "hospital"):
        if doc_group == "zh" and rng.random() < BIG_CLIENT_SHARE[kind]:
            home[kind] = process.registry.big[kind]
        else:
            home[kind] = process.draw(rng, kind, doc_group)
    return group, home


def layout_summary(doc: Doc, page_count: int | None) -> dict:
    """Layout summary, in the format `audit/fingerprint.py` expects. The real-data side of this is
    produced by `scripts/build_numbers.layout_summary`."""
    columns = list(doc.tables[0].headers[0]) if doc.tables and doc.tables[0].headers else []
    refs, flags = [], []
    for table in doc.tables:
        for c in table.rows:
            if c.printed is None:
                continue
            if c.range:
                refs.append(re.sub(r"\d+(?:\.\d+)?", "{n}", c.range.split("\n")[0]))
            if c.flag:
                flags.append(re.sub(r"[\d.]+", "", c.flag).strip()[:6])
    langs = {doc.family.language}
    latin = re.compile(r"[A-Za-z]{2,}")
    if doc.family.language != "en" and (doc.family.bilingual_header or any(
            latin.search(p.item_name) for p in doc.printed)):
        langs.add("en")
    return {"columns": columns, "reference_templates": refs, "flag_markers": flags,
            "page_count": page_count, "languages": sorted(langs)}


def documents_for(rng: random.Random, person: Person, enc: Encounter, idx: int, family: layout.Family,
                  previous: dict, banner: bool) -> list[Doc]:
    """Which files one visit produces. Checkup -> one report book; clinic -> an outpatient record plus
    lab slips; specialty -> an ECG report / ultrasound report / one slip; follow-up -> one or a few lab
    slips."""
    base = f"{person.person_id}_{enc.exam_date.isoformat()}_e{idx:02d}"
    docs: list[Doc] = []
    if enc.exam_type == "routine":
        groups = [g for s in split_encounter(rng, enc) for g in s]
        docs.append(book.build_book(rng, f"{base}a", person, enc, groups, family, previous, banner=banner))
        return docs
    if enc.exam_type == "specialty" and "ecg" in enc.panels:
        docs.append(book.build_ecg_report(rng, f"{base}a", person, enc, family, previous, banner=banner))
        return docs
    if enc.exam_type == "specialty" and "ultrasound" in enc.panels:
        docs.append(book.build_ultrasound_report(rng, f"{base}a", person, enc, family, banner=banner))
        return docs
    letters = "abcdefgh"
    if enc.exam_type == "specialty" and "imaging" in enc.panels:
        docs.append(book.build_imaging_report(rng, f"{base}a", person, enc, family, banner=banner))
        return docs
    if enc.exam_type == "clinic":
        clinic_keys = set(spec.cohort()["orders"]["vitals_clinic"])
        rest = dataclasses.replace(enc, readings=[r for r in enc.readings if r.key not in clinic_keys])
        # Outpatient records are a small class in the real corpus (4/627): most clinic visits leave
        # behind only a lab slip, with the record itself staying in the hospital's own system. Only a
        # visit with no lab slip to leave behind (a hypertension follow-up that only measures blood
        # pressure) is guaranteed to produce a record.
        if not rest.readings or rng.random() < 0.35:
            docs.append(book.build_outpatient(rng, f"{base}a", person, enc, family, banner=banner))
        if not rest.readings:
            return docs
        for j, groups in enumerate(split_encounter(rng, rest), start=1):
            d = build_doc(rng, f"{base}{letters[j]}", person, rest, groups, family, previous, banner=banner)
            d.kind = "lab_slip"
            docs.append(d)
        return docs
    for j, groups in enumerate(split_encounter(rng, enc)):
        d = build_doc(rng, f"{base}{letters[j]}", person, enc, groups, family, previous, banner=banner)
        d.kind = "lab_slip"
        docs.append(d)
    return docs


def record(doc: Doc, path: pathlib.Path, out_root: pathlib.Path, pages: int | None,
           encounters: list[Encounter], extra: dict | None = None, delivery: dict | None = None) -> dict:
    f = doc.family
    hazard_list = [{"name": k, "source": v["source"], "rows": sorted(v["rows"])}
                   for k, v in sorted(doc.hazards.items())]
    delivery = delivery or {"tier": TIER[f.fmt], "scene": None, "severity": None, "ops": [], "container": f.fmt,
                            "annotations": []}
    stress = len(hazard_list) > MAIN_CAP or delivery.get("severity") == "severe"
    out = {
        "file": str(path.relative_to(out_root)),
        "synthetic": True,
        "doc_id": doc.doc_id,
        "person_id": doc.person_id,
        "kind": doc.kind,
        "encounter_dates": [e.exam_date.isoformat() for e in encounters],
        "exam_type": encounters[0].exam_type if len(encounters) == 1 else "export",
        "package": encounters[0].package if len(encounters) == 1 else None,
        "family": f.family_id,
        "institution": f.institution,
        "issuer_kind": f.kind,
        "format": path.suffix.lstrip("."),
        "source_format": f.fmt,
        "tier": delivery["tier"],
        "scene": delivery.get("scene"),
        "severity": delivery.get("severity"),
        "ops": delivery.get("ops", []),
        "annotations": delivery.get("annotations", []),
        "dpi": delivery.get("dpi"),
        "image_size": delivery.get("image_size"),
        "language": f.language,
        "page_count": pages,
        "layout": layout_summary(doc, pages),
        "jitter": doc.jitter,
        "dates": doc.dates,
        "subject": dict(doc.subject),
        "cover": doc.cover,
        "hazards": hazard_list,
        "hazard_count": len(hazard_list),
        "split": "stress" if stress else "main",
        "printed_rows": [asdict(p) for p in doc.printed],
        "readings": [asdict(r) for r in doc.readings],
        "distractors": doc.distractors,
        # Truth outside the tables. blocks are key-value/narrative entries (Physiological / Imaging
        # rows); findings is the coded truth for named findings; summary is the overall conclusions and
        # recommendations; complaints/diagnoses come from outpatient records.
        "blocks": [{"kind": b.kind, "title": b.title, "section": b.section_id,
                    "items": b.truth, "printed": [t for pair in b.rows for t in pair if t]}
                   for b in doc.blocks if b.kind in ("kv", "narrative", "summary")],
        "findings": doc.findings_truth,
        "summary": doc.summary_truth,
        "complaints": doc.complaints_truth,
        "diagnoses": doc.diagnoses_truth,
    }
    if extra:
        out.update(extra)
    return out


def render_corpus(seed: int, people: list[Person], encounters: dict[str, list[Encounter]],
                  out_dir: pathlib.Path, banner: bool = True, handwriting: bool = False) -> list[dict]:
    """Render every file and write files.jsonl.

    `handwriting` adds the handwritten files (`handwriting.py`). They draw only from streams of their own and
    their records are written after all printed ones, so the printed files and the first lines of
    files.jsonl are byte-identical with the option on or off."""
    registry = layout.build_registry(seed)
    process = Institutions(registry, seed)
    files_root = out_dir / "files"
    records: list[dict] = []
    hand_records: list[dict] = []
    for person in people:
        rng = random.Random(f"docs:{seed}:{person.person_id}")
        group, home = home_institutions(seed, person, process)
        habit = upload_habit(seed, person)
        previous: dict[str, tuple[str, str]] = {}
        facilities, filenames = [], []
        visits: list[tuple[int, Encounter, layout.Family]] = []
        for idx, enc in enumerate(encounters[person.person_id], start=1):
            kind = KIND_OF_LOCATION.get(enc.exam_location, "hospital")
            visit_group = group if rng.random() >= 0.08 else ("zh" if group == "en" else "en")
            if kind == "checkup_center" and visit_group == group:
                index = home["checkup_center"]
            elif kind == "hospital" and visit_group == group and rng.random() < STICKY_FOLLOWUP:
                index = home["hospital"]
            else:
                index = process.draw(rng, kind, visit_group)
            process.use(index)
            family, rev = layout.revised(registry.family(index), enc.exam_date.year)
            family, jit = layout.jitter(rng, family)
            jit = rev + jit
            visits.append((idx, enc, family))
            first_file = ""
            for doc in documents_for(rng, person, enc, idx, family, previous, banner):
                doc.jitter = jit
                if doc.tables:
                    hazards.inject(doc, rng)
                hazards.detect(doc)
                path, pages, delivery = render_doc(doc, files_root, f"{person.person_id}/{doc.doc_id}", habit, rng)
                records.append(record(doc, path, out_dir, pages, [enc], delivery=delivery))
                first_file = first_file or path.name
            facilities.append(family.institution)
            filenames.append(first_file)
            for r in enc.readings:
                previous[r.key] = (r.value, enc.exam_date.isoformat())
        # Home logs: a person with a cuff gets a blood-pressure diary, a person who weighs themselves
        # regularly gets a weight log (the material comes from the device series, the same physiology line)
        from . import devices as devices_mod
        from .person import person_lang

        series = devices_mod.series_for(person, seed, person_lang(seed, person.person_id))
        logs = []
        if series["habits"]["cuff"]:
            logs += [("bp", w) for w in devices_mod.bp_log_windows(series, rng)]
        if series["habits"]["weigh_rate"] >= 0.5 and rng.random() < 0.5:
            by_day: dict[str, list[dict]] = {}
            for r in series["records"]:
                if r["_metric"] == "weight":
                    by_day.setdefault(r["time"][:10], []).append(r)
            days = sorted(by_day)
            if len(days) >= 7:
                start = rng.randint(0, len(days) - 7)
                logs.append(("weight", [{"day": d, "records": by_day[d]} for d in days[start:start + rng.randint(7, 14)]]))
        for k, (log_kind, window) in enumerate(logs):
            fam = registry.family(home["checkup_center"])
            fam = dataclasses.replace(fam, fmt=rng.choices(["xlsx", "csv", "pdf"], weights=[45, 15, 40])[0],
                                      furniture=(), watermark=False, signatures=())
            doc = book.build_home_log(rng, f"{person.person_id}_log{k + 1:02d}", person, window, fam, log_kind, banner)
            hazards.detect(doc)
            path, pages, delivery = render_doc(doc, files_root, f"{person.person_id}/{doc.doc_id}", habit, rng)
            first = date.fromisoformat(window[0]["day"])
            records.append(record(doc, path, out_dir, pages, [Encounter(person_id=person.person_id, exam_date=first,
                                                                          exam_type="home_log", exam_location="home",
                                                                          panels=(log_kind,))], delivery=delivery))
        encs = encounters[person.person_id]
        if export.eligible(encs) and rng.random() < 0.3:
            doc_id = f"{person.person_id}_export"
            doc = export.build_export(rng, doc_id, person, encs, facilities, filenames,
                                      registry.family(home["checkup_center"]), banner)
            hazards.detect(doc)
            path, pages, delivery = render_doc(doc, files_root, f"{person.person_id}/{doc_id}")
            records.append(record(doc, path, out_dir, pages,
                                  [e for e in encs if e.exam_type == "routine"], delivery=delivery))
        if handwriting:
            from . import handwriting as hand_docs

            base = registry.family(home["checkup_center"])
            for doc, path, delivery, enc, extra in hand_docs.render_person(seed, person, group, base, visits,
                                                                           files_root, banner):
                hand_records.append(record(doc, path, out_dir, 1, [enc], extra=extra, delivery=delivery))
    records += hand_records
    with (out_dir / "files.jsonl").open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return records


