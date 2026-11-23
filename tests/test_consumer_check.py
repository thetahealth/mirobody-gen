"""consumer-check scores mirobody's answers against the build's intent; mirobody itself is faked here."""
from __future__ import annotations

from types import SimpleNamespace

from mirobody_gen.harness import consumer_check as cc


def _sig(text, count, period, unit="d", slots=None, as_needed=False):
    return {"text": text, "slots_per_day": slots,
            "timing": {"count": count, "period": period, "period_unit": unit, "as_needed": as_needed}}


ROWS = [_sig("每日2次，每次1片", 2, 1, slots=2), _sig("一日2次，每次1片", 2, 1, slots=2),
        _sig("隔日1次，每次1片", 1, 2), _sig("每周1次，每次1片", 1, 1, "wk"),
        _sig("每周2次，每次1片", 2, 1, "wk"), _sig("必要时，每日最多3次", 3, 1, as_needed=True)]
#: What a consumer made of each spelling: doses a day, days between doses, as needed; None for no parse
READ = {"每日2次，每次1片": (2, None, False), "一日2次，每次1片": (0, None, False), "隔日1次，每次1片": (1, 2, False),
        "每周1次，每次1片": (1, 7, False), "每周2次，每次1片": None, "必要时，每日最多3次": (0, None, True)}


def test_instructions_separate_a_wrong_reading_from_a_declined_one():
    def parse(text):
        return None if READ[text] is None else (text,)

    def describe(text):
        per_day, period, as_needed = READ[text]
        return SimpleNamespace(doses_per_day=per_day, period_days=period, as_needed=as_needed)

    lines = cc.instructions(ROWS, parse, describe)
    assert lines[0] == "6 spellings · right 4 · wrong 1 · declined 1"
    assert "  ✗ 一日2次，每次1片: 0 a day every 1 days" in lines
    assert "  - 每周2次，每次1片 (declined)" in lines


def test_layers_compare_the_ocr_decision_with_the_truth(tmp_path):
    for name in ("a", "b", "c"):
        (tmp_path / f"{name}.pdf").write_bytes(name.encode())
    records = [{"file": "a.pdf", "pdf_layers": {"container": "hybridpdf", "needs_ocr": True}},
               {"file": "b.pdf", "pdf_layers": {"container": "hybridpdf", "needs_ocr": True}},
               {"file": "c.pdf", "pdf_layers": {"container": "ocrpdf", "needs_ocr": False}}]
    lines = cc.layers(records, tmp_path, lambda data: data in (b"a", b"c"))
    assert lines == ["  hybridpdf: missed OCR 1", "  hybridpdf: ok 1", "  ocrpdf: needless OCR 1"]
