"""Clinical profile, checkup content: text-only ultrasound, both kidneys and thyroid lobes described, ECG
off the lab tables, and no H. pylori antibody beside a breath test."""
from __future__ import annotations

import random
from datetime import date

import pytest

from mirobody_gen import book, clinical, clinical_checkup, document, layout, person, spec
from mirobody_gen.model import Finding, Person

DAY = date(2025, 3, 2)
ONE_SIDED = ["kidney_stone", "kidney_cyst", "thyroid_nodule"]


@pytest.fixture
def clinical_on(monkeypatch):
    monkeypatch.setattr(clinical, "ENABLED", True)


def _finding(fid, side="左", side_en="left", item="kidneys"):
    return Finding(fid, "abd_us", item, "", None, params={"side": side, "side_en": side_en, "mm": 5})


def _family(lang="zh"):
    registry = layout.Registry(7)
    return registry.family(registry.pools[(lang, "checkup_center")][0])


@pytest.mark.parametrize("fid", ONE_SIDED)
def test_a_one_sided_finding_is_followed_by_the_other_side(fid):
    x = _finding(fid)
    zh, en = clinical_checkup.contralateral(x, [x], "zh"), clinical_checkup.contralateral(x, [x], "en")
    assert zh.startswith(("右", "甲状腺右")) and "左" not in zh
    assert en.startswith(" The right ") and "left" not in en
    assert clinical_checkup.contralateral(x, [x, _finding("kidney_cyst", "右", "right")], "zh") == ""


def test_findings_that_name_no_side_get_no_sentence():
    x = _finding("thyroid_diffuse", item="thyroid")
    assert clinical_checkup.contralateral(x, [x], "zh") == ""


def _woman():
    return Person("checkup-contract", "female", 1972, 162, "healthy", "test")


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_the_ultrasound_report_is_text_only_and_names_both_kidneys(lang, clinical_on):
    p = _woman()
    enc = person.encounter_for(p, DAY, "routine", ["checkup_standard"], random.Random(7), package="standard")
    enc.findings = [_finding("kidney_cyst", "右", "right")]
    f = _family(lang)
    doc = document.Doc(doc_id="us-test", person_id=p.person_id, family=f, title="", subject=[], dates=[], tables=[])
    for s in range(12):
        (block,) = book.aux_blocks(random.Random(s), doc, p, enc, f, only=["abd_us"])
        assert block.image is None
        findings = dict(block.rows)[spec.narratives()["aux"]["abd_us"]["labels"][lang]["finding"]]
        assert ("左肾形态大小正常" if lang == "zh" else "The left kidney is normal") in findings


def _premium(monkeypatch, enabled):
    monkeypatch.setattr(clinical, "ENABLED", enabled)
    return person.encounter_for(_woman(), DAY, "routine", ["checkup_premium"], random.Random(7), package="premium")


def test_a_breath_test_checkup_orders_no_antibody_and_keeps_the_other_draws(monkeypatch):
    default, kept = _premium(monkeypatch, False), _premium(monkeypatch, True)
    assert kept.ordered_keys == tuple(k for k in default.ordered_keys if k != "hp_ab") != default.ordered_keys
    kept.validate_order_results()
    # The antibody's draw is still made, so the qualitative results drawn after it keep their values.
    qualitative = {r.key: r.value for r in default.readings if r.value_kind == "qualitative" and r.key != "hp_ab"}
    assert qualitative and qualitative == {r.key: r.value for r in kept.readings if r.value_kind == "qualitative"}


def test_without_a_printed_breath_test_the_antibody_stays(clinical_on):
    p = _woman()
    assert clinical_checkup.breath_test_printed(p, DAY, "premium")
    assert not clinical_checkup.breath_test_printed(p, DAY, "basic")
    enc = person.encounter_for(p, DAY, "routine", ["checkup_premium"], random.Random(7), package="basic")
    assert "hp_ab" in enc.ordered_keys
    assert "hp_ab" in person.encounter_for(p, DAY, "followup", ["hp"], random.Random(7)).ordered_keys


def test_a_checkup_book_prints_ecg_with_the_tracing_not_in_the_lab_tables(monkeypatch):
    p = _woman()
    enc = person.encounter_for(p, DAY, "routine", ["checkup_standard", "ecg"], random.Random(7), package="standard")
    ecg = {k for k, item in spec.indicators().items() if item["panel"] == "ecg"}
    groups = [g for s in document.split_encounter(random.Random(1), enc) for g in s]
    assert "ecg" in groups

    def printed(enabled):
        monkeypatch.setattr(clinical, "ENABLED", enabled)
        doc = book.build_book(random.Random(3), "book-test", p, enc, groups, _family(), {})
        return {r.key for r in doc.readings}

    assert printed(False) & ecg and not printed(True) & ecg
