"""The internal model of one file and its two truth layers (printed and semantic).

## Two truth layers (why this corpus can test both collect and translate)

* **Printed truth** (`PrintedRow`): what this row prints on paper — name, value, unit, reference
  range, abnormal flag. The five fields share their names and meaning with MedRepBench's objective
  track (`item_name / item_value / item_unit / item_range / is_abnormal`). It answers "was this read
  correctly?"
* **Semantic truth** (`DocReading`): what this row **means** — indicator key, LOINC, clinical value,
  UCUM unit, observation date. It answers "once read, does it land on the right standard code?"

The two layers are not one-to-one: a single `128/82` cell is one printed row but two readings
(systolic, diastolic); a "previous result" column lets one printed row carry a reading dated earlier;
one cell of a transposed export table is one reading. MedRepBench has only the first layer (printed
content annotated row by row, OCR-assisted and human-checked; whether it was transcribed verbatim is
not documented), so it cannot test the terminology layer — measured against its own real indicator
names, mirobody's parser covers only about half of them (PAPER §9, item 8).

## Printed-truth conventions (the basis for scoring)

* `item_name`: the text printed in the name cell, with line breaks removed. An abbreviation in its
  own column is not folded in.
* `item_value`: the value itself. **Excludes** a glued-on unit, arrow or parenthetical flag; keeps
  comparison operators and the decimal comma as printed.
* `item_unit`: the unit that applies to this row, wherever it's printed — unit column, value cell,
  reference-range cell or header; `""` if printed nowhere.
* `item_range`: the reference-range segment that applies to this subject, without a glued-on unit.
  When printed as separate rows by sex, the subject's own sex is taken; the whole cell's original text
  goes into `alternatives`.
* `is_abnormal`: `"1"` abnormal, `"0"` normal, `""` **indeterminate**. Indeterminate = quantitative,
  no printed reference range, and no printed flag. This matches MedRepBench's official prompt file's
  definition of the field (`prompts/objective_extraction_prompt.md`, not the paper body); against its
  real annotations, this rule matches annotator behavior better than "blank when there's no range"
  (92.3% vs 88.2%; see the handoff evaluation plan, §S4).

Rows with `readable=False` join the "must abstain" set and are excluded from the recall denominator
(docs/zh-CN/plan.md §3.6.2, item 1). A field that is unreadable on its own (e.g. a truncated reference
range) is recorded in `unreadable_fields`, which drops only that field from the denominator.
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


#: Which table a reading belongs to is found by walking this order; tables are laid out in this order
#: too. Each entry is the **fullest** panel of its table (the 14-item liver-function version), so a
#: basic package's 6 liver-function items and a deep package's 14 both land on the same "liver
#: function" table; a key in no entry is tabled under its catalogue panel name (`group_of`).
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
    """One row's printed components. When a table is serialized, the layout assembles these into columns."""
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
    unit_newline: bool = False          # unit on its own line (unit.on_separate_line)
    status: str = ""                    # normal / high / low: a two-column result layout uses this to pick the column
    printed: int | None = None          # index into PrintedRow; None means a distractor row
    raw: dict[str, str] | None = None   # a distractor row gives its column text directly


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
    #: Content blocks outside the lab tables (department key-value sections, auxiliary-exam
    #: narratives, overall summary); see book.py.
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


# ── Printed conventions ──
def _paren(text: str, f: Family) -> str:
    return f"（{text}）" if f.paren_style == "fullwidth" else f"({text})"


def _variant_for(item: dict, script: str, rng: random.Random) -> str | None:
    pool = [v for v in item.get("name_variants") or [] if script_of(v) == script]
    return rng.choice(pool) if pool else None


def print_name(item: dict, f: Family, rng: random.Random) -> tuple[str, str]:
    """(name-column text, abbreviation-column text). The same institution always writes the same
    indicator the same way - one LIS has one dictionary."""
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
    """(the segment that applies to this subject, the whole cell's original text as an alternative)."""
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
    results localized to the document's language (an English report prints "positive", not its Chinese
    wording)."""
    value = _t().get("value_localization", {}).get(f.lang_group, {}).get(value, value)
    return value.replace(".", ",") if f.decimal_comma and re.fullmatch(r"-?\d+\.\d+", value) else value


def determinable(value_kind: str, printed_range: str, printed_flag: str) -> bool:
    return value_kind in ("qualitative", "categorical") or bool(printed_range) or bool(printed_flag)


# ── Dates ──
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


# ── Subject fields ──
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


# ── File splitting ──
def split_encounter(rng: random.Random, encounter: Encounter) -> list[list[str]]:
    """One visit -> several slips (each slip is one or more "groups"). Checkups split finest; follow-ups
    are often combined onto a single slip."""
    groups: dict[str, list[Reading]] = {}
    for reading in encounter.readings:
        groups.setdefault(group_of(reading.key), []).append(reading)
    names = [g for g in GROUP_ORDER if g in groups] + (["other"] if "other" in groups else [])
    slips: list[list[str]] = []
    for g in names:
        # A small group has some chance of merging into the previous slip (e.g. "comprehensive
        # biochemistry", "glucose and lipids")
        if slips and len(groups[g]) <= 12 and rng.random() < 0.45 and g not in ("vitals", "ecg"):
            slips[-1].append(g)
        else:
            slips.append([g])
    return slips


def build_doc(rng: random.Random, doc_id: str, person: Person, encounter: Encounter,
              groups: list[str], family: Family, previous: dict[str, tuple[str, str]],
              banner: bool = True, book: bool = False) -> Doc:
    """Lay out one slip's readings into a file, following the given layout. `previous` is
    key -> (last value, last date).

    `book=True` builds a checkup report: each group becomes its own captioned table, laid out as one
    multi-page file (checkup report books are the largest document class in the reference corpus). The
    full report (department key-value sections, auxiliary-exam narratives, overall summary) is
    assembled on top of this by book.py."""
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

            # ── Printed truth: count only what's actually visible on paper ──
            printed_range = range_text if has_ref_col else ""
            unit_visible = bool(unit) and (unit_at in ("value", "header") or
                                           (unit_at == "column" and "unit" in f.columns) or
                                           (unit_at in ("reference", "reference_amp") and has_ref_col))
            printed_unit = unit if unit_visible else ""
            printed_flag = flag if (f.flag_at == "column" or (f.flag_at in ("glued", "spaced", "paren")
                                                              and status != "normal")) else ""
            # US-style two result columns (In Range / Out Of Range): which column the value prints in is the flag
            two_columns = "result_out" in f.columns
            flag_code = ("1" if status != "normal" else "0") \
                if determinable(reading.value_kind, printed_range, printed_flag) or two_columns else ""
            p_index = len(doc.printed)
            doc.printed.append(PrintedRow(
                item_name=name, item_value=value, item_unit=printed_unit, item_range=printed_range,
                is_abnormal=flag_code, table=t_index,
                alternatives={"item_range": alts} if alts and printed_range else {}))

            # ── Semantic truth ──
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
