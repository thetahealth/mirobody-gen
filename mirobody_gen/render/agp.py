"""CGM reports as PDFs with a text layer: the one-page AGP report and the hospital's CGM report sheet.

Two layouts, chosen by the report style (`resources/streams.json`, `reports`):

- `agp`: the consensus AGP report as the International Diabetes Center's v5 lays it out and the 2023 Chinese
  consensus adopts it. Time in ranges with each band's goal (top left), the patient, period and time the
  sensor was active over the glucose metrics (top right), the ambulatory glucose profile across the page with
  its bands and median coloured by glucose zone, and fourteen daily profiles, two rows of seven, at the foot.
- `sheet`: the 持续葡萄糖监测（CGM）报告单 of the 2017 Chinese CGM guideline: the institution, the patient fields,
  one column per day against the normal values, the guideline's summary sentence and the signature line.

Every string drawn is handed in already formatted (`cgm_reports.build`), and the same strings are the truth's
`printed_rows`. Deterministic like `render/pdf.py`: the built-in CJK font, metadata dates set from the report
date, no new document id.
"""

from __future__ import annotations

import math

import fitz

from .pdf import BANNER

A4P = fitz.paper_rect("a4")
A4L = fitz.paper_rect("a4-l")
MARGIN = 34
INK = (0.13, 0.13, 0.13)
GREY = (0.45, 0.45, 0.45)
RULE = (0.75, 0.75, 0.75)
PANEL = (0.90, 0.92, 0.94)


def _rgb(hex_colour: str) -> tuple[float, float, float]:
    h = hex_colour.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _font() -> fitz.Font:
    return fitz.Font("cjk")


def _w(text: str, size: float) -> float:
    return _font().text_length(text, fontsize=size)


def _text(page: fitz.Page, x: float, y: float, text: str, size: float = 8, color=INK, align: str = "left") -> None:
    if align == "right":
        x -= _w(text, size)
    elif align == "center":
        x -= _w(text, size) / 2
    page.insert_text((x, y), text, fontname="cjk", fontsize=size, color=color)


def _wrap(text: str, size: float, width: float) -> list[str]:
    """Greedy wrap by measured width: anywhere in Chinese text, at spaces in Latin text."""
    lines, line = [], ""
    cjk = any("一" <= c <= "鿿" for c in text)
    for ch in text:
        if _w(line + ch, size) > width and line:
            cut = -1 if cjk else line.rfind(" ")
            if cut > 0:
                lines.append(line[:cut])
                line = line[cut + 1:] + ch
            else:
                lines.append(line)
                line = ch
        else:
            line += ch
    if line:
        lines.append(line)
    return lines


def _rect(page: fitz.Page, r: fitz.Rect, fill=None, color=None, width: float = 0.5) -> None:
    shape = page.new_shape()
    shape.draw_rect(r)
    shape.finish(color=color, fill=fill, width=width)
    shape.commit()


def _line(page: fitz.Page, a, b, color=RULE, width: float = 0.5) -> None:
    shape = page.new_shape()
    shape.draw_line(a, b)
    shape.finish(color=color, width=width)
    shape.commit()


def _spread(ys: list[float], gap: float, top: float, bottom: float) -> list[float]:
    """Positions pushed apart to at least `gap`, kept within [top, bottom] (labels beside thin bands)."""
    ys = list(ys)
    for i in range(1, len(ys)):
        ys[i] = max(ys[i], ys[i - 1] + gap)
    ys[-1] = min(ys[-1], bottom)
    for i in range(len(ys) - 2, -1, -1):
        ys[i] = min(ys[i], ys[i + 1] - gap)
    ys[0] = max(ys[0], top)
    return ys


# ── Zone colouring ───────────────────────────────────────────────────────────
#: Glucose zones in mmol/L, low to high; the AGP colours its bands and median by the zone they pass through.
ZONES = (("very_low", -1.0, 3.0), ("low", 3.0, 3.9), ("target", 3.9, 10.0), ("high", 10.0, 13.9),
         ("very_high", 13.9, 99.0))


def _clip_strip(poly: list[tuple[float, float]], y0: float, y1: float) -> list[tuple[float, float]]:
    """A polygon clipped to the horizontal strip y0 <= y <= y1 (Sutherland-Hodgman against two edges)."""
    def clip(points, inside, cross):
        out = []
        for i, cur in enumerate(points):
            prev = points[i - 1]
            if inside(cur):
                if not inside(prev):
                    out.append(cross(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(cross(prev, cur))
        return out

    def at_y(y):
        def cross(a, b):
            t = (y - a[1]) / (b[1] - a[1]) if b[1] != a[1] else 0.0
            return (a[0] + (b[0] - a[0]) * t, y)
        return cross

    pts = clip(poly, lambda p: p[1] >= y0, at_y(y0))
    return clip(pts, lambda p: p[1] <= y1, at_y(y1)) if pts else []


def _zoned_polyline(points: list[tuple[float, float]], bounds: list[tuple[str, float, float]]):
    """Split a polyline in page coordinates where it crosses a zone boundary: [(zone, [points])]."""
    def zone_of(y: float) -> str:
        for name, top, bottom in bounds:             # page y grows downwards: top < bottom
            if top <= y <= bottom:
                return name
        return bounds[-1][0] if y < min(t for _, t, _ in bounds) else bounds[0][0]

    edges = sorted({e for _, top, bottom in bounds for e in (top, bottom)})
    out: list[tuple[str, list]] = []
    for i, p in enumerate(points):
        if i == 0:
            out.append((zone_of(p[1]), [p]))
            continue
        prev = points[i - 1]
        lo, hi = sorted((prev[1], p[1]))
        crossings = [e for e in edges if lo < e < hi]
        crossings.sort(reverse=p[1] < prev[1])
        for e in crossings:
            t = (e - prev[1]) / (p[1] - prev[1])
            q = (prev[0] + (p[0] - prev[0]) * t, e)
            out[-1][1].append(q)
            nxt = e + (0.01 if p[1] > prev[1] else -0.01)
            out.append((zone_of(nxt), [q]))
        out[-1][1].append(p)
    return out


# ── AGP layout ───────────────────────────────────────────────────────────────
def _scale(unit: str, axis: dict) -> tuple[float, float]:
    """(ceiling in the unit, factor from mmol/L)."""
    return (axis["ceiling_mgdl"], 18.0156) if unit == "mg/dL" else (axis["ceiling_mmol"], 1.0)


def _agp_page(page: fitz.Page, report: dict) -> None:
    st, pal = report["labels"], report["palette"]
    w = page.rect.width
    _text(page, MARGIN, 46, report["title"], 15, _rgb(pal["title"]))

    # Top right: patient, period and active time on ruled lines, then the glucose metrics panel.
    x0 = w / 2 + 6
    y = 68
    for line in report["header_lines"]:
        _text(page, x0, y, line, 8.5)
        _line(page, (x0, y + 4), (w - MARGIN, y + 4))
        y += 15
    y += 4
    _rect(page, fitz.Rect(x0, y, w - MARGIN, y + 15), fill=PANEL)
    _text(page, x0 + 5, y + 11, st["metrics_title"], 9)
    y += 28
    mode = report.get("metrics_mode", "dots")
    if mode == "table":                                   # label | value | reference (the Chinese consensus)
        cols = st["metrics_cols"]
        _text(page, x0 + 4, y, cols[0], 7, GREY)
        _text(page, w - MARGIN - 70, y, cols[1], 7, GREY, "right")
        _text(page, w - MARGIN - 4, y, cols[2], 7, GREY, "right")
        y += 13
    if mode == "cards":                                   # value over label, two to a row
        cw = (w - MARGIN - x0) / 2
        for i, m in enumerate(report["metrics"]):
            cx, cy = x0 + cw * (i % 2), y + 34 * (i // 2)
            _rect(page, fitz.Rect(cx + 2, cy - 10, cx + cw - 2, cy + 20), color=RULE)
            _text(page, cx + 8, cy + 3, m["value"], 11)
            _text(page, cx + 8, cy + 15, m["label"], 6.5, GREY)
        y += 34 * ((len(report["metrics"]) + 1) // 2) + 6
    else:
        for m in report["metrics"]:
            if mode == "table":
                _text(page, x0 + 4, y, m["label"], 8)
                _text(page, w - MARGIN - 70, y, m["value"], 8.5, align="right")
                _text(page, w - MARGIN - 4, y, m["goal"], 7.5, GREY, "right")
            else:
                room = w - MARGIN - x0 - _w(m["label"], 8) - _w(m["value"], 9) - 18
                _text(page, x0 + 4, y, f"{m['label']} {'.' * max(3, int(room / _w('.', 8)))}", 8)
                _text(page, w - MARGIN - 4, y, m["value"], 9, align="right")
            for note in m["notes"]:
                y += 10
                _text(page, x0 + 4, y, note, 6.5, GREY)
            y += 15
    top_end = y

    # Top left: time in ranges, each band with its value and goal, brackets over the paired bands.
    lx = MARGIN
    _rect(page, fitz.Rect(lx, 56, w / 2 - 8, 71), fill=PANEL)
    _text(page, lx + 5, 67, st["tir_title"], 9)
    _text(page, w / 2 - 12, 67, st["tir_goals"], 6.5, GREY, "right")
    bar = fitz.Rect(lx + 34, 86, lx + 58, max(top_end - 30, 250))
    order = report.get("band_order", ["very_high", "high", "target", "low", "very_low"])
    heights = {k: max(report["ranges"][k], 1.0) for k in order}
    total = sum(heights.values())
    yb, spans = bar.y0, {}
    for k in order:
        h = bar.height * heights[k] / total
        _rect(page, fitz.Rect(bar.x0, yb, bar.x1, yb + h), fill=_rgb(pal["bands"][k]), color=(1, 1, 1), width=0.8)
        spans[k] = (yb, yb + h)
        yb += h
    ticks = report["tir_ticks"]
    tick_ys = _spread([spans[k][1] + 2.5 for k, _ in ticks], 8, bar.y0, bar.y1 + 6)
    for (_, tick), ty in zip(ticks, tick_ys):
        _text(page, bar.x0 - 4, ty, tick, 6.5, GREY, "right")
    ys = _spread([(spans[k][0] + spans[k][1]) / 2 for k in order], 24.0, bar.y0 + 6, bar.y1 - 2)
    tx = bar.x1 + 10
    widest = max(_w(report["tir_rows"][k][0], 8) for k in order)
    vx = tx + max(widest + 14 + (_w("00% (00h00min)", 8.5) if report.get("tir_wide") else _w("100%", 9)), 112)
    for k, yy in zip(order, ys):
        name, value, goal = report["tir_rows"][k]
        _text(page, tx, yy, name, 7.5 if report.get("tir_wide") else 8)
        _text(page, vx, yy, value, 8.5, align="right")
        if goal:
            _text(page, tx, yy + 9, goal, 6.5, GREY)
    for (a, b), parts in report["tir_brackets"]:
        ya, yz = ys[order.index(a)] - 8, ys[order.index(b)] + 10
        bx = vx + 8
        shape = page.new_shape()
        shape.draw_polyline([(bx, ya), (bx + 4, ya), (bx + 4, yz), (bx, yz)])
        shape.finish(color=GREY, width=0.5, closePath=False)
        shape.commit()
        for i, part in enumerate(parts):
            _text(page, bx + 8, (ya + yz) / 2 + i * 9, part, 7.5 if i == 0 else 6.5, INK if i == 0 else GREY)
    for i, note in enumerate(report["tir_notes"]):
        _text(page, lx, bar.y1 + 14 + 9 * i, note, 6.5, GREY)
    y = max(top_end, bar.y1 + 14 + 9 * len(report["tir_notes"])) + 6

    # The ambulatory glucose profile across the page.
    _rect(page, fitz.Rect(MARGIN, y, w - MARGIN, y + 15), fill=PANEL)
    _text(page, MARGIN + 5, y + 11, st["agp_title"], 9)
    note = _wrap(st["agp_note"], 6.5, w - 2 * MARGIN)
    for i, line in enumerate(note):
        _text(page, MARGIN, y + 25 + 8 * i, line, 6.5, GREY)
    extra = 24 if report.get("period_means") else 0
    chart = fitz.Rect(MARGIN + 34, y + 34 + 8 * len(note) + extra, w - MARGIN - 26, y + 200 + 8 * len(note) + extra)
    if report.get("period_means"):
        label, cells = report["period_means"]
        _text(page, chart.x0, chart.y0 - 17, label, 6.5)
        cw = chart.width / len(cells)
        for i, cell in enumerate(cells):
            _text(page, chart.x0 + cw * (i + 0.5), chart.y0 - 5, cell, 6.5, align="center")
    _agp_chart(page, chart, report)
    y = chart.y1 + 22
    if report.get("legend"):
        swatches = (report["palette"]["outer"]["target"], report["palette"]["inner"]["target"],
                    report["palette"]["median"]["target"], "#E3E3E2")
        for i, (text, colour) in enumerate(zip(report["legend"], swatches)):
            lx0 = MARGIN + 34 + i * 120
            _rect(page, fitz.Rect(lx0, y - 1, lx0 + 12, y + 5), fill=_rgb(colour))
            _text(page, lx0 + 16, y + 5, text, 6.5)
        y += 16

    # Daily profiles: two rows of seven.
    _rect(page, fitz.Rect(MARGIN, y, w - MARGIN, y + 15), fill=PANEL)
    _text(page, MARGIN + 5, y + 11, st["daily_title"], 9)
    _text(page, MARGIN, y + 25, st["daily_note"], 6.5, GREY)
    _daily(page, fitz.Rect(MARGIN + 18, y + 32, w - MARGIN, page.rect.height - 34), report)


def _agp_chart(page: fitz.Page, rect: fitz.Rect, report: dict) -> None:
    pal, unit, axis = report["palette"], report["unit"], report["axis"]
    ceiling, factor = _scale(unit, axis)

    def xm(minute: float) -> float:
        return rect.x0 + rect.width * minute / 1440

    def yv(mmol: float) -> float:
        return rect.y1 - rect.height * min(mmol * factor, ceiling) / ceiling

    _rect(page, rect, color=RULE)
    for h in range(3, 24, 3):
        _line(page, (xm(h * 60), rect.y0), (xm(h * 60), rect.y1), color=(0.9, 0.9, 0.9), width=0.3)
    ticks = axis["ticks_mgdl"] if unit == "mg/dL" else axis["ticks_mmol"]
    for t in ticks:
        label = f"{t:g}" if unit == "mg/dL" else f"{t:.1f}"
        _text(page, rect.x0 - 4, rect.y1 - rect.height * t / ceiling + 2.5, label, 6.5, GREY, "right")
    _text(page, rect.x0 + 3, rect.y0 + 9, unit, 6.5, GREY)
    for i, label in enumerate(report["x_labels"]):
        _text(page, xm(i * 180), rect.y1 + 10, label, 6.5, INK, "center")
    pk = report.get("pkeys", ("p5", "p25", "p50", "p75", "p95"))
    t_lo, t_hi = report.get("target_band", (3.9, 10.0))
    pts = [(p["minute"] + 7.5, p) for p in report["profile"] if not math.isnan(p[pk[2]])]
    if not pts:
        return
    bounds = [(name, yv(hi), yv(lo)) for name, lo, hi in ZONES]
    for lo_key, hi_key, shade in ((pk[0], pk[4], "outer"), (pk[1], pk[3], "inner")):
        poly = [(xm(m), yv(p[hi_key])) for m, p in pts] + [(xm(m), yv(p[lo_key])) for m, p in reversed(pts)]
        for name, top, bottom in bounds:
            part = _clip_strip(poly, top, bottom)
            if len(part) >= 3:
                shape = page.new_shape()
                shape.draw_polyline(part)
                shape.finish(color=None, fill=_rgb(pal[shade][name]), closePath=True)
                shape.commit()
    for v in (t_lo, t_hi):
        _line(page, (rect.x0, yv(v)), (rect.x1, yv(v)), color=_rgb(pal["target_line"]), width=0.9)
    for zone, seg in _zoned_polyline([(xm(m), yv(p[pk[2]])) for m, p in pts], bounds):
        if len(seg) > 1:
            shape = page.new_shape()
            shape.draw_polyline(seg)
            shape.finish(color=_rgb(pal["median"][zone]), width=1.8, closePath=False)   # paths close by default
            shape.commit()
    _text(page, rect.x0 + 3, (yv(t_lo) + yv(t_hi)) / 2 + 3, report["labels"]["target_range"], 6.5,
          _rgb(pal["target_line"]))
    last = pts[-1][1]
    keys = tuple(reversed(pk))
    for key, y in zip(keys, _spread([yv(last[k]) + 2.5 for k in keys], 7, rect.y0 + 4, rect.y1)):
        _text(page, rect.x1 + 3, y, key[1:] + "%", 6, GREY)


def _daily(page: fitz.Page, area: fitz.Rect, report: dict) -> None:
    pal, unit, axis = report["palette"], report["unit"], report["axis"]
    ceiling, factor = _scale(unit, axis)
    tile_w = area.width / 7
    tile_h = min(80.0, (area.height - 44) / 2)
    for i, (weekday, number, trace) in enumerate(report["daily"][:14]):
        r, c = divmod(i, 7)
        x0 = area.x0 + c * tile_w
        y0 = area.y0 + r * (tile_h + 22)
        rect = fitz.Rect(x0 + 1, y0 + 10, x0 + tile_w - 1, y0 + 10 + tile_h)
        if r == 0:
            _text(page, (rect.x0 + rect.x1) / 2, y0 + 6, weekday, 6.5, GREY, "center")

        def yv(mmol: float, rect=rect) -> float:
            return rect.y1 - rect.height * min(mmol * factor, ceiling) / ceiling

        t_lo, t_hi = report.get("target_band", (3.9, 10.0))
        _rect(page, fitz.Rect(rect.x0, yv(t_hi), rect.x1, yv(t_lo)), fill=(0.91, 0.91, 0.90))
        _rect(page, rect, color=RULE, width=0.4)
        _text(page, rect.x0 + 2, rect.y0 + 7, number, 6, GREY)
        _text(page, (rect.x0 + rect.x1) / 2, rect.y1 + 7, report["noon"], 5, GREY, "center")
        if c == 0:
            for v in (t_lo, t_hi):
                tick = f"{v * factor:.0f}" if unit == "mg/dL" else f"{v:.1f}"
                _text(page, rect.x0 - 2, yv(v) + 2, tick, 5, GREY, "right")
        segment, prev = [], None
        shape = page.new_shape()
        for t, v in trace:
            x = rect.x0 + rect.width * (t.hour * 60 + t.minute + t.second / 60) / 1440
            if prev is not None and (t - prev).total_seconds() > 1800:
                if len(segment) > 1:
                    shape.draw_polyline(segment)
                segment = []
            segment.append((x, yv(v)))
            prev = t
        if len(segment) > 1:
            shape.draw_polyline(segment)
        shape.finish(color=_rgb(pal["daily_line"]), width=0.6, closePath=False)
        shape.commit()


# ── Tables ───────────────────────────────────────────────────────────────────
def _table(page: fitz.Page, y: float, first_w: float, second_w: float | None, header: list[str],
           rows: list[list[str]], bands: dict[int, str] | None = None, size: float = 6.5) -> float:
    """A ruled table across the page; `bands` puts a shaded section label before the given row index."""
    w = page.rect.width
    fixed = first_w + (second_w or 0)
    n_rest = len(header) - (2 if second_w else 1)
    widths = [first_w] + ([second_w] if second_w else []) + [(w - 2 * MARGIN - fixed) / max(n_rest, 1)] * n_rest
    xs = [MARGIN]
    for cw in widths:
        xs.append(xs[-1] + cw)
    lines = [("header", header)]
    for i, row in enumerate(rows):
        if bands and i in bands:
            lines.append(("band", [bands[i]]))
        lines.append(("row", row))
    top = y
    for kind, cells in lines:
        if kind != "row":
            _rect(page, fitz.Rect(MARGIN, y, w - MARGIN, y + 16), fill=PANEL)
        if kind == "band":
            _text(page, MARGIN + 3, y + 11, cells[0], size + 0.5)
        else:
            for ci, cell in enumerate(cells):
                if ci == 0:
                    _text(page, xs[0] + 3, y + 11, cell, size + 0.5)
                else:
                    _text(page, (xs[ci] + xs[ci + 1]) / 2, y + 11, cell, size, align="center")
        _line(page, (MARGIN, y + 16), (w - MARGIN, y + 16), width=0.4)
        y += 16
    for x in xs:
        _line(page, (x, top), (x, y), width=0.4)
    _line(page, (MARGIN, top), (w - MARGIN, top), width=0.4)
    return y


class _Pager:
    """Lays blocks down landscape pages, opening a new page when the next piece does not fit; a table that
    crosses a page break repeats its header row."""

    BOTTOM = 34

    def __init__(self, doc: fitz.Document, title: str, header: str):
        self.doc, self.title, self.header = doc, title, header
        self.pages: list[fitz.Page] = []
        self.new_page()

    def new_page(self) -> None:
        self.page = self.doc.new_page(width=A4L.width, height=A4L.height)
        self.page.insert_font(fontname="cjk", fontbuffer=_font().buffer)
        self.pages.append(self.page)
        _text(self.page, MARGIN, 46, self.title, 12)
        if self.header:
            _text(self.page, A4L.width - MARGIN, 46, self.header, 7.5, GREY, "right")
        self.y = 62

    def room(self, height: float) -> None:
        if self.y + height > A4L.height - self.BOTTOM:
            self.new_page()

    def heading(self, text: str) -> None:
        self.room(40)
        _rect(self.page, fitz.Rect(MARGIN, self.y, A4L.width - MARGIN, self.y + 15), fill=PANEL)
        _text(self.page, MARGIN + 5, self.y + 11, text, 9)
        self.y += 22

    def lines(self, lines: list[str]) -> None:
        for line in lines:
            self.room(13)
            _text(self.page, MARGIN + 4, self.y + 8, line, 8)
            self.y += 13
        self.y += 4

    def table(self, block: dict) -> None:
        rows, bands = block["rows"], block.get("bands") or {}
        i = 0
        while i < len(rows):
            self.room(16 * 3)
            fit = max(1, int((A4L.height - self.BOTTOM - self.y) // 16) - 1 - sum(1 for k in bands if k >= i))
            part = rows[i:i + fit]
            part_bands = {k - i: v for k, v in bands.items() if i <= k < i + len(part)}
            self.y = _table(self.page, self.y, block.get("first_w", 118), None, block["columns"], part, part_bands,
                            size=6)
            i += len(part)
            if i < len(rows):
                self.new_page()
        for note in block.get("footnotes", []):
            self.room(12)
            _text(self.page, MARGIN, self.y + 12, note, 6.5, GREY)
            self.y += 10
        self.y += 16


def _table_pages(doc: fitz.Document, spec_: dict) -> list[fitz.Page]:
    """Statistics pages: a title, the report's header on one line at the right, then blocks in order —
    headings, lines of text, and tables (`columns`, `rows`, optional section `bands`, `footnotes`)."""
    pager = _Pager(doc, spec_["title"], "   ".join(spec_.get("header_lines", [])))
    blocks = spec_.get("blocks") or [{"kind": "table", **{k: spec_[k] for k in ("columns", "rows")},
                                      "bands": spec_.get("bands"), "footnotes": spec_.get("footnotes", [])}]
    for block in blocks:
        if block["kind"] == "heading":
            pager.heading(block["text"])
        elif block["kind"] == "lines":
            pager.lines(block["lines"])
        else:
            pager.table(block)
    return pager.pages


# ── The hospital's CGM report sheet ──────────────────────────────────────────
def _sheet_page(page: fitz.Page, report: dict) -> None:
    st = report["labels"]
    w = page.rect.width
    _text(page, w / 2, 44, report["institution"], 13, align="center")
    _text(page, w / 2, 62, report["title"], 11, align="center")
    y = 82
    col = (w - 2 * MARGIN) / 5
    for i, (k, v) in enumerate(report["header"]):
        _text(page, MARGIN + col * (i % 5), y + 14 * (i // 5), f"{k}{st['colon']}{v}", 7.5)
    y += 14 * math.ceil(len(report["header"]) / 5) + 4
    _line(page, (MARGIN, y), (w - MARGIN, y), color=INK, width=0.8)
    y = _table(page, y + 6, 160, 62, [st["item"], st["normal"]] + report["day_labels"], report["rows"]) + 18
    summary = _wrap(report["summary"], 8, w - 2 * MARGIN)
    for i, line in enumerate(summary):
        _text(page, MARGIN, y + 12 * i, line, 8)
    y += 12 * len(summary) + 18
    for i, (k, v) in enumerate(report["signatures"]):
        _text(page, MARGIN + i * (w - 2 * MARGIN) / 3, y, f"{k}{st['colon']}{v}", 8)


def render(report: dict) -> bytes:
    """`report` from `cgm_reports.build`. Returns the PDF bytes."""
    doc = fitz.open()
    box = A4L if report["layout"] == "sheet" else A4P
    page = doc.new_page(width=box.width, height=box.height)
    page.insert_font(fontname="cjk", fontbuffer=_font().buffer)
    (_sheet_page if report["layout"] == "sheet" else _agp_page)(page, report)
    for extra in report.get("extra_pages", []):
        _table_pages(doc, extra)
    n = doc.page_count
    for i, page in enumerate(doc, start=1):           # furniture last, once the page count is known
        if report["banner"]:
            _text(page, MARGIN, 14, BANNER, 6, GREY)
        if report.get("page_label"):
            _text(page, page.rect.width - MARGIN, 14, report["page_label"].format(i=i, n=n), 7, GREY, "right")
        for j, line in enumerate(report["footer"]):
            _text(page, MARGIN, page.rect.height - 16 + 8 * j, line, 6, GREY)
    stamp = report["created"].strftime("%Y%m%d%H%M%S")
    doc.set_metadata({"title": report["title"], "author": report["author"], "subject": "SYNTHETIC",
                      "keywords": "synthetic; mirobody-gen", "creator": "mirobody-gen", "producer": "mirobody-gen",
                      "creationDate": f"D:{stamp}", "modDate": f"D:{stamp}"})
    doc.subset_fonts()
    return doc.tobytes(garbage=4, deflate=True, no_new_id=True)


def text_of(pdf: bytes) -> str:
    with fitz.open("pdf", pdf) as d:
        return "\n".join(p.get_text() for p in d)
