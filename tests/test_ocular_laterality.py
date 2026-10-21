from datetime import date
from pathlib import Path
import random

import pytest

from mirobody_gen import book, corpus, layout, person
from mirobody_gen.document import Doc


def _case(lang="zh", seed=0):
    p = person.build_cohort(7, 1)[0]
    enc = person.encounter_for(p, date(2025, 3, 2), "routine", ["checkup"], random.Random(7), "standard")
    registry = layout.Registry(7)
    family = registry.family(registry.pools[(lang, "checkup_center")][0])
    doc = Doc("eye-test", p.person_id, family, "eye", [], [], [], kind="checkup_book")
    doc.blocks = book.section_blocks(random.Random(seed), doc, p, enc, family)
    return p, enc, doc


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_acuity_pairs_always_name_both_eyes(lang):
    _, _, doc = _case(lang)
    labels = book._n()["book"][lang]
    for seed in range(60):
        text, left, right = book._acuity_pair(random.Random(seed), False, doc.family)
        assert labels["left"]+left in text or labels["left_eye"]+" "+left in text
        assert labels["right"]+right in text or labels["right_eye"]+" "+right in text


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_iop_pair_is_labelled_and_has_exact_side_unit_truth(lang):
    for seed in range(30):
        p, enc, doc = _case(lang, seed)
        eye = next(b for b in doc.blocks if b.section_id == "eye")
        item = next((x for x in eye.truth if x["label"] == ("眼压" if lang=="zh" else "Intraocular pressure")), None)
        if item is None: continue
        measurements = item["measurements"]
        assert item["laterality_policy_version"] == "ocular-laterality-v1"
        assert [x["laterality"] for x in measurements] == ["left", "right"]
        assert all(x["unit"] == "mmHg" and x["status"] == "measured" and
                   x["notation"] is None and x["authority"] == "synthetic_model" for x in measurements)
        labels = book._n()["book"][lang]
        assert labels["left"]+measurements[0]["value"] in item["value"]
        assert labels["right"]+measurements[1]["value"] in item["value"]
        record = corpus.record(doc, Path("/out/eye.pdf"), Path("/out"), 1, [enc])
        actual = next(x for b in record["blocks"] if b["section"] == "eye" for x in b["items"] if x.get("item_id") == "iop")
        assert actual["measurements"] == measurements
        return
    pytest.fail("fixture never selected IOP")


def test_omitted_optional_items_are_unknown_not_normal_or_not_measured():
    for seed in range(30):
        _, enc, doc = _case(seed=seed)
        record = corpus.record(doc, Path("/out/eye.pdf"), Path("/out"), 1, [enc])
        context = record["ocular_exam"]
        missing = context["not_reported"]
        if not missing: continue
        assert context["policy_version"] == "ocular-laterality-v1"
        assert all(x["left_status"] == x["right_status"] == "unknown" for x in missing)
        assert all("value" not in x and "abnormal" not in x for x in missing)
        assert {x["item_id"] for x in missing} <= {"va_corrected", "iop"}
        return
    pytest.fail("fixture never omitted an optional item")


def test_non_eye_file_does_not_claim_ocular_coverage():
    _, enc, doc = _case()
    doc.blocks = [b for b in doc.blocks if b.section_id != "eye"]
    assert "ocular_exam" not in corpus.record(doc, Path("/out/eye.pdf"), Path("/out"), 1, [enc])


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_side_labels_are_in_actual_pdf_text(lang):
    import pymupdf
    from mirobody_gen.render import pdf
    _, _, doc = _case(lang)
    payload, pages = pdf.render(doc, "2025-03-02")
    with pymupdf.open(stream=payload, filetype="pdf") as rendered:
        text = "".join(p.get_text() for p in rendered)
    eye = next(b for b in doc.blocks if b.section_id == "eye")
    for item in eye.truth:
        if item.get("item_id") not in ("va", "va_corrected", "iop"): continue
        assert item["value"].replace(" ", "") in "".join(text.split())
    # This also guards against a vacuous check if side metadata never reached the block.
    assert any(x.get("item_id") == "va" for x in eye.truth) and pages >= 1
