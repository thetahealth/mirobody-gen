"""Vendor-style pharmacogenomics report page: a one-page PDF rendering of
`genomics_report.rows_for`, in WeGene's layout for zh and a generic consumer-PGx-report layout for
en (23andMe/Ancestry style).

Self-contained PDF generation (PyMuPDF Story), not routed through the Doc/layout/hazard pipeline:
this page is a flat gene/rsID/genotype/phenotype table with no reference ranges or injectable
hazards, so reusing that machinery would buy nothing and couple two unrelated report kinds.
"""

from __future__ import annotations

import html
import io
import re

import fitz

from .pdf import BANNER

TITLE = {"zh": "药物基因组检测报告", "en": "Pharmacogenomics Report"}
VENDOR_TITLE = {"zh": "WeGene 基因检测", "en": "Consumer Genomics Report"}
_COLUMNS = {"zh": ["基因", "位点 (rsID)", "星号等位基因", "基因型", "解读"],
            "en": ["Gene", "rsID", "Star allele", "Genotype", "Interpretation"]}
_DISCLAIMER = {
    "zh": "本报告由合成数据生成，所有基因型均为模拟数据，不对应任何真实个体，仅用于软件测试。",
    "en": "This report is generated from synthetic data. All genotypes are simulated and do not "
          "correspond to any real individual; for software testing only.",
}
_CSS = """
* { font-family: cjk; font-size: 8.5pt; }
.inst { font-size: 14pt; text-align: center; font-weight: bold; padding-bottom: 2px; }
.title { font-size: 11pt; text-align: center; padding-bottom: 8px; }
.subject { padding-bottom: 8px; }
table.data { border-collapse: collapse; width: 100%; }
table.data td, table.data th { border-bottom: 0.5px solid #888; padding: 2px 4px; text-align: left; }
table.data th { border-top: 1px solid #000; border-bottom: 1px solid #000; font-weight: bold; }
.disc { font-size: 7pt; color: #555; padding-top: 10px; }
"""
_FONT = fitz.Font("cjk")


def _esc(text: str) -> str:
    return html.escape(str(text))


def _check_glyphs(doc_id: str, text: str) -> None:
    """Same defence as `render/pdf.py`: a character missing from the built-in font prints as a
    null byte, so what's on the page would no longer match the printed truth."""
    plain = re.sub(r"<[^>]+>", "", text)
    missing = sorted({c for c in plain if not c.isspace() and not _FONT.has_glyph(ord(c))})
    if missing:
        raise RuntimeError(f"{doc_id}: font is missing glyphs for {missing!r}")


def _html(person_id: str, lang: str, when_text: str, rows: list[dict]) -> str:
    cols = _COLUMNS[lang]
    head = "".join(f"<th>{_esc(c)}</th>" for c in cols)
    body = []
    for r in rows:
        phenotype = r["phenotype_zh"] if lang == "zh" else r["phenotype_en"]
        cells = (r["gene"], r["rsid"], r["label"] or "--", r["genotype"], phenotype)
        body.append("<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in cells) + "</tr>")
    subject = (f"样本编号{person_id}　检测日期{when_text}　检测机构{VENDOR_TITLE['zh']}" if lang == "zh"
              else f"Sample ID {person_id}   Report date {when_text}   Provider {VENDOR_TITLE['en']}")
    return (f"<html><body><div class='inst'>{_esc(VENDOR_TITLE[lang])}</div>"
            f"<div class='title'>{_esc(TITLE[lang])}</div>"
            f"<div class='subject'>{subject}</div>"
            f"<table class='data'><tr>{head}</tr>{''.join(body)}</table>"
            f"<div class='disc'>{_esc(_DISCLAIMER[lang])}</div></body></html>")


def render(person_id: str, lang: str, when_text: str, rows: list[dict]) -> tuple[bytes, int]:
    """Returns (PDF bytes, page count). `when_text` is the report date as printed (ISO)."""
    text = _html(person_id, lang, when_text, rows)
    _check_glyphs(person_id, text)
    mediabox = fitz.paper_rect("a4")
    margin = 34
    story = fitz.Story(html=text, user_css=_CSS)
    buf = io.BytesIO()
    writer = fitz.DocumentWriter(buf)
    more, pages = 1, 0
    while more:
        device = writer.begin_page(mediabox)
        more, filled = story.place(mediabox + (margin, margin + 8, -margin, -margin - 10))
        story.draw(device)
        writer.end_page()
        pages += 1
        if more and (fitz.Rect(filled).is_empty or pages > 10):
            raise RuntimeError(f"{person_id}: genetics report, nothing fit on page {pages}")
    writer.close()
    pdf = fitz.open("pdf", buf.getvalue())
    font = fitz.Font("cjk")
    for page in pdf:
        page.insert_font(fontname="cjk", fontbuffer=font.buffer)
        page.insert_text((margin, 14), BANNER, fontname="cjk", fontsize=6, color=(0.45, 0.45, 0.45))
    stamp = when_text.replace("-", "")
    pdf.set_metadata({"title": TITLE[lang], "author": VENDOR_TITLE[lang], "subject": "SYNTHETIC",
                      "keywords": "synthetic; mirobody-gen", "creator": "mirobody-gen",
                      "producer": "mirobody-gen", "creationDate": f"D:{stamp}000000",
                      "modDate": f"D:{stamp}000000"})
    pdf.subset_fonts()
    data = pdf.tobytes(garbage=4, deflate=True, no_new_id=True)
    n = pdf.page_count
    pdf.close()
    return data, n
