"""Delivery tiers: whether a PDF reaches the user as a text-layer PDF, a scan, a phone photo, a degraded copy or a screenshot.

交付形态：一份 PDF 家族的文件到用户手里是文本层 PDF、扫描件、手机照片、劣化件还是屏幕截图。

分层权重、场景权重、严重度分布、栅格化分辨率与笔圈率都在 `resources/delivery.json`
（手写，校准依据是参考集图像的聚合统计 `numbers_images.json`，见 docs/zh-CN/degradation.md §3）。
每个人再叠一层对数正态扰动（`upload_habit`）：有人总是拍照，有人只传 PDF。
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
    """PDF 跨页：续页上出现了某张表的行，却没有那张表的表头 → `table.page_break_loses_header`。"""
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
    """一个人上传文件的习惯：有人总是拍照，有人只传 PDF，有人全是截图。
    按人给分层权重加一个对数正态扰动，整体均值仍是 TIER_WEIGHTS。"""
    rng = random.Random(f"habit:{seed}:{person.person_id}")
    raw = {t: w * math.exp(rng.gauss(0, 0.9)) for t, w in _d()["tier_weights"].items()}
    total = sum(raw.values())
    return {t: w / total for t, w in raw.items()}


def choose_delivery(rng: random.Random, habit: dict[str, float], doc: Doc, pages: int) -> dict:
    """这份 PDF 家族的文件以什么形态交付。多页文件只能整体扫描（scanpdf）或保持文本层：
    手机一页一张照片会把一份文件拆成多个图像，真值随之要拆，这一版不做。"""
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
    """渲染一份文件。返回 (路径, 页数, 交付记录)。`delivery` 给定时按它来（对照对用）。"""
    reported = next((d["iso"] for d in doc.dates if d["role"] == "reported"),
                    doc.dates[0]["iso"] if doc.dates else "2026-01-01T00:00:00")
    fmt = doc.family.fmt
    if doc.kind not in ("lab_slip", "export"):
        fmt = "pdf"                      # 报告书、病历、心电图与超声报告没有表格导出这回事
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

    # 图像层：先（可能）用笔圈出异常值，再栅格化、跑场景算子链、编码
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


