"""Naming classes with a pair: an abbreviation variant against a control that prints the catalogue's own."""
from __future__ import annotations

import pytest

from mirobody_gen import pairs, person, surface
from mirobody_gen.harness import score

SEED = 7


def _render_stub(doc, out_root, rel, *, delivery=None):
    delivery = delivery or {"tier": "T0", "scene": None, "severity": None,
                            "ops": [], "container": "pdf", "annotations": []}
    return out_root / (rel + ".pdf"), 1, delivery


@pytest.fixture(scope="module")
def fuzz_pairs(tmp_path_factory):
    surface.set_mode("fuzz")
    try:
        cohort = person.build_cohort(SEED, 12)
        encounters = {p.person_id: person.encounters_for(p, SEED) for p in cohort}
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(pairs, "render_doc", _render_stub)
            records = pairs.render_pairs(SEED, cohort, encounters, tmp_path_factory.mktemp("pairs"), 8)
    finally:
        surface.set_mode("realistic")
    groups: dict[str, dict[str, dict]] = {}
    for r in records:
        groups.setdefault(r["pair"]["pair_id"], {})[r["pair"]["variant"]] = r
    return groups


def _names(record) -> list[str]:
    return [row["item_name"] for row in record["printed_rows"]]


def _meaning(record) -> list[tuple]:
    return [(r["key"], r["canonical_value"], r["role"]) for r in record["readings"]]


def test_an_abbreviation_variant_differs_from_its_own_control_in_abbreviations_only(fuzz_pairs):
    name, control = "name.abbr_variant", pairs.CONTROL + "name.abbr_variant"
    found = [g for g in fuzz_pairs.values() if name in g]
    assert found, "no fuzz pair drew an abbreviation variant"
    for group in found:
        variant, ctrl = group[name], group[control]
        assert variant["pair"]["control"] == control and ctrl["pair"]["controls"] == name
        assert ctrl["presentation"]["purpose"] == "ordinary" and variant["presentation"]["purpose"] == "stress"
        assert _meaning(variant) == _meaning(ctrl) == _meaning(group["base"])
        changed = [(a, b) for a, b in zip(_names(ctrl), _names(variant)) if a != b]
        assert changed and len(_names(ctrl)) == len(_names(variant))
        assert all(a.split("(")[0] == b.split("(")[0] for a, b in changed)     # the native name is the same
        assert all("name.abbr_variant" in row["hazards"] for row in variant["printed_rows"]
                   if row["item_name"] not in _names(ctrl))


def test_a_control_without_its_variant_is_never_written(fuzz_pairs):
    for group in fuzz_pairs.values():
        for variant, record in group.items():
            if controls := record["pair"].get("controls"):
                assert controls in group, variant


def test_paired_effects_subtract_a_variant_from_its_own_control(fuzz_pairs):
    group = next(g for g in fuzz_pairs.values() if "name.abbr_variant" in g)
    records = list(group.values())
    fields = ("item_name", "item_value", "item_unit", "item_range", "is_abnormal")
    perfect = {r["file"]: [{k: row[k] for k in fields} for row in r["printed_rows"] if row["readable"]]
               for r in records}
    missed = {**perfect, group["name.abbr_variant"]["file"]: []}       # the extractor drops every renamed row
    effects = score.paired_effects(records, missed)
    assert pairs.CONTROL + "name.abbr_variant" not in effects
    assert effects["name.abbr_variant"]["delta_recall"] < 0
    assert score.paired_effects(records, perfect)["name.abbr_variant"]["delta_recall"] == 0


def test_a_synonym_variant_renames_native_names_against_the_base(tmp_path, monkeypatch):
    from mirobody_gen import spec
    from mirobody_gen.llm import propose

    monkeypatch.setattr(spec, "GENERATED", tmp_path / "surface_generated.json")
    spec.surface.cache_clear()
    words = {"wbc": "白细胞总计数", "hgb": "血色素", "plt": "血小板总数", "alt": "谷丙转氨酶活性", "crea": "血肌酐值"}
    propose.write("synonym", {k: {w: {"m"}} for k, w in words.items() if k in spec.indicators()}, {},
                  {"judge": "j", "date": "2026-11-24"})
    spec.surface.cache_clear()
    surface.set_mode("fuzz")
    try:
        cohort = person.build_cohort(SEED, 12)
        encounters = {p.person_id: person.encounters_for(p, SEED) for p in cohort}
        monkeypatch.setattr(pairs, "render_doc", _render_stub)
        records = pairs.render_pairs(SEED, cohort, encounters, tmp_path, 8)
    finally:
        surface.set_mode("realistic")
        spec.surface.cache_clear()
    groups: dict[str, dict[str, dict]] = {}
    for r in records:
        groups.setdefault(r["pair"]["pair_id"], {})[r["pair"]["variant"]] = r
    found = [g for g in groups.values() if "name.synonym" in g]
    assert found, "no fuzz pair drew a synonym"
    for group in found:
        variant, base = group["name.synonym"], group["base"]
        assert "control" not in variant["pair"] and _meaning(variant) == _meaning(base)
        changed = {(a, b) for a, b in zip(_names(base), _names(variant)) if a != b}
        assert changed and {b for _, b in changed} <= set(words.values())
        assert all("name.synonym" in row["hazards"] for row in variant["printed_rows"] if row["item_name"] in words.values())
