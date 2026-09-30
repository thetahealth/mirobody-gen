"""The internal model of one file and its two truth layers (printed and semantic).

一份文件的内部模型，以及它的两层真值。

## 两层真值（这是整套语料能同时考 collect 与 translate 的原因）

* **印刷真值**（`PrintedRow`）：纸上这一行印了什么——名称、值、单位、参考范围、异常标记，
  五个字段与 MedRepBench 的客观赛道同名同义（`item_name / item_value / item_unit /
  item_range / is_abnormal`）。它回答"读得对不对"。
* **语义真值**（`DocReading`）：这一行**意味着**什么——指标键、LOINC、临床数值、UCUM 单位、
  观测日期。它回答"读出来之后，落到标准编码上对不对"。

两层之间不是一一对应：一格 `128/82` 是一个印刷行、两个读数（收缩压、舒张压）；
一个"上次结果"列让一个印刷行带出一个更早日期的读数；转置导出表的一个单元格是一个读数。
MedRepBench 只有第一层（逐行标注印刷内容，OCR 辅助构建、人工核对；是否逐字转写论文未写明），所以它考不了术语层——
这一点用它自己的真实指标名量过：mirobody 的解析器只覆盖约一半（PAPER §9 第 8 条）。

## 印刷真值的约定（评分口径的根）

* `item_name`：名称列这一格印的字，去掉换行。缩写另起一列时不并入。
* `item_value`：值本身。**不含**粘上去的单位、箭头、括号标记；保留比较符与逗号小数点（原样）。
* `item_unit`：这一行适用的单位，无论印在单位列、值格、参考范围格还是表头；哪里都没印就是 `""`。
* `item_range`：适用于这位受检者的那一段参考范围，不含粘上去的单位。按性别分行印的，
  取本人性别那一段；整格原文放进 `alternatives`。
* `is_abnormal`：`"1"` 异常、`"0"` 正常、`""` **无法判定**。无法判定 = 数值型、没印参考范围、
  也没印标记。这与 MedRepBench 官方提示词文件（`prompts/objective_extraction_prompt.md`，不是论文正文）
  对该字段的定义一致；在它的真实标注上，
  这条规则比"没范围就空"更贴合标注习惯（92.3% vs 88.2%，见 docs/handoff 评测计划 §S4）。

`readable=False` 的行进"必须弃权"集合，不进召回分母（docs/zh-CN/plan.md §3.6.2 第 1 条）。
字段级不可读（参考范围被截断）记在 `unreadable_fields`，只把那个字段移出分母。
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import spec, synthid
from .layout import Family, date_role, script_of
from .model import Encounter, Person, Reading

def _t() -> dict:
    return spec.templates()


def panel_title(group: str, en: bool) -> str:
    pair = _t()["panel_titles"].get(group)
    return pair[1 if en else 0] if pair else group


#: 一个读数印在哪张表里，按这个顺序找它属于哪个医嘱组；表也按这个顺序排。
#: 列的是每类表**最全**的那个医嘱组（肝功能 14 项那版），这样基础套餐的 6 项肝功能和深度套餐的
#: 14 项都落在同一张"肝功能"表里；不在任何组里的按目录套餐名归表（`group_of`）。
GROUP_ORDER = ["vitals", "body_composition", "cbc", "blood_type", "anemia", "coagulation_full", "liver_full",
               "renal", "renal_early", "electrolyte_full", "pancreas", "lipid_full", "glucose_full", "thyroid_full",
               "urinalysis", "stool", "inflammation", "immune", "hepatitis_full", "hp", "tumor_full", "cervical",
               "vitamin", "cardiac_full", "ecg", "spirometry", "arterial", "echo"]


def group_of(key: str) -> str:
    orders = spec.cohort()["orders"]
    for g in GROUP_ORDER:
        if key in orders.get(g, []):
            return g
    panel = spec.indicators()[key]["panel"]
    return next((g for g in GROUP_ORDER if g == panel or g.startswith(panel + "_")), "other")


@dataclass
class PrintedRow:
    item_name: str
    item_value: str
    item_unit: str
    item_range: str
    is_abnormal: str
    readings: list[int] = field(default_factory=list)
    alternatives: dict[str, list[str]] = field(default_factory=dict)
    readable: bool = True
    unreadable_fields: list[str] = field(default_factory=list)
    hazards: list[str] = field(default_factory=list)
    table: int = 0


@dataclass
class DocReading:
    key: str
    loinc: str | None
    canonical_value: float | None
    value_text: str
    unit_ucum: str
    value_kind: str
    status: str
    observed: str
    role: str = "current"              # current / previous / export
    expect_resolvable: bool = False
    printed_row: int | None = None


@dataclass
class Cells:
    """一行的印刷成分。表格序列化时按版式把它们拼进各列。"""
    name: str
    value: str
    unit: str
    range: str
    flag: str
    abbr: str = ""
    previous: str = ""
    category: str = ""
    unit_at: str = "column"
    flag_at: str = "column"
    unit_newline: bool = False          # 单位另起一行（unit.on_separate_line）
    status: str = ""                    # normal / high / low：两列结果版式据此决定值印在哪一列
    printed: int | None = None          # 指向 PrintedRow；None 表示干扰行
    raw: dict[str, str] | None = None   # 干扰行直接给出各列文本


@dataclass
class Table:
    columns: list[str]
    headers: list[list[str]]
    rows: list[Cells]
    caption: str = ""


@dataclass
class Doc:
    doc_id: str
    person_id: str
    family: Family
    title: str
    subject: list[tuple[str, str]]
    dates: list[dict]
    tables: list[Table]
    narratives: list[tuple[str, str]] = field(default_factory=list)
    footer: list[tuple[str, str]] = field(default_factory=list)
    printed: list[PrintedRow] = field(default_factory=list)
    readings: list[DocReading] = field(default_factory=list)
    distractors: list[dict] = field(default_factory=list)
    hazards: dict[str, dict] = field(default_factory=dict)
    jitter: list[str] = field(default_factory=list)
    banner: bool = True
    kind: str = "lab_slip"              # lab_slip / export / checkup_book / outpatient_record / ecg_report / ultrasound_report / imaging_report / home_log
    #: 检验表格之外的内容块（科室键值对、辅助检查叙述、总检），见 book.py。
    blocks: list = field(default_factory=list)
    cover: dict | None = None
    findings_truth: list[dict] = field(default_factory=list)
    summary_truth: list[dict] = field(default_factory=list)
    complaints_truth: list[dict] = field(default_factory=list)
    diagnoses_truth: list[dict] = field(default_factory=list)

    def mark(self, name: str, source: str, rows: list[int] | None = None) -> None:
        entry = self.hazards.setdefault(name, {"source": source, "rows": []})
        for r in rows or []:
            if r not in entry["rows"]:
                entry["rows"].append(r)
                if name not in self.printed[r].hazards:
                    self.printed[r].hazards.append(name)


# ── 印刷写法 ─────────────────────────────────────────────────────
def _paren(text: str, f: Family) -> str:
    return f"（{text}）" if f.paren_style == "fullwidth" else f"({text})"


def _variant_for(item: dict, script: str, rng: random.Random) -> str | None:
    pool = [v for v in item.get("name_variants") or [] if script_of(v) == script]
    return rng.choice(pool) if pool else None


def print_name(item: dict, f: Family, rng: random.Random) -> tuple[str, str]:
    """(名称列文字, 缩写列文字)。同一机构同一指标总是同一个写法——一个 LIS 只有一份字典。"""
    sticky = random.Random(f"name:{f.family_id}:{item['key']}")
    if f.language == "en":
        native = item["en"]
    elif f.language == "zh-Hant":
        native = _variant_for(item, "zh-Hant", sticky) or item["zh"]
    else:
        native = item["zh"]
    abbr = item.get("abbr") or ""
    style = f.name_style
    if style == "variant":
        native = _variant_for(item, script_of(native), sticky) or native
    if style == "abbr" and abbr and "abbr" not in f.columns:
        return abbr, ""
    if style == "native(abbr)" and abbr and "abbr" not in f.columns:
        return native + _paren(abbr, f), ""
    return native, abbr


def print_unit(unit: str, f: Family) -> str:
    if not unit:
        return ""
    unit = _t()["unit_localization"].get(f.lang_group, {}).get(unit, unit)
    powers = _t()["power_styles"]
    if unit in ("10^9/L", "10^12/L"):
        unit = powers[unit][f.power_style]
    if f.unit_case == "lower":
        unit = unit.replace("fL", "fl").replace("mmol/L", "mmol/l").replace("umol/L", "umol/l")
        unit = unit.replace("μmol/L", "μmol/l").replace("g/L", "g/l").replace("U/L", "U/l")
    return unit


def _fmt(x: float, decimals: int) -> str:
    return f"{x:.{decimals}f}" if decimals else f"{x:g}"


def print_range(key: str, sex: str, f: Family, multiline: bool = False) -> tuple[str, list[str]]:
    """(适用于本人的那段, 整格原文的备选写法)。"""
    item = spec.indicators()[key]
    ref = item["reference"]
    if not ref:
        return "", []
    kind, dec = ref[0], item["decimals"]
    if kind == "qualitative":
        return str(ref[1]), []
    if kind == "range_sex" and f.sex_partitioned or (kind == "range_sex" and multiline):
        (ml, mh), (fl, fh) = ref[1], ref[2]
        m = f.reference_dialect.format(lo=_fmt(ml, dec), hi=_fmt(mh, dec))
        w = f.reference_dialect.format(lo=_fmt(fl, dec), hi=_fmt(fh, dec))
        labels = _t()["sex_short"][f.lang_group]
        sep = "\n" if multiline else " "
        full = f"{labels[0]}:{m}{sep}{labels[1]}:{w}"
        return (m if sex == "male" else w), [full, full.replace("\n", " ")]
    lo, hi = spec.reference_bounds(key, sex)
    if kind == "upper":
        return f.upper_dialect.format(hi=_fmt(hi, dec)), []
    if kind == "lower":
        return f.lower_dialect.format(lo=_fmt(lo, dec)), []
    return f.reference_dialect.format(lo=_fmt(lo, dec), hi=_fmt(hi, dec)), []


def print_flag(status: str, f: Family) -> str:
    return {"high": f.flag_high, "low": f.flag_low}.get(status, f.flag_normal if f.flag_at == "column" else "")


def print_value(value: str, f: Family) -> str:
    """The value as this institution prints it: decimal comma where the family uses it, and qualitative
    results in the document's language (an English report does not print 阳性)."""
    value = _t().get("value_localization", {}).get(f.lang_group, {}).get(value, value)
    return value.replace(".", ",") if f.decimal_comma and re.fullmatch(r"-?\d+\.\d+", value) else value


def determinable(value_kind: str, printed_range: str, printed_flag: str) -> bool:
    return value_kind in ("qualitative", "categorical") or bool(printed_range) or bool(printed_flag)


# ── 日期 ────────────────────────────────────────────────────────
def format_date(when: datetime, pattern: str) -> str:
    out = pattern
    for token, value in (("YYYY", f"{when.year:04d}"), ("YY", f"{when.year % 100:02d}"),
                         ("MM", f"{when.month:02d}"), ("DD", f"{when.day:02d}"),
                         ("HH", f"{when.hour:02d}"), ("mm", f"{when.minute:02d}"),
                         ("ss", f"{when.second:02d}")):
        out = out.replace(token, value)
    out = out.replace("M月", f"{when.month}月").replace("D日", f"{when.day}日")
    out = out.replace("/M/", f"/{when.month}/").replace("/D ", f"/{when.day} ")
    return out


def print_dates(rng: random.Random, f: Family, collected: date) -> list[dict]:
    base = datetime(collected.year, collected.month, collected.day,
                    rng.randint(7, 10), rng.randint(0, 59), rng.randint(0, 59))
    offsets = {"collected": timedelta(0), "received": timedelta(minutes=rng.randint(20, 120)),
               "tested": timedelta(hours=rng.randint(1, 5)),
               "verified": timedelta(hours=rng.randint(3, 20)),
               "reported": timedelta(hours=rng.randint(3, 30)),
               "printed": timedelta(days=rng.randint(0, 20), hours=rng.randint(1, 8))}
    out = []
    for label in f.date_labels:
        role = date_role(label)
        when = base + offsets[role]
        out.append({"label": label, "role": role, "printed": format_date(when, f.date_format),
                    "iso": when.isoformat(timespec="seconds")})
    return out


# ── 受检者字段 ───────────────────────────────────────────────────
def subject_fields(rng: random.Random, person: Person, f: Family, collected: date,
                   department: str, specimen: str) -> list[tuple[str, str]]:
    fiction = spec.fiction()
    sticky = random.Random(f"patient:{person.person_id}")
    en = f.language == "en"
    lang = "en" if en else "zh"
    t, labels = _t(), _t()["subject_labels"][lang]
    name = sticky.choice(fiction["person_names_en" if en else "person_names_zh"])
    doctor = rng.choice(fiction["person_names_en" if en else "person_names_zh"])
    sex = t["sex_words"][lang][person.sex]
    age = t["age_format"][lang].format(n=person.age_at(collected))
    pid = synthid.make(random.Random(f"pid:{f.family_id}:{person.person_id}"), 10)
    lab_no = synthid.make(rng, 9)
    specimen = specimen or rng.choice(t["specimens"][lang])
    fields = [(labels["name"], name), (labels["sex"], sex), (labels["age"], age),
              (rng.choice(labels["pid"]), pid), (labels["lab_no"], lab_no)]
    if "department" in labels:
        fields.append((labels["department"], department))
    fields += [(labels["specimen"], specimen), (labels["doctor"], doctor)]
    keep = rng.randint(5, len(fields))
    return fields[:keep]


# ── 文件拆分 ─────────────────────────────────────────────────────
def split_encounter(rng: random.Random, encounter: Encounter) -> list[list[str]]:
    """一次就诊 → 若干张单子（每张单子是若干"组"）。体检拆得最细，复查常合在一张上。"""
    groups: dict[str, list[Reading]] = {}
    for reading in encounter.readings:
        groups.setdefault(group_of(reading.key), []).append(reading)
    names = [g for g in GROUP_ORDER if g in groups] + (["other"] if "other" in groups else [])
    slips: list[list[str]] = []
    for g in names:
        # 小组有一定概率并进上一张单子（"生化全项"、"血糖血脂"）
        if slips and len(groups[g]) <= 12 and rng.random() < 0.45 and g not in ("vitals", "ecg"):
            slips[-1].append(g)
        else:
            slips.append([g])
    return slips


def build_doc(rng: random.Random, doc_id: str, person: Person, encounter: Encounter,
              groups: list[str], family: Family, previous: dict[str, tuple[str, str]],
              banner: bool = True, book: bool = False) -> Doc:
    """把一张单子的读数按版式排成一份文件。`previous` 是 key → (上次的值, 上次日期)。

    `book=True` 是体检报告：所有组各成一张带小标题的表，排成一份多页文件
    （参考集里体检报告书是最大的一类文档）。完整的报告书（科室键值对、辅助检查叙述、总检）由 book.py 在这之上组装。"""
    f = family
    en = f.language == "en"
    catalogue = spec.indicators()
    readings = [r for r in encounter.readings if group_of(r.key) in groups]
    main = groups[0]
    t = _t()
    lang = "en" if en else "zh"
    titles = [panel_title(g, en) for g in groups]
    title = rng.choice(t["doc_titles"][lang]).format(t=titles[0] if en else "".join(titles[:2]))
    if book:
        title = rng.choice(t["book_titles"][lang])
    specimen = t["specimens"]["urine"][1 if en else 0] if main == "urinalysis" else ""
    department = rng.choice(t["departments"][lang])
    doc = Doc(doc_id=doc_id, person_id=person.person_id, family=f, title=title,
              subject=subject_fields(rng, person, f, encounter.exam_date, department, specimen),
              dates=print_dates(rng, f, encounter.exam_date), tables=[], banner=banner)

    unit_at = f.unit_at
    if unit_at == "header" and len({r.unit for r in readings}) != 1:
        unit_at = "reference" if "reference" in f.columns else "value"
    if unit_at in ("reference", "reference_amp") and "reference" not in f.columns:
        unit_at = "value"
    has_ref_col = "reference" in f.columns
    observed = encounter.exam_date.isoformat()

    split_tables = len(groups) > 1 and (book or rng.random() < 0.5) and f.columns.count("name") == 1
    table_groups = [[g] for g in groups] if split_tables else [groups]
    bp_pair = {"sbp", "dbp"} <= {r.key for r in readings} and rng.random() < 0.6

    for t_index, tgroups in enumerate(table_groups):
        rows: list[Cells] = []
        members = [r for r in readings if group_of(r.key) in tgroups]
        for reading in members:
            if bp_pair and reading.key == "dbp":
                continue
            item = catalogue[reading.key]
            name, abbr = print_name(item, f, rng)
            unit = print_unit(reading.unit, f)
            range_text, alts = print_range(reading.key, person.sex, f)
            value = print_value(reading.value, f)
            status = reading.status
            group_readings = [reading]
            if bp_pair and reading.key == "sbp":
                dbp = next(r for r in members if r.key == "dbp")
                name, abbr = t["bp"][lang], (t["bp"]["abbr"] if abbr else "")
                value = f"{reading.value}/{dbp.value}"
                range_text, alts = t["bp"]["reference"], []
                status = "high" if "high" in (reading.status, dbp.status) else "normal"
                group_readings = [reading, dbp]
            flag = print_flag(status, f)
            group = group_of(reading.key)

            # ── 印刷真值：只算纸上**看得见**的成分 ──
            printed_range = range_text if has_ref_col else ""
            unit_visible = bool(unit) and (unit_at in ("value", "header") or
                                           (unit_at == "column" and "unit" in f.columns) or
                                           (unit_at in ("reference", "reference_amp") and has_ref_col))
            printed_unit = unit if unit_visible else ""
            printed_flag = flag if (f.flag_at == "column" or (f.flag_at in ("glued", "spaced", "paren")
                                                              and status != "normal")) else ""
            # 美式两列结果（In Range / Out Of Range）：值印在哪一列就是标记
            two_columns = "result_out" in f.columns
            flag_code = ("1" if status != "normal" else "0") \
                if determinable(reading.value_kind, printed_range, printed_flag) or two_columns else ""
            p_index = len(doc.printed)
            doc.printed.append(PrintedRow(
                item_name=name, item_value=value, item_unit=printed_unit, item_range=printed_range,
                is_abnormal=flag_code, table=t_index,
                alternatives={"item_range": alts} if alts and printed_range else {}))

            # ── 语义真值 ──
            for k in group_readings:
                doc.printed[p_index].readings.append(len(doc.readings))
                doc.readings.append(DocReading(
                    key=k.key, loinc=k.loinc, canonical_value=k.canonical_value, value_text=k.value,
                    unit_ucum=k.unit_ucum, value_kind=k.value_kind, status=k.status, observed=observed,
                    expect_resolvable=k.expect_resolvable, printed_row=p_index))
            prev = ""
            if f.previous_column and reading.key in previous and len(group_readings) == 1:
                prev_value, prev_date = previous[reading.key]
                prev = print_value(prev_value, f)
                doc.readings.append(DocReading(
                    key=reading.key, loinc=reading.loinc,
                    canonical_value=float(prev_value) if re.fullmatch(r"-?\d+(\.\d+)?", prev_value) else None,
                    value_text=prev_value, unit_ucum=reading.unit_ucum, value_kind=reading.value_kind,
                    status="", observed=prev_date, role="previous",
                    expect_resolvable=reading.expect_resolvable, printed_row=p_index))
            rows.append(Cells(name=name, value=value, unit=unit,
                              range=((alts[0] if alts else range_text) if has_ref_col else ""),
                              flag=printed_flag, abbr=abbr, previous=prev,
                              category=panel_title(group, en),
                              unit_at=unit_at if unit_visible else "none", flag_at=f.flag_at,
                              status=status, printed=p_index))
        headers = [[f.headers.get(c, c) for c in f.columns]]
        if unit_at == "header" and rows:
            idx = f.columns.index("result")
            headers[0][idx] = f"{headers[0][idx]}({rows[0].unit})"
        if f.bilingual_header:
            headers.append([f.header_en.get(c, c) for c in f.columns])
        caption = panel_title(tgroups[0], en) if split_tables else ""
        doc.tables.append(Table(columns=list(f.columns), headers=headers, rows=rows, caption=caption))

    if f.subject_in_table and doc.tables:
        lead = []
        for label, value in doc.subject[:rng.randint(2, 4)]:
            lead.append(Cells(name="", value="", unit="", range="", flag="", printed=None,
                              raw={"name": label, "result": value}))
            doc.distractors.append({"kind": "subject_field", "text": f"{label} {value}"})
        doc.tables[0].rows[:0] = lead

    doc.footer = [(role, rng.choice(spec.fiction()["person_names_en" if en else "person_names_zh"]))
                  for role in f.signatures]
    if "disclaimer" in f.furniture:
        doc.footer.append(("", rng.choice(t["disclaimers"][lang])))
    return doc
