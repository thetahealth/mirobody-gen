"""T1：XLSX 与 CSV。与 PDF 共用 `grid.table_grid`，版式只决定一次。

单元格一律存**文本**：`5.90` 存成数字就只剩 `5.9`，印刷真值随之改变。数值单元格
（存数、靠 number_format 显示位数）是真实导出里确实有的另一种陷阱，但它需要自己的真值口径
（抽取器读到的是 5.9 还是 5.90？），留到那时一起定。

确定性：openpyxl 把"现在"写进 docProps 与 zip 条目的时间戳，这里全部改成报告日期。
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
                # openpyxl 保存时无视 properties.modified，一律写"现在"。这里改回报告日期。
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
