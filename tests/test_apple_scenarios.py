"""Apple Health export scenarios: a long span, a re-import, a trip abroad, each with per-day truth."""
from __future__ import annotations

import re

import pytest

from mirobody_gen import apple_scenarios, devices, person

SEED = 7
RECORD = re.compile(r"^ <Record .*?(?:/>|</Record>)$", re.M | re.S)


@pytest.fixture(scope="module")
def made():
    cohort = person.build_cohort(SEED, 20)
    for p in cohort:
        lang = person.person_lang(SEED, p.person_id)
        if (out := apple_scenarios.scenarios(p, SEED, lang)) is not None:
            return p, lang, {name: (files, truth) for name, files, truth in out}
    pytest.fail("no Apple Health person with a long enough series")


def test_people_without_a_long_apple_series_get_none():
    for p in person.build_cohort(SEED, 20):
        lang = person.person_lang(SEED, p.person_id)
        series = devices.series_for(p, SEED, lang)
        if series["vendor"] != "apple":
            assert apple_scenarios.scenarios(p, SEED, lang) is None


def test_the_long_span_covers_a_year_and_its_days_match_the_device_series(made):
    p, lang, out = made
    _, truth = out["long_span"]
    assert len(truth["days"]) > apple_scenarios.MIN_SPAN_DAYS
    series = devices.series_for(p, SEED, lang)["records"]
    for metric, field in (("steps", "steps"), ("rhr", "resting_hr")):
        want = {r["time"][:10]: r["value"] for r in series if r["_metric"] == metric}
        got = {d["date"]: d[field] for d in truth["days"] if field in d}
        assert got == want, metric
    sleep = sum(r["value"] for r in series if r["_metric"] == "sleep")
    assert sum(d.get("sleep_min", 0) for d in truth["days"]) == sleep
    weights = {r["time"][:10]: round(r["value"], 1) for r in series if r["_metric"] == "weight"}
    assert {d["date"]: d["weight_kg"] for d in truth["days"] if "weight_kg" in d} == weights
    readings = {}
    for r in series:
        if r["_metric"] == "sbp":
            readings[r["time"][:10]] = readings.get(r["time"][:10], 0) + 1
    assert {d["date"]: d["bp_readings"] for d in truth["days"] if "bp_readings" in d} == readings


def test_a_reimport_repeats_every_record_of_the_first_export(made):
    _, _, out = made
    files, truth = out["reimport"]
    first, second = (RECORD.findall(files[f].decode()) for f in ("export_1.xml", "export_2.xml"))
    assert first and len(second) > len(first) and set(first) <= set(second)
    exported = re.search(r'ExportDate value="([^"]+)"', files["export_1.xml"].decode())[1]
    ends = {m for x in first for m in re.findall(r'endDate="([^"]+)"', x)}
    assert max(ends) <= exported                                  # nothing after the moment it was taken
    assert exported[:10] == truth["overlap"]["last"] == truth["exports"][0]["last"]
    assert truth["days"] == out["long_span"][1]["days"]            # merged, each day counts once


def test_a_trip_changes_the_offset_not_the_daily_figures(made):
    _, _, out = made
    files, truth = out["trip"]
    trip, home = truth["trip"], truth["home_offset"]
    stamps = re.findall(r'startDate="(\d{4}-\d\d-\d\d) [\d:]+ ([+-]\d{4})"', files["export.xml"].decode())
    assert {o for d, o in stamps if trip["first"] <= d <= trip["last"]} == {trip["offset"].replace(":", "")}
    assert {o for d, o in stamps if not trip["first"] <= d <= trip["last"]} == {home.replace(":", "")}
    for day in truth["days"]:
        assert day["offset"] == (trip["offset"] if trip["first"] <= day["date"] <= trip["last"] else home)
    strip = [{k: v for k, v in d.items() if k != "offset"} for d in truth["days"]]
    assert strip == [{k: v for k, v in d.items() if k != "offset"} for d in out["long_span"][1]["days"]]


def test_scenarios_are_deterministic(made):
    p, lang, out = made
    again = {name: files for name, files, _ in apple_scenarios.scenarios(p, SEED, lang)}
    assert again == {name: files for name, (files, _) in out.items()}
