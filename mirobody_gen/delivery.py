"""Delivery tiers: whether a PDF reaches the user as a text-layer PDF, a scan, a phone photo, a degraded copy or a screenshot.

Tier weights, scene weights, severity distribution, rasterization DPI and pen-circling rate all live in
`resources/delivery.json` (hand-written, calibrated against the reference corpus's aggregate image
statistics in `numbers_images.json`; see docs/zh-CN/degradation.md §3). Each person gets an additional
log-normal perturbation on top (`upload_habit`): some always photograph, others only ever upload PDFs.
"""

from __future__ import annotations

import math
import pathlib
import random

from . import spec
from .document import Doc
from .model import Person
from .render import degrade, pdf, sheet


def _d() -> dict:
    return spec.delivery()


def page_break_check(doc: Doc, data: bytes) -> None:
    """PDF page break: a continuation page carries a table's rows but not its header -> `table.page_break_loses_header`."""
    import fitz

    with fitz.open("pdf", data) as pdf_doc:
        if pdf_doc.page_count < 2:
            return
        for page in list(pdf_doc)[1:]:
            text = page.get_text()
            for table in doc.tables:
                if not table.headers or not table.rows:
                    continue
                header = next((h for h in table.headers[0] if h), "")
                names = [doc.printed[c.printed].item_name for c in table.rows if c.printed is not None]
                if header and header not in text and any(n and n.split("(")[0] in text for n in names):
                    doc.mark("table.page_break_loses_header", "content")
                    return


def upload_habit(seed: int, person: Person) -> dict[str, float]:
    """One person's upload habit: some always photograph, some only upload PDFs, some only screenshot.
    Applies a log-normal perturbation to the tier weights per person; the overall mean stays TIER_WEIGHTS."""
    rng = random.Random(f"habit:{seed}:{person.person_id}")
    raw = {t: w * math.exp(rng.gauss(0, 0.9)) for t, w in _d()["tier_weights"].items()}
    total = sum(raw.values())
    return {t: w / total for t, w in raw.items()}


def choose_delivery(rng: random.Random, habit: dict[str, float], doc: Doc, pages: int) -> dict:
    """What form this PDF-family file is delivered in. A multi-page file can only be scanned whole
    (scanpdf) or kept as a text layer: one phone photo per page would split a file into several
    images, which would require splitting the truth too, and this version does not do that."""
    weights = dict(habit)
    if pages > 1:
        weights = {t: w for t, w in weights.items() if t in ("T0", "T2")}
    tiers = list(weights)
    tier = rng.choices(tiers, weights=[weights[t] for t in tiers])[0]
    if tier == "T0":
        return {"tier": "T0", "scene": None, "severity": None, "ops": [], "container": "pdf", "annotations": []}
    scenes = _d()["scene_weights"][tier]
    scene = rng.choices(list(scenes), weights=list(scenes.values()))[0]
    severity = rng.choices(list(_d()["severity_weights"]), weights=list(_d()["severity_weights"].values()))[0]
    if tier == "T2":
        container = "scanpdf" if pages > 1 or rng.random() < 0.5 else "jpg"
    elif tier == "T6":
        container = "png" if scene in ("screenshot", "desktop_screenshot") and rng.random() < 0.7 else "jpg"
    else:
        container = "jpg"
    return {"tier": tier, "scene": scene, "severity": severity, "ops": [], "container": container, "annotations": []}


def render_doc(doc: Doc, out_root: pathlib.Path, rel: str, habit: dict[str, float] | None = None,
               rng: random.Random | None = None, delivery: dict | None = None
               ) -> tuple[pathlib.Path, int | None, dict]:
    """Render one file. Returns (path, page count, delivery record). When `delivery` is given, it is used
    as-is (for contrast pairs)."""
    reported = next((d["iso"] for d in doc.dates if d["role"] == "reported"),
                    doc.dates[0]["iso"] if doc.dates else "2026-01-01T00:00:00")
    fmt = doc.family.fmt
    if doc.kind not in ("lab_slip", "export"):
        fmt = "pdf"                      # report books, medical records, ECG and ultrasound reports have no table export
    (out_root / rel).parent.mkdir(parents=True, exist_ok=True)
    pages: int | None = None
    if fmt == "xlsx":
        data = sheet.to_xlsx(doc, reported, with_preamble=doc.kind != "export")
        path = out_root / f"{rel}.xlsx"
        path.write_bytes(data)
        return path, None, {"tier": "T1", "scene": None, "severity": None, "ops": [], "container": "xlsx", "annotations": []}
    if fmt == "csv":
        data = sheet.to_csv(doc, with_preamble=doc.kind != "export" and doc.family.fmt == "csv")
        path = out_root / f"{rel}.csv"
        path.write_bytes(data)
        return path, None, {"tier": "T1", "scene": None, "severity": None, "ops": [], "container": "csv", "annotations": []}

    data, pages = pdf.render(doc, reported)
    page_break_check(doc, data)
    if delivery is None:
        delivery = choose_delivery(rng, habit, doc, pages) if (habit and rng) else \
            {"tier": "T0", "scene": None, "severity": None, "ops": [], "container": "pdf", "annotations": []}
    if delivery["tier"] == "T0":
        path = out_root / f"{rel}.pdf"
        path.write_bytes(data)
        return path, pages, delivery

    # Image layer: optionally circle abnormal values in pen first, then rasterize, run the scene's
    # operator chain, and encode.
    marked: list[str] = []
    if delivery["tier"] in ("T2", "T3") and rng is not None and rng.random() < _d()["annotate_rate"]:
        targets = [p.item_value for p in doc.printed if p.is_abnormal == "1" and p.readable]
        if targets:
            data, marked = degrade.annotate(data, doc.doc_id, targets)
    dpi = _d()["dpi_of_tier"][delivery["tier"]]
    pages_img = degrade.rasterize(data, dpi)
    margins = degrade.content_margins(data, dpi)
    out_pages, ops = degrade.apply_scene(pages_img, delivery["scene"], delivery["severity"], doc.doc_id, margins)
    container = delivery["container"]
    blob = degrade.encode(out_pages, container, doc.title)
    ext = {"scanpdf": "pdf", "jpg": "jpg", "png": "png"}[container]
    path = out_root / f"{rel}.{ext}"
    path.write_bytes(blob)
    delivery = {**delivery, "ops": ops, "annotations": marked, "dpi": dpi,
                "image_size": list(out_pages[0].size)}
    return path, pages, delivery


