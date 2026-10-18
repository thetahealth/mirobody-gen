"""Physical page preservation for explicit image delivery requests."""
from __future__ import annotations

import copy
from datetime import date

import fitz
import pytest
from PIL import Image

from mirobody_gen import corpus, delivery, layout, pairs
from mirobody_gen.document import Doc
from mirobody_gen.model import Encounter
from mirobody_gen.render import degrade


@pytest.mark.parametrize("container", ["jpg", "png"])
def test_single_image_encoder_rejects_multiple_pages(container):
    pages = [Image.new("RGB", (80, 80), "red"), Image.new("RGB", (80, 80), "blue")]
    with pytest.raises(ValueError, match="multi-page"):
        degrade.encode(pages, container, "synthetic page test")


@pytest.mark.parametrize("container,scene,tier", [("jpg", "flatbed_scan", "T2"), ("png", "screenshot", "T6")])
def test_explicit_capture_keeps_both_pages_and_records_fallback(container, scene, tier, tmp_path, monkeypatch):
    source = fitz.open()
    for color in [(1, 0, 0), (0, 0, 1)]:
        page = source.new_page(width=80, height=120)
        page.draw_rect(page.rect, color=color, fill=color)
    data = source.tobytes()
    monkeypatch.setattr(delivery.pdf, "render", lambda *a: (data, 2))
    family = pairs.clean_family(layout.build_registry(7).family(0))
    doc = Doc("page-test", "p-test", family, "Synthetic pages", [], [], [])
    request = {"tier": tier, "scene": scene, "severity": "moderate", "container": container,
               "ops": [], "annotations": []}
    original_request = copy.deepcopy(request)
    path, pages, result = delivery.render_doc(doc, tmp_path, "capture", delivery=request)
    assert request == original_request
    assert pages == 2
    with fitz.open(path) as delivered:
        assert len(delivered) == 2
        assert not any(page.get_text().strip() for page in delivered)
        # Check distinct content and order, not only two possibly duplicated pages.
        first, last = [p.get_pixmap() for p in delivered]
        rgb_first = first.pixel(first.width // 2, first.height // 2)
        rgb_last = last.pixel(last.width // 2, last.height // 2)
        assert rgb_first[0] > rgb_first[2]
        assert rgb_last[2] > rgb_last[0]
    enc = Encounter("p-test", date(2026, 1, 1), "specialty", "hospital", ("cbc",))
    record = corpus.record(doc, path, tmp_path, pages, [enc], delivery=result)
    assert record["container_fallback"] == {
        "policy_version": "all-pages-v1", "requested": container,
        "actual": "scanpdf", "reason": "multiple_pages"}
