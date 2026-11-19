"""Device units: an app's unit changes the number it sends, never the night it describes."""
from __future__ import annotations

import random

import pytest

from mirobody_gen import devices, person, spec, surface

SEED = 7


@pytest.fixture(autouse=True)
def _restore_mode():
    yield
    surface.set_mode("realistic")


def _sent(mode: str) -> list[list[dict]]:
    surface.set_mode(mode)
    out = []
    for p in person.build_cohort(SEED, 60):
        records = devices.series_for(p, SEED, "en")["records"]
        out.append(devices.sent_units(records, random.Random(f"device-unit:{SEED}:{p.person_id}")))
    return out


def _sleep(people: list[list[dict]]) -> list[list[dict]]:
    return [[r for r in recs if r["_metric"] == "sleep"] for recs in people if any(r["_metric"] == "sleep" for r in recs)]


def test_off_sends_the_series_untouched():
    surface.set_mode("off")
    p = person.build_cohort(SEED, 60)[0]
    records = devices.series_for(p, SEED, "en")["records"]
    assert devices.sent_units(records, random.Random(0)) is records


def test_realistic_sends_the_canonical_unit_and_records_it():
    for nights in _sleep(_sent("realistic")):
        for r in nights:
            assert r["unit"] == "min" and r["_canonical"] == {"value": r["value"], "unit": "min"}


def test_fuzz_converts_every_night_from_its_canonical_minutes():
    per_minute = {c["unit"]: c["per_minute"] for c in spec.surface()["device_units"]["sleep"]["choices"]}
    people = _sleep(_sent("fuzz"))
    assert {r["unit"] for nights in people for r in nights} == set(per_minute)
    for nights in people:
        for r in nights:
            minutes = r["_canonical"]["value"]
            assert r["value"] == pytest.approx(minutes * per_minute[r["unit"]], abs=0.01)


def test_a_unit_switches_at_most_once_and_sometimes_does():
    switched = 0
    for nights in _sleep(_sent("fuzz")):
        units = [r["unit"] for r in nights]
        changes = sum(a != b for a, b in zip(units, units[1:]))
        assert changes <= 1
        switched += changes
    assert switched


def test_other_metrics_are_not_touched():
    surface.set_mode("off")
    before = [devices.series_for(p, SEED, "en")["records"] for p in person.build_cohort(SEED, 60)]
    after = _sent("fuzz")
    for old, new in zip(before, after):
        assert [r for r in old if r["_metric"] != "sleep"] == [r for r in new if r["_metric"] != "sleep"]
