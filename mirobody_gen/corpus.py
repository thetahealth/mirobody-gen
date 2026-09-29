"""Render the truth layer into files and write one record per file to files.jsonl.

把真值层渲染成文件，并写出逐文件的 files.jsonl。

    mirobody-gen build --seed 7 --out out/p2 --render
    mirobody-gen build --seed 7 --out out/p2 --render --pairs 12

一次就诊 → 一到两份检验单，或一本体检报告；部分人另有一份转置导出表。
每份文件一条 manifest 记录（`files.jsonl`），带两层真值、陷阱清单（逐行归因）与版式摘要。

## 最小对照对（`--pairs`）

同一次就诊、同一套内容，先按一个**干净版式**排一份（单位列、参考范围列、箭头标记列、
标准分隔符、无水印……），再每次只打开**一类**陷阱排一份。两份的差别只有那一类陷阱，
所以两份文件上抽取结果的差就是这类陷阱的**因果效应**，而不是和版式里其他十几种东西缠在一起的相关。

这是 CheckList [ribeiro2020beyond] 的不变性测试（INV：施加保持标签的扰动，期望预测不变）
搬到文档上。有一处要小心：**在印刷真值那一层，有的陷阱会合法地改变正确答案**
（`unit.missing` 之后单位本来就不该被抽出来）；不变的是语义真值那一层（指标、数值、UCUM 单位）。
所以对照对的评分以语义层为准——这正是两层真值的用处。
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
#: 主榜单每份文件的陷阱类数上限，取参考集实测的 p95（docs/zh-CN/numbers.md）。超过的进 stress 分层。
MAIN_CAP = spec.hazards()["per_document_count"]["p95"]


#: 机构分配的参数。全部来自真实语料的**聚合计数**（resources/numbers.json），不来自任何一份记录：
#: * 长尾：中国餐馆过程（Ewens）的集中度 α。按 627 份文档出现 491 种版式指纹反解得 α≈1041；
#: * 大客户：真实最大的两个版式家族占文档的 60/627 与 37/627。在纵向队列里它们体现为
#:   "一部分人的体检中心 / 常去医院就是这两家"，份额按人算；
#: * 粘性：慢病复查多半回同一家医院（这一条没有真实计数可依，是纵向队列的结构假设，照实写出）。
CRP_ALPHA = 1041.0
BIG_CLIENT_SHARE = {"checkup_center": 0.30, "hospital": 0.20}
STICKY_FOLLOWUP = 0.6


class Institutions:
    """一批语料里"谁去了哪家机构"。状态跨人累积：先被很多人去过的机构更可能再被去。"""

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
    """这个人的语言组、体检中心与常去医院。大客户只在中文机构里。"""
    rng = person_mod.home_stream(seed, person.person_id)
    group = person_mod.draw_lang(rng)
    home = {}
    for kind in ("checkup_center", "hospital"):
        if group == "zh" and rng.random() < BIG_CLIENT_SHARE[kind]:
            home[kind] = process.registry.big[kind]
        else:
            home[kind] = process.draw(rng, kind, group)
    return group, home


def layout_summary(doc: Doc, page_count: int | None) -> dict:
    """与 `audit/fingerprint.py` 约定的版式摘要。真实侧由 `scripts/build_numbers.layout_summary` 产出。"""
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
    """一次就诊产出哪些文件。体检 → 一本报告书；门诊 → 一份门诊病历 + 化验单；
    专项 → 心电图报告 / 超声报告 / 一张单子；复查 → 一到几张化验单。"""
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
        # 真实语料里门诊病历是小类（4/627）：多数门诊只留下化验单，病历本身在医院系统里。
        # 没有化验单可留的（高血压复查只测血压）才一定出病历。
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
        # 表格之外的真值。blocks 是键值对/叙述条目（Physiological / Imaging 行），
        # findings 是具名所见的编码真值，summary 是总检结论与建议，complaints/diagnoses 来自门诊病历。
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
                  out_dir: pathlib.Path, banner: bool = True) -> list[dict]:
    registry = layout.build_registry(seed)
    process = Institutions(registry, seed)
    files_root = out_dir / "files"
    records: list[dict] = []
    for person in people:
        rng = random.Random(f"docs:{seed}:{person.person_id}")
        group, home = home_institutions(seed, person, process)
        habit = upload_habit(seed, person)
        previous: dict[str, tuple[str, str]] = {}
        facilities, filenames = [], []
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
        # 居家记录表：有血压计的人出血压日记，常称重的人出体重记录（素材来自设备序列，同一条生理线）
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
    with (out_dir / "files.jsonl").open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return records


