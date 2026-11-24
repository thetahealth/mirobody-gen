"""PDF layers: a hybrid page must reach OCR; a searchable scan and a templated page must not need it."""
from __future__ import annotations

import pathlib
import random

import fitz
import pytest

from mirobody_gen import layout, pairs, person
from mirobody_gen.corpus import record
from mirobody_gen.delivery import render_doc
from mirobody_gen.document import build_doc, group_of

SEED = 7
SCAN = {"tier": "T2", "scene": "flatbed_scan", "severity": "moderate", "ops": [], "annotations": []}
DELIVERIES = {
    "scan": {**SCAN, "container": "scanpdf"},
    "hybrid": {**SCAN, "container": "hybridpdf"},
    "searchable": {**SCAN, "container": "ocrpdf"},
    "letterhead": {"tier": "T0", "scene": None, "severity": None, "ops": [], "container": "pdf", "annotations": [],
                   "background": "letterhead"},
}


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    root = tmp_path_factory.mktemp("layers")
    p = person.build_cohort(SEED, 4)[0]
    enc = next(e for e in person.encounters_for(p, SEED) if len(e.readings) >= 8)
    registry = layout.build_registry(SEED)
    family = pairs.clean_family(registry.family(registry.pools[("zh", "hospital")][0]))
    groups = list(dict.fromkeys(group_of(r.key) for r in enc.readings))
    doc = build_doc(random.Random("layers"), "d", p, enc, groups, family, {}, banner=False)
    out = {}
    for name, delivery in DELIVERIES.items():
        path, pages, delivery = render_doc(doc, root, name, delivery=dict(delivery))
        out[name] = (fitz.open(path), record(doc, path, root, pages, [enc], delivery=delivery))
    return doc, out


def _layers(page):
    images = [fitz.Rect(info["bbox"]) for info in page.get_image_info()]
    words = page.get_text("words")
    on_image = sum(len(w[4]) for w in words if any(fitz.Rect(w[:4]).intersects(r) for r in images))
    cover = sum(r.width * r.height for r in images) / (page.rect.width * page.rect.height)
    return page.get_text(), on_image, cover


def _truth(doc) -> list[str]:
    return [t for p in doc.printed if p.readable for t in (p.item_name, p.item_value) if len(t) >= 3]


def test_a_hybrid_page_has_a_text_layer_that_holds_no_result_and_sits_off_the_image(rendered):
    doc, out = rendered
    pdf, rec = out["hybrid"]
    for page in pdf:
        text, on_image, cover = _layers(page)
        assert len("".join(text.split())) > 40              # past a "this page has text" threshold
        assert on_image == 0 and cover > 0.5                # the text is in the margins of a page-sized scan
        assert not [t for t in _truth(doc) if t in text]
    assert rec["pdf_layers"] == {"container": "hybridpdf", "text_layer": "furniture", "text_on_image": False,
                                 "needs_ocr": True, "live_text": rec["pdf_layers"]["live_text"]}
    assert rec["pdf_layers"]["live_text"] and rec["printed_rows"] == out["scan"][1]["printed_rows"]


def test_the_negative_controls_carry_the_page_text_on_the_image(rendered):
    doc, out = rendered
    for name in ("searchable", "letterhead"):
        pdf, rec = out[name]
        for page in pdf:
            text, on_image, cover = _layers(page)
            assert cover > 0.95 and on_image > 0.8 * len("".join(text.split())), name
        text = "".join(page.get_text() for page in pdf)
        assert all(t in text for t in _truth(doc)), name
        assert rec["pdf_layers"]["needs_ocr"] is False


def test_an_image_only_scan_has_no_text_layer(rendered):
    _, out = rendered
    pdf, rec = out["scan"]
    assert not "".join(page.get_text() for page in pdf).strip() and "pdf_layers" not in rec


def test_the_readability_audit_reads_a_hybrid_page_from_pixels_and_checks_the_controls(rendered, tmp_path):
    import json

    from mirobody_gen.audit import readability

    _, out = rendered
    root = pathlib.Path(out["hybrid"][0].name).parent
    manifest = root / "layers.jsonl"
    manifest.write_text("".join(json.dumps(out[n][1], ensure_ascii=False) + "\n"
                                for n in ("hybrid", "searchable", "letterhead")), encoding="utf-8")
    stats: dict = {}
    assert readability.audit(manifest, stats=stats) == 0
    assert stats["image_files_skipped"] == 1 and stats["checked_rows"] > 0


def test_every_institution_stamps_past_the_text_threshold():
    from mirobody_gen import spec

    t = spec.templates()
    for lang in ("zh", "en"):
        shortest = min((i["name"] for i in spec.fiction()["institutions"]), key=len)
        stamp = [f"{shortest}  {min(t['doc_titles'][lang], key=len)}", t["page_number"][lang],
                 t["printed_at"][lang].format(t="2024-01-02 10:11:12"), "报告时间: 2024-01-02"]
        assert len("".join("".join(stamp).split())) > 50, lang


def test_the_classifier_reads_each_layered_delivery_as_what_it_is(rendered):
    from mirobody_gen.render import layers

    _, out = rendered
    got = {name: layers.classify(pdf.tobytes()) for name, (pdf, _) in out.items()}
    assert got == {"scan": "image_only", "hybrid": "hybrid", "searchable": "searchable", "letterhead": "background"}


def test_scanned_pdfs_become_hybrid_at_the_measured_share_and_only_where_a_mode_allows():
    from mirobody_gen import delivery, spec, surface

    scanned = {c["word"]: c for c in spec.delivery()["pdf_layers"]["scanned"]}
    share = scanned["hybridpdf"]["share"] / sum(c["share"] for c in scanned.values() if c["tier"] == "observed")
    try:
        for mode in ("off", "realistic", "fuzz"):
            surface.set_mode(mode)
            drawn = [delivery.pdf_layer(f"d{i}", {"tier": "T2", "container": "scanpdf"})["container"]
                     for i in range(3000)]
            born = [delivery.pdf_layer(f"d{i}", {"tier": "T0", "container": "pdf"}).get("background")
                    for i in range(3000)]
            if mode == "off":
                assert set(drawn) == {"scanpdf"} and not any(born)
            elif mode == "realistic":
                assert set(drawn) == {"scanpdf", "hybridpdf"} and not any(born)       # nothing unobserved
                assert abs(drawn.count("hybridpdf") / len(drawn) - share) < 0.03
            else:
                assert set(drawn) == set(scanned) and any(born)
    finally:
        surface.set_mode("realistic")
