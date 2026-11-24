"""Catalogue additions: RDW-SD, procalcitonin, the full urine dipstick, ECG voltages, abbreviation senses."""
from __future__ import annotations

import dataclasses
import random

import pytest

from mirobody_gen import layout, person, physiology, spec, surface
from mirobody_gen.book import build_ecg_report
from mirobody_gen.document import print_name


@pytest.fixture(autouse=True)
def _restore_mode():
    yield
    surface.set_mode("realistic")


def test_confusable_senses_name_real_keys_and_agree_both_ways():
    catalogue = spec.indicators()
    drawn = spec.surface()["abbr_variants"]["indicators"]

    def printed(item):
        return {item["abbr"], *item["name_variants"], *(v["word"] for v in drawn.get(item["key"], []))}

    for key, item in catalogue.items():
        for sense in item["confusable_with"]:
            assert sense.get("key") or sense.get("note"), key
            assert sense["abbr"] in printed(item), key
            if "key" in sense:
                other = catalogue[sense["key"]]
                assert sense["abbr"] in printed(other), key                 # both print the same spelling
                assert {"abbr": sense["abbr"], "key": key} in other["confusable_with"], key
    assert catalogue["pct"]["unit"] != catalogue["procalcitonin"]["unit"]       # only the unit tells them apart


def test_new_items_are_ordered_where_real_reports_print_them():
    orders = spec.cohort()["orders"]
    assert {"urine_bil", "urine_uro", "urine_leu"} <= set(orders["urinalysis"])
    assert orders["cbc"].index("rdw_sd") == orders["cbc"].index("rdw") + 1
    assert {"rv5", "sv1"} <= set(orders["ecg"]) and "pulse" in orders["ecg"]
    assert "procalcitonin" in orders["inflammation"]
    assert all("procalcitonin" not in orders[f"checkup_{tier}"] for tier in spec.cohort()["checkup_packages"])


def test_rdw_sd_follows_rdw_cv_and_mcv():
    p = person.build_cohort(7, 1)[0]
    values = physiology.derive({"rdw": 13.0, "mcv": 90.0}, p, p.weight_anchors[0][0])
    assert values["rdw_sd"] == pytest.approx(42.0, abs=0.1)
    lo, hi = spec.reference_bounds("rdw_sd", p.sex)
    assert lo < values["rdw_sd"] < hi


def test_an_ecg_report_prints_rv5_and_sv1_by_their_bare_lead_names():
    seed = 7
    for p in person.build_cohort(seed, 30):
        for enc in person.encounters_for(p, seed):
            if {"rv5", "sv1"} <= {r.key for r in enc.readings} and enc.exam_type != "routine":
                registry = layout.build_registry(seed)
                family = registry.family(registry.pools[("zh", "hospital")][0])
                family = dataclasses.replace(family, name_style="native", columns=("name", "result", "unit"), unit_at="column")
                doc = build_ecg_report(random.Random(0), "e", p, enc, family, {}, banner=False)
                names = {doc.readings[r.readings[0]].key: r.item_name for r in doc.printed}
                assert names["rv5"] == "RV5" and names["sv1"] == "SV1"
                assert next(r.item_unit for r in doc.printed if r.item_name == "RV5") == "mV"
                return
    pytest.fail("no ECG visit with RV5 and SV1")


def _zh_hans_families(mode: str) -> list[layout.Family]:
    surface.set_mode(mode)
    registry = layout.build_registry(7)
    families = (registry.family(i) for i in registry.pools[("zh", "hospital")] + registry.pools[("zh", "checkup_center")])
    return [dataclasses.replace(f, name_style="native(abbr)", columns=("name", "result"))
            for f in families if f.language == "zh-Hans"]


def test_realistic_prints_the_catalogue_abbreviation_while_evidence_is_thin():
    for f in _zh_hans_families("realistic"):
        for key in spec.surface()["abbr_variants"]["indicators"]:
            name, _, applied = print_name(spec.indicators()[key], f, random.Random(0))
            assert spec.indicators()[key]["abbr"] in name and "name.abbr_variant" not in applied


def test_fuzz_prints_the_reported_collision_and_keeps_its_meaning():
    printed = {}
    for f in _zh_hans_families("fuzz"):
        name, _, applied = print_name(spec.indicators()["crea"], f, random.Random(0))
        printed[name] = applied
    collision = [n for n in printed if "Cre" in n and "CREA" not in n]
    assert collision and all("name.abbr_variant" in printed[n] for n in collision)
    assert all("name.abbr_variant" not in a for n, a in printed.items() if "CREA" in n or "Cr" not in n)


def test_indicators_on_their_own_stream_leave_the_cohort_as_it_was(monkeypatch):
    full = person.build_cohort(7, 12)
    older = {k: v for k, v in spec.indicators().items() if not v["own_baseline_stream"]}
    assert len(older) < len(spec.indicators())
    monkeypatch.setattr(spec, "indicators", lambda: older)
    without = person.build_cohort(7, 12)
    for a, b in zip(full, without):
        assert (a.sex, a.birth_year, a.height_cm, a.weight_anchors, a.events) == \
               (b.sex, b.birth_year, b.height_cm, b.weight_anchors, b.events)
        assert {k: a.baseline[k] for k in older} == b.baseline


def test_each_abbreviation_axis_starts_with_the_catalogue_abbreviation():
    for key, listed in spec.surface()["abbr_variants"]["indicators"].items():
        assert listed[0]["word"] == spec.indicators()[key]["abbr"], key
