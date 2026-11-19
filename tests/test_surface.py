"""The expression layer: tiers gate what each mode draws, and every written form keeps its meaning."""
from __future__ import annotations

import dataclasses
import random

import pytest

from mirobody_gen import layout, pairs, person, spec, surface
from mirobody_gen.document import build_doc, group_of, print_name


@pytest.fixture(autouse=True)
def _restore_mode():
    yield
    surface.set_mode("realistic")


def _zh_families(mode: str, seed: int = 7, n: int = 600) -> list[layout.Family]:
    surface.set_mode(mode)
    registry = layout.build_registry(seed)
    zh = [i for (group, _), idx in registry.pools.items() if group == "zh" for i in idx]
    return [f for f in (registry.family(i) for i in sorted(zh)[:n]) if f.language == "zh-Hans"]


def _clean(group: str = "zh", language: str = "zh-Hans") -> layout.Family:
    registry = layout.build_registry(7)
    return next(pairs.clean_family(f) for f in map(registry.family, registry.pools[(group, "hospital")])
                if f.language == language)


def _visit():
    """A routine visit with chemistry results, and a clean Chinese hospital layout to print it with."""
    seed = 7
    cohort = person.build_cohort(seed, 8)
    for p in cohort:
        for enc in person.encounters_for(p, seed):
            panels = {spec.indicators()[r.key]["panel"] for r in enc.readings}
            if {"chemistry", "vitals"} <= panels:
                groups = list(dict.fromkeys(group_of(r.key) for r in enc.readings))
                return p, enc, groups, _clean()
    raise AssertionError("no visit with chemistry and vitals results")


def _doc(family, stream: str = "surface-test"):
    p, enc, groups, _ = _visit()
    return build_doc(random.Random(stream), "d", p, enc, groups, family, {}, banner=False)


def _assert_attribution_is_true(doc, base):
    """A row carries a naming class exactly when its printed name differs from the clean layout's."""
    for row, clean in zip(doc.printed, base.printed):
        named = [h for h in row.hazards if h.startswith("name.")]
        assert bool(named) == (row.item_name != clean.item_name), (row.item_name, clean.item_name, named)


def test_resource_contract():
    s = spec.surface()
    assert s["modes"]["realistic"] == ["observed"] and s["modes"]["off"] == []
    assert set(s["modes"]["fuzz"]) == set(s["tiers"])
    choices = s["name_join"]["choices"] + s["name_suffix"]["choices"] + s["name_marker"]["choices"]
    choices += [c for a in s["sig"]["axes"].values() for c in a["choices"]]
    choices += [c for a in s["device_units"].values() for c in a["choices"]]
    assert all(c["tier"] in s["tiers"] for c in choices)
    assert all(c["share"] > 0 for c in choices if c["tier"] == "observed")
    assert all(c.get("sources") for c in choices if c["tier"] in ("reference", "field", "generated"))
    names = list(surface.hazard_classes())
    assert len(names) == len(set(names)) and all(n.startswith("name.") for n in names)


def test_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        surface.set_mode("loose")


def test_off_leaves_every_family_as_sampled():
    registry = layout.build_registry(7)
    surface.set_mode("off")
    for f in _zh_families("off", n=200):
        index = int(f.family_id[1:])
        assert f == layout.sample_family(random.Random(f"family:7:{index}"), f.family_id, registry.institutions[index])


def test_realistic_draws_only_observed_forms():
    observed = {c["word"] for c in spec.surface()["name_marker"]["choices"] if c["tier"] == "observed"}
    realistic = _zh_families("realistic")
    assert {f.name_marker for f in realistic} <= observed | {""}
    assert {f.name_join for f in realistic} > {"paren"}
    fuzz = _zh_families("fuzz")
    assert "*" in {f.name_marker for f in fuzz}                 # a field-tier form, fuzz only


def test_the_rest_of_a_layout_is_the_same_in_every_mode():
    fields = ("name_join", "name_suffix", "name_suffix_share", "name_marker", "name_marker_share")

    def strip(f):
        return dataclasses.replace(f, **{k: layout.Family.__dataclass_fields__[k].default for k in fields})

    assert [strip(f) for f in _zh_families("fuzz", n=100)] == [strip(f) for f in _zh_families("off", n=100)]


@pytest.mark.parametrize("form,expected,hazard", [
    ("paren", "{n}({a})", None),
    ("glued", "{n}{a}", "name.glued_to_abbr"),
    ("spaced", "{n} {a}", "name.spaced_abbr"),
    ("abbr_first", "{a}{n}", "name.abbr_first"),
])
def test_join_forms(form, expected, hazard):
    _, _, _, family = _visit()
    family = dataclasses.replace(family, name_style="native(abbr)", name_join=form)
    item = spec.indicators()["wbc"]
    name, abbr_cell, applied = print_name(item, family, random.Random(0))
    assert name == expected.format(n=item["zh"], a=item["abbr"])
    assert abbr_cell == ""
    assert applied == ([hazard] if hazard else [])


def test_suffix_and_marker_keep_meaning_and_explain_the_mark():
    _, _, _, family = _visit()
    base = _doc(family)
    marked = _doc(dataclasses.replace(family, name_suffix="测定", name_suffix_share=1.0,
                                      name_marker="#", name_marker_share=1.0))
    assert [r.key for r in marked.readings] == [r.key for r in base.readings]
    assert [r.canonical_value for r in marked.readings] == [r.canonical_value for r in base.readings]
    panels = set(spec.surface()["panels"])
    for before, after in zip(base.printed, marked.printed):
        key = marked.readings[after.readings[0]].key
        if spec.indicators()[key]["panel"] in panels:
            assert after.item_name == before.item_name + "测定#"
            assert {"name.assay_suffix", "name.trailing_marker"} <= set(after.hazards)
        else:
            assert after.item_name == before.item_name and not after.hazards
    assert ("", surface.footnotes(marked.family, marked.hazards)[0]) in marked.footer
    assert not surface.footnotes(base.family, base.hazards)


@pytest.mark.parametrize("mode", ["realistic", "fuzz"])
def test_every_toggle_isolates_its_class_and_keeps_semantics(mode):
    surface.set_mode(mode)
    _, _, _, family = _visit()
    base = _doc(family)
    toggles = surface.toggles()
    assert set(toggles) <= set(surface.hazard_classes())
    for name, toggle in toggles.items():
        doc = _doc(dataclasses.replace(family, **toggle(family)))
        assert name in doc.hazards, name
        assert {h for h in doc.hazards if h.startswith("name.")} == {name}, name
        _assert_attribution_is_true(doc, base)
        assert [(r.key, r.canonical_value) for r in doc.readings] == \
               [(r.key, r.canonical_value) for r in base.readings], name


@pytest.mark.parametrize("group,language", [("en", "en"), ("zh", "zh-Hant")])
def test_toggles_leave_unmeasured_layouts_alone(group, language):
    family = _clean(group, language)
    assert all(toggle(family) == {} for toggle in surface.toggles().values())


def test_drawn_forms_are_simplified_only():
    surface.set_mode("fuzz")
    registry = layout.build_registry(7)
    zh = [registry.family(i) for (g, _), idx in registry.pools.items() if g == "zh" for i in idx]
    assert all(f.name_join == "paren" and not f.name_suffix and not f.name_marker
               for f in zh if f.language == "zh-Hant")


def test_blood_pressure_label_carries_no_naming_class():
    family = dataclasses.replace(_clean(), name_style="native(abbr)", name_join="glued")
    base = dataclasses.replace(_clean(), name_style="native(abbr)")
    label = spec.templates()["bp"]["zh"]
    pairs_seen = 0
    for k in range(12):
        doc, clean = _doc(family, f"bp-{k}"), _doc(base, f"bp-{k}")
        _assert_attribution_is_true(doc, clean)
        pairs_seen += any(r.item_name == label for r in doc.printed)
    assert pairs_seen


def test_name_drift_across_revisions_is_fuzz_only():
    families = _zh_families("fuzz", n=300)
    drifted = [f for f in families if surface.revised(f, 2026)[1]]
    assert drifted
    surface.set_mode("realistic")
    assert not any(surface.revised(f, 2026)[1] for f in families)
