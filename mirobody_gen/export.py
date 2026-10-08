"""Transposed export tables: one person's results over the years, indicators in columns, one row per visit.

A large family in the real corpus (column group `meta_checkup / meta_report_time / meta_facility /
meta_filename / indicators...`, dozens of files in total), produced by a health app's "export my
records" feature. It tests what lab slips cannot:

* **one file, many dates**. mirobody's `resolve_report_date` assigns the whole file a single
  date — the demo/README measured "7 rows in, 1 row out". Here each reading's `observed` is its
  own row's report time;
* **indicators in columns**: a row-based extractor will read an entire row as one indicator;
* **no reference range, no flag**: `is_abnormal` is always indeterminate (`""`) for numeric items.
"""

from __future__ import annotations

import dataclasses
import random
from datetime import datetime

from . import spec
from .document import Cells, Doc, DocReading, PrintedRow, Table, format_date, print_unit, print_value
from .layout import Family, spellings
from .model import Encounter, Person


def export_family(base: Family, rng: random.Random) -> Family:
    lang = base.language
    headers = {}
    for role in ("meta_checkup", "meta_report_time", "meta_facility", "meta_filename", "category"):
        pool = spellings(role, lang) or spellings(role, "zh-Hans") or [role]
        headers[role] = rng.choice(pool)
    return dataclasses.replace(
        base, family_id=f"export-{'en' if lang == 'en' else 'zh'}", kind="app_export",
        institution="", columns=("meta_checkup", "meta_report_time", "meta_facility", "meta_filename"),
        headers=headers, bilingual_header=False, subject_in_table=False, previous_column=False,
        watermark=False, date_labels=(), furniture=(), signatures=(),
        fmt=rng.choices(["xlsx", "csv"], weights=[65, 35])[0],
        date_format=rng.choice(["YYYY-MM-DD HH:mm:ss", "YYYY-MM-DD", "YYYY/MM/DD HH:mm"]))


def build_export(rng: random.Random, doc_id: str, person: Person, all_encounters: list[Encounter],
                 facilities: list[str], filenames: list[str], base: Family, banner: bool) -> Doc:
    f = export_family(base, rng)
    en = f.language == "en"
    catalogue = spec.indicators()
    order = {k: i for i, k in enumerate(catalogue)}
    # Real export tables have 4-37 indicator columns (per spec's column groups); only checkups are
    # included, and each table draws a fixed subset.
    encounters = [e for e in all_encounters if e.exam_type == "routine"]
    facilities = [fa for e, fa in zip(all_encounters, facilities) if e.exam_type == "routine"]
    filenames = [fn for e, fn in zip(all_encounters, filenames) if e.exam_type == "routine"]
    measured = sorted({r.key for e in encounters for r in e.readings}, key=lambda k: order[k])
    keys = sorted(rng.sample(measured, min(len(measured), rng.randint(8, 36))), key=lambda k: order[k])
    unit_in_header = rng.random() < 0.5
    with_category = rng.random() < 0.4

    def header_of(key: str) -> str:
        item = catalogue[key]
        name = item["en"] if en else item["zh"]
        unit = print_unit(item["unit"], f)
        return f"{name}({unit})" if unit_in_header and unit else name

    meta_cols = list(f.columns)
    columns = (["category"] if with_category else []) + meta_cols + [f"analyte:{k}" for k in keys]
    header = [f.headers.get(c, c) if not c.startswith("analyte:") else header_of(c[8:]) for c in columns]
    doc = Doc(doc_id=doc_id, person_id=person.person_id, family=f, title="", subject=[], dates=[],
              tables=[], banner=banner, kind="export")
    rows: list[Cells] = []
    t, lang = spec.templates(), ("en" if en else "zh")
    yes, no = t["yes_no"][lang]
    for e, facility, filename in zip(encounters, facilities, filenames):
        when = format_date(datetime(e.exam_date.year, e.exam_date.month, e.exam_date.day, 9, 30),
                           f.date_format)
        raw = {"category": t["export_category"][lang]["routine" if e.exam_type == "routine" else "other"],
               "meta_checkup": yes if e.exam_type == "routine" else no,
               "meta_report_time": when, "meta_facility": facility, "meta_filename": filename}
        values = {r.key: r for r in e.readings}
        for key in keys:
            reading = values.get(key)
            raw[f"analyte:{key}"] = print_value(reading.value, f) if reading else ""
            if not reading:
                continue
            item = catalogue[key]
            flag = ("1" if reading.status != "normal" else "0") if reading.value_kind != "quantitative" else ""
            p_index = len(doc.printed)
            doc.printed.append(PrintedRow(
                item_name=item["en"] if en else item["zh"], item_value=print_value(reading.value, f),
                item_unit=print_unit(item["unit"], f) if unit_in_header else "", item_range="",
                is_abnormal=flag, readings=[len(doc.readings)]))
            doc.readings.append(DocReading(
                key=key, loinc=reading.loinc, canonical_value=reading.canonical_value,
                value_text=reading.value, unit_ucum=reading.unit_ucum, value_kind=reading.value_kind,
                status=reading.status, observed=e.exam_date.isoformat(), role="export",
                expect_resolvable=reading.expect_resolvable, printed_row=p_index))
        rows.append(Cells(name="", value="", unit="", range="", flag="", raw=raw))
    doc.tables.append(Table(columns=columns, headers=[header], rows=rows))
    return doc


def eligible(encounters: list[Encounter]) -> bool:
    return len(encounters) >= 3 and sum(e.exam_type == "routine" for e in encounters) >= 2

