"""Extraction hazards: injection and detection. Which named hazards a file carries is decided only here.

A hazard has one of three origins, recorded verbatim in the manifest's `source` field:

* `layout` — a consequence of the layout choice. An institution whose layout glues the unit to the
  value puts `unit.glued_to_value` on every one of its slips. But it only counts when the content
  actually prints it: a slip with no `10^9/L`-style unit never carries `unit.superscript`, however
  distinctive the layout's superscript convention is.
* `content` — a consequence of the content itself: a qualitative item brings `value.non_numeric`;
  blood pressure printed in one cell brings `value.pair_in_one_cell`.
* `incident` — a one-off event on a single file (a name wraps, a reference range gets truncated),
  drawn independently at the real corpus's **document rate**.

Every class is registered in `STATUS`: `injected / layout / content` are the ones this layer can
produce; `channel` hazards come from the **channel** (OCR misreads a character, a stray space appears
in a number) and do not occur naturally in a text-layer PDF — forcing them there would be fake, so
they're left for the image layer's degradation to produce naturally; `deferred` records why a class
isn't implemented yet. `tests/test_render.py` checks that every `generate=true` class in spec is
registered, so none is silently dropped.
"""

from __future__ import annotations

import random
import re

from . import spec
from .document import Cells, Doc
from .layout import script_of

STATUS: dict[str, tuple[str, str]] = {
    # ── layout ──
    "unit.glued_to_value": ("layout", "layout glues the unit to the value"),
    "unit.glued_to_reference": ("layout", "unit printed in the reference-range cell, joined by a space or &"),
    "unit.in_header_or_reference_only": ("layout", "unit only in the header"),
    "unit.missing": ("layout", "layout prints no unit"),
    "reference.absent": ("layout", "layout has no reference-range column"),
    "table.bilingual_header": ("layout", "second header row in English"),
    "reference.separator_dialect": ("layout", "range-separator dialect"),
    "flag.text_dialect": ("layout", "text flag"),
    "flag.arrow_glued": ("layout", "arrow glued to the value"),
    "value.flag_combined_in_cell": ("layout", "value and flag share a cell"),
    "value.parenthetical": ("layout", "flag in parentheses after the value"),
    "unit.superscript": ("layout", "the various ways of writing 10^9/L"),
    "unit.case_variant": ("layout", "unit letter case"),
    "reference.sex_partitioned": ("layout", "sex-partitioned reference range printed in one cell"),
    "meta.subject_field_as_indicator": ("layout", "subject field printed as a table row"),
    "value.multiple_per_row": ("layout", "previous-result column"),
    "table.row_label_as_column": ("layout", "category column"),
    "meta.date_format_dialect": ("layout", "date format dialect"),
    "meta.multiple_dates": ("layout", "multiple dates: collected/received/reported/printed"),
    "meta.page_furniture": ("layout", "page numbers, print metadata"),
    "ocr.noise_text": ("layout", "watermarks and stamps; in the text layer a watermark's text is noise text"),
    "value.decimal_comma": ("layout", "decimal comma"),
    "ocr.punctuation_swap": ("layout", "full-width parentheses"),
    "value.blank_column": ("layout", "a whole column left blank"),
    "table.transposed": ("layout", "transposed export table"),
    # ── content ──
    "value.non_numeric": ("content", "qualitative result"),
    "table.two_result_columns": ("layout", "normal and abnormal values in separate columns (In Range / Out Of Range on US-style lab slips)"),
    "reference.inequality": ("content", "one-sided reference range"),
    "value.pair_in_one_cell": ("content", "blood pressure as two values in one cell"),
    "unit.slash_ambiguous": ("content", "units like 次/分 (times/minute)"),
    "table.multiple_tables": ("content", "several tables in one file"),
    "table.page_break_loses_header": ("content", "table spans a page break; the continuation has no header (detected after rendering)"),
    # ── incidental ──
    "table.wrapped_cell": ("injected", "name wraps onto two lines"),
    "reference.split_across_lines": ("injected", "reference range wraps onto two lines"),
    "value.missing": ("injected", "a row has no result; that row joins the must-abstain set"),
    "table.truncated": ("injected", "reference range truncated; that field is dropped from the denominator"),
    "table.misalignment": ("injected", "a missing cell shifts the following cells left"),
    "meta.mixed_into_rows": ("injected", "a date/institution line mixed into the result rows"),
    "table.flag_row_as_data": ("injected", "a summary row such as 'any abnormal' mixed into the data"),
    "meta.narrative_block": ("injected", "a narrative paragraph"),
    "value.duplicated_in_summary": ("injected", "a table value repeated in the summary"),
    "ocr.mixed_script": ("injected", "simplified and traditional characters mixed"),
    "table.header_ambiguous": ("injected", "some column header left blank"),
    "reference.ambiguous_column": ("injected", "the reference-range column header reads like a flag column"),
    "flag.column_confusion": ("injected", "flag column blank, flag crowded into the result cell"),
    "reference.multiple_rows": ("injected", "reference range split across several rows"),
    "unit.on_separate_line": ("injected", "unit on its own line (after the value or the reference range)"),
    # ── channel (produced naturally by the image layer; never forced in the text layer) ──
    "ocr.other": ("channel", "OCR error catch-all"),
    "ocr.name_misspelled": ("channel", "OCR misreads the indicator name"),
    "unit.ocr_corrupted": ("channel", "OCR distorts the unit"),
    "reference.garbled": ("channel", "OCR garbles the reference range"),
    "value.space_in_number": ("channel", "a stray space inside a number"),
    "reference.space_inside_number": ("channel", "a stray space inside a reference-range number"),
    "value.missing_decimal_point": ("channel", "decimal point dropped; printing it as a wrong value would put the "
                                               "printed and clinical truth layers in conflict and needs a separate "
                                               "truth convention, so it is not produced yet"),
    # ── deferred ──
    "ocr.chart_annotation": ("deferred", "needs a trend chart; checkup report books don't draw charts yet"),
    "reference.ratio_not_unit": ("deferred", "needs a serology indicator (s/co, index), not yet in the catalogue"),
    "value.contradicts_other_section": ("deferred", "needs two sets of values, summary and table, with an undecided truth convention"),
    "value.comparator": ("deferred", "needs each assay's detection limit, which varies by reagent vendor with no citable unified source"),
    "unit.inconsistent_across_sets": ("deferred", "needs the same indicator to appear twice in one file"),
    "flag.contradicts_reference": ("deferred", "measured document rate is 0"),
    "value.everything_in_one_cell": ("deferred", "measured document rate is 0"),
}


def rate(name: str) -> float:
    for c in spec.hazards()["classes"]:
        if c["name"] == name:
            return c["document_rate"]
    return 0.0


def _reading_rows(doc: Doc) -> list[tuple[int, int, Cells]]:
    """(table index, row index, cell), limited to rows that hold a real reading."""
    return [(t, i, c) for t, table in enumerate(doc.tables)
            for i, c in enumerate(table.rows) if c.printed is not None]


# ── Incidental injection ──
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
    # One line per item: "high" sitting right next to the next item's "Total Cholesterol" spells out a
    # phrase that also occurs in the real corpus - two common words butted together. The replay detector
    # does not exempt this by design (hit confirmed 2026-09-23).
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
    """Draw incidental hazards independently at their real document rate. When `only` is given, inject
    just those classes (for minimal-contrast pairs) and always attempt them."""
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


# ── Detecting layout and content hazards ──
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
        # Keep the two apart: joined by `&` is "glued" (glued_to_reference: commonly separated by '&');
        # joined by a space, with no unit in the result cell, is "unit only in the reference range"
        # (in_header_or_reference_only).
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
