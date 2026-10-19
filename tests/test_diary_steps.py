"""A diary's step count is the person's own: the same day's watch total, never an independent draw.

Before, every diary sentence that mentioned steps drew them from N(6500, 1800) whatever the person's
activity, so on seed 7 52 of 86 diary counts with a same-day watch total were more than 30% off it.
"""
from __future__ import annotations

from mirobody_gen import devices, journal, person


def _diary_and_watch(n=24):
    for p in person.build_cohort(7)[:n]:
        lang = person.person_lang(7, p.person_id)
        series = devices.series_for(p, 7, lang)
        watch = {r["time"][:10]: r["value"] for r in series["records"] if r["_metric"] == "steps"}
        for entry in journal.entries_for(p, 7, lang):
            for m in entry["expected"]:
                if m.get("key") == "steps":
                    yield p, series, watch, entry["date"], m["value"]


def test_a_diary_step_count_is_the_watch_total_of_that_day():
    paired = [(v, watch[day]) for _, _, watch, day, v in _diary_and_watch() if day in watch]
    assert paired and all(v == w for v, w in paired)


def test_without_a_watch_the_count_follows_the_persons_own_activity():
    unpaired = [(v, series["baseline"]["steps"]) for _, series, watch, day, v in _diary_and_watch(60)
                if day not in watch]
    assert unpaired and all(base / 4 < v < base * 4 for v, base in unpaired)
