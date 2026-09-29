"""Extraction hazards: injection and detection. Which named hazards a file carries is decided only here.

陷阱：注入与认定。一份文件带哪些具名陷阱，**只在这里判定**。

陷阱有三种来路，manifest 里的 `source` 字段照实写：

* `layout`  —— 版式选择的后果。机构的版式把单位粘在值上，这家的每张单子就都带
  `unit.glued_to_value`。但**只有纸上真的印出了对应内容才算**：没有 10^9/L 这类单位的单子，
  版式里的上标写法再特别，也不带 `unit.superscript`。
* `content` —— 内容本身带来的：有定性项就有 `value.non_numeric`，血压印成一格就有
  `value.pair_in_one_cell`。
* `incident` —— 单份文件的偶发事件（某一行名字折行、某一格参考范围被截断），
  按真实语料的**文档率**独立抽取。

每一类在 `STATUS` 里都有登记：`injected / layout / content` 是这一层能造的；
`channel` 是**通道**造成的（OCR 认错字、数字里多出空格），它们在文本层 PDF 里不会自然发生，
硬造就是伪造——留给图像层由劣化自然产生；`deferred` 写明了为什么还没做。
`tests/test_render.py` 检查 spec 里每一个 `generate=true` 的类都有登记，没有一类是被静默丢掉的。
"""

from __future__ import annotations

import random
import re

from . import spec
from .document import Cells, Doc
from .layout import script_of

STATUS: dict[str, tuple[str, str]] = {
    # ── 版式 ──
    "unit.glued_to_value": ("layout", "版式把单位粘在值上"),
    "unit.glued_to_reference": ("layout", "单位印在参考范围格里，空格或 & 相连"),
    "unit.in_header_or_reference_only": ("layout", "单位只在表头"),
    "unit.missing": ("layout", "版式不印单位"),
    "reference.absent": ("layout", "版式没有参考范围列"),
    "table.bilingual_header": ("layout", "第二行英文表头"),
    "reference.separator_dialect": ("layout", "区间分隔符方言"),
    "flag.text_dialect": ("layout", "文字标记"),
    "flag.arrow_glued": ("layout", "箭头粘在数值上"),
    "value.flag_combined_in_cell": ("layout", "值与标记同格"),
    "value.parenthetical": ("layout", "值后括号标记"),
    "unit.superscript": ("layout", "10^9/L 的各种写法"),
    "unit.case_variant": ("layout", "单位大小写"),
    "reference.sex_partitioned": ("layout", "按性别分层的参考范围印在一格"),
    "meta.subject_field_as_indicator": ("layout", "受检者字段印成表格行"),
    "value.multiple_per_row": ("layout", "上次结果列"),
    "table.row_label_as_column": ("layout", "分类列"),
    "meta.date_format_dialect": ("layout", "日期格式方言"),
    "meta.multiple_dates": ("layout", "采样/接收/报告/打印多个日期"),
    "meta.page_furniture": ("layout", "页码、打印信息"),
    "ocr.noise_text": ("layout", "水印与印章。文本层里的水印字就是噪声文本"),
    "value.decimal_comma": ("layout", "逗号小数点"),
    "ocr.punctuation_swap": ("layout", "全角括号"),
    "value.blank_column": ("layout", "整列为空"),
    "table.transposed": ("layout", "转置导出表"),
    # ── 内容 ──
    "value.non_numeric": ("content", "定性结果"),
    "table.two_result_columns": ("layout", "正常值与异常值分列（美式化验单的 In Range / Out Of Range）"),
    "reference.inequality": ("content", "单侧参考范围"),
    "value.pair_in_one_cell": ("content", "血压一格两值"),
    "unit.slash_ambiguous": ("content", "次/分 这类单位"),
    "table.multiple_tables": ("content", "一份文件多张表"),
    "table.page_break_loses_header": ("content", "表格跨页、续页无表头（渲染后认定）"),
    # ── 偶发 ──
    "table.wrapped_cell": ("injected", "名称折行"),
    "reference.split_across_lines": ("injected", "参考范围折行"),
    "value.missing": ("injected", "某一行没有结果；该行进必须弃权集合"),
    "table.truncated": ("injected", "参考范围被截断；该字段移出分母"),
    "table.misalignment": ("injected", "缺一格导致后面的格左移"),
    "meta.mixed_into_rows": ("injected", "日期/机构混进结果行"),
    "table.flag_row_as_data": ("injected", "是否异常 之类的统计行"),
    "meta.narrative_block": ("injected", "叙述段落"),
    "value.duplicated_in_summary": ("injected", "小结里重复表中数值"),
    "ocr.mixed_script": ("injected", "繁简混排"),
    "table.header_ambiguous": ("injected", "某列表头为空"),
    "reference.ambiguous_column": ("injected", "参考范围列的表头像标记列"),
    "flag.column_confusion": ("injected", "标记列为空，标记挤进结果格"),
    "reference.multiple_rows": ("injected", "参考范围分多行"),
    "unit.on_separate_line": ("injected", "单位另起一行（在值或参考范围之后）"),
    # ── 通道（图像层自然产生，文本层不造）──
    "ocr.other": ("channel", "OCR 错误兜底类"),
    "ocr.name_misspelled": ("channel", "OCR 认错指标名"),
    "unit.ocr_corrupted": ("channel", "OCR 改形单位"),
    "reference.garbled": ("channel", "OCR 弄坏参考范围"),
    "value.space_in_number": ("channel", "数值里多出空格"),
    "reference.space_inside_number": ("channel", "参考范围数字里多出空格"),
    "value.missing_decimal_point": ("channel", "小数点丢失。印成错值会让印刷真值与临床真值冲突，"
                                               "需要单独的真值口径，先不造"),
    # ── 延后 ──
    "ocr.chart_annotation": ("deferred", "需要趋势图；体检报告书目前不画图表"),
    "reference.ratio_not_unit": ("deferred", "需要血清学（s/co、index）指标，目录里还没有"),
    "value.contradicts_other_section": ("deferred", "需要小结与表格两套数值，真值口径未定"),
    "value.comparator": ("deferred", "需要各项目的检出限，不同试剂厂家不同，没有可引用的统一出处"),
    "unit.inconsistent_across_sets": ("deferred", "需要同一指标在一份文件里出现两次"),
    "flag.contradicts_reference": ("deferred", "实测文档率为 0"),
    "value.everything_in_one_cell": ("deferred", "实测文档率为 0"),
}


def rate(name: str) -> float:
    for c in spec.hazards()["classes"]:
        if c["name"] == name:
            return c["document_rate"]
    return 0.0


def _reading_rows(doc: Doc) -> list[tuple[int, int, Cells]]:
    """(表序号, 行序号, 成分)，只含真读数行。"""
    return [(t, i, c) for t, table in enumerate(doc.tables)
            for i, c in enumerate(table.rows) if c.printed is not None]


# ── 偶发注入 ─────────────────────────────────────────────────────
def _wrapped_cell(doc: Doc, rng: random.Random) -> list[int]:
    cands = [c for _, _, c in _reading_rows(doc) if len(c.name) >= 5 and "\n" not in c.name]
    out = []
    for c in rng.sample(cands, min(len(cands), rng.randint(1, 2))):
        cut = len(c.name) // 2
        c.name = c.name[:cut] + "\n" + c.name[cut:]
        out.append(c.printed)
    return out


def _split_reference(doc: Doc, rng: random.Random) -> list[int]:
    cands = [c for _, _, c in _reading_rows(doc) if re.search(r"\d[-~—–一]+\d", c.range) and "\n" not in c.range]
    out = []
    for c in rng.sample(cands, min(len(cands), rng.randint(1, 2))):
        m = re.search(r"(\d)([-~—–一]+)(\d)", c.range)
        c.range = c.range[:m.end(2)] + "\n" + c.range[m.end(2):]
        out.append(c.printed)
    return out


def _value_missing(doc: Doc, rng: random.Random) -> list[int]:
    cands = [c for _, _, c in _reading_rows(doc)]
    if len(cands) < 3:
        return []
    c = rng.choice(cands)
    c.value = rng.choice(spec.templates()["missing_values"][_lang(doc)])
    c.flag = ""
    row = doc.printed[c.printed]
    row.readable = False
    return [c.printed]


def _truncated(doc: Doc, rng: random.Random) -> list[int]:
    cands = [c for _, _, c in _reading_rows(doc) if len(c.range) >= 5]
    if not cands:
        return []
    c = rng.choice(cands)
    c.range = c.range[:max(2, int(len(c.range) * 0.6))] + "…"
    row = doc.printed[c.printed]
    row.unreadable_fields.append("item_range")
    if not c.flag and row.is_abnormal != "" and doc.readings[row.readings[0]].value_kind != "qualitative":
        row.unreadable_fields.append("is_abnormal")
    return [c.printed]


def _misalignment(doc: Doc, rng: random.Random) -> list[int]:
    cols = doc.family.columns
    if "unit" not in cols or "reference" not in cols or cols.count("name") > 1:
        return []
    cands = [c for _, _, c in _reading_rows(doc) if c.unit_at == "column" and c.unit]
    if not cands:
        return []
    from .render.grid import cell_text

    c = rng.choice(cands)
    roles = [r for r in cols if r != "seq"]
    texts = [cell_text(c, r) for r in roles if r != "unit"] + [""]
    c.raw = dict(zip(roles, texts))
    doc.printed[c.printed].item_unit = ""
    return [c.printed]


def _mixed_into_rows(doc: Doc, rng: random.Random) -> list[int]:
    if not doc.tables or not doc.dates:
        return []
    table = rng.choice(doc.tables)
    d = rng.choice(doc.dates)
    row = Cells(name="", value="", unit="", range="", flag="", raw={"name": d["label"], "result": d["printed"]})
    table.rows.insert(rng.randint(1, max(1, len(table.rows))), row)
    doc.distractors.append({"kind": "meta_row", "text": f"{d['label']} {d['printed']}"})
    return []


def _flag_row(doc: Doc, rng: random.Random) -> list[int]:
    if not doc.tables:
        return []
    n_abn = sum(1 for p in doc.printed if p.is_abnormal == "1")
    t, lang = spec.templates(), _lang(doc)
    yes, no = t["yes_no"][lang]
    label, value = rng.choice([(t["flag_rows"][lang]["count"], str(n_abn)),
                               (t["flag_rows"][lang]["any"], yes if n_abn else no)])
    doc.tables[-1].rows.append(Cells(name="", value="", unit="", range="", flag="",
                                     raw={"name": label, "result": value}))
    doc.distractors.append({"kind": "flag_row", "text": f"{label} {value}"})
    return []


def _lang(doc: Doc) -> str:
    return doc.family.lang_group


def _narrative(doc: Doc, rng: random.Random) -> list[int]:
    t, lang = spec.templates(), _lang(doc)
    doc.narratives.append((rng.choice(t["narrative_labels"][lang]), rng.choice(t["notes"][lang])))
    return []


def _duplicated_summary(doc: Doc, rng: random.Random) -> list[int]:
    abn = [i for i, p in enumerate(doc.printed) if p.is_abnormal == "1" and p.readable]
    if not abn:
        return []
    lang = _lang(doc)
    words = spec.templates()["summary"][lang]
    parts = []
    for i in abn[:4]:
        p = doc.printed[i]
        word = words["high"] if doc.readings[p.readings[0]].status == "high" else words["low"]
        parts.append(f"{p.item_name} {p.item_value}{(' ' + p.item_unit) if p.item_unit else ''} {word}")
    # 每项一行：同一格里"high"紧挨着下一项的"Total Cholesterol"，会拼出一个真实语料里也有的
    # 英文短语——两个公共词首尾相接，回放检测按设计不豁免（2026-09-23 实测命中）。
    doc.narratives.append((words["label"], "\n".join(parts)))
    doc.distractors.append({"kind": "summary_duplicate", "items": parts})
    return abn[:4]


def _mixed_script(doc: Doc, rng: random.Random) -> list[int]:
    if doc.family.language != "zh-Hans":
        return []
    out = []
    for _, _, c in _reading_rows(doc):
        key = doc.readings[doc.printed[c.printed].readings[0]].key
        trad = [v for v in spec.indicators()[key].get("name_variants") or [] if script_of(v) == "zh-Hant"]
        if trad and len(out) < 3 and rng.random() < 0.6:
            c.name = trad[0]
            doc.printed[c.printed].item_name = trad[0]
            out.append(c.printed)
    return out


def _header_blank(doc: Doc, rng: random.Random) -> list[int]:
    cols = [c for c in doc.family.columns if c in ("unit", "flag", "abbr", "reference", "seq")]
    if not cols or not doc.tables:
        return []
    idx = doc.family.columns.index(rng.choice(cols))
    for table in doc.tables:
        for header in table.headers:
            header[idx] = ""
    return []


def _ambiguous_reference(doc: Doc, rng: random.Random) -> list[int]:
    if "reference" not in doc.family.columns or not doc.tables:
        return []
    idx = doc.family.columns.index("reference")
    word = rng.choice(spec.templates()["ambiguous_reference_headers"][_lang(doc)])
    for table in doc.tables:
        table.headers[0][idx] = word
    return []


def _flag_confusion(doc: Doc, rng: random.Random) -> list[int]:
    if doc.family.flag_at != "column":
        return []
    out = []
    for _, _, c in _reading_rows(doc):
        if c.flag:
            c.flag_at = "spaced"
            if c.flag == doc.family.flag_normal:
                c.flag = ""
                doc.printed[c.printed].is_abnormal = doc.printed[c.printed].is_abnormal \
                    if doc.printed[c.printed].item_range else ""
            out.append(c.printed)
    return out


def _multiple_rows(doc: Doc, rng: random.Random) -> list[int]:
    from .document import print_range

    out = []
    for _, _, c in _reading_rows(doc):
        row = doc.printed[c.printed]
        reading = doc.readings[row.readings[0]]
        ref = spec.indicators()[reading.key]["reference"]
        if ref and ref[0] == "range_sex" and "\n" not in c.range and row.item_range:
            sex = "male" if row.item_range == print_range(reading.key, "male", doc.family)[0] else "female"
            applicable, alts = print_range(reading.key, sex, doc.family, multiline=True)
            c.range = alts[0]
            row.item_range = applicable
            row.alternatives["item_range"] = alts
            out.append(c.printed)
    return out


def _unit_newline(doc: Doc, rng: random.Random) -> list[int]:
    cands = [c for _, _, c in _reading_rows(doc) if c.unit and c.unit_at in ("value", "reference")]
    out = []
    for c in rng.sample(cands, min(len(cands), rng.randint(1, 3))):
        c.unit_newline = True
        out.append(c.printed)
    return out


INCIDENTS = {
    "table.wrapped_cell": _wrapped_cell,
    "reference.split_across_lines": _split_reference,
    "value.missing": _value_missing,
    "table.truncated": _truncated,
    "table.misalignment": _misalignment,
    "meta.mixed_into_rows": _mixed_into_rows,
    "table.flag_row_as_data": _flag_row,
    "meta.narrative_block": _narrative,
    "value.duplicated_in_summary": _duplicated_summary,
    "ocr.mixed_script": _mixed_script,
    "table.header_ambiguous": _header_blank,
    "reference.ambiguous_column": _ambiguous_reference,
    "flag.column_confusion": _flag_confusion,
    "reference.multiple_rows": _multiple_rows,
    "unit.on_separate_line": _unit_newline,
}


def inject(doc: Doc, rng: random.Random, only: list[str] | None = None) -> None:
    """按真实文档率独立抽取偶发陷阱。`only` 给定时只注入这几类（最小对照对用），且必定尝试。"""
    names = only if only is not None else sorted(INCIDENTS)
    for name in names:
        if only is None and rng.random() >= rate(name):
            continue
        before = len(doc.distractors) + len(doc.narratives)
        rows = INCIDENTS[name](doc, rng)
        if rows or len(doc.distractors) + len(doc.narratives) > before or name in (
                "table.header_ambiguous", "reference.ambiguous_column") and _applied_header(doc, name):
            doc.mark(name, "incident", rows)


def _applied_header(doc: Doc, name: str) -> bool:
    cols = doc.family.columns
    if name == "reference.ambiguous_column":
        return "reference" in cols and bool(doc.tables)
    return any(c in cols for c in ("unit", "flag", "abbr", "reference", "seq")) and bool(doc.tables)


# ── 版式与内容陷阱的认定 ─────────────────────────────────────────
_ARROWS = ("↑", "↓", "↑↑", "↓↓")
_POWER = re.compile(r"\^|×|\*|E\d|⁹|¹²|^[GT]/L$")


def detect(doc: Doc) -> None:
    f = doc.family
    rows = _reading_rows(doc)
    hit: dict[str, list[int]] = {}

    def add(name: str, idx: int | None = None) -> None:
        hit.setdefault(name, [])
        if idx is not None:
            hit[name].append(idx)

    for _, _, c in rows:
        p = doc.printed[c.printed]
        reading = doc.readings[p.readings[0]]
        if c.unit and c.unit_at == "value" and not c.unit[0].isdigit():
            add("unit.glued_to_value", c.printed)
        # 两类要分开：`&` 相连是"粘连"（glued_to_reference 的描述："常见 '&' 作分隔"）；
        # 空格相连、结果格里没有单位，是"单位只在参考范围里"（in_header_or_reference_only）。
        if c.unit and c.unit_at == "reference_amp":
            add("unit.glued_to_reference", c.printed)
        if c.unit and c.unit_at in ("reference", "header"):
            add("unit.in_header_or_reference_only", c.printed)
        if f.unit_at == "none" and reading.unit_ucum:
            add("unit.missing", c.printed)
        if "reference" not in f.columns and spec.indicators()[reading.key]["reference"]:
            add("reference.absent", c.printed)
        if re.search(r"\d\s*[-~—–一～]+\s*\d", c.range) and \
                f.reference_dialect.replace("{lo}", "").replace("{hi}", "") != "-":
            add("reference.separator_dialect", c.printed)
        if c.flag and c.flag not in _ARROWS:
            add("flag.text_dialect", c.printed)
        if c.flag and c.flag_at == "glued":
            add("flag.arrow_glued" if c.flag in _ARROWS else "value.flag_combined_in_cell", c.printed)
        if c.flag and c.flag_at == "spaced":
            add("value.flag_combined_in_cell", c.printed)
        if c.flag and c.flag_at == "paren":
            add("value.parenthetical", c.printed)
        if c.unit and (c.unit_at != "none") and _POWER.search(c.unit):
            add("unit.superscript", c.printed)
        if c.unit and c.unit_at != "none" and f.unit_case == "lower" and re.search(r"fl$|/l$", c.unit):
            add("unit.case_variant", c.printed)
        if p.alternatives.get("item_range") and f.sex_partitioned:
            add("reference.sex_partitioned", c.printed)
        if c.previous:
            add("value.multiple_per_row", c.printed)
        if reading.value_kind != "quantitative":
            add("value.non_numeric", c.printed)
        if re.match(r"^\(?\s*[<>≤≥]", c.range):
            add("reference.inequality", c.printed)
        if len([r for r in p.readings if doc.readings[r].role == "current"]) > 1:
            add("value.pair_in_one_cell", c.printed)
        if c.unit and ("次/分" in c.unit or "/HP" in c.unit or "/min" in c.unit):
            add("unit.slash_ambiguous", c.printed)
        if f.decimal_comma and re.fullmatch(r"-?\d+,\d+", c.value):
            add("value.decimal_comma", c.printed)
        if "（" in c.name:
            add("ocr.punctuation_swap", c.printed)
    if any(c.raw is not None and c.printed is None for t in doc.tables for c in t.rows) and \
            any(d["kind"] == "subject_field" for d in doc.distractors):
        add("meta.subject_field_as_indicator")
    if f.bilingual_header and doc.tables:
        add("table.bilingual_header")
    if "category" in f.columns and rows:
        add("table.row_label_as_column")
    if "result_out" in f.columns and rows:
        add("table.two_result_columns")
    if any(c in ("note", "method") for c in f.columns) and rows:
        add("value.blank_column")
    if len(doc.tables) > 1 or f.columns.count("name") > 1:
        add("table.multiple_tables")
    if f.date_format not in ("YYYY-MM-DD", "YYYY-MM-DD HH:mm:ss", "YYYY-MM-DD HH:mm") and doc.dates:
        add("meta.date_format_dialect")
    if len(doc.dates) >= 3:
        add("meta.multiple_dates")
    if f.watermark:
        add("ocr.noise_text")
    if doc.kind == "export":
        add("table.transposed")
    for name, idxs in hit.items():
        source = STATUS[name][0]
        doc.mark(name, source, sorted(set(idxs)))
