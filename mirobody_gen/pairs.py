"""Minimal-contrast pairs: the same visit rendered clean, then with one hazard class at a time.

最小对照对：同一次就诊、同一套内容，先按一个**干净版式**排一份，再每次只打开**一类**陷阱排一份。

两份的差别只有那一类陷阱，所以两份文件上抽取结果的差就是这类陷阱的**因果效应**，而不是和版式里
其他十几种东西缠在一起的相关。这是 CheckList [ribeiro2020beyond] 的不变性测试（INV）搬到文档上。
有一处要小心：在印刷真值那一层，有的陷阱会合法地改变正确答案（`unit.missing` 之后单位本来就不该被抽出来）；
不变的是语义真值那一层。所以对照对的评分以语义层为准——这正是两层真值的用处。

对齐视图（`view:<场景>`）是 PureDocBench 的 Clean / Digital Degraded / Real Degraded 三视图搬到这里：
同一份 base 只换交付形态，真值一字不变。
"""

from __future__ import annotations

import copy
import dataclasses
import json
import pathlib
import random

from . import hazards, layout, spec
from .corpus import Institutions, home_institutions, record
from .delivery import render_doc
from .document import Doc, build_doc, split_encounter
from .model import Encounter, Person
from .render import degrade


def clean_family(f: layout.Family) -> layout.Family:
    """把一个机构的版式"洗干净"：所有版式类陷阱都关掉，列头写法保留该机构的语言。"""
    en = f.language == "en"
    cols = ("seq", "name", "result", "unit", "reference", "flag")
    headers = {r: (layout.spellings(r, f.language) or [r])[0] for r in cols}
    return dataclasses.replace(
        f, columns=cols, headers=headers, bilingual_header=False,
        reference_dialect="{lo}-{hi}", upper_dialect="<{hi}", lower_dialect=">{lo}",
        flag_high="↑", flag_low="↓", flag_normal="", flag_at="column", unit_at="column",
        unit_case="as_is", power_style="10^9/L", name_style="native", paren_style="ascii",
        sex_partitioned=False, subject_in_table=False, previous_column=False,
        date_labels=f.date_labels[:1] or (("Reported",) if en else ("报告时间",)),
        date_format="YYYY-MM-DD HH:mm", furniture=(), watermark=False, decimal_comma=False,
        fmt="pdf", rules="grid")


def _drop(cols: tuple[str, ...], role: str) -> tuple[str, ...]:
    return tuple(c for c in cols if c != role)


#: 版式类陷阱 → 在干净版式上只打开这一类需要改什么。
TOGGLES = {
    "unit.glued_to_value": lambda f: dict(unit_at="value", columns=_drop(f.columns, "unit")),
    "unit.glued_to_reference": lambda f: dict(unit_at="reference_amp", columns=_drop(f.columns, "unit")),
    "unit.missing": lambda f: dict(unit_at="none", columns=_drop(f.columns, "unit")),
    "reference.absent": lambda f: dict(columns=_drop(f.columns, "reference")),
    "table.bilingual_header": lambda f: dict(bilingual_header=f.language != "en"),
    "reference.separator_dialect": lambda f: dict(reference_dialect="{lo}--{hi}"),
    "flag.text_dialect": lambda f: dict(flag_high="High" if f.language == "en" else "偏高",
                                        flag_low="Low" if f.language == "en" else "偏低"),
    "flag.arrow_glued": lambda f: dict(flag_at="glued", columns=_drop(f.columns, "flag")),
    "value.flag_combined_in_cell": lambda f: dict(flag_at="spaced", flag_high="H", flag_low="L",
                                                  columns=_drop(f.columns, "flag")),
    "value.parenthetical": lambda f: dict(flag_at="paren", flag_high="H", flag_low="L",
                                          columns=_drop(f.columns, "flag")),
    "unit.superscript": lambda f: dict(power_style="×10^9/L"),
    "unit.case_variant": lambda f: dict(unit_case="lower"),
    "reference.sex_partitioned": lambda f: dict(sex_partitioned=True),
    "meta.subject_field_as_indicator": lambda f: dict(subject_in_table=True),
    "value.multiple_per_row": lambda f: dict(previous_column=True, columns=f.columns[:3] + ("previous",) + f.columns[3:],
                                             headers={**f.headers, "previous": spec.templates()["previous_header"][f.language]}),
    "table.row_label_as_column": lambda f: dict(columns=("category",) + f.columns,
                                                headers={**f.headers, "category": (layout.spellings("category", f.language) or ["category"])[0]}),
    "meta.date_format_dialect": lambda f: dict(date_format="YYYYMMDD"),
    "meta.multiple_dates": lambda f: dict(date_labels=tuple(
        spec.templates()["multiple_dates"][f.lang_group])),
    "ocr.noise_text": lambda f: dict(watermark=True),
    "value.blank_column": lambda f: dict(columns=f.columns + ("note",),
                                         headers={**f.headers, "note": (layout.spellings("note", f.language) or ["note"])[0]}),
    "value.decimal_comma": lambda f: dict(decimal_comma=f.language == "en"),
}


def render_pairs(seed: int, people: list[Person], encounters: dict[str, list[Encounter]],
                 out_dir: pathlib.Path, n_pairs: int, banner: bool = True) -> list[dict]:
    registry = layout.build_registry(seed)
    process = Institutions(registry, seed)
    pool = [(p, e) for p in people for e in encounters[p.person_id]
            if e.exam_type != "routine" and len(e.readings) >= 5]
    pick = random.Random(f"pairs:{seed}")
    chosen = pick.sample(pool, min(n_pairs, len(pool)))
    root = out_dir / "pairs"
    records: list[dict] = []
    for k, (person, enc) in enumerate(chosen):
        _, home = home_institutions(seed, person, process)
        base_family = clean_family(registry.family(home["hospital"]))
        pair_id = f"pair{k:03d}"
        groups = [g for s in split_encounter(random.Random(f"{pair_id}:split"), enc) for g in s]
        # 上一次就诊的数值：只有"上次结果"列那一类变体会把它印出来，其余变体里它不出现。
        earlier = [e for e in encounters[person.person_id] if e.exam_date < enc.exam_date]
        previous = {r.key: (r.value, e.exam_date.isoformat()) for e in earlier for r in e.readings}

        def build(family: layout.Family) -> Doc:
            doc = build_doc(random.Random(f"{pair_id}:doc"), f"{pair_id}", person, enc, groups,
                            family, previous, banner=banner)
            return doc

        base = build(base_family)
        variants: list[tuple[str, Doc]] = [("base", base)]
        for name, toggle in TOGGLES.items():
            fam = dataclasses.replace(base_family, **toggle(base_family))
            if fam == base_family:
                continue
            variants.append((name, build(fam)))
        for name in sorted(hazards.INCIDENTS):
            doc = copy.deepcopy(base)
            hazards.inject(doc, random.Random(f"{pair_id}:{name}"), only=[name])
            if name in doc.hazards:
                variants.append((name, doc))
        for name, doc in variants:
            doc.doc_id = f"{pair_id}_{name}"
            hazards.detect(doc)
            if name != "base" and name not in doc.hazards:
                continue                                   # 这份内容上这类陷阱没有着力点
            path, pages, delivery = render_doc(doc, root, f"{pair_id}/{name}")
            records.append(record(doc, path, out_dir, pages, [enc],
                                  {"pair": {"pair_id": pair_id, "variant": name}}, delivery=delivery))
        # 对齐视图：同一份 base，只换交付形态。真值一个字不变，差的只有像素——
        # 这是 PureDocBench 的 Clean / Digital Degraded / Real Degraded 三视图搬到这里。
        for scene in ("flatbed_scan", "app_enhanced", "phone_flat_top", "phone_creased", "photocopy", "screenshot"):
            doc = copy.deepcopy(base)
            doc.doc_id = f"{pair_id}_view_{scene}"
            hazards.detect(doc)
            delivery = {"tier": degrade.TIER_OF_SCENE[scene], "scene": scene, "severity": "moderate", "ops": [],
                        "container": "png" if scene == "screenshot" else "jpg", "annotations": []}
            path, pages, delivery = render_doc(doc, root, f"{pair_id}/view_{scene}", delivery=delivery)
            records.append(record(doc, path, out_dir, pages, [enc],
                                  {"pair": {"pair_id": pair_id, "variant": f"view:{scene}"}}, delivery=delivery))
    with (out_dir / "pairs.jsonl").open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return records
