"""Unit systems: an institution may print mg/dL; the reading it describes stays the same."""
from __future__ import annotations

import dataclasses
import random

import pytest

from mirobody_gen import layout, pairs, person, spec, surface
from mirobody_gen.document import build_doc, group_of

SEED = 7


@pytest.fixture(autouse=True)
def _restore_mode():
    yield
    surface.set_mode("realistic")


def _visit():
    for p in person.build_cohort(SEED, 10):
        for enc in person.encounters_for(p, SEED):
            if {"glu", "crea", "chol", "tbil"} <= {r.key for r in enc.readings}:
                return p, enc
    raise AssertionError("no visit with glucose, creatinine, cholesterol and bilirubin")


def _doc(unit_system: str):
    p, enc = _visit()
    registry = layout.build_registry(SEED)
    family = pairs.clean_family(registry.family(registry.pools[("en", "hospital")][0]))
    family = dataclasses.replace(family, unit_system=unit_system)
    groups = list(dict.fromkeys(group_of(r.key) for r in enc.readings))
    return p, build_doc(random.Random("units"), "d", p, enc, groups, family, {}, banner=False)


def test_conventional_rows_convert_the_page_but_not_the_reading():
    _, si = _doc("SI")
    p, conv = _doc("conventional")
    assert [r.key for r in si.readings] == [r.key for r in conv.readings]
    for a, b in zip(si.readings, conv.readings):
        assert a == b                                    # the semantic truth is canonical SI on either page
        spec_conv = spec.indicators()[a.key].get("conventional")
        if spec_conv:
            row = conv.printed[b.printed_row]
            assert row.item_unit == "mg/dL" and "unit.conventional" in row.hazards
            assert float(row.item_value) == pytest.approx(a.canonical_value * spec_conv["factor"],
                                                          abs=0.51 * 10 ** -spec_conv["decimals"])


def test_the_flag_agrees_with_the_printed_value_and_range():
    p, doc = _doc("conventional")
    for row in doc.printed:
        reading = doc.readings[row.readings[0]]
        conv = spec.indicators()[reading.key].get("conventional")
        if not conv or not row.item_range or "-" not in row.item_range:
            continue
        lo, hi = (float(x) for x in row.item_range.replace(" ", "").split("-"))
        value = float(row.item_value)
        assert row.is_abnormal == ("1" if value > hi or value < lo else "0"), (row.item_name, row.item_value)


def test_the_catalogue_names_a_mass_concentration_code_for_each_conventional_unit():
    for key, item in spec.indicators().items():
        if conv := item.get("conventional"):
            assert conv["unit"] == "mg/dL" and conv["loinc"] != item["loinc"], key
            assert conv["factor"] > 0 and conv["decimals"] >= 0, key


def test_realistic_systems_follow_the_measured_language_split():
    registry = layout.build_registry(SEED)
    systems = {group: [registry.family(i).unit_system for (g, _), idx in registry.pools.items() if g == group
                       for i in idx[:200]] for group in ("zh", "en")}
    share = {g: s.count("conventional") / len(s) for g, s in systems.items()}
    assert share["en"] > 0.4 > 0.15 > share["zh"]


def test_off_prints_si_everywhere():
    surface.set_mode("off")
    registry = layout.build_registry(SEED)
    assert {registry.family(i).unit_system for i in range(300)} == {"SI"}
    assert "unit.conventional" not in surface.toggles()


def test_a_previous_result_prints_converted_and_stays_si_in_truth():
    p, enc = _visit()
    registry = layout.build_registry(SEED)
    family = pairs.clean_family(registry.family(registry.pools[("en", "hospital")][0]))
    family = dataclasses.replace(family, unit_system="conventional",
                                 **pairs.TOGGLES["value.multiple_per_row"](family))
    groups = list(dict.fromkeys(group_of(r.key) for r in enc.readings))
    doc = build_doc(random.Random("units"), "d", p, enc, groups, family, {"glu": ("5.12", "2024-01-02")},
                    banner=False)
    previous = next(r for r in doc.readings if r.key == "glu" and r.role == "previous")
    assert (previous.canonical_value, previous.unit_ucum, previous.value_text) == (5.12, "mmol/L", "5.12")
    cell = next(c for t in doc.tables for c in t.rows if c.printed == previous.printed_row)
    assert cell.previous == "92"


def test_the_unit_toggle_switches_any_layout_to_conventional_units():
    _, base = _doc("SI")
    assert surface.toggles()["unit.conventional"](base.family) == {"unit_system": "conventional"}
