"""T0: PDF with a text layer. HTML laid out by PyMuPDF's Story, with a banner, watermark, stamp and page
numbers then added directly on the page.

Why PyMuPDF rather than reportlab/weasyprint: it's already one of mirobody's own PDF-reading dependencies,
its bundled Droid Sans Fallback font makes output independent of local fonts (a precondition for
byte-identical output at a given seed), and the image tier's rasterization (PDF -> image -> degradation)
plugs directly into the same library.

A document is more than tables: `doc.blocks`' key-value pairs, narratives, parameter tables, image blocks
and the summary are laid into the HTML in order; two marker blocks (`general_table`, `tables`) decide where
the lab tables go; `doc.cover`, when present, is laid out as a separate cover page and merged to the front.
Image blocks (ECG strips, ultrasound images) are fed in as PNG bytes through the Story's Archive.

Deterministic: metadata dates use the report date, never "now"; saving with `no_new_id=True`, or a random
/ID would be generated every time.
"""

from __future__ import annotations

import hashlib
import html
import io
import random

import fitz

from .. import spec
from ..document import Doc
from .grid import table_grid

BANNER = spec.templates()["banner"]


class LayoutError(RuntimeError):
    """Content doesn't fit on the page. Never fails silently: better to raise than ship a truncated file."""
PAGES = {"a4": "a4", "a5l": "a5-l", "letter": "letter"}


def _esc(text: str) -> str:
    return html.escape(text).replace("\n", "<br/>")


def _css(doc: Doc) -> str:
    f = doc.family
    rule = {"grid": "border: 0.6px solid #444;",
            "horizontal": "border-bottom: 0.5px solid #888;",
            "none": ""}[f.rules]
    head = "border-bottom: 1px solid #000; border-top: 1px solid #000;" if f.rules != "grid" else rule
    return f"""
    * {{ font-family: cjk; font-size: {f.font_size}pt; }}
    .inst {{ font-size: {f.font_size + 5}pt; text-align: center; font-weight: bold; }}
    .title {{ font-size: {f.font_size + 3}pt; text-align: center; padding-bottom: 4px; }}
    .subject td {{ padding: 1px 8px 1px 0; }}
    .dates {{ padding: 2px 0 4px 0; }}
    table.data {{ border-collapse: collapse; width: 100%; }}
    table.data td {{ {rule} padding: 2px 4px; }}
    table.data th {{ {head} padding: 2px 4px; text-align: left; font-weight: bold; }}
    table.kv {{ border-collapse: collapse; width: 100%; }}
    table.kv td {{ {rule} padding: 2px 5px; }}
    table.kv td.k {{ color: #222; }}
    .caption {{ font-weight: bold; padding-top: 6px; }}
    .heading {{ font-weight: bold; font-size: {f.font_size + 2}pt; padding-top: 10px; padding-bottom: 2px; }}
    .narr {{ padding-top: 6px; }}
    .para {{ padding: 2px 0 2px 0; }}
    .label {{ font-weight: bold; }}
    .summary {{ padding: 6px 0 6px 0; }}
    .foot td {{ padding: 6px 18px 0 0; }}
    .disc {{ font-size: {f.font_size - 1}pt; color: #555; padding-top: 4px; }}
    """
# padding only, never margin: MuPDF's Story reflows forever when a margin-top block lands exactly at a
# page break (observed 2026-09-23: a 64-row check-up report still reported "more to place" at page 81).
# render() also carries a page-count ceiling as a backstop.

#: Characters missing from the font -> a printable substitute (applied before `_check_glyphs`).
_SUBSTITUTE = {"Ⅰ": "I", "Ⅱ": "II", "Ⅲ": "III", "Ⅳ": "IV", "‰": "‰"}


def _table_html(doc: Doc, table) -> str:
    headers, body, _ = table_grid(doc, table)
    rows = ["<tr>" + "".join(f"<th>{_esc(h)}</th>" for h in header) + "</tr>" for header in headers]
    rows += ["<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in row) + "</tr>" for row in body]
    out = ""
    if table.caption:
        out += f"<div class='caption'>{_esc(table.caption)}</div>"
    return out + "<table class='data'>" + "".join(rows) + "</table>"


def _kv_html(block, colon: str) -> str:
    per = block.columns // 2
    # Column width only works through the `width` attribute (Story ignores CSS td width/min-width,
    # observed 2026-09-29); without it the label column gets squeezed to one character per line.
    kw, vw = ("18%", "32%") if per == 2 else ("22%", "78%")
    cells = [f"<td class='k' width='{kw}'>{_esc(k)}</td><td width='{vw}'>{_esc(v)}</td>" for k, v in block.rows]
    rows = ["<tr>" + "".join(cells[i:i + per]) + "</tr>" for i in range(0, len(cells), per)]
    return (f"<div class='caption'>{_esc(block.title)}</div>" if block.title else "") + \
        "<table class='kv'>" + "".join(rows) + "</table>"


def _narrative_html(block, colon: str, images: list[tuple[str, bytes]], width_pt: float) -> str:
    out = f"<div class='caption'>{_esc(block.title)}</div>" if block.title else ""
    if block.image:
        name = f"img{len(images)}.png"
        images.append((name, block.image))
        w, h = block.image_size
        scale = min(1.0, (width_pt * 0.9) / max(w * 0.5, 1))
        out += f"<div class='para'><img src='{name}' width='{int(w * 0.5 * scale)}' height='{int(h * 0.5 * scale)}'/></div>"
    for label, text in block.rows:
        if label:
            out += f"<div class='para'><span class='label'>{_esc(label)}{colon}</span>{_esc(text)}</div>"
        else:
            out += f"<div class='para'>{_esc(text)}</div>"
    return out


def to_html(doc: Doc) -> tuple[str, list[tuple[str, bytes]], list[str]]:
    """Returns (the whole page as HTML, [(image name, PNG bytes)], the list of top-level fragments).

    A fragment is the unit of layout: `render` places each top-level element as its own Story, one after
    another (see `_flow`), rather than handing the whole page to a single Story. Observed 2026-09-29: a
    Story spanning a page break silently drops some of its blocks (in a 60-person corpus, 14 check-up
    books each lost one or two pages of content, caught by the readability audit; the trigger depends on
    where the break falls, and removing padding-top or changing the page size shifts the symptom without
    a fix that works for every file). A small block rarely spans a page on its own; only long tables do,
    and that path has stayed reliable.
    """
    f = doc.family
    colon = ": " if f.language == "en" else "："
    width_pt = fitz.paper_rect(PAGES[f.page]).width - 2 * (34 if f.page != "a5l" else 26)
    images: list[tuple[str, bytes]] = []
    parts = [f"<div class='inst'>{_esc(f.institution)}</div>", f"<div class='title'>{_esc(doc.title)}</div>"]
    if not f.subject_in_table and doc.subject:
        per_row = 4 if f.page != "a4" else 3
        cells = [f"<td>{_esc(k)}{colon}{_esc(v)}</td>" for k, v in doc.subject]
        rows = ["<tr>" + "".join(cells[i:i + per_row]) + "</tr>" for i in range(0, len(cells), per_row)]
        parts.append("<table class='subject'>" + "".join(rows) + "</table>")
    elif doc.subject:
        # subject fields are already printed as table rows in the results table; keep just the name line here
        k, v = doc.subject[0]
        parts.append(f"<div class='dates'>{_esc(k)}{colon}{_esc(v)}</div>")
    if doc.dates:
        parts.append("<div class='dates'>" + "&#160;&#160;&#160;".join(
            f"{_esc(d['label'])}{colon}{_esc(d['printed'])}" for d in doc.dates) + "</div>")

    general_title = spec.templates()["panel_titles"]["vitals"][1 if f.language == "en" else 0]
    general = [t for t in doc.tables if t.caption == general_title]
    others = [t for t in doc.tables if t.caption != general_title]
    placed = False
    for block in doc.blocks:
        if block.kind == "general_table":
            for t in general:
                parts.append(_table_html(doc, t))
            general = []
        elif block.kind == "tables":
            for t in general + others:
                parts.append(_table_html(doc, t))
            general, others, placed = [], [], True
        elif block.kind == "heading":
            parts.append(f"<div class='heading'>{_esc(block.title)}</div>")
        elif block.kind == "kv":
            parts.append(_kv_html(block, colon))
        elif block.kind in ("narrative", "summary"):
            cls = "summary" if block.kind == "summary" else "narr"
            parts.append(f"<div class='{cls}'>" + (f"<div class='heading'>{_esc(block.title)}</div>" if block.kind == "summary" and block.title else "")
                         + _narrative_html(block if block.kind == "narrative" else _untitled(block), colon, images, width_pt) + "</div>")
    if not placed:
        for t in general + others:
            parts.append(_table_html(doc, t))
    for label, text in doc.narratives:
        parts.append(f"<div class='narr'><b>{_esc(label)}{colon}</b>{_esc(text)}</div>")
    signed = [(k, v) for k, v in doc.footer if k]
    if signed:
        parts.append("<table class='foot'><tr>" + "".join(
            f"<td>{_esc(k)}{colon}{_esc(v)}</td>" for k, v in signed) + "</tr></table>")
    for k, v in doc.footer:
        if not k:
            parts.append(f"<div class='disc'>{_esc(v)}</div>")
    text = "<html><body>" + "".join(parts) + "</body></html>"
    for bad, good in _SUBSTITUTE.items():
        text = text.replace(bad, good)
    fragments = []
    for part in parts:
        for bad, good in _SUBSTITUTE.items():
            part = part.replace(bad, good)
        fragments.append(part)
    return text, images, fragments


def _untitled(block):
    import copy
    b = copy.copy(block)
    b.title = ""
    return b


def cover_html(doc: Doc) -> str:
    f = doc.family
    colon = ": " if f.language == "en" else "："
    c = doc.cover
    fields = [(k, v) for k, v in doc.subject[:3]] + list(c["fields"])
    rows = "".join(f"<tr><td class='k'>{_esc(k)}{colon}</td><td>{_esc(v)}</td></tr>" for k, v in fields)
    seed = int(hashlib.sha1(f.family_id.encode()).hexdigest()[:4], 16)
    hue = ["#1f5f9a", "#2a7d5b", "#8a2f2f", "#5b4b8a", "#b0742c"][seed % 5]
    return f"""<html><body>
    <div style='height: 90px'></div>
    <div style='background-color: {hue}; padding: 26px 30px; color: white; font-size: {f.font_size + 12}pt; font-weight: bold'>{_esc(f.institution)}</div>
    <div style='height: 60px'></div>
    <div style='text-align: center; font-size: {f.font_size + 16}pt; font-weight: bold'>{_esc(c['title'])}</div>
    <div style='height: 80px'></div>
    <table style='margin-left: 90px'>{rows}</table>
    <div style='height: 220px'></div>
    <div style='text-align: center; color: #666; font-size: {f.font_size - 1}pt'>{_esc(f.institution)}</div>
    </body></html>"""


_FONT = fitz.Font("cjk")


def _check_glyphs(doc: Doc, html_text: str) -> None:
    """A character missing from the font prints as a null byte (observed 2026-09-23: the superscript 9 in
    `×10⁹/L` printed as `\x00`), so what's on the page would no longer match the printed truth. Better to
    raise than to ship silently."""
    import re as _re

    text = _re.sub(r"<[^>]+>", "", html_text)
    missing = sorted({ch for ch in text if not ch.isspace() and ch not in "&;#" and not _FONT.has_glyph(ord(ch))})
    if missing:
        raise LayoutError(f"{doc.doc_id}: font is missing glyphs for {missing!r}")


def _qr(page: fitz.Page, rect: fitz.Rect, seed: str) -> None:
    """A black-and-white grid that looks like a QR code. A visual prop only (the `qr_code` furniture item),
    encoding nothing."""
    rng = random.Random(seed)
    n = 17
    cell = rect.width / n
    shape = page.new_shape()
    for i in range(n):
        for j in range(n):
            corner = (i < 4 and j < 4) or (i < 4 and j > n - 5) or (i > n - 5 and j < 4)
            if corner or rng.random() < 0.45:
                shape.draw_rect(fitz.Rect(rect.x0 + i * cell, rect.y0 + j * cell,
                                          rect.x0 + (i + 1) * cell, rect.y0 + (j + 1) * cell))
    shape.finish(color=None, fill=(0, 0, 0))
    shape.commit()


def _barcode(page: fitz.Page, rect: fitz.Rect, seed: str) -> None:
    rng = random.Random(seed)
    shape = page.new_shape()
    x = rect.x0
    while x < rect.x1:
        w = rng.choice([0.6, 1.0, 1.6])
        shape.draw_rect(fitz.Rect(x, rect.y0, x + w, rect.y1))
        x += w + rng.choice([0.6, 1.0, 1.6])
    shape.finish(color=None, fill=(0, 0, 0))
    shape.commit()


def _place(html_text: str, css: str, images: list[tuple[str, bytes]], mediabox: fitz.Rect, margin: int,
           doc_id: str) -> tuple[bytes, int]:
    archive = fitz.Archive()
    for name, data in images:
        archive.add(data, name)
    story = fitz.Story(html=html_text, user_css=css, archive=archive)
    buf = io.BytesIO()
    writer = fitz.DocumentWriter(buf)
    more, pages = 1, 0
    while more:
        device = writer.begin_page(mediabox)
        more, filled = story.place(mediabox + (margin, margin + 8, -margin, -margin - 10))
        story.draw(device)
        writer.end_page()
        pages += 1
        # Story reports "more to place" forever without progress when an element can't fit on any page;
        # without a cap this loops forever.
        if more and (fitz.Rect(filled).is_empty or pages > 80):
            raise LayoutError(f"{doc_id}: nothing fit on page {pages} (filled={filled})")
    writer.close()
    return buf.getvalue(), pages


def _flow(fragments: list[str], css: str, images: list[tuple[str, bytes]], mediabox: fitz.Rect,
          margin: int, doc_id: str, breaks: set[int] = frozenset()) -> tuple[bytes, int]:
    """Lay out fragment by fragment: one Story per top-level fragment, a new page when it doesn't fit; a
    fragment spanning a page break continues in its own Story. Fragments in `breaks` are forced onto a
    fresh page (added by `_flow_verified` once it finds lost content)."""
    archive = fitz.Archive()
    for name, data in images:
        archive.add(data, name)
    buf = io.BytesIO()
    writer = fitz.DocumentWriter(buf)
    top, bottom = margin + 8, mediabox.height - margin - 10
    device = writer.begin_page(mediabox)
    pages, y = 1, top
    gap = 2.0

    def new_page():
        nonlocal device, pages, y
        writer.end_page()
        device = writer.begin_page(mediabox)
        pages += 1
        y = top

    for index, frag in enumerate(fragments):
        fresh = y == top
        # break to a new page once less than three lines remain: Story drops content that doesn't fit when
        # placing into a very small remaining rectangle (observed 2026-09-29)
        if (index in breaks or bottom - y < 3.2 * 12) and not fresh:
            new_page()
            fresh = True
        story = fitz.Story(html=frag, user_css=css, archive=archive)
        more = 1
        while more:
            more, filled = story.place(fitz.Rect(margin, y, mediabox.width - margin, bottom))
            filled = fitz.Rect(filled)
            if filled.is_empty or filled.height <= 0:
                if fresh:
                    raise LayoutError(f"{doc_id}: a block doesn't fit even on a blank page")
                new_page()
                fresh = True
                continue
            story.draw(device)
            y = filled.y1 + gap
            if more:
                new_page()
                fresh = True
            else:
                fresh = False
            if pages > 80:
                raise LayoutError(f"{doc_id}: over 80 pages")
    writer.end_page()
    writer.close()
    return buf.getvalue(), pages


def _text_nodes(fragment: str) -> list[str]:
    import re as _re

    return [html.unescape(t) for t in _re.findall(r">([^<>]{4,})<", fragment.replace("<br/>", " "))]


def _norm(text: str) -> str:
    import re as _re
    import unicodedata

    return _re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def _lost_fragments(fragments: list[str], pdf_bytes: bytes) -> list[int]:
    """Which fragments' text didn't fully make it onto the page (checked in 16-character chunks, so a
    page footer inserted in the middle doesn't matter)."""
    with fitz.open("pdf", pdf_bytes) as doc:
        text = _norm("".join(page.get_text() for page in doc))
    lost = []
    for index, frag in enumerate(fragments):
        for node in _text_nodes(frag):
            n = _norm(node)
            if len(n) < 4:
                continue
            chunks = [n[i:i + 16] for i in range(0, max(1, len(n) - 15), 16)] or [n]
            if not all(c in text for c in chunks):
                lost.append(index)
                break
    return lost


def _split_table(fragment: str, rows_per_part: int = 12) -> list[str] | None:
    """Split a long table into several, each with its own header. Story occasionally drops rows from a
    table that spans a page; splitting it small enough keeps it on one page."""
    import re as _re

    m = _re.search(r"^(.*?)(<table class='data'>)(.*)(</table>)(.*)$", fragment, _re.S)
    if not m:
        return None
    prefix, open_tag, body, close_tag, suffix = m.groups()
    rows = _re.findall(r"<tr>.*?</tr>", body, _re.S)
    header = [r for r in rows if "<th>" in r]
    data = [r for r in rows if "<th>" not in r]
    if len(data) <= rows_per_part:
        return None
    parts = []
    for i in range(0, len(data), rows_per_part):
        parts.append((prefix if i == 0 else "") + open_tag + "".join(header) + "".join(data[i:i + rows_per_part]) + close_tag)
    parts[-1] += suffix
    return parts


def _flow_verified(fragments: list[str], css: str, images: list[tuple[str, bytes]], mediabox: fitz.Rect,
                   margin: int, doc_id: str) -> tuple[bytes, int]:
    """Lay out, then verify: every fragment's text must be on the page. If some is missing, move that
    fragment to a fresh page and retry; if it's still missing, split it (a table) smaller. MuPDF's Story
    silently drops content at page breaks, and this is the only reliable defence -- better to retry a few
    times than ship a file with missing pages."""
    frags = list(fragments)
    breaks: set[int] = set()
    for attempt in range(16):
        data, pages = _flow(frags, css, images, mediabox, margin, doc_id, breaks)
        lost = _lost_fragments(frags, data)
        if not lost:
            return data, pages
        index = lost[0]
        if index not in breaks:
            breaks.add(index)
            continue
        parts = _split_table(frags[index])
        if parts:
            frags[index:index + 1] = parts
            breaks = {b if b <= index else b + len(parts) - 1 for b in breaks}
            continue
        # already at the top of a fresh page, and not a table: push the block after it to a new page too,
        # to shift where the break falls
        if index + 1 < len(frags) and index + 1 not in breaks:
            breaks.add(index + 1)
            continue
        raise LayoutError(f"{doc_id}: fragment {index} keeps losing content: {_text_nodes(frags[index])[:2]!r}")
    raise LayoutError(f"{doc_id}: still losing content after 16 retries")


def render(doc: Doc, reported_iso: str) -> tuple[bytes, int]:
    """Returns (PDF bytes, page count)."""
    f = doc.family
    html_text, images, fragments = to_html(doc)
    _check_glyphs(doc, html_text)
    mediabox = fitz.paper_rect(PAGES[f.page])
    margin = 34 if f.page != "a5l" else 26
    body, _ = _flow_verified(fragments, _css(doc), images, mediabox, margin, doc.doc_id)
    pdf = fitz.open("pdf", body)
    if doc.cover:
        cover_text = cover_html(doc)
        for bad, good in _SUBSTITUTE.items():
            cover_text = cover_text.replace(bad, good)
        _check_glyphs(doc, cover_text)
        cover_bytes, _ = _place(cover_text, _css(doc) + " table td.k { padding: 6px 12px 6px 0; }", [],
                                mediabox, margin, doc.doc_id + ":cover")
        with fitz.open("pdf", cover_bytes) as cover:
            pdf.insert_pdf(cover, from_page=0, to_page=0, start_at=0)
    font = fitz.Font("cjk")
    n = pdf.page_count
    seed = hashlib.sha1(doc.doc_id.encode()).hexdigest()
    t, lang = spec.templates(), (f.lang_group)
    for i, page in enumerate(pdf):
        page.insert_font(fontname="cjk", fontbuffer=font.buffer)
        w, h = page.rect.width, page.rect.height
        if doc.banner:
            page.insert_text((margin, 14), BANNER, fontname="cjk", fontsize=6, color=(0.45, 0.45, 0.45))
        if "page_number" in f.furniture:
            text = t["page_number"][lang].format(i=i + 1, n=n)
            page.insert_text((w / 2 - 30, h - 12), text, fontname="cjk", fontsize=7)
        if "print_info" in f.furniture:
            stamp = reported_iso.replace("T", " ")
            text = t["printed_at"][lang].format(t=stamp)
            page.insert_text((w - margin - 130, h - 12), text, fontname="cjk", fontsize=6.5)
        first_content = 1 if doc.cover else 0
        if i == first_content and "qr_code" in f.furniture:
            _qr(page, fitz.Rect(w - margin - 34, margin - 6, w - margin, margin + 28), seed)
        if i == first_content and "barcode" in f.furniture:
            _barcode(page, fitz.Rect(margin, margin - 4, margin + 70, margin + 12), seed)
        if f.watermark and i >= first_content:
            text = f.institution if (int(seed[:2], 16) % 2) else t["watermarks"][lang][0]
            page.insert_text(fitz.Point(w * 0.22, h * 0.62), text, fontname="cjk", fontsize=28,
                             color=(0.82, 0.82, 0.82), morph=(fitz.Point(w * 0.22, h * 0.62), fitz.Matrix(-28)))
        if i == n - 1 and ("department_stamp" in f.furniture or doc.kind in ("checkup_book", "outpatient_record")):
            c = fitz.Point(w - margin - 60, h - margin - 40)
            page.draw_circle(c, 26, color=(0.8, 0.1, 0.1), width=1.2)
            label = t["stamps"][lang]
            page.insert_text(fitz.Point(c.x - 20, c.y + 3), label, fontname="cjk", fontsize=7,
                             color=(0.8, 0.1, 0.1))
    stamp = reported_iso.replace("-", "").replace(":", "").replace("T", "")
    pdf.set_metadata({"title": doc.title, "author": f.institution, "subject": "SYNTHETIC",
                      "keywords": "synthetic; mirobody-gen", "creator": "mirobody-gen",
                      "producer": "mirobody-gen", "creationDate": f"D:{stamp}", "modDate": f"D:{stamp}"})
    pdf.subset_fonts()
    return pdf.tobytes(garbage=4, deflate=True, no_new_id=True), n
