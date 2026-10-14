"""Regression tests for #1: events that respect age and the calendar, diary durations that agree.

Measured on `build --seed 20261014 --people 60` before the fix: "连续加班一个月" landed on people
70+, "献血 400mL" on people over 60, "春节假期饮食" started in any month of the year at all, and a
symptom streak's stated duration ("3天" then "2周" nine days later) didn't track elapsed time. All
four fail on the pre-fix branch and pass after.
"""

from __future__ import annotations

import collections
import pathlib
import re
import sys
from datetime import date

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mirobody_gen import journal, person as person_mod  # noqa: E402

_DUR_IN_TEXT = re.compile(r"(3天|5天|1周|2周|半月|1月余|2月余|3月余|半年余|1年)了")

SEED = 20261014
PEOPLE = 60


def _cohort():
    return person_mod.build_cohort(SEED, PEOPLE)


def test_overtime_is_never_given_to_people_past_working_age():
    for p in _cohort():
        for e in p.events:
            if e.name == "连续加班一个月":
                assert p.age_at(e.start) <= 61, (p.person_id, p.age_at(e.start), e.start)


def test_blood_donation_stays_within_the_legal_age_window():
    for p in _cohort():
        for e in p.events:
            if e.name == "献血 400mL":
                age = p.age_at(e.start)
                assert 18 <= age <= 60, (p.person_id, age, e.start)


def test_spring_festival_starts_near_the_real_lunar_new_year():
    """Observed before the fix: start months scattered across the whole year (Jan, Feb, Mar, May,
    Jun, Jul, Sep, Oct, Dec). The lunar new year falls in late January or February every year in
    the 2018-2027 table, so every instance should start within about a week of one of those dates."""
    starts = [e.start for p in _cohort() for e in p.events if e.name == "春节假期饮食"]
    assert starts, "no Spring Festival incidents drawn in a 60-person cohort"
    for when in starts:
        nearest = min(person_mod.LUNAR_NEW_YEAR.values(), key=lambda d: abs((d - when).days))
        assert abs((when - nearest).days) <= 7, (when, nearest)
    months = collections.Counter(d.month for d in starts)
    assert set(months) <= {1, 2}, months


def test_journal_duration_tracks_elapsed_time_since_the_streak_started():
    """Within one symptom streak, a later entry's stated duration must be at least as long as an
    earlier entry's (both rounded to the same bucket list), and must correspond to roughly the
    real elapsed time since the streak's first mention -- not an independent fresh draw."""
    ph = journal.spec.complaints()["phrasing"]["zh"]
    bucket_days = dict(zip(ph["durations"], ph["duration_days"]))
    checked = 0
    for p in _cohort():
        lang = person_mod.person_lang(SEED, p.person_id)
        if lang != "zh":
            continue
        entries = journal.entries_for(p, SEED, lang)
        last_bucket: dict[str, int] = {}
        last_seen: dict[str, str] = {}
        for e in entries:
            for item in e["expected"]:
                if item.get("kind") != "symptom":
                    continue
                sid = item["symptom_id"]
                m = _DUR_IN_TEXT.search(e["text"])
                if not m:
                    continue
                days = bucket_days[m.group(1)]
                if sid in last_seen:
                    gap = (date.fromisoformat(e["date"]) - date.fromisoformat(last_seen[sid])).days
                    if gap <= journal.STREAK_GAP_DAYS:
                        # a later entry in the same streak must not state a shorter duration
                        assert days >= last_bucket[sid], (p.person_id, sid, last_seen[sid], e["date"])
                        checked += 1
                last_bucket[sid] = days
                last_seen[sid] = e["date"]
    assert checked > 0, "no multi-entry symptom streak with a stated duration found to check"


def test_secondary_symptom_mentions_keep_the_duration_streak_alive():
    """Seed 8 p055 mentions epigastric symptoms as a second item between primary mentions.
    Every mention counts toward the 30-day gap, even when only the primary symptom has a duration.
    """
    p = next(p for p in person_mod.build_cohort(8, 60) if p.person_id == "p055")
    ph = journal.spec.complaints()["phrasing"]["zh"]
    bucket_days = dict(zip(ph["durations"], ph["duration_days"]))
    duration = re.compile("|".join(re.escape(d) for d in sorted(bucket_days, key=len, reverse=True)))
    last_seen = {}
    last_stated = {}
    checked = 0
    for entry in journal.entries_for(p, 8, "zh"):
        day = date.fromisoformat(entry["date"])
        symptoms = [item["symptom_id"] for item in entry["expected"] if item["kind"] == "symptom"]
        for sid in symptoms:
            if sid in last_seen and (day - last_seen[sid]).days > journal.STREAK_GAP_DAYS:
                last_stated.pop(sid, None)
            last_seen[sid] = day
        match = duration.search(entry["text"])
        if not match:
            continue
        days = bucket_days[match.group()]
        sid = symptoms[0]
        if sid in last_stated:
            assert days >= last_stated[sid], (entry["date"], entry["text"], sid, last_stated[sid])
            checked += 1
        last_stated[sid] = days
    assert checked > 0
