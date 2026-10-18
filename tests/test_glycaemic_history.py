"""Laboratory HbA1c is the weighted history of the glycaemic state; the CGM reads the state itself.

Before, each CGM day's mean was solved to the eAG of that same day's expected HbA1c, so glucose followed
HbA1c instead of the reverse, and lab HbA1c had no memory: a 90-day onset on metformin's HbA1c effect
stood in for the lag. Now HbA1c(d) is the glycaemic state over the preceding 100 days with a linearly
falling weight (Tahara & Shima 1995), and GMI is computed from sensor readings alone.
"""

import dataclasses
import random
from datetime import datetime, time, timedelta

import pytest

from mirobody_gen import continuous, devices, person, physiology
from mirobody_gen.cgm_metrics import MGDL_PER_MMOL

METFORMIN = "开始二甲双胍"


def subject():
    p = next(x for x in person.build_cohort(7) if any(e.name == METFORMIN for e in x.events))
    return p, next(e for e in p.events if e.name == METFORMIN)


def test_hba1c_is_the_linear_weighted_state_history():
    p, m = subject()
    day = m.start + timedelta(days=50)
    n = physiology.HBA1C_WINDOW_DAYS
    states = [physiology.glycaemic_state(p, day - timedelta(days=k)) for k in range(n)]
    assert physiology.expected(p, "hba1c", day) == pytest.approx(
        sum((n - k) * s for k, s in enumerate(states)) / (n * (n + 1) / 2))


def test_hba1c_lags_the_state_after_metformin():
    p, m = subject()
    s = m.start
    state = {d: physiology.glycaemic_state(p, s + timedelta(days=d)) for d in (-1, 30, 130)}
    a1c = {d: physiology.expected(p, "hba1c", s + timedelta(days=d)) for d in (-1, 15, 30, 60, 130)}
    drop_state = state[-1] - state[30]
    assert drop_state > 0.5
    # the state has fully dropped at 30 days; the laboratory value has moved less than half of it
    assert state[-1] - a1c[30] < 0.5 * drop_state
    assert a1c[-1] > a1c[15] > a1c[30] > a1c[60] > a1c[130]
    # a hundred days after the state settled, HbA1c has caught up (only the slow trend remains)
    assert a1c[130] == pytest.approx(state[130], abs=0.03)


def test_history_window_and_future_isolation():
    p, m = subject()
    day = m.start + timedelta(days=40)
    base = physiology.expected(p, "hba1c", day)
    without = dataclasses.replace(p, events=tuple(e for e in p.events if e.name != METFORMIN))
    assert physiology.expected(without, "hba1c", day) != pytest.approx(base)          # within the window
    future = dataclasses.replace(p, events=tuple(
        dataclasses.replace(e, start=day + timedelta(days=1)) if e.name == METFORMIN else e for e in p.events))
    assert physiology.expected(future, "hba1c", day) == pytest.approx(physiology.expected(without, "hba1c", day))
    old = dataclasses.replace(p, events=tuple(
        dataclasses.replace(e, start=day - timedelta(days=200)) if e.name == METFORMIN else e for e in p.events))
    # an effect that settled more than a window ago is fully absorbed: the untreated history times
    # the effect (the slow trend's own lag is shared by both)
    assert physiology.expected(old, "hba1c", day) == pytest.approx(
        physiology.expected(without, "hba1c", day) * (1 + m.effects["hba1c"][0]))


def test_cgm_follows_the_state_while_hba1c_lags():
    p, m = subject()
    life = continuous.Life(7, p, devices.series_for(p, 7, person.person_lang(7, p.person_id)))

    def cgm_mean(day):
        bg = continuous.glucose_minutes(life, datetime.combine(day, time()), 1440)
        return sum(bg) / len(bg)

    before, after = m.start - timedelta(days=5), m.start + timedelta(days=35)
    for day in (before, after):  # each day's sensor mean is the state's eAG (the meal solver's target)
        assert cgm_mean(day) == pytest.approx(1.59 * physiology.glycaemic_state(p, day) - 2.59, abs=0.4)
    gmi_drop = 0.02392 * MGDL_PER_MMOL * (cgm_mean(before) - cgm_mean(after))
    a1c_drop = physiology.expected(p, "hba1c", before) - physiology.expected(p, "hba1c", after)
    # GMI comes from the readings: five weeks in it has moved about twice as far as laboratory HbA1c
    assert gmi_drop > 1.5 * a1c_drop > 0


def test_measured_hba1c_is_drawn_around_the_history():
    p, m = subject()
    day = m.start + timedelta(days=45)
    draws = [physiology.measure(random.Random(i), p, "hba1c", day) for i in range(4000)]
    assert sum(draws) / len(draws) == pytest.approx(physiology.expected(p, "hba1c", day), rel=0.01)


def test_an_interval_in_the_hba1c_lag_is_attributed_to_the_treatment():
    _, m = subject()
    s = m.start
    # the glucose state settled at 30 days, but laboratory HbA1c is still moving until about 130
    assert person._changing(m, s + timedelta(days=40), s + timedelta(days=120))
    assert not person._changing(m, s + timedelta(days=140), s + timedelta(days=300))
