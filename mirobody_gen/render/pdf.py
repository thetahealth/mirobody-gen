"""T0：带文本层的 PDF。HTML → PyMuPDF Story 排版，再在页面上补横幅、水印、印章、页码。

为什么用 PyMuPDF 而不是 reportlab/weasyprint：它已经是 mirobody 读 PDF 的依赖之一，
自带的 Droid Sans Fallback 字体让输出**不依赖本机字体**（同 seed 逐字节一致的前提），
而且图像层的栅格化（PDF → 图像 → 劣化）直接接在同一个库上。

一份文件不只是表格：`doc.blocks` 里的键值对、叙述、参数表、图像块、总检按顺序排进 HTML，
两个标记块（`general_table`、`tables`）决定检验表格插在哪里；`doc.cover` 单独排成封面页，
合并到最前面。图像块（心电图条图、超声图）通过 Story 的 Archive 以 PNG 字节喂进去。

确定性：元数据日期取报告日期，不取"现在"；保存时 `no_new_id=True`，否则每次生成一个随机 /ID。
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
    """内容排不进页面。不静默出片：宁可报错，也不产出一份被截掉的文件。"""
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
# 只用 padding，不用 margin：MuPDF 的 Story 在带 margin-top 的块恰好落在分页处时会无限重排
# （2026-09-23 实测：64 行的体检报告排到第 81 页还报告"有剩余"）。render() 里另有页数上限兜底。

#: 字体里没有的字符 → 可印的替代（`_check_glyphs` 之前做）。
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
    # 列宽只认 width 属性（Story 不认 CSS 的 td width / min-width，2026-09-29 实测），不给的话
    # 标签列会被挤成一字一行。
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
    """返回 (整页 HTML, [(图像名, PNG 字节)], 顶层片段列表)。

    片段是排版的单位：`render` 把每个顶层元素当成一个独立的 Story 依次放到页面上（见 `_flow`），
    而不是把整页交给一个 Story。2026-09-29 实测：一个 Story 跨页时会**静默丢掉**若干块
    （60 人的语料里 14 本报告书各丢一到两页内容，可读性审计抓到；触发条件与分页位置有关，
    去掉 padding-top 或换页幅能改变结果但没有一种改法对所有文件都管用）。
    小块自己很少跨页，跨页的只剩长表格——那条路径一直是稳的。"""
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
        # 受检者字段已经作为表格行印进了结果表，这里只留姓名一行
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
    """字体里没有的字符会被印成空字符（2026-09-23 实测：`×10⁹/L` 的上标 9 印成了 `\x00`），
    纸上的东西就和印刷真值对不上了。宁可报错，不静默出片。"""
    import re as _re

    text = _re.sub(r"<[^>]+>", "", html_text)
    missing = sorted({ch for ch in text if not ch.isspace() and ch not in "&;#" and not _FONT.has_glyph(ord(ch))})
    if missing:
        raise LayoutError(f"{doc.doc_id}: 字体缺字形 {missing!r}")


def _qr(page: fitz.Page, rect: fitz.Rect, seed: str) -> None:
    """一块像二维码的黑白方阵。只是视觉构件（`qr_code` 家具），不编码任何东西。"""
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
        # Story 遇到放不进一页的元素时会一直报告"还有剩余"却不前进——不设上限就是死循环。
        if more and (fitz.Rect(filled).is_empty or pages > 80):
            raise LayoutError(f"{doc_id}: 第 {pages} 页排不下任何内容（filled={filled}）")
    writer.close()
    return buf.getvalue(), pages


def _flow(fragments: list[str], css: str, images: list[tuple[str, bytes]], mediabox: fitz.Rect,
          margin: int, doc_id: str, breaks: set[int] = frozenset()) -> tuple[bytes, int]:
    """逐块排版：每个顶层片段一个 Story，放不下就翻页；同一块跨页时由它自己的 Story 续排。
    `breaks` 里的片段强制从新页开始（`_flow_verified` 在发现丢内容后加进来的）。"""
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
        # 剩不到三行就翻页：Story 在极小的剩余矩形里放东西时会把放不下的部分吃掉（2026-09-29 实测）
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
                    raise LayoutError(f"{doc_id}: 一个块在空白页上排不下任何内容")
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
                raise LayoutError(f"{doc_id}: 超过 80 页")
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
    """哪些片段的文字没有全部出现在纸上（按 16 字块查，跨页的页脚插在中间也不影响）。"""
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
    """把一张长表拆成几张（各自带表头）。Story 对跨页长表偶尔也会吃行，拆小了就不跨页。"""
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
    """排完就核对：每个片段的文字都得在纸上。丢了就把那个片段挪到新页重排；还丢就把它（表格）拆小。
    MuPDF 的 Story 在分页处会静默吃内容，这是唯一可靠的对策——宁可多试几次，不出一份缺页的文件。"""
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
        # 已经在新页开头、又不是表格：把它后面的一块也推到新页，改变分页位置
        if index + 1 < len(frags) and index + 1 not in breaks:
            breaks.add(index + 1)
            continue
        raise LayoutError(f"{doc_id}: 片段 {index} 反复丢内容：{_text_nodes(frags[index])[:2]!r}")
    raise LayoutError(f"{doc_id}: 16 次重排后仍有内容丢失")


def render(doc: Doc, reported_iso: str) -> tuple[bytes, int]:
    """返回 (PDF 字节, 页数)。"""
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
