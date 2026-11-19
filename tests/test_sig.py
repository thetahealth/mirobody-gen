"""Medication instructions: every spelling of a plan carries the intent of its canonical text."""
from __future__ import annotations

import pytest

from mirobody_gen import medications, person, sig, spec, surface

SEED = 20261014


@pytest.fixture(autouse=True)
def _restore_mode():
    yield
    surface.set_mode("realistic")


def _plans(mode: str) -> list[dict]:
    surface.set_mode(mode)
    return [plan for p in person.build_cohort(SEED, 60) for plan in medications.plans_for(p, SEED)]


def _entries(plans: list[dict]) -> list[dict]:
    return [e for plan in plans for e in (plan, *plan["changes"])]


def _words(tiers: set[str]) -> set[str]:
    return {c["word"] for a in spec.surface()["sig"]["axes"].values() for c in a["choices"] if c["tier"] in tiers}


def test_canonical_texts_round_trip():
    canonical = {axis: a["choices"][0]["word"] for axis, a in spec.surface()["sig"]["axes"].items()}
    texts = {e["dose_text_zh"] for e in _entries(_plans("off"))}
    texts |= {p["dose_text_zh"] for p in spec.cohort()["medication_plans"].values()}
    for text in texts:
        assert sig.write(sig.parse(text), canonical) == text


def test_off_adds_nothing():
    for entry in _entries(_plans("off")):
        assert not {"timing", "slots_per_day", "dose_per_slot", "sig_form"} & set(entry)


@pytest.mark.parametrize("mode", ["realistic", "fuzz"])
def test_every_spelling_keeps_the_canonical_intent(mode):
    off, spelled = _plans("off"), _plans(mode)
    assert [p["plan_id"] for p in off] == [p["plan_id"] for p in spelled]
    for before, after in zip(_entries(off), _entries(spelled)):
        intent = sig.parse(before["dose_text_zh"])
        assert {k: after[k] for k in ("timing", "slots_per_day", "dose_per_slot")} == intent.truth()
        assert after["dose_text_en"] == before["dose_text_en"]


def test_realistic_keeps_thinly_observed_axes_canonical():
    """The reference set holds a handful of complete instructions, under `min_support` per axis."""
    axes = spec.surface()["sig"]["axes"]
    assert all(sum(c["share"] for c in a["choices"] if c["tier"] == "observed") < spec.surface()["min_support"]
               for a in axes.values())
    for plan in _plans("realistic"):
        assert plan["sig_form"] == {axis: axes[axis]["choices"][0]["word"] for axis in plan["sig_form"]}


def test_min_support_gates_only_realistic_shares():
    thin = [{"word": "a", "tier": "observed", "share": 2}, {"word": "b", "tier": "observed", "share": 1},
            {"word": "c", "tier": "field", "share": 0}]
    surface.set_mode("realistic")
    assert surface.allowed(thin) == thin[:1]
    assert surface.allowed([{**c, "share": c["share"] * 10} for c in thin])[1]["word"] == "b"
    surface.set_mode("fuzz")
    assert surface.allowed(thin) == thin


def test_fuzz_reaches_field_spellings():
    forms = {w for p in _plans("fuzz") for w in p["sig_form"].values()}
    assert forms & (_words({"field"}) - _words({"observed"}))


def test_a_plan_and_its_changes_share_one_dialect():
    for plan in _plans("fuzz"):
        for change in plan["changes"]:
            assert change["dose_text_zh"].startswith(plan["sig_form"]["day_word"])


@pytest.mark.parametrize("mode", ["realistic", "fuzz"])
def test_pairs_change_one_axis_and_keep_the_truth(mode):
    surface.set_mode(mode)
    rows = sig.pairs()
    bases = {r["pair_id"]: r for r in rows if r["variant"] == "base"}
    for r in rows:
        base = bases[r["pair_id"]]
        assert {k: r[k] for k in ("timing", "slots_per_day", "dose_per_slot")} == \
               {k: base[k] for k in ("timing", "slots_per_day", "dose_per_slot")}
        changed = [a for a in r["sig_form"] if r["sig_form"][a] != base["sig_form"][a]]
        assert len(changed) == (r["variant"] != "base")
    allowed = _words(set(spec.surface()["modes"][mode]))
    assert all(set(r["sig_form"].values()) <= allowed for r in rows)


def test_fuzz_pairs_cover_the_reported_spellings():
    surface.set_mode("fuzz")
    texts = {r["text"] for r in sig.pairs()}
    assert {"每日2次，每次1片", "每天2次，每次1片", "一天2次，每次1片", "一日2次，每次1片", "每日 2 次，每次1片",
            "每日2次，一次1片", "每周2次，每次1片", "隔一日一次，每次1片"} <= texts
    weekly = next(r for r in sig.pairs() if r["text"] == "每周2次，每次1片")
    assert weekly["slots_per_day"] is None and weekly["timing"]["period_unit"] == "wk"


def test_non_canonical_text_is_refused():
    with pytest.raises(ValueError):
        sig.parse("每天2次，每次1片")


def test_every_other_day_spells_only_once():
    form = {"every_other_day": "隔日1次", "dose_word": "每次"}
    assert sig.write(sig.Intent(1, "1", "片", period=2), form) == "隔日1次，每次1片"
    with pytest.raises(ValueError):
        sig.write(sig.Intent(2, "1", "片", period=2), form)
