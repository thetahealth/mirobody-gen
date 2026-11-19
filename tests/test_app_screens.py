"""Consumer health-app screenshots: one smart-scale page under each of its tabs, fuzz builds only."""
from __future__ import annotations

import pytest

from mirobody_gen import app_screens, devices, person, spec, surface

SEED = 7
TABS = ["weight", "fat_mass", "muscle_mass"]
#: The body-composition codes a device chart carries, never a laboratory analyte's
DEVICE_LOINC = {"weight": "29463-7", "fat_mass": "73708-0", "muscle_mass": "73964-9"}


@pytest.fixture(autouse=True)
def _restore_mode():
    yield
    surface.set_mode("realistic")


def _eligible(mode: str, seed: int = SEED, people: int = 20):
    surface.set_mode(mode)
    for p in person.build_cohort(seed, people):
        lang = person.person_lang(seed, p.person_id)
        series = devices.series_for(p, seed, lang)
        if app_screens.drawn(seed, p, series):
            yield p, lang, series


@pytest.fixture(scope="module")
def shots(tmp_path_factory):
    p, lang, series = next(_eligible("fuzz"))
    root = tmp_path_factory.mktemp("app")
    out = app_screens.records(SEED, p, series, lang, root / "files", root, banner=True)
    surface.set_mode("realistic")
    return p, lang, series, root, out


def test_only_fuzz_builds_draw_the_page():
    assert next(_eligible("fuzz"), None) is not None
    assert next(_eligible("realistic"), None) is None
    assert next(_eligible("off"), None) is None


def test_one_screenshot_per_tab_with_the_active_tab_in_truth(shots):
    _, _, _, root, out = shots
    labels = [t[spec.doc_lang(out[0]["language"][:2])] for t in spec.app_screens()["tabs"]]
    assert [r["readings"][0]["key"] for r in out] == TABS
    assert [r["screen"]["active_tab"] for r in out] == labels
    assert all(r["screen"]["tabs"] == labels for r in out)
    for r in out:
        assert r["kind"] == "app_screenshot" and r["coding_context"] == "device"
        assert (root / r["file"]).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_chart_is_the_active_tab_metric_coded_as_a_device_reading(shots):
    _, _, _, _, out = shots
    for r, tab in zip(out, TABS):
        assert {x["key"] for x in r["readings"]} == {tab}
        assert {x["loinc"] for x in r["readings"]} == {DEVICE_LOINC[tab]}
        assert {x["source"] for x in r["readings"]} == {"consumer_app"}
        assert len(r["readings"]) == app_screens.POINTS
        # Every chart point is printed under the tab's own label, nowhere a name of its own
        assert [row["item_value"] for row in r["printed_rows"]] == [x["value_text"] for x in r["readings"]]
        assert {row["item_name"] for row in r["printed_rows"]} == {r["screen"]["active_tab"]}
    assert len({tuple(x["canonical_value"] for x in r["readings"]) for r in out}) == len(TABS)


def test_the_weight_chart_is_the_device_series_and_the_masses_fit_inside_it(shots):
    _, _, series, _, out = shots
    weighs = sorted((r["time"][:10], round(r["value"], 1)) for r in series["records"] if r["_metric"] == "weight")
    weight, fat, muscle = ([(x["observed"], x["canonical_value"]) for x in r["readings"]] for r in out)
    assert weight == weighs[-app_screens.POINTS:]
    for (_, w), (_, f), (_, m) in zip(weight, fat, muscle):
        assert 0 < f < w and 0 < m < w - f + 0.1


def test_the_summary_card_is_context_identical_on_every_tab(shots):
    _, _, _, _, out = shots
    assert all(r["screen_context"] == out[0]["screen_context"] for r in out)
    card = {c["role"]: c for c in out[0]["screen_context"] if c["role"] in ("baseline", "goal")}
    latest = {c["key"]: c for c in out[0]["screen_context"] if c["role"] == "latest"}
    assert "loinc" not in card["goal"] and "loinc" not in card["baseline"]   # targets, not measurements
    last = out[0]["readings"][-1]
    assert latest["weight"]["value"] == last["value_text"] and latest["weight"]["observed"] == last["observed"]
    assert set(latest) == {"weight", "body_fat", "bmi"}
    assert all(x["role"] == "export" for r in out for x in r["readings"])


@pytest.mark.parametrize("seed", [7, 14])
def test_a_goal_is_a_healthy_weight_and_only_a_lean_person_aims_to_gain(seed):
    for p, _, series in _eligible("fuzz", seed, 60):
        page = app_screens._page(seed, p, series)
        floor = 18.5 * (p.height_cm / 100) ** 2
        assert page["goal"] >= round(floor) and abs(page["goal"] - page["baseline"]) >= 1, p.person_id
        assert page["goal"] < page["baseline"] or page["baseline"] - floor < 4, p.person_id


def test_screenshots_are_deterministic(shots, tmp_path):
    p, lang, series, root, out = shots
    again = app_screens.records(SEED, p, series, lang, tmp_path / "files", tmp_path, banner=True)
    assert [{k: v for k, v in r.items() if k != "file"} for r in again] == \
           [{k: v for k, v in r.items() if k != "file"} for r in out]
    assert [(tmp_path / r["file"]).read_bytes() for r in again] == [(root / r["file"]).read_bytes() for r in out]
