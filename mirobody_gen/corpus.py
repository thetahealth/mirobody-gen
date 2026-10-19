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

from . import book, clinical, export, genomics, genomics_report, hazards, layout, spec
from . import person as person_mod
from .delivery import choose_delivery, render_doc, upload_habit
from .document import Doc, build_doc, split_encounter
from .model import Encounter, Person
from .render import pgx_report

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
INSTITUTION_POOL_POLICY = "institution-pool-v2"
#: Visits are assigned institutions in calendar order across the whole cohort, each from a stream
#: addressed by the visit itself, so no visit's institution depends on any later visit (of the same
#: person or anyone rendered earlier). v1 drew in person order from the document stream.
INSTITUTION_ASSIGNMENT_POLICY = "institution-assignment-v2"


class Institutions:
    """Which institution each person in a corpus went to. State accumulates across people: an
    institution many people have already visited is more likely to be visited again."""

    def __init__(self, registry: layout.Registry, seed: int, service_profile=None):
        self.registry = registry
        self.service_profile = service_profile
        if service_profile is not None:
            service_profile.assert_catalog(registry.institutions)
        self.counts: dict[int, int] = {}
        self.fresh = {key: random.Random(f"pool:{seed}:{key}").sample(pool, len(pool))
                      for key, pool in registry.pools.items()}
        #: What an unconstrained draw may pick: every institution, or in clinical mode only those whose
        #: template prints complete tables (`clinical_tables`). A service-constrained draw follows the
        #: authored profile as declared.
        self.allowed: frozenset[int] | None = None
        if clinical.ENABLED:
            from . import clinical_tables

            self.allowed = clinical_tables.complete_institutions(registry)
            self.fresh = {key: [i for i in order if i in self.allowed] for key, order in self.fresh.items()}

    def admits(self, index: int) -> bool:
        return self.allowed is None or index in self.allowed

    def draw(self, rng: random.Random, kind: str, group: str, *, required_service: str | None = None) -> int:
        if required_service is not None:
            if self.service_profile is None:
                raise ValueError("Service-constrained selection requires an explicit service profile")
            eligible = self.service_profile.eligible(kind, group, required_service)
            if not eligible:
                raise ValueError(f"No declared facility for service {required_service!r}, {kind}/{group}")
            seen = [i for i in eligible if i in self.counts]
            fresh = [i for i in eligible if i not in self.counts]
            total = sum(self.counts[i] for i in seen)
            if seen and (not fresh or rng.random() < total / (total + CRP_ALPHA)):
                return rng.choices(seen, weights=[self.counts[i] for i in seen])[0]
            return rng.choice(fresh)
        key = (group, kind) if (group, kind) in self.fresh else (group, "hospital")
        seen = [i for i in self.counts if self.registry.institutions[i]["kind"] == key[1]
                and ("en" if self.registry.institutions[i]["language"] == "en" else "zh") == group
                and self.admits(i)]
        total = sum(self.counts[i] for i in seen)
        if seen and rng.random() < total / (total + CRP_ALPHA):
            index = rng.choices(seen, weights=[self.counts[i] for i in seen])[0]
        else:
            index = next((i for i in self.fresh[key]
                          if i not in self.counts and i not in self.registry.big.values()), None)
            if index is None:
                if not seen:
                    raise ValueError(f"No available or reusable institution in {key[1]}/{key[0]}")
                # A finite fictional catalogue cannot supply a new provider forever. Reuse only
                # the same pool, preserving every successful legacy draw and its RNG state.
                index = rng.choices(seen, weights=[self.counts[i] for i in seen])[0]
        return index

    def use(self, index: int) -> None:
        self.counts[index] = self.counts.get(index, 0) + 1


def preflight_services(service_profile, encounters: dict[str, list[Encounter]]) -> None:
    """Validate all requested pools before creating corpus files, including cross-language visits."""
    if service_profile is None:
        return
    service_profile.assert_catalog(spec.fiction()["institutions"])
    requests = {(KIND_OF_LOCATION.get(e.exam_location, "hospital"), service_profile.required[e.exam_type])
                for visits in encounters.values() for e in visits if e.exam_type in service_profile.required}
    for kind, service in sorted(requests):
        for group in ("zh", "en"):
            if not service_profile.eligible(kind, group, service):
                raise ValueError(f"No declared facility for service {service!r}, {kind}/{group}")


def assign_institutions(seed: int, people: list[Person], encounters: dict[str, list[Encounter]],
                        process: Institutions, service_profile=None) -> tuple[dict, dict, dict]:
    """Every visit's institution, decided in calendar order across the cohort.

    Popularity (the reuse probability) accumulates over time, and a person's usual checkup centre and
    hospital are chosen at their first visit. Each visit draws from its own stream, addressed like the
    encounter streams by date/type/orders/package and occurrence, so deleting or editing a visit can
    only change visits after it. Returns `(homes, chosen, service_evidence)`: `homes[pid] = (group,
    home)`, `chosen[(pid, idx)]` the institution index of the person's idx-th visit (1-based).
    """
    registry = process.registry
    order = {p.person_id: i for i, p in enumerate(people)}
    visits = []
    for p in people:
        occurrences: dict[tuple, int] = {}
        for idx, enc in enumerate(encounters[p.person_id], start=1):
            identity = (enc.exam_date.isoformat(), enc.exam_type, tuple(enc.panels), enc.package)
            occurrence = occurrences.get(identity, 0)
            occurrences[identity] = occurrence + 1
            visits.append((enc.exam_date, order[p.person_id], idx, p, enc, identity, occurrence))
    visits.sort(key=lambda v: (v[0], v[1], v[2]))
    homes: dict[str, tuple[str, dict[str, int]]] = {}
    chosen: dict[tuple[str, int], int] = {}
    evidence: dict[tuple, dict] = {}
    for _, _, idx, p, enc, identity, occurrence in visits:
        if p.person_id not in homes:
            homes[p.person_id] = home_institutions(seed, p, process)
            if clinical.ENABLED:
                from . import clinical_issuers

                group, home = homes[p.person_id]
                homes[p.person_id] = group, clinical_issuers.homes(seed, p, enc.exam_date.year, process, home,
                                                                   spec.doc_lang(group))
        group, home = homes[p.person_id]
        doc_group = spec.doc_lang(group)
        rng = random.Random(json.dumps([INSTITUTION_ASSIGNMENT_POLICY, seed, p.person_id, list(identity), occurrence]))
        service = service_profile.required.get(enc.exam_type) if service_profile else None
        kind = KIND_OF_LOCATION.get(enc.exam_location, "hospital")
        visit_group = doc_group if rng.random() >= 0.08 else ("zh" if doc_group == "en" else "en")
        if kind == "checkup_center" and visit_group == doc_group:
            index = home["checkup_center"]
        elif kind == "hospital" and visit_group == doc_group and rng.random() < STICKY_FOLLOWUP:
            index = home["hospital"]
        else:
            index = process.draw(rng, kind, visit_group, required_service=service)
        if service is not None and not service_profile.supports(index, service):
            index = process.draw(rng, kind, visit_group, required_service=service)
        if clinical.ENABLED:
            from . import clinical_issuers

            index = clinical_issuers.visit(seed, p, enc, list(identity), occurrence, process, index, kind, visit_group,
                                           service_profile.eligible(kind, visit_group, service) if service else None)
        if service is not None:
            evidence[(p.person_id, enc.exam_date.isoformat(), enc.exam_type,
                      registry.institutions[index]["name"])] = service_profile.evidence(index, service)
        process.use(index)
        chosen[(p.person_id, idx)] = index
    for p in people:  # a person with no visit still has a usual centre for home logs
        if p.person_id not in homes:
            homes[p.person_id] = home_institutions(seed, p, process)
    return homes, chosen, evidence


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
        if doc_group == "zh" and rng.random() < BIG_CLIENT_SHARE[kind] and process.admits(process.registry.big[kind]):
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
        if not rest.readings and not rest.missing_results:
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
        "missing_results": doc.missing_results,
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
    if delivery.get("container_fallback"):
        out["container_fallback"] = dict(delivery["container_fallback"])
    eye = next((b for b in doc.blocks if b.section_id == "eye"), None)
    if doc.kind == "checkup_book" and eye is not None:
        present = {t.get("item_id") for t in eye.truth if t.get("measurements")}
        out["ocular_exam"] = {"policy_version": book.OCULAR_POLICY,
                              "scope": "generated_eye_quantitative_items",
                              "not_reported": [{"item_id": item, "left_status": "unknown", "right_status": "unknown"}
                                               for item in book.OCULAR_ITEMS if item not in present]}
    if doc.kind == "outpatient_record":
        raw_department = encounters[0].department if len(encounters) == 1 else None
        department = raw_department if raw_department and raw_department.strip() else None
        out["care_context"] = {"policy_version": book.OUTPATIENT_CONTEXT_POLICY,
                               "department": department,
                               "department_source": "encounter" if department else "unknown"}
    if extra:
        out.update(extra)
    return out


def genetics_report_file(person: Person, seed: int, lang: str, habit: dict[str, float],
                         rng: random.Random, files_root: pathlib.Path, out_dir: pathlib.Path) -> dict | None:
    """A vendor-style pharmacogenomics report page for a person with a genomics profile, or `None`.
    `genomics.profile_for` is deterministic from `(seed, person_id, lang)`, the same inputs
    `genomics.write_all` already used for the raw export -- so the report and the raw genotype
    file describe the same 41 PGx calls, and a reader cross-checking the two never finds them
    disagreeing.

    Not routed through the Doc/hazard pipeline (this isn't a lab report), but still uses the same
    per-person upload habit and the same `degrade` primitives for the phone-photo/scan tiers, so a
    genetics report shows up as a scan or a photo exactly as often as everything else this person
    uploads.
    """
    profile = genomics.profile_for(person, seed, lang)
    if profile is None:
        return None
    rows = genomics_report.rows_for(profile)
    vendor = profile["vendor"]
    report_lang = pgx_report.VENDOR_LANG[vendor]
    when = person.weight_anchors[0][0]
    data, pages = pgx_report.render(person.person_id, vendor, when.isoformat(), rows)
    deliv = choose_delivery(rng, habit, pages)
    (files_root / person.person_id).mkdir(parents=True, exist_ok=True)
    rel = f"{person.person_id}/{person.person_id}_pgx_report"
    if deliv["tier"] == "T0":
        path = files_root / f"{rel}.pdf"
        path.write_bytes(data)
    else:
        from .render import degrade

        dpi = spec.delivery()["dpi_of_tier"][deliv["tier"]]
        pages_img = degrade.rasterize(data, dpi)
        margins = degrade.content_margins(data, dpi)
        out_pages, ops = degrade.apply_scene(pages_img, deliv["scene"], deliv["severity"],
                                             f"{person.person_id}:pgx_report", margins)
        blob = degrade.encode(out_pages, deliv["container"], pgx_report.TITLE[report_lang])
        ext = {"scanpdf": "pdf", "jpg": "jpg", "png": "png"}[deliv["container"]]
        path = files_root / f"{rel}.{ext}"
        path.write_bytes(blob)
        deliv = {**deliv, "ops": ops, "annotations": [], "dpi": dpi, "image_size": list(out_pages[0].size)}
    return {
        "file": str(path.relative_to(out_dir)), "synthetic": True,
        "doc_id": f"{person.person_id}_pgx_report", "person_id": person.person_id,
        "kind": "genetics_report", "encounter_dates": [], "exam_type": "genomics", "package": None,
        "family": None, "institution": pgx_report.VENDOR_BRAND[vendor], "issuer_kind": "genomics_vendor",
        "format": path.suffix.lstrip("."), "source_format": "pdf",
        "tier": deliv["tier"], "scene": deliv.get("scene"), "severity": deliv.get("severity"),
        "ops": deliv.get("ops", []), "annotations": deliv.get("annotations", []),
        "dpi": deliv.get("dpi"), "image_size": deliv.get("image_size"),
        "language": report_lang, "page_count": pages, "vendor": vendor, "pgx_rows": rows,
        "split": "main",
    }


def render_corpus(seed: int, people: list[Person], encounters: dict[str, list[Encounter]],
                  out_dir: pathlib.Path, banner: bool = True, handwriting: bool = False,
                  service_profile=None) -> list[dict]:
    """Render every file and write files.jsonl.

    `handwriting` adds the handwritten files (`handwriting.py`). They draw only from streams of their own and
    their records are written after all printed ones, so the printed files and the first lines of
    files.jsonl are byte-identical with the option on or off."""
    preflight_services(service_profile, encounters)
    registry = layout.build_registry(seed)
    process = Institutions(registry, seed, service_profile)
    # Institution pools are keyed by document-wording group; a person's group (possibly `ja`) stays
    # the demographic group for genetics and devices.
    homes, chosen, service_evidence = assign_institutions(seed, people, encounters, process, service_profile)
    files_root = out_dir / "files"
    records: list[dict] = []
    hand_records: list[dict] = []
    inject = hazards.inject
    if clinical.ENABLED:
        from . import clinical_tables

        inject = clinical_tables.skip_incidents
    for person in people:
        # The document stream is consumed visit by visit in date order; files made after the visits
        # (genetics report, home logs, export) draw from streams of their own, so a later visit
        # never changes them.
        rng = random.Random(f"docs:{seed}:{person.person_id}")
        group, home = homes[person.person_id]
        habit = upload_habit(seed, person)
        previous: dict[str, tuple[str, str]] = {}
        facilities, filenames = [], []
        visits: list[tuple[int, Encounter, layout.Family]] = []
        for idx, enc in enumerate(encounters[person.person_id], start=1):
            index = chosen[(person.person_id, idx)]
            family, rev = layout.revised(registry.family(index), enc.exam_date.year)
            family, jit = layout.jitter(rng, family)
            jit = rev + jit
            visits.append((idx, enc, family))
            first_file = ""
            for doc in documents_for(rng, person, enc, idx, family, previous, banner):
                doc.jitter = jit
                if doc.tables:
                    inject(doc, rng)
                hazards.detect(doc)
                path, pages, delivery = render_doc(doc, files_root, f"{person.person_id}/{doc.doc_id}", habit, rng)
                records.append(record(doc, path, out_dir, pages, [enc], delivery=delivery))
                first_file = first_file or path.name
            facilities.append(family.institution)
            filenames.append(first_file)
            for r in enc.readings:
                previous[r.key] = (r.value, enc.exam_date.isoformat())
        genetics_rec = genetics_report_file(person, seed, group, habit,
                                            random.Random(f"genetics-doc:{seed}:{person.person_id}"), files_root, out_dir)
        if genetics_rec is not None:
            records.append(genetics_rec)
        # Home logs: a person with a cuff gets a blood-pressure diary, a person who weighs themselves
        # regularly gets a weight log (the material comes from the device series, the same physiology line)
        from . import devices as devices_mod
        from .person import person_lang

        series = devices_mod.series_for(person, seed, person_lang(seed, person.person_id))
        log_rng = random.Random(f"home-logs:{seed}:{person.person_id}")
        logs = []
        if series["habits"]["cuff"]:
            logs += [("bp", w) for w in devices_mod.bp_log_windows(series, log_rng)]
        if series["habits"]["weigh_rate"] >= 0.5 and log_rng.random() < 0.5:
            by_day: dict[str, list[dict]] = {}
            for r in series["records"]:
                if r["_metric"] == "weight":
                    by_day.setdefault(r["time"][:10], []).append(r)
            days = sorted(by_day)
            if len(days) >= 7:
                start = log_rng.randint(0, len(days) - 7)
                logs.append(("weight", [{"day": d, "records": by_day[d]} for d in days[start:start + log_rng.randint(7, 14)]]))
        for k, (log_kind, window) in enumerate(logs):
            fam = registry.family(home["checkup_center"])
            fam = dataclasses.replace(fam, fmt=log_rng.choices(["xlsx", "csv", "pdf"], weights=[45, 15, 40])[0],
                                      furniture=(), watermark=False, signatures=())
            doc = book.build_home_log(log_rng, f"{person.person_id}_log{k + 1:02d}", person, window, fam, log_kind, banner)
            hazards.detect(doc)
            path, pages, delivery = render_doc(doc, files_root, f"{person.person_id}/{doc.doc_id}", habit, log_rng)
            first = date.fromisoformat(window[0]["day"])
            records.append(record(doc, path, out_dir, pages, [Encounter(person_id=person.person_id, exam_date=first,
                                                                          exam_type="home_log", exam_location="home",
                                                                          panels=(log_kind,))], delivery=delivery))
        encs = encounters[person.person_id]
        export_rng = random.Random(f"export:{seed}:{person.person_id}")
        if export.eligible(encs) and export_rng.random() < 0.3:
            doc_id = f"{person.person_id}_export"
            doc = export.build_export(export_rng, doc_id, person, encs, facilities, filenames,
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
    if service_profile is not None:
        for rec in records:
            if len(rec["encounter_dates"]) == 1:
                evidence = service_evidence.get((rec["person_id"], rec["encounter_dates"][0],
                                                 rec["exam_type"], rec["institution"]))
                if evidence is not None and rec["exam_type"] in service_profile.required:
                    rec["facility_service"] = dict(evidence)
    with (out_dir / "files.jsonl").open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return records


