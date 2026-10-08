"""T1: XLSX and CSV. Shares `grid.table_grid` with the PDF renderer, so layout is decided once.

Every cell is stored as **text**: storing `5.90` as a number leaves only `5.9`, changing the printed
truth. Numeric cells (a stored number, digits shown via `number_format`) are a real trap real-world
exports do have, but they need their own ground-truth convention (does an extractor read 5.9 or 5.90?) --
left for when that's taken on.

Deterministic: openpyxl stamps "now" into docProps and the zip entries' timestamps; all of that is
rewritten to the report date here.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import datetime

from ..document import Doc
from .grid import table_grid
from .pdf import BANNER


def _preamble(doc: Doc) -> list[list[str]]:
    f = doc.family
    colon = ": " if f.language == "en" else "："
    rows = [[f.institution], [doc.title]]
    if not f.subject_in_table:
        rows.append([f"{k}{colon}{v}" for k, v in doc.subject])
    rows.append([f"{d['label']}{colon}{d['printed']}" for d in doc.dates])
    rows.append([])
    return rows


def rows_of(doc: Doc, with_preamble: bool) -> list[list[str]]:
    out: list[list[str]] = []
    if doc.banner:
        out.append([BANNER])
    if with_preamble:
        out += _preamble(doc)
    for table in doc.tables:
        headers, body, _ = table_grid(doc, table)
        if table.caption:
            out.append([table.caption])
        out += [list(h) for h in headers] + [list(r) for r in body]
        out.append([])
    colon = ": " if doc.family.language == "en" else "："
    for label, text in doc.narratives:
        lines = text.split("\n")
        out.append([f"{label}{colon}{lines[0]}"])
        out += [[line] for line in lines[1:]]
    for k, v in doc.footer:
        out.append([f"{k}{colon}{v}" if k else v])
    while out and not out[-1]:
        out.pop()
    return out


def to_xlsx(doc: Doc, reported_iso: str, with_preamble: bool) -> bytes:
    import openpyxl

    when = datetime.fromisoformat(reported_iso)
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Report" if doc.family.language == "en" else "检验结果"
    for row in rows_of(doc, with_preamble):
        sheet.append(row)
    book.properties.creator = "mirobody-gen (synthetic)"
    book.properties.title = doc.title
    book.properties.subject = "SYNTHETIC"
    book.properties.keywords = "synthetic; mirobody-gen"
    book.properties.created = when
    book.properties.modified = when
    raw = io.BytesIO()
    book.save(raw)
    return _normalize_zip(raw.getvalue(), when)


def _normalize_zip(data: bytes, when: datetime) -> bytes:
    stamp = (when.year, when.month, when.day, when.hour, when.minute, when.second)
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for name in sorted(src.namelist()):
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            data = src.read(name)
            if name == "docProps/core.xml":
                # openpyxl ignores properties.modified on save and always writes "now"; put the report date back
                iso = when.strftime("%Y-%m-%dT%H:%M:%SZ").encode()
                data = re.sub(rb"(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)",
                              rb"\g<1>" + iso + rb"\g<2>", data)
            dst.writestr(info, data)
    return out.getvalue()


def to_csv(doc: Doc, with_preamble: bool) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    for row in rows_of(doc, with_preamble):
        writer.writerow(row)
    encoding = "utf-8" if doc.family.language == "en" else "utf-8-sig"
    return buf.getvalue().encode(encoding)
