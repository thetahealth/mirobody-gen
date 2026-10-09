"""Continuous device streams: CGM sensor sessions and intraday heart rate (opt-in: `build --continuous`).

    mirobody-gen build --seed 7 --out out/p3 --continuous

The phone-store batches (`devices.py`) and the vendor payloads (`vendor_signals.py`) carry a person's
daily numbers: one resting heart rate, one step count, a cuff reading. A glucose sensor or a watch makes
something else: a curve, one reading every few minutes for days on end, with the gaps, artefacts and
export quirks of that device. This module draws those curves for the same person from the same physiology
and hands them to `continuous_formats.py`, which writes each device's own export shapes. Every device
number and every shape is sourced in `resources/streams.json` (built by `scripts/build_streams.py`), and
the URLs are listed in `docs/DEVICE_FORMATS.md`.

## One day, every stream

A day is planned once (`Life.plan`): when the person slept (the device series' sleep record when it has
one), when they ate and how much, whether they ran, how their steps were walked. The glucose sensor and the
heart-rate stream both read that plan, so an evening run is a heart-rate peak and a glucose dip at the
same minute, and the night's samples fall inside the sleep record the phone store already holds.

## Glucose

Blood glucose is built minute by minute: a basal level (the physiology model's fasting glucose for that
day, with the indicator's own within-subject variation), the dawn rise of impaired glucose metabolism, one
gamma-shaped response per meal, a dip during and after a run, and a slow physiological wander. The meal
responses are scaled so that the day's mean glucose equals the estimated average glucose of the person's
expected HbA1c that day (ADAG: eAG mmol/L = 1.59 × HbA1c − 2.59; Nathan et al., Diabetes Care
2008;31:1473), the same relation the handwritten glucose logs use. A sensor's GMI therefore tracks the
HbA1c on the person's laboratory slips, and the metformin course moves both.

The sensor reads interstitial glucose, which lags blood glucose by several minutes (a first-order lag),
through a per-sensor calibration gain that drifts over the session, correlated noise (noisier on day one),
and the occasional compression low while the wearer lies on the sensor at night. It reports nothing
during warm-up or signal loss, clips at its reportable range ("Low"/"High" text), and, for scan-based
sensors, loses history it was not scanned in time to keep.

## Heart rate

Heart rate follows the same day: below the day's resting rate while asleep, above it while awake and still,
higher in walking bouts that add up to the day's step count, and towards a running heart rate (Karvonen,
with HRmax = 208 − 0.7 × age; Tanaka et al., JACC 2001;37:153) during a run, with first-order on- and
off-kinetics. The day's resting heart rate is the device series' own record, so the curve and the daily
summary in `devices/` agree. A watch comes off to charge; a ring mostly does not.

## Truth

`continuous.jsonl`, one record per stream (a sensor session or a heart-rate window): the device, the
files it was delivered as, every sample with the value as delivered, the canonical value, the underlying
true value and its status, the gaps with their causes, the day plans that drove the curve, the summary
metrics computed from the delivered readings, and the named hazards (`docs/SCHEMA.md`).

## Isolation

Every draw comes from streams of its own (`habits:`, `plan:`, `cgm:`, `hr:`); `devices.series_for` is
read, never re-drawn. With `--continuous` off a build is byte-identical to one made without this module;
with it on, every other file is unchanged and the streams are written next to them.
"""

from __future__ import annotations

import dataclasses
import json
import math
import random
from datetime import date, datetime, time, timedelta

from . import physiology, spec
from .model import Person
from .person import CORPUS_END

#: mg/dL per mmol/L for glucose (molar mass 180.156 g/mol).
MGDL_PER_MMOL = 18.0156
#: Event names as the cohort spells them (`resources/cohort.json`); `devices.series_for` reads the same.
RUN, URI, FEAST = "开始规律跑步", "急性上呼吸道感染", "春节假期饮食"

# ── Lifestyle (hand-set; these shape a day, they are not measurements) ──────────
#: Meal clock in hours (mean, sd across people), and how much a meal raises glucose relative to lunch.
MEALS = {"breakfast": (7.3, 0.45, 0.8), "lunch": (12.2, 0.3, 1.0), "dinner": (18.8, 0.5, 1.2)}
SNACK_LOAD, LATE_SNACK_LOAD = 0.3, 0.5
#: Day-to-day spread of a meal's size (log scale) and the holiday feast's extra.
LOAD_SIGMA, FEAST_FACTOR = 0.25, 1.35
#: Cadence (steps/min) for brisk walking and easy running, and the share of a day's steps walked in bouts
#: (the rest is pottering that barely moves heart rate).
WALK_SPM, RUN_SPM, WALK_SHARE = 105, 160, 0.55

# ── Glucose model (mmol/L, minutes) ─────────────────────────────────────────────
#: A meal response peaks at `TAU` minutes in normal glucose tolerance and later, and longer, as HbA1c
#: rises (nondiabetic peaks fall 30-60 min after a meal; type 2 diabetes peaks later and stays up).
TAU, TAU_PER_A1C, TAU_MAX = 40.0, 15.0, 80.0
#: The smallest rise a lunch-sized meal produces, and the largest, which grows with HbA1c: about 3 mmol/L
#: in normal glucose tolerance (nondiabetic CGM rarely passes 10 mmol/L), up to 14 in poor control. When
#: the eAG asks for more than the meals may give, the rest is a raised waking-day level (`DAY_LIFT`):
#: between meals a nondiabetic trace sits above its overnight level, and putting everything into spikes
#: gave healthy people 15-18 mmol/L peaks.
MIN_RISE, RISE_CAP, RISE_CAP_PER_A1C, MAX_RISE = 1.0, 3.0, 4.5, 14.0
#: The largest waking-day lift; a day that would need more ends below its eAG (the truth records both).
DAY_LIFT = 2.0
#: Dawn phenomenon: a pre-breakfast rise, nadir to breakfast, in about half of people with type 2
#: diabetes not on insulin, median about 16 mg/dL (0.9 mmol/L) and much the same across HbA1c levels
#: (Monnier et al., Diabetes Care 2013;36:4057). A per-person trait once HbA1c reaches 6%; centred 06:30,
#: four hours wide.
DAWN_FROM_A1C, DAWN_SHARE, DAWN_MMOL = 6.0, 0.5, (0.9, 0.3)
#: A run lowers glucose by up to this much at its end, recovering with a 45-minute time constant.
RUN_DIP, RUN_DIP_TAU = 0.7, 45.0
#: Minute-scale physiological wander: AR(1) with a one-hour memory and a stationary sd of 3.5% of basal.
WANDER_PHI, WANDER_SD = 0.985, 0.035
#: Interstitial glucose lags blood glucose by several minutes (Basu et al., Diabetes 2013;62:4083).
ISF_LAG_MIN = 6.0
#: Physiological floor of the latent curve; readings below a sensor's range come from the sensor.
BG_FLOOR = 2.6

# ── Heart-rate model (bpm, minutes) ─────────────────────────────────────────────
#: Relative to the day's resting heart rate: asleep, awake and still; and a floor no reading goes under
#: (a resting rate in the mid-forties sleeps in the low forties, not in the twenties).
HR_SLEEP, HR_AWAKE, HR_FLOOR = 0.93, 1.14, 36.0
#: Extra beats in a walking bout, and the share of heart-rate reserve an easy run holds.
HR_WALK, HR_RUN_RESERVE = (18, 30), (0.62, 0.80)
#: On- and off-kinetics time constants (min), and the slow post-exercise elevation.
HR_TAU_UP, HR_TAU_DOWN, HR_EPOC, HR_EPOC_TAU = 1.0, 2.5, 8.0, 40.0
#: A meal adds a few beats for an hour or two.
HR_MEAL = 4.0


def _hours(day: date, h: float) -> datetime:
    return datetime.combine(day, time()) + timedelta(minutes=round(h * 60))


def _local(stamp: str) -> datetime:
    """A device-series timestamp ("2024-03-01T07:12:00+08:00") as local wall-clock time."""
    return datetime.fromisoformat(stamp[:19])


def _running(person: Person, day: date) -> bool:
    return any(e.name == RUN and e.start <= day for e in person.events)


def _during(person: Person, name: str, day: date) -> bool:
    return any(e.name == name and 0 <= (day - e.start).days <= e.duration_days for e in person.events)


@dataclasses.dataclass(frozen=True)
class DayPlan:
    """What a person did on one local day; every stream reads the same plan."""

    day: date
    wake: datetime                                       # local wall-clock time
    bed: datetime                                        # the night that starts this evening
    meals: tuple[tuple[datetime, str, float], ...]       # (time, kind, load relative to lunch)
    run: tuple[datetime, int] | None                     # (start, minutes)
    walks: tuple[tuple[datetime, int], ...]              # (start, minutes)
    steps: int
    rhr: float

    def describe(self) -> dict:
        return {"day": self.day.isoformat(), "wake": self.wake.isoformat(), "bed": self.bed.isoformat(),
                "meals": [{"time": t.isoformat(), "kind": k, "load": round(load, 2)} for t, k, load in self.meals],
                "run": {"start": self.run[0].isoformat(), "minutes": self.run[1]} if self.run else None,
                "walk_minutes": sum(m for _, m in self.walks), "steps": self.steps, "rhr": round(self.rhr, 1)}


class Life:
    """One person's days, planned once and shared by every stream of theirs."""

    def __init__(self, seed: int, person: Person, series: dict):
        self.seed, self.person, self.series = seed, person, series
        self.tz = series["baseline"]["tz"]
        rhr, steps, sleep = {}, {}, {}
        for r in series["records"]:
            if r["_metric"] == "rhr":
                rhr[r["time"][:10]] = r["value"]
            elif r["_metric"] == "steps":
                steps[r["time"][:10]] = r["value"]
            elif r["_metric"] == "sleep":
                sleep[r["end_time"][:10]] = (_local(r["time"]), _local(r["end_time"]))   # keyed by wake day
        self._rhr, self._steps, self._sleep = rhr, steps, sleep
        h = random.Random(f"habits:{seed}:{person.person_id}")
        self.meal_clock = {k: h.gauss(mu, sd) for k, (mu, sd, _) in MEALS.items()}
        self.skip_breakfast = h.choice([0.03, 0.1, 0.3])
        self.snack_rate = h.choice([0.2, 0.5, 0.8])
        self.meal_size = math.exp(h.gauss(0, 0.15))
        self.bed_h, self.sleep_h = h.gauss(23.3, 0.5), h.gauss(7.2, 0.5)
        self.run_evening = h.random() < 0.6
        self.run_rate = h.choice([0.35, 0.5, 0.65])
        self.dawn = min(1.6, max(0.3, h.gauss(*DAWN_MMOL))) if h.random() < DAWN_SHARE else 0.0
        self._plans: dict[date, DayPlan] = {}

    def resting_hr(self, day: date) -> float:
        """The day's resting heart rate: the device series' record, or, on a day or a store without one,
        the person's baseline under the same event effects the series applies."""
        if day.isoformat() in self._rhr:
            return float(self._rhr[day.isoformat()])
        factor, _ = physiology.event_factor(self.person, "pulse", day)
        return self.series["baseline"]["rhr"] * factor * (1.10 if _during(self.person, URI, day) else 1.0)

    def plan(self, day: date) -> DayPlan:
        if day not in self._plans:
            self._plans[day] = self._make_plan(day)
        return self._plans[day]

    def _make_plan(self, day: date) -> DayPlan:
        r = random.Random(f"plan:{self.seed}:{self.person.person_id}:{day.isoformat()}")
        weekend = day.weekday() >= 5
        if day.isoformat() in self._sleep:
            wake = self._sleep[day.isoformat()][1]
        else:
            wake = _hours(day, self.bed_h + self.sleep_h - 24 + (0.6 if weekend else 0) + r.gauss(0, 0.3))
        tomorrow = (day + timedelta(days=1)).isoformat()
        if tomorrow in self._sleep:
            bed = self._sleep[tomorrow][0]
        else:
            bed = _hours(day, self.bed_h + (0.5 if weekend else 0) + r.gauss(0, 0.4))
        feast = _during(self.person, FEAST, day)

        def load(kind_factor: float) -> float:
            return kind_factor * self.meal_size * math.exp(r.gauss(0, LOAD_SIGMA)) * (FEAST_FACTOR if feast else 1.0)

        meals = []
        b = max(_hours(day, self.meal_clock["breakfast"] + (0.75 if weekend else 0) + r.gauss(0, 0.3)),
                wake + timedelta(minutes=15))
        if r.random() >= self.skip_breakfast:
            meals.append((b, "breakfast", load(MEALS["breakfast"][2])))
        meals.append((_hours(day, self.meal_clock["lunch"] + r.gauss(0, 0.25)), "lunch", load(MEALS["lunch"][2])))
        if r.random() < self.snack_rate:
            meals.append((_hours(day, r.uniform(14.5, 16.5)), "snack", load(SNACK_LOAD)))
        meals.append((_hours(day, self.meal_clock["dinner"] + r.gauss(0, 0.4)), "dinner", load(MEALS["dinner"][2])))
        if feast:
            meals.append((_hours(day, r.uniform(21.0, 22.0)), "snack", load(LATE_SNACK_LOAD)))
        meals.sort()

        run = None
        if _running(self.person, day) and not _during(self.person, URI, day) and r.random() < self.run_rate:
            start = _hours(day, 19.5 + r.gauss(0, 0.5)) if self.run_evening else \
                max(_hours(day, 6.5 + r.gauss(0, 0.3)), wake + timedelta(minutes=10))
            run = (start, r.randint(25, 50))

        steps = int(self._steps.get(day.isoformat(), self.series["baseline"]["steps"]))
        walk_steps = max(0, steps - (run[1] * RUN_SPM if run else 0))
        walk_min = int(WALK_SHARE * walk_steps / WALK_SPM)
        walks = []
        lo, hi = wake + timedelta(minutes=20), bed - timedelta(minutes=45)
        span = max(1, int((hi - lo).total_seconds() // 60))
        while walk_min > 0:
            minutes = min(walk_min, r.randint(5, 25))
            walks.append((lo + timedelta(minutes=r.randrange(span)), minutes))
            walk_min -= minutes
        walks.sort()
        return DayPlan(day, wake, bed, tuple(meals), run, tuple(walks), steps, self.resting_hr(day))

    def asleep(self, when: datetime) -> bool:
        today = self.plan(when.date())
        return when < today.wake or when >= today.bed


# ── Glucose ──────────────────────────────────────────────────────────────────
def _eag(person: Person, day: date) -> float:
    return 1.59 * physiology.expected(person, "hba1c", day) - 2.59


def glucose_minutes(life: Life, start: datetime, minutes: int) -> list[float]:
    """Blood glucose in mmol/L, one value per minute from `start` (local wall-clock time) for `minutes`."""
    person = life.person
    first = start.date() - timedelta(days=1)            # yesterday's dinner still shapes this morning
    n_days = (start + timedelta(minutes=minutes)).date().toordinal() - first.toordinal() + 2
    days = [first + timedelta(days=i) for i in range(n_days)]
    t0 = datetime.combine(first, time())
    total = n_days * 1440
    cvi = (spec.indicators()["glu"].get("cvi") or physiology.DEFAULT_CVI) / 100

    # Basal: the day's fasting level, anchored at 04:00 and interpolated in between.
    anchors = []
    for d in days:
        r = random.Random(f"cgm-basal:{life.seed}:{person.person_id}:{d.isoformat()}")
        anchors.append((((d - first).days * 1440) + 240,
                        physiology.expected(person, "glu", d) * math.exp(r.gauss(0, cvi) - cvi ** 2 / 2)))
    curve = [0.0] * total
    k = 0
    for i in range(total):
        while k + 1 < len(anchors) and anchors[k + 1][0] <= i:
            k += 1
        if i <= anchors[0][0]:
            curve[i] = anchors[0][1]
        elif k + 1 < len(anchors):
            (i0, v0), (i1, v1) = anchors[k], anchors[k + 1]
            curve[i] = v0 + (v1 - v0) * (i - i0) / (i1 - i0)
        else:
            curve[i] = anchors[-1][1]

    wr = random.Random(f"cgm-wander:{life.seed}:{person.person_id}:{start.isoformat()}")
    x = 0.0
    innovation = WANDER_SD * math.sqrt(1 - WANDER_PHI ** 2)
    for i in range(total):
        x = WANDER_PHI * x + wr.gauss(0, innovation)
        curve[i] *= 1 + x

    for di, d in enumerate(days):
        base0 = di * 1440
        if life.dawn and physiology.expected(person, "hba1c", d) >= DAWN_FROM_A1C:
            for m in range(270, 510):                    # 04:30-08:30, raised cosine centred 06:30
                curve[base0 + m] += life.dawn * 0.5 * (1 + math.cos(math.pi * (m - 390) / 120))
        plan = life.plan(d)
        if plan.run:
            rs = int((plan.run[0] - t0).total_seconds() // 60)
            for m in range(rs, min(total, rs + plan.run[1] + int(6 * RUN_DIP_TAU))):
                into = m - rs
                f = into / plan.run[1] if into < plan.run[1] else math.exp(-(into - plan.run[1]) / RUN_DIP_TAU)
                curve[m] -= RUN_DIP * f

    # Meals last: their size is solved so that each day's mean is that day's eAG.
    for di, d in enumerate(days):
        plan = life.plan(d)
        if not plan.meals:
            continue
        a1c = physiology.expected(person, "hba1c", d)
        tau = min(TAU_MAX, TAU + TAU_PER_A1C * max(0.0, a1c - 5.6))
        base0 = di * 1440
        need = _eag(person, d) - sum(curve[base0:base0 + 1440]) / 1440
        area = sum(load for _, _, load in plan.meals) * tau * math.e / 1440
        cap = min(MAX_RISE, RISE_CAP + RISE_CAP_PER_A1C * max(0.0, a1c - 5.7))
        rise = min(cap, max(MIN_RISE, need / area))
        # What the meals cannot carry lifts the waking day: ramps up over the first hour awake, down over
        # the last, so the overnight level stays the day's fasting glucose.
        w0, w1 = (int((plan.wake - t0).total_seconds() // 60) + 60, int((plan.bed - t0).total_seconds() // 60) - 60)
        if need > rise * area and w1 - w0 > 240:
            shape = [min(1.0, (m - w0 + 60) / 60, (w1 + 60 - m) / 60) for m in range(w0 - 60, w1 + 60)]
            lift = min(DAY_LIFT, (need - rise * area) * 1440 / sum(shape))
            for j, m in enumerate(range(w0 - 60, min(total, w1 + 60))):
                curve[m] += lift * shape[j]
        span = int(8 * tau)
        kernel = [(t / tau) * math.exp(1 - t / tau) for t in range(span)]
        for when, _, load in plan.meals:
            m0 = int((when - t0).total_seconds() // 60)
            for t in range(min(span, total - m0)):
                curve[m0 + t] += rise * load * kernel[t]

    off = int((start - t0).total_seconds() // 60)
    return [max(BG_FLOOR, v) for v in curve[off:off + minutes]]


def interstitial(bg: list[float], lag: float = ISF_LAG_MIN) -> list[float]:
    """First-order lag of blood glucose: what a sensor in interstitial fluid sees."""
    out, y = [], bg[0]
    a = 1 - math.exp(-1 / lag)
    for v in bg:
        y += (v - y) * a
        out.append(y)
    return out


# ── Heart rate ───────────────────────────────────────────────────────────────
def hr_max(person: Person, day: date) -> float:
    return 208 - 0.7 * person.age_at(day)


def heart_minutes(life: Life, start: datetime, minutes: int) -> list[tuple[float, str]]:
    """Heart rate (bpm) and state, one per minute from `start`. States: `sleep`, `rest`, `walk`, `run`."""
    person = life.person
    r = random.Random(f"hr-curve:{life.seed}:{person.person_id}:{start.isoformat()}")
    plans = {}
    d = (start - timedelta(days=1)).date()
    while d <= (start + timedelta(minutes=minutes)).date():
        plans[d] = life.plan(d)
        d += timedelta(days=1)
    t0 = start
    state = ["rest"] * minutes
    target = [0.0] * minutes
    extra = [0.0] * minutes
    for d, plan in plans.items():
        def idx(when: datetime) -> int:
            return int((when - t0).total_seconds() // 60)

        for when, _, load in plan.meals:
            m0 = idx(when)
            for t in range(max(0, -m0), min(240, minutes - m0)):
                extra[m0 + t] += HR_MEAL * min(load, 1.5) * (t / 45) * math.exp(1 - t / 45)
        for when, mins in plan.walks:
            m0 = idx(when)
            bump = r.uniform(*HR_WALK)
            for t in range(max(0, -m0), min(mins, minutes - m0)):
                state[m0 + t], target[m0 + t] = "walk", bump
        if plan.run:
            m0, mins = idx(plan.run[0]), plan.run[1]
            reserve = r.uniform(*HR_RUN_RESERVE)
            for t in range(max(0, -m0), min(mins, minutes - m0)):
                state[m0 + t] = "run"
                target[m0 + t] = reserve * (hr_max(person, d) - plan.rhr * HR_AWAKE)
            for t in range(max(mins, -m0), min(mins + int(5 * HR_EPOC_TAU), minutes - m0)):
                extra[m0 + t] += HR_EPOC * math.exp(-(t - mins) / HR_EPOC_TAU)

    out = []
    hr = None
    noise = 0.0
    for i in range(minutes):
        when = t0 + timedelta(minutes=i)
        plan = plans[when.date()]
        asleep = life.asleep(when)
        if asleep:
            state[i] = "sleep"
            # deepest a third of the way into the night
            night_start = plan.bed if when >= plan.bed else plans[when.date() - timedelta(days=1)].bed
            frac = min(1.0, (when - night_start).total_seconds() / 3600 / max(life.sleep_h, 4))
            goal = plan.rhr * HR_SLEEP - 2 * math.sin(math.pi * min(1.0, frac * 1.5))
        else:
            goal = plan.rhr * HR_AWAKE + target[i]
        goal += extra[i]
        if hr is None:
            hr = goal
        tau = HR_TAU_UP if goal > hr else HR_TAU_DOWN
        hr += (goal - hr) * (1 - math.exp(-1 / tau))
        noise = 0.9 * noise + r.gauss(0, 0.5 if asleep else 2.0)
        out.append((max(HR_FLOOR, hr + noise), state[i]))
    return out


def window_days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def clamp_end(day: date) -> date:
    return min(day, CORPUS_END)


# ── Who wears what, and when ─────────────────────────────────────────────────
#: Share of each archetype that wears a CGM at some point. Prediabetes progressing to type 2 diabetes is
#: the archetype a clinician puts a sensor on; anyone else is a wellness wearer (over-the-counter sensors).
CGM_RATE = {"prediabetes_to_t2dm": 0.7}
CGM_RATE_OTHER = 0.06
#: A heart-rate window is a week of a watch or ring's intraday samples.
HR_WINDOW_DAYS = 7


def _metformin(person: Person):
    name = spec.cohort()["scripts"]["metformin"]["name"]
    return next((e for e in person.events if e.name == name), None)


def _weighted(r: random.Random, weights: dict) -> str:
    return r.choices(list(weights), weights=list(weights.values()))[0]


def cgm_plan(seed: int, person: Person, lang: str) -> list[dict]:
    """A person's sensor sessions: device, display unit and start, deterministic in seed and person.

    For prediabetes progressing to diabetes: one sensor in the weeks before metformin starts and one a few
    months into it (the drug's effect is visible on the curve and in the GMI), sometimes a third later on.
    Anyone else wears at most one, in the last two years of their timeline."""
    r = random.Random(f"cgm:{seed}:{person.person_id}")
    if r.random() >= CGM_RATE.get(person.archetype, CGM_RATE_OTHER):
        return []
    s = spec.streams()
    adoption = s["adoption"].get(lang, s["adoption"]["en"])
    device = _weighted(r, adoption["cgm"])
    unit = _weighted(r, adoption["glucose_unit"])
    first = person.weight_anchors[0][0]
    last = min(person.weight_anchors[-1][0], CORPUS_END)
    days = []
    drug = _metformin(person)
    if drug is not None:
        days.append(drug.start - timedelta(days=r.randint(14, 40)))
        days.append(drug.start + timedelta(days=r.randint(120, 200)))
        if r.random() < 0.4:
            days.append(drug.start + timedelta(days=r.randint(400, 900)))
    else:
        lo = max(first, last - timedelta(days=730))
        days.append(lo + timedelta(days=r.randrange(max(1, (last - lo).days))))
    out = []
    for n, day in enumerate(sorted(days)):
        dev = device if r.random() < 0.85 else _weighted(r, adoption["cgm"])     # most stay with one device
        spec_dev = s["devices"][dev]
        start = datetime.combine(day, time()) + timedelta(minutes=r.randint(8 * 60, 21 * 60), seconds=r.randrange(60))
        wear = timedelta(days=spec_dev["wear_days"]) + timedelta(hours=spec_dev.get("grace_hours", 0) * (r.random() < 0.5))
        failed = r.random() < 0.04                                              # a sensor that gives up early
        if failed:
            wear = timedelta(hours=r.randint(48, spec_dev["wear_days"] * 24 - 12))
        if day < first or (start + wear).date() > last:
            continue
        # English-speaking wearers who read mg/dL are taken to be in the US, where some sensors are cleared
        # with a narrower range and LibreView defaults to month-first dates.
        region = "us" if lang == "en" and unit == "mg/dL" else "default"
        # The sensor before metformin is often a clinic's, placed and read out by the hospital.
        setting = "clinic" if (drug is not None and n == 0 and spec_dev.get("clinic_share", 0)
                               and r.random() < spec_dev["clinic_share"]) else "home"
        out.append({"n": n + 1, "device": dev, "unit": unit, "region": region, "setting": setting, "start": start,
                    "end": start + wear, "ended": "sensor_failed" if failed else "wear_period"})
    return out


def hr_windows(seed: int, person: Person, series: dict, sessions: list[dict]) -> list[dict]:
    """Days of intraday heart rate for someone who wears a watch or ring: the days of their first glucose
    sensor (so the two streams overlap), the days around a cold, and an ordinary week."""
    if not series["habits"]["wearable"]:
        return []
    r = random.Random(f"hr:{seed}:{person.person_id}")
    first = date.fromisoformat(min(x["time"][:10] for x in series["records"])) if series["records"] else None
    if first is None:
        return []
    last = min(person.weight_anchors[-1][0], CORPUS_END) - timedelta(days=1)
    wanted = []
    if sessions:
        s0 = sessions[0]
        wanted.append((s0["start"].date(), min(s0["end"].date(), s0["start"].date() + timedelta(days=13)), "cgm"))
    cold = next((e for e in person.events if e.name == URI and first <= e.start <= last), None)
    if cold is not None and r.random() < 0.8:
        wanted.append((cold.start - timedelta(days=2), cold.start + timedelta(days=HR_WINDOW_DAYS + 2), "infection"))
    if (last - first).days > HR_WINDOW_DAYS:
        d0 = first + timedelta(days=r.randrange((last - first).days - HR_WINDOW_DAYS))
        wanted.append((d0, d0 + timedelta(days=HR_WINDOW_DAYS - 1), "routine"))
    out: list[dict] = []
    for d0, d1, why in wanted:
        d0, d1 = max(d0, first), min(d1, last)
        if d1 < d0 or any(not (d1 < w["start"] or d0 > w["end"]) for w in out):
            continue                                                             # overlapping windows merge into the first
        out.append({"start": d0, "end": d1, "reason": why})
    return out


# ── The sensor ───────────────────────────────────────────────────────────────
#: Mean absolute relative difference of a normal error is 0.798 sd; a device's published MARD sets the
#: sd of its total error, split between a per-sensor calibration gain and correlated reading noise.
_MARD_TO_SD = 1 / 0.798
GAIN_SHARE, NOISE_SHARE, NOISE_PHI, FIRST_DAY_NOISE = 0.6, 0.8, 0.7, 1.6
#: A night with a compression low (lying on the sensor), its depth and length.
COMPRESSION_RATE, COMPRESSION_DEPTH, COMPRESSION_MIN = 0.12, (0.25, 0.5), (20, 60)
#: A scan-based sensor is scanned every few waking hours (the gap is what loses history).
SCAN_EVERY_H = (1.5, 6.0)


def _gaps_and_scans(life: Life, session: dict, dev: dict, r: random.Random) -> tuple[list[dict], list[datetime]]:
    start, end = session["start"], session["end"]
    gaps = [{"start": start, "end": start + timedelta(minutes=dev["warmup_min"]), "cause": "warmup"}]
    day = start.date()
    while day <= end.date():
        for _ in range(_poisson(r, dev["dropouts_per_day"])):
            at = datetime.combine(day, time()) + timedelta(minutes=r.randrange(1440))
            lo, hi = dev["dropout_min"]
            length = timedelta(minutes=round(math.exp(r.uniform(math.log(lo), math.log(hi)))))
            # A receiver that reconnects within the sensor's backfill window recovers the readings it
            # missed: the outage is in the truth, the readings are in every history export.
            filled = length <= timedelta(hours=dev.get("backfill_h", 0))
            gaps.append({"start": at, "end": at + length, "cause": "signal_loss", "filled": filled})
        day += timedelta(days=1)
    scans: list[datetime] = []
    if dev.get("scans"):
        t = start + timedelta(minutes=dev["warmup_min"] + 5)
        while t < end:
            if life.asleep(t):
                t = life.plan(t.date()).wake + timedelta(minutes=r.randint(5, 90)) if t.hour < 12 else \
                    life.plan(t.date() + timedelta(days=1)).wake + timedelta(minutes=r.randint(5, 90))
                continue
            scans.append(t)
            t += timedelta(minutes=round(r.uniform(*SCAN_EVERY_H) * 60))
        # History older than the sensor's memory at the next scan is gone (scan-based sensors only; a
        # Libre 3 keeps its whole wear period and its "scans" are app views).
        if dev.get("memory_h"):
            keep = timedelta(hours=dev["memory_h"])
            for a, b in zip(scans, scans[1:] + [end]):
                if b - a > keep:
                    gaps.append({"start": a, "end": b - keep, "cause": "not_scanned", "filled": False})
    gaps[0]["filled"] = False
    gaps = [g for g in gaps if g["end"] > g["start"]]
    return sorted(gaps, key=lambda g: g["start"]), scans


def _poisson(r: random.Random, lam: float) -> int:
    n, p, limit = 0, 1.0, math.exp(-lam)
    while True:
        p *= r.random()
        if p <= limit:
            return n
        n += 1


def session_range(dev: dict, session: dict) -> tuple[int, int]:
    """The reportable range in mg/dL; some sensors are cleared with a narrower range in the US."""
    table = dev["range_mgdl"]
    return tuple(table.get(session.get("region", "default"), table["default"]))


def sensor_readings(life: Life, session: dict) -> dict:
    """Everything a sensor session produced, as truth: readings (with the true glucose behind each),
    scans, gaps and artefacts. Formats choose what each export shows of it."""
    dev = spec.streams()["devices"][session["device"]]
    r = random.Random(f"cgm-sensor:{life.seed}:{life.person.person_id}:{session['n']}")
    start, end = session["start"], session["end"]
    minutes = int((end - start).total_seconds() // 60) + 2
    bg = glucose_minutes(life, start, minutes)
    ig = interstitial(bg)
    sd = dev["mard_pct"] / 100 * _MARD_TO_SD
    gain = math.exp(r.gauss(0, GAIN_SHARE * sd))
    drift = r.gauss(0, 0.5 * GAIN_SHARE * sd)
    innovation = NOISE_SHARE * sd * math.sqrt(1 - NOISE_PHI ** 2)
    compressions = []
    day = start.date()
    while day <= end.date():
        if r.random() < COMPRESSION_RATE:
            plan = life.plan(day)
            night = (plan.bed, life.plan(day + timedelta(days=1)).wake)
            span = max(1, int((night[1] - night[0]).total_seconds() // 60) - 90)
            at = night[0] + timedelta(minutes=30 + r.randrange(span))
            compressions.append((at, r.randint(*COMPRESSION_MIN), r.uniform(*COMPRESSION_DEPTH)))
        day += timedelta(days=1)
    gaps, scans = _gaps_and_scans(life, session, dev, r)
    lo, hi = session_range(dev, session)

    noise = 0.0
    readings = []

    def read(t: datetime, kind: str) -> dict:
        i = int((t - start).total_seconds() // 60)
        frac = (t - start) / (end - start)
        value = ig[i] * gain * (1 + drift * frac) * (1 + noise)
        artifact = None
        for at, length, depth in compressions:
            if at <= t < at + timedelta(minutes=length):
                into = (t - at).total_seconds() / 60 / length
                value *= 1 - depth * math.sin(math.pi * into)
                artifact = "compression_low"
        mgdl = round(value * MGDL_PER_MMOL)
        flag = "low" if mgdl < lo else ("high" if mgdl > hi else None)
        return {"time": t, "kind": kind, "mgdl": None if flag else mgdl,
                "mmol": None if flag else round(value, 1), "flag": flag,
                "true_mmol": round(bg[i], 2), "artifact": artifact}

    first = start + timedelta(minutes=dev["warmup_min"], seconds=r.randrange(60))
    step = timedelta(minutes=dev["interval_min"])
    jitter = dev.get("time_jitter_s", 0)
    lost = [g for g in gaps if not g["filled"]]
    k = 0
    while first + k * step <= end:
        t = first + k * step
        noise = NOISE_PHI * noise + r.gauss(0, innovation * (FIRST_DAY_NOISE if t - start < timedelta(days=1) else 1))
        if not any(g["start"] <= t < g["end"] for g in lost):
            x = read(t + timedelta(seconds=r.randint(-jitter, jitter)), "historic")
            x["ticks"] = int((t - start).total_seconds())          # seconds since the sensor started
            readings.append(x)
        k += 1
    for at in scans:
        readings.append(read(at.replace(second=r.randrange(60)), "scan"))
    readings.sort(key=lambda x: x["time"])
    return {"device": session["device"], "unit": session["unit"], "region": session["region"],
            "setting": session.get("setting", "home"), "range": (lo, hi), "start": start, "end": end,
            "ended": session["ended"], "gain": round(gain, 4), "readings": readings, "gaps": gaps,
            "compressions": [{"start": a, "minutes": m, "depth": round(d, 2)} for a, m, d in compressions]}


def glucose_summary(readings: list[dict], expected: int) -> dict:
    """Standard CGM metrics from the delivered historic readings (International Consensus on Time in
    Range, Battelino et al., Diabetes Care 2019;42:1593; GMI, Bergenstal et al., Diabetes Care
    2018;41:2275). A reading shown as Low or High counts at the range limit it fell past."""
    vals = []
    for x in readings:
        if x["kind"] != "historic":
            continue
        vals.append(x["mmol"] if x["mmol"] is not None else (2.2 if x["flag"] == "low" else 22.2))
    if not vals:
        return {}
    n = len(vals)
    mean = sum(vals) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / n)
    mgdl = mean * MGDL_PER_MMOL

    def share(test) -> float:
        return round(100 * sum(1 for v in vals if test(v)) / n, 1)

    return {"n_readings": n, "coverage_pct": round(100 * n / max(expected, 1), 1),
            "mean_mmol": round(mean, 2), "mean_mgdl": round(mgdl, 1), "sd_mmol": round(sd, 2),
            "cv_pct": round(100 * sd / mean, 1), "gmi_pct": round(3.31 + 0.02392 * mgdl, 2),
            "tbr_lt_3_0_pct": share(lambda v: v < 3.0), "tbr_3_0_3_8_pct": share(lambda v: 3.0 <= v < 3.9),
            "tir_3_9_10_0_pct": share(lambda v: 3.9 <= v <= 10.0),
            "tar_10_1_13_9_pct": share(lambda v: 10.0 < v <= 13.9), "tar_gt_13_9_pct": share(lambda v: v > 13.9)}


# ── Heart-rate samples as a device records them ──────────────────────────────
#: Which wearable writes a store's heart rate: the phone platform decides the watch.
STORE_WEARABLE = {"apple": "apple_watch", "huawei": "huawei_watch", "xiaomi": "mi_band",
                  "health_connect": "android_watch"}


def wearables_of(person: Person, seed: int, series: dict) -> list[str]:
    from . import vendor_signals

    out = [STORE_WEARABLE[series["vendor"]]]
    if "oura" in vendor_signals.adopted_vendors(person, seed, series):
        out.append("oura_ring")
    return out


def _off_wrist(life: Life, wear: dict, days: list[date], r: random.Random) -> list[tuple[datetime, datetime, str]]:
    """When the device was not on the body: a watch charging after the morning wake-up, a night it was
    left on the charger (only a night the store has no sleep record for: a sleep record means it was
    worn), a ring's charge every few days."""
    out = []
    has_sleep = life._sleep
    for d in days:
        plan = life.plan(d)
        if wear["charge"] == "daily" and r.random() < 0.75:
            at = plan.wake + timedelta(minutes=r.randint(5, 60))
            out.append((at, at + timedelta(minutes=r.randint(40, 90)), "charging"))
        elif wear["charge"] == "weekly" and r.random() < 1 / 5:
            at = plan.wake + timedelta(minutes=r.randint(60, 600))
            out.append((at, at + timedelta(minutes=r.randint(60, 90)), "charging"))
        night_free = (d + timedelta(days=1)).isoformat() not in has_sleep
        if wear["charge"] == "daily" and night_free and (life.series["vendor"] in ("apple", "health_connect")
                                                          or r.random() < 0.1):
            out.append((plan.bed, life.plan(d + timedelta(days=1)).wake, "off_overnight"))
    return out


def hr_samples(life: Life, window: dict, wearable: str, curve: list[tuple[float, str]],
               start: datetime) -> tuple[list[dict], list[dict]]:
    """A wearable's heart-rate samples over a window, and the spans it was off the body.

    Background cadence and workout bursts come from `resources/streams.json`; an Apple Watch measures in
    the background only while the wearer is still, so walking thins its samples. Every sample keeps the
    true heart rate it was read from."""
    wear = spec.streams()["wearables"][wearable]
    r = random.Random(f"hr-device:{life.seed}:{life.person.person_id}:{window['start']}:{wearable}")
    days = window_days(window["start"], window["end"])
    off = _off_wrist(life, wear, days, r)
    every = r.choice(wear["background_s"]) if isinstance(wear["background_s"][0], list) else wear["background_s"]
    end = start + timedelta(minutes=len(curve))
    workout = wear.get("workout_s")
    t = start + timedelta(seconds=r.randrange(every[0]))
    out = []
    while t < end:
        i = int((t - start).total_seconds() // 60)
        bpm, state = curve[i]
        step = workout if (state == "run" and workout) else r.randint(*every)
        worn = not any(a <= t < b for a, b, _ in off)
        skip = wear.get("still_only") and state == "walk" and r.random() < 0.6
        if worn and not skip:
            out.append({"time": t, "bpm": int(round(bpm + r.gauss(0, wear["noise_bpm"]))), "true_bpm": round(bpm, 1),
                        "state": state})
        t += timedelta(seconds=step)
    return out, [{"start": a, "end": b, "cause": c} for a, b, c in off]


# ── Writing ──────────────────────────────────────────────────────────────────
def _name(person: Person, lang: str) -> tuple[str, str]:
    """(first, last) of the fictional name the person's own documents print (`document.subject_fields`
    draws it from the same sticky stream), so a CGM export links to the lab slips by name."""
    fiction = spec.fiction()
    full = random.Random(f"patient:{person.person_id}").choice(
        fiction["person_names_en" if spec.doc_lang(lang) == "en" else "person_names_zh"])
    if " " in full:
        first, last = full.split(" ", 1)
        return first, last
    return full[1:], full[:1]                       # a Chinese name: surname first, one character


def _platform(series: dict) -> str:
    return "apple" if series["vendor"] == "apple" else "android"


class _Out:
    """Files written for one person, with the record each contributes to the truth."""

    def __init__(self, out_dir, person: Person):
        self.out_dir, self.root = out_dir, out_dir / "continuous" / person.person_id

    def write(self, rel: str, data: bytes, **meta) -> dict:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"file": str(path.relative_to(self.out_dir))} | meta


def _counts(names: list[str]) -> list[dict]:
    seen: dict[str, int] = {}
    for n in names:
        seen[n] = seen.get(n, 0) + 1
    return [{"name": k, "count": v} for k, v in sorted(seen.items())]


def _iso(x: datetime, tz: str) -> str:
    return x.replace(microsecond=0).isoformat() + tz


def _loinc(unit: str) -> str:
    """What mirobody's resolver should code a glucose reading as: the device catalogue's 2339-0 is a mass
    concentration; a reading in mmol/L switches to its molar sibling, 15074-8."""
    return "2339-0" if unit == "mg/dL" else "15074-8"


def _clinic(person: Person) -> str:
    """The hospital that placed a clinic sensor: one per person, from the fiction pool."""
    zh = [i for i in spec.fiction()["institutions"] if i["language"] == "zh" and i["kind"] == "hospital"]
    return random.Random(f"cgm-clinic:{person.person_id}").choice(zh)["name"]


def _report(life: Life, sess: dict, dev: dict, out: _Out, lang: str, banner: bool) -> tuple[list[dict], list[str]]:
    """The report a wearer holds for a sensor: the app's report at the end of a wear period, or the hospital's
    CGM report sheet for a sensor it placed. A PDF with a text layer whose printed rows are the truth an
    extractor is scored against (`cgm_reports`)."""
    from . import cgm_reports
    from .render import agp

    rep = dev.get("report")
    if not rep:
        return [], []
    group = "en" if spec.doc_lang(lang) == "en" else "zh"
    style = rep.get("clinic") if sess.get("setting") == "clinic" and rep.get("clinic") else rep["styles"].get(group)
    if not style or group not in spec.streams()["reports"][style]:
        return [], []
    first, last = _name(life.person, lang)
    name = f"{first} {last}" if group == "en" else f"{last}{first}"
    created = min(cgm_reports.created_at(sess, life.seed, life.person.person_id),
                  datetime.combine(CORPUS_END, time(23, 0)))
    # What the report prints beside the curve: the meals the wearer logged in the app (a share of the day
    # plan's meals) and the device's serial in the vendor's format.
    r = random.Random(f"report-extra:{life.seed}:{life.person.person_id}:{sess['n']}")
    sess["meals"] = [when for d in window_days(sess["start"].date(), sess["end"].date())
                     for when, _, _ in life.plan(d).meals if sess["start"] <= when <= sess["end"] and r.random() < 0.4]
    sn = rep.get("sn")
    if sn == "alnum12":
        from .continuous_formats import _clean

        while not _clean(sn := "".join(r.choice("0123456789ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(12))):
            pass
        sess["report_sn"] = sn
    elif sn == "letter10":
        sess["report_sn"] = r.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") + "".join(str(r.randrange(10)) for _ in range(10))
    report, rows, file_name = cgm_reports.build(sess, dev, style, group, life.person, name, banner, created,
                                                institution=_clinic(life.person), seed=life.seed)
    entry = out.write(f"cgm/{sess['n']:02d}_{sess['device']}/{file_name}", agp.render(report), format="cgm_report_pdf",
                      channel="file_upload", unit=sess["unit"], loinc=_loinc(sess["unit"]), style=style,
                      language=group, created=_iso(created, life.tz), printed_rows=rows)
    return [entry], ["stream.report_only", "stream.chart_values"]


def write_all(out_dir, people: list[Person], seed: int, langs: dict[str, str], banner: bool = True) -> dict:
    """Write every person's CGM sessions and heart-rate windows and the truth in `continuous.jsonl`."""
    from . import continuous_formats as fmt
    from . import devices

    s = spec.streams()
    counts = {"cgm": 0, "heart_rate": 0, "files": 0}
    with (out_dir / "continuous.jsonl").open("w", encoding="utf-8") as fh:
        for person in people:
            lang = langs.get(person.person_id, "zh")
            series = devices.series_for(person, seed, lang)
            life = Life(seed, person, series)
            tz = life.tz
            out = _Out(out_dir, person)
            platform = _platform(series)
            fr = random.Random(f"formats:{seed}:{person.person_id}")
            apple = fmt.AppleExport(tz, s["formats"]["apple_export"]["locale"].get(lang, "en_US"), person.sex, fr) \
                if series["vendor"] == "apple" else None
            truths = []

            sessions = cgm_plan(seed, person, lang)
            for sess_plan in sessions:
                dev = s["devices"][sess_plan["device"]]
                sess = sensor_readings(life, sess_plan) | {"n": sess_plan["n"]}
                writer = FORMATS.get(dev["family"])
                files, hazards = writer(fmt, life, sess, dev, out, platform, lang, fr, apple) if writer else ([], [])
                files += _store_glucose(fmt, life, sess, dev, out, series, fr)
                more, hz = _diy(fmt, life, sess, dev, out, platform, lang)
                files, hazards = files + more, hazards + hz
                more, hz = _report(life, sess, dev, out, lang, banner)
                files, hazards = files + more, hazards + hz
                if files and "stream.no_export" in hazards:
                    hazards = [h for h in hazards if h != "stream.no_export"]
                hist = [x for x in sess["readings"] if x["kind"] == "historic"]
                expected = int((sess["end"] - sess["start"]).total_seconds() // 60 - dev["warmup_min"]) // dev["interval_min"] + 1
                days = window_days(sess["start"].date(), sess["end"].date())
                hz = hazards + ["stream.gap." + g["cause"] for g in sess["gaps"] if not g["filled"]]
                hz += ["stream.backfilled" for g in sess["gaps"] if g["filled"]]
                hz += ["stream.compression_low"] * len(sess["compressions"])
                if sess["ended"] == "sensor_failed":
                    hz.append("stream.sensor_failed")
                if any(x["kind"] == "scan" for x in sess["readings"]):
                    hz.append("stream.scan_duplicate")
                truths.append({
                    "person_id": person.person_id, "synthetic": True, "stream": "cgm", "session": sess["n"],
                    "device": sess["device"], "maker": dev["maker"], "model": dev["model"], "unit": sess["unit"],
                    "tz": tz, "start": _iso(sess["start"], tz), "end": _iso(sess["end"], tz), "ended": sess["ended"],
                    "interval_min": dev["interval_min"], "files": files,
                    "expected": {"metric": "bloodGlucoses", "specimen": "interstitial fluid"},
                    "summary": glucose_summary(hist, expected) | {
                        "hba1c_expected_pct": round(physiology.expected(person, "hba1c", sess["start"].date()), 2),
                        "eag_mmol": round(_eag(person, sess["start"].date()), 2)},
                    "readings": [{"time": _iso(x["time"], tz), "kind": x["kind"], "mgdl": x["mgdl"], "mmol": x["mmol"],
                                  "flag": x["flag"], "true_mmol": x["true_mmol"], "artifact": x["artifact"]}
                                 for x in sess["readings"]],
                    "gaps": [{"start": _iso(g["start"], tz), "end": _iso(g["end"], tz), "cause": g["cause"],
                              "filled": g["filled"]} for g in sess["gaps"]],
                    "compressions": [{"start": _iso(c["start"], tz), "minutes": c["minutes"], "depth": c["depth"]}
                                     for c in sess["compressions"]],
                    "days": [life.plan(d).describe() for d in days],
                    "hazards": _counts(hz)})
                counts["cgm"] += 1

            for wn, window in enumerate(hr_windows(seed, person, series, sessions), start=1):
                start = datetime.combine(window["start"], time())
                minutes = ((window["end"] - window["start"]).days + 1) * 1440
                curve = heart_minutes(life, start, minutes)
                devices_out = []
                for wearable in wearables_of(person, seed, series):
                    samples, off = hr_samples(life, window, wearable, curve, start)
                    files, hz = _hr_files(fmt, life, wearable, wn, samples, out, series, apple)
                    devices_out.append({
                        "wearable": wearable, "files": files, "hazards": _counts(hz + ["stream.off_wrist"] * len(off)),
                        "off_body": [{"start": _iso(o["start"], tz), "end": _iso(o["end"], tz), "cause": o["cause"]} for o in off],
                        "samples": [{"time": _iso(x["time"], tz), "bpm": x["bpm"], "true_bpm": x["true_bpm"],
                                     "state": x["state"]} for x in samples]})
                truths.append({
                    "person_id": person.person_id, "synthetic": True, "stream": "heart_rate", "window": wn,
                    "reason": window["reason"], "tz": tz, "start": window["start"].isoformat(),
                    "end": window["end"].isoformat(), "expected": {"metric": "heartRates", "loinc": "8867-4"},
                    "multi_source": len(devices_out) > 1, "devices": devices_out,
                    "days": [life.plan(d).describe() for d in window_days(window["start"], window["end"])]})
                counts["heart_rate"] += 1

            if sessions:
                _libre_export(fmt, life, out, lang, sessions[0]["unit"], truths, fr)
            if apple is not None and (sessions or any(t["stream"] == "heart_rate" for t in truths)):
                _apple_daily(apple, life, series)
                exported = datetime.combine(min(person.weight_anchors[-1][0], CORPUS_END), time(21, 0))
                entry = out.write("apple_health_export/export.xml", apple.to_bytes(exported),
                                  format="apple_health_export_xml", channel="file_upload")
                for t in truths:
                    t.setdefault("exports", []).append(entry["file"])
            for t in truths:
                counts["files"] += len(t.get("files", [])) + sum(len(d["files"]) for d in t.get("devices", []))
                fh.write(json.dumps(t, ensure_ascii=False) + "\n")
    return counts


def _transmitter(life: Life, sess: dict, dev: dict, r: random.Random) -> None:
    """The transmitter a session reports through: a G6 transmitter outlives several sensors (its tick
    counter keeps running), a G7 sensor is its own transmitter (12 digits, counter from zero)."""
    tx = dev["transmitter"]
    if tx["format"] == "digits":
        sess["transmitter_id"] = str(r.randrange(1, 10)) + "".join(str(r.randrange(10)) for _ in range(tx["length"] - 1))
        carried = 0
    else:
        sess["transmitter_id"] = tx["prefix"] + "".join(r.choice("0123456789ABCDEFGHJKLMNPQRSTUVWXYZ")
                                                         for _ in range(tx["length"] - len(tx["prefix"])))
        carried = r.randrange(tx.get("lifetime_days", 1)) * 86400
    sess["first_tick"] = carried + r.randint(*dev["first_tick_s"])


def _dexcom(fmt, life: Life, sess: dict, dev: dict, out: _Out, platform: str, lang: str,
            r: random.Random, apple) -> tuple[list[dict], list[str]]:
    from . import continuous_formats as cf

    person, tz = life.person, life.tz
    _transmitter(life, sess, dev, r)
    sess["transmitter_hash"] = cf.sha_id("dexcom-transmitter", life.seed, person.person_id, sess["transmitter_id"])
    # A fingerstick calibration now and then (optional on G6 and G7): a meter reading of the blood glucose.
    sess["calibrations"] = []
    for d in window_days(sess["start"].date(), sess["end"].date()):
        if r.random() < dev.get("calibrations_per_day", 0):
            at = life.plan(d).wake + timedelta(minutes=r.randint(30, 600))
            if sess["start"] + timedelta(minutes=dev["warmup_min"]) <= at <= sess["end"]:
                bg = glucose_minutes(life, at.replace(second=0), 1)[0] * math.exp(r.gauss(0, 0.05))
                sess["calibrations"].append((at.replace(second=r.randrange(60)),
                                             str(round(bg * MGDL_PER_MMOL)) if sess["unit"] == "mg/dL" else f"{bg:.1f}"))
    files, hazards = [], []
    folder = f"cgm/{sess['n']:02d}_{sess['device']}"
    plans = [life.plan(d) for d in window_days(sess["start"].date(), sess["end"].date())]
    if "clarity_csv" in dev["exports"]:
        first, last = _name(person, lang)
        dob = None
        if r.random() < 0.5:
            b = random.Random(f"dob:{person.person_id}")
            dob = f"{person.birth_year}-{b.randint(1, 12):02d}-{b.randint(1, 28):02d}"
        exported = min(sess["end"] + timedelta(days=r.randint(1, 30), minutes=r.randrange(1440)),
                       datetime.combine(CORPUS_END, time(23, 0)))
        data, hz = fmt.clarity_csv(sess, dev, (first, last), dob, platform, plans, r)
        name = f"Clarity_Export_{last}_{first}_{exported:%Y-%m-%d_%H%M%S}.csv"
        files.append(out.write(f"{folder}/{name}", data, format="dexcom_clarity_csv", channel="file_upload",
                               unit=sess["unit"], loinc=_loinc(sess["unit"])))
        hazards += hz
    if "dexcom_api_v3" in dev["exports"]:
        user = fmt.sha_id("dexcom-user", life.seed, person.person_id)
        data, hz = fmt.dexcom_egvs(sess, dev, user, platform, tz, r)
        files.append(out.write(f"{folder}/dexcom_v3_egvs.json", data, format="dexcom_api_v3_egvs",
                               channel="vendor_api", unit="mg/dL", loinc=_loinc("mg/dL")))
        hazards += hz
    store = dev.get("stores", {}).get("apple")
    if apple is not None and store:
        for x in sess["readings"]:
            if x["kind"] != "historic" or x["flag"]:
                continue
            value = str(x["mgdl"]) if store["unit"] == "mg/dL" else f"{x['mmol']:.1f}"
            created = x["time"] + timedelta(hours=store["delay_h"], seconds=r.randint(1, 300))
            apple.add("HKQuantityTypeIdentifierBloodGlucose", x["time"], x["time"], value=value,
                      unit=store["unit"] if store["unit"] == "mg/dL" else fmt.HK_MMOL, source=store["source_name"],
                      source_version=None, device=None, created=created)
        hazards.append("stream.store_delay")
    return files, hazards


def _libre(fmt, life: Life, sess: dict, dev: dict, out: _Out, platform: str, lang: str,
           r: random.Random, apple) -> tuple[list[dict], list[str]]:
    """A Libre session leaves no file of its own: LibreView exports every session an account holds in one
    CSV (`_libre_export`, written after the person's last session). The phone app's serial is the app
    install, so it carries over from one sensor to the next unless the app was reinstalled; a reader
    user has the reader's serial."""
    state = life.__dict__.setdefault("_libre", {"blocks": [], "serial": None})
    reader = dev.get("reader_share", 0) and r.random() < dev["reader_share"]
    if reader:
        serial, name = fmt.libre_reader_serial(r), dev["reader_name"]
    else:
        if state["serial"] is None or r.random() < 0.3:
            u = fmt.uuid4(r)
            state["serial"] = u.upper() if platform == "apple" else u
        serial, name = state["serial"], dev["app_name"]
    food = []
    for d in window_days(sess["start"].date(), sess["end"].date()):
        for when, _, load in life.plan(d).meals:
            if sess["start"] <= when <= sess["end"] and r.random() < 0.05:
                food.append((when, load))
    state["blocks"].append({"session": sess, "device": dev, "serial": serial, "device_name": name, "food": food})
    return [], []


def _libre_export(fmt, life: Life, out: _Out, lang: str, unit: str, truths: list[dict], r: random.Random) -> None:
    state = life.__dict__.get("_libre")
    if not state or not state["blocks"]:
        return
    s = spec.streams()["formats"]["libreview_csv"]
    account = dict(s["accounts"][lang if lang in s["accounts"] else "en"][unit])
    first, last = _name(life.person, lang)
    full = f"{first} {last}" if spec.doc_lang(lang) == "en" else f"{last}{first}"
    end = max(b["session"]["end"] for b in state["blocks"])
    local = min(end + timedelta(days=r.randint(1, 30), minutes=r.randrange(1440)), datetime.combine(CORPUS_END, time(23, 0)))
    generated = local - _offset(life.tz)
    data, hz = fmt.libreview_csv(state["blocks"], unit, account, full, generated, r)
    name = f"{full.replace(' ', '')}_glucose_{local.day}-{local.month}-{local.year}.csv"
    entry = out.write(f"cgm/{name}", data, format="libreview_csv", channel="file_upload", unit=unit, loinc=_loinc(unit))
    for t in truths:
        if t["stream"] == "cgm" and spec.streams()["devices"][t["device"]]["family"] == "libre":
            t["files"].append(entry)
            t["hazards"] = _counts([h["name"] for h in t["hazards"] for _ in range(h["count"])] + hz)


def _offset(tz: str) -> timedelta:
    sign = 1 if tz[0] == "+" else -1
    return sign * timedelta(hours=int(tz[1:3]), minutes=int(tz[4:6]))


def _sibionics(fmt, life: Life, sess: dict, dev: dict, out: _Out, platform: str, lang: str,
               r: random.Random, apple) -> tuple[list[dict], list[str]]:
    """A clinic's sensor comes back as the hospital system's CSV; a sensor the person bought gives the
    international app's Excel export (English-speaking wearers), and since mid-2024 the Chinese app
    writes to Apple Health. A Chinese home wearer on Android has no export at all."""
    person = life.person
    serial = fmt.sibionics_serial(r, sess["start"])
    folder = f"cgm/{sess['n']:02d}_{sess['device']}"
    files, hazards = [], []
    if sess["setting"] == "clinic":
        institution = _clinic(person)
        first, last = _name(person, lang)
        name = f"{last}{first}" if spec.doc_lang(lang) != "en" else f"{first} {last}"
        data, hz = fmt.sibionics_clinic_csv(sess, institution, name, serial)
        end = sess["end"] + timedelta(days=r.randint(0, 3), minutes=r.randrange(600))
        files.append(out.write(f"{folder}/{serial}_{end:%Y%m%d%H%M%S}.csv", data, format="sibionics_clinic_csv",
                               channel="file_upload", unit="mmol/L", loinc=_loinc("mmol/L")))
        return files, hazards + hz
    if spec.doc_lang(lang) == "en":
        exported = min(sess["end"] + timedelta(days=r.randint(1, 20), minutes=r.randrange(1440)),
                       datetime.combine(CORPUS_END, time(23, 0)))
        data, hz = fmt.sibionics_app_xlsx(sess, sess["unit"], life.tz, exported)
        name = spec.streams()["formats"]["sibionics_app_xlsx"]["file_name"]
        files.append(out.write(f"{folder}/{name}", data, format="sibionics_app_xlsx", channel="file_upload",
                               unit=sess["unit"], loinc=_loinc(sess["unit"])))
        hazards += hz
    store = dev.get("stores", {}).get("apple")
    if apple is not None and store and sess["start"].date() >= date.fromisoformat(store["since"]):
        for x in sess["readings"]:
            if x["kind"] != "historic" or x["flag"]:
                continue
            created = x["time"] + timedelta(minutes=r.randint(*store["delay_min"]), seconds=r.randrange(60))
            apple.add("HKQuantityTypeIdentifierBloodGlucose", x["time"], x["time"], value=f"{x['mmol']:.1f}",
                      unit=fmt.HK_MMOL, source=store["source_name"], source_version=None, device=None, created=created)
        hazards += ["stream.store_delay", "stream.unit_healthkit_mmol"]
    if not files and not hazards:
        hazards.append("stream.no_export")
    return files, hazards


def _no_export(fmt, life: Life, sess: dict, dev: dict, out: _Out, platform: str, lang: str,
               r: random.Random, apple) -> tuple[list[dict], list[str]]:
    """A sensor whose app gives the wearer reports and screenshots, never the readings: the session is in
    the truth (a curve someone has, and cannot hand over) and in no file."""
    return [], ["stream.no_export"]


def _medtronic(fmt, life: Life, sess: dict, dev: dict, out: _Out, platform: str, lang: str,
               r: random.Random, apple) -> tuple[list[dict], list[str]]:
    """A Guardian sensor with the Guardian app: CareLink's CSV export. The app's alarms, logbook entries
    and fingersticks go in the Pump section, the sensor glucose in the Sensor section."""
    events = {"alarms": [], "markers": [], "fingersticks": []}
    warm = sess["start"] + timedelta(minutes=dev["warmup_min"])
    events["alarms"].append((warm + timedelta(seconds=r.randint(5, 50)), "SENSOR CONNECTED"))
    for g in sess["gaps"]:
        if g["cause"] == "signal_loss" and not g["filled"] and g["end"] - g["start"] > timedelta(minutes=20):
            events["alarms"].append((g["start"] + timedelta(minutes=20, seconds=r.randint(0, 59)), "LOST SENSOR SIGNAL"))
    low_since = None
    for x in (x for x in sess["readings"] if x["kind"] == "historic"):
        mgdl = x["mgdl"] if x["mgdl"] is not None else (0 if x["flag"] == "low" else 999)
        if mgdl < 70 and low_since is None:
            low_since = x["time"]
            events["alarms"].append((x["time"] + timedelta(seconds=r.randint(1, 20)),
                                     "URGENT LOW SENSOR GLUCOSE" if mgdl < 55 else "LOW SG"))
        elif mgdl >= 80:
            low_since = None
    for d in window_days(sess["start"].date(), sess["end"].date()):
        plan = life.plan(d)
        for when, _, load in plan.meals:
            if warm <= when <= sess["end"] and r.random() < 0.1:
                events["markers"].append((when.replace(second=r.randrange(60)), f"Meal: {load * 45:.2f}grams"))
        if r.random() < 0.1:
            at = plan.bed - timedelta(minutes=r.randint(30, 120))
            if warm <= at <= sess["end"]:
                events["alarms"].append((at, "MOBILE DEVICE BATTERY LOW"))
        if r.random() < dev.get("calibrations_per_day", 0):
            at = plan.wake + timedelta(minutes=r.randint(20, 600), seconds=r.randrange(60))
            if warm <= at <= sess["end"]:
                bg = glucose_minutes(life, at.replace(second=0), 1)[0] * math.exp(r.gauss(0, 0.05))
                events["fingersticks"].append((at, round(bg * MGDL_PER_MMOL)))
    apps = [fmt.carelink_app_id(r) for _ in range(2 if r.random() < 0.3 else 1)]
    s = spec.streams()["formats"]["carelink_csv"]
    account = s["accounts"].get(lang, s["accounts"]["en"])[sess["unit"]]
    first = datetime.combine(sess["start"].date(), time())
    selected = (first, first + timedelta(days=dev["wear_days"] + 1))
    data, hz = fmt.carelink_csv(sess, dev, _name(life.person, lang), account, apps, events, selected)
    exported = min(sess["end"] + timedelta(days=r.randint(1, 14), minutes=r.randrange(1440)),
                   datetime.combine(CORPUS_END, time(23, 0)))
    folder = f"cgm/{sess['n']:02d}_{sess['device']}"
    entry = out.write(f"{folder}/CareLink-Export-{fmt.epoch_ms(exported, life.tz)}.csv", data, format="carelink_csv",
                      channel="file_upload", unit=sess["unit"], loinc=_loinc(sess["unit"]))
    return [entry], hz


FORMATS = {"dexcom": _dexcom, "libre": _libre, "sibionics": _sibionics, "medtronic": _medtronic, "none": _no_export}

#: Who goes beyond the vendor's app: a Nightscout site (a share of English-speaking Dexcom and Libre wearers),
#: fed by the vendor bridge or by xDrip+ on Android; a follower who sees the vendor's share feed.
NIGHTSCOUT_RATE, XDRIP_SHARE, FOLLOWER_RATE = 0.2, 0.5, 0.3
#: A Tidepool account (a clinic's or the wearer's own) whose web export the person downloads; Excel is the
#: dialog's default format.
TIDEPOOL_RATE, TIDEPOOL_EXCEL = 0.15, 0.7
#: IANA zone a Tidepool upload records for each of the cohort's device clocks.
TIMEZONES = {"+08:00": "Asia/Shanghai", "+00:00": "Europe/London", "+09:00": "Asia/Tokyo"}


def _diy(fmt, life: Life, sess: dict, dev: dict, out: _Out, platform: str, lang: str) -> tuple[list[dict], list[str]]:
    """Files beyond the vendor's own export: a Nightscout site's entries (and, for xDrip+ on Android, the
    SiDiary CSV it exports), and a follower's snapshot of Dexcom Share or LibreLinkUp."""
    if dev["family"] not in ("dexcom", "libre") or spec.doc_lang(lang) != "en":
        return [], []
    r = random.Random(f"diy:{life.seed}:{life.person.person_id}")        # a person's set-up, not a sensor's
    rs = random.Random(f"diy:{life.seed}:{life.person.person_id}:{sess['n']}")
    nightscout, follower = r.random() < NIGHTSCOUT_RATE, r.random() < FOLLOWER_RATE
    tidepool = r.random() < TIDEPOOL_RATE
    excel = r.random() < TIDEPOOL_EXCEL
    xdrip = nightscout and platform == "android" and r.random() < XDRIP_SHARE
    folder = f"cgm/{sess['n']:02d}_{sess['device']}"
    files, hazards = [], []
    sess["family"] = dev["family"]
    if nightscout:
        uploader = "xdrip" if xdrip else ("share2" if dev["family"] == "dexcom" else "librelinkup")
        docs, hz = fmt.nightscout_entries(sess, uploader, life.tz, rs)
        # A client that wants the whole sensor asks with find[date][$gte]: the database path, compact JSON,
        # no `mills` (only the in-memory cache of the last two days adds it).
        files.append(out.write(f"{folder}/nightscout_entries.json", json.dumps(docs, separators=(",", ":")).encode("utf-8"),
                               format="nightscout_entries_json", channel="vendor_api", unit="mg/dL",
                               loinc=_loinc("mg/dL"), uploader=uploader))
        files.append(out.write(f"{folder}/nightscout_entries.csv", fmt.nightscout_csv(docs),
                               format="nightscout_entries_csv", channel="vendor_api", unit="mg/dL", loinc=_loinc("mg/dL"),
                               uploader=uploader))
        hazards += hz
    if xdrip:
        exported = min(sess["end"] + timedelta(days=rs.randint(1, 10), minutes=rs.randrange(1440)),
                       datetime.combine(CORPUS_END, time(23, 0)))
        sess["calibrations_mgdl"] = [(t, int(v) if v.isdigit() else round(float(v) * MGDL_PER_MMOL))
                                     for t, v in sess.get("calibrations", [])]
        carbs = [(when, round(load * 45)) for d in window_days(sess["start"].date(), sess["end"].date())
                 for when, _, load in life.plan(d).meals if sess["start"] <= when <= sess["end"] and rs.random() < 0.08]
        data, name, hz = fmt.xdrip_sidiary_zip(sess, exported, carbs)
        files.append(out.write(f"{folder}/{name}", data, format="xdrip_sidiary_csv_zip", channel="file_upload",
                               unit="mg/dL", loinc=_loinc("mg/dL")))
        hazards += hz
    if tidepool:
        uploaded = min(sess["end"] + timedelta(days=rs.randint(0, 5), minutes=rs.randrange(1440)),
                       datetime.combine(CORPUS_END, time(22, 0))).replace(microsecond=0)
        exported = uploaded + timedelta(days=rs.randint(1, 20), minutes=rs.randrange(600))
        serial = sess.get("transmitter_id") or "".join(rs.choice("0123456789ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(10))
        device = {"id": f"{dev['maker']}{dev['model'].replace(' ', '')}_{fmt.hex_id(rs, 8)}", "maker": dev["maker"],
                  "model": dev["model"], "serial": serial, "timezone": TIMEZONES.get(life.tz, "UTC"),
                  "uploader_version": spec.streams()["formats"]["tidepool_export"]["uploader_version"]}
        records = fmt.tidepool_records(sess, life.tz, sess["unit"], device, rs, uploaded)
        if excel:
            data = fmt.tidepool_xlsx(records, sess["unit"], exported - _offset(life.tz))
            files.append(out.write(f"{folder}/TidepoolExport.xlsx", data, format="tidepool_export_xlsx",
                                   channel="file_upload", unit=sess["unit"], loinc=_loinc(sess["unit"])))
        else:
            files.append(out.write(f"{folder}/TidepoolExport.json", fmt.tidepool_json(records),
                                   format="tidepool_export_json", channel="file_upload", unit=sess["unit"],
                                   loinc=_loinc(sess["unit"])))
        hazards += ["stream.unrounded_conversion", "stream.utc_and_local_pair"]
    if follower:
        at = sess["start"] + (sess["end"] - sess["start"]) * rs.uniform(0.3, 1.0)
        at = at.replace(microsecond=0)
        if dev["family"] == "dexcom":
            data, hz = fmt.dexcom_share(sess, at, life.tz)
            files.append(out.write(f"{folder}/dexcom_share_latest.json", data, format="dexcom_share_json",
                                   channel="vendor_api", unit="mg/dL", loinc=_loinc("mg/dL"), at=_iso(at, life.tz)))
        else:
            sess["llu_sn"] = "".join(rs.choice("0123456789ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(10))
            country = {"zh": "CN", "ja": "JP"}.get(lang, "US" if sess["unit"] == "mg/dL" else "GB")
            data, hz = fmt.librelinkup_graph(sess, dev, at, life.tz, _name(life.person, lang), sess["unit"], country, rs)
            files.append(out.write(f"{folder}/librelinkup_graph.json", data, format="librelinkup_graph_json",
                                   channel="vendor_api", unit=sess["unit"], loinc=_loinc(sess["unit"]),
                                   at=_iso(at, life.tz)))
        hazards += hz
    return files, hazards


def _store_glucose(fmt, life: Life, sess: dict, dev: dict, out: _Out, series: dict, r: random.Random) -> list[dict]:
    """The sensor app's readings as the phone health store holds them, in mirobody's `/api/data` batch:
    only for a store the app is known to write to (`devices.<id>.stores`)."""
    store = dev.get("stores", {}).get(series["vendor"])
    if not store or sess["start"].date() < date.fromisoformat(store.get("since", "2000-01-01")):
        return []
    field = spec.streams()["store_fields"]["glucose"][series["vendor"]]
    unit = store.get("unit") or sess["unit"]
    recs = []
    for x in sess["readings"]:
        if x["kind"] != "historic" or x["flag"]:
            continue
        recs.append({"indicator": field, "value": x["mgdl"] if unit == "mg/dL" else x["mmol"], "unit": unit,
                     "time": _iso(x["time"], life.tz)})
    files = []
    for i, data in enumerate(fmt.api_data_batches(recs, series["source"], 500), start=1):
        files.append(out.write(f"cgm/{sess['n']:02d}_{sess['device']}/{series['vendor']}_glucose_batch{i:02d}.json", data,
                               format="api_data_batch", channel="phone_store", unit=unit, loinc=_loinc(unit),
                               indicator=field))
    return files


_MOTION = {"sleep": "1", "rest": "1", "walk": "2", "run": "0"}
_OURA_SOURCE = {"sleep": "sleep", "rest": "awake", "walk": "awake", "run": "workout"}


def _hr_files(fmt, life: Life, wearable: str, wn: int, samples: list[dict], out: _Out, series: dict,
              apple) -> tuple[list[dict], list[str]]:
    from . import devices

    tz = life.tz
    folder = f"hr/{wn:02d}_{wearable}"
    files, hazards = [], []
    if any(x["state"] == "run" for x in samples) and spec.streams()["wearables"][wearable].get("workout_s"):
        hazards.append("stream.workout_burst")
    if wearable == "oura_ring":
        data, hz = fmt.oura_heartrate([x | {"source": _OURA_SOURCE[x["state"]]} for x in samples], tz)
        files.append(out.write(f"{folder}/oura_v2_heartrate.json", data, format="oura_api_v2_heartrate",
                               channel="vendor_api"))
        return files, hazards + hz
    vendor = series["vendor"]
    field, unit = devices.VENDORS[vendor]["fields"].get("hr") or devices.FALLBACK_FIELDS["hr"]
    recs = [{"indicator": field, "value": x["bpm"], "unit": unit, "time": _iso(x["time"], tz)} for x in samples]
    for i, data in enumerate(fmt.api_data_batches(recs, series["source"], 500), start=1):
        files.append(out.write(f"{folder}/{vendor}_hr_batch{i:02d}.json", data, format="api_data_batch",
                               channel="phone_store", indicator=field))
    if wearable == "mi_band" and samples:
        r = random.Random(f"zepp:{life.seed}:{life.person.person_id}")
        uid = str(r.randint(10 ** 9, 2 * 10 ** 9))
        ms = fmt.epoch_ms(samples[-1]["time"] + timedelta(days=r.randint(1, 20)), tz)
        by_minute = {}
        for x in samples:
            by_minute.setdefault(x["time"].replace(second=0), x)
        data, hz = fmt.zepp_heartrate_auto(list(by_minute.values()))
        files.append(out.write(f"{folder}/{uid}_{ms}/HEARTRATE_AUTO/HEARTRATE_AUTO_{ms}.csv", data,
                               format="zepp_life_heartrate_auto_csv", channel="file_upload"))
        hazards += hz
    if wearable == "apple_watch" and apple is not None:
        r = random.Random(f"hk-hr:{life.seed}:{life.person.person_id}:{wn}")
        for x in samples:
            apple.add("HKQuantityTypeIdentifierHeartRate", x["time"], x["time"], value=str(x["bpm"]),
                      unit="count/min", source="Apple Watch", source_version=apple._version("watch", x["time"]),
                      device=apple.device("watch", x["time"]), created=x["time"] + timedelta(seconds=r.randint(2, 9)),
                      metadata={"HKMetadataKeyHeartRateMotionContext": _MOTION[x["state"]]})
    return files, hazards


def _apple_daily(apple, life: Life, series: dict) -> None:
    """The rest of an Apple Health export: the daily records the phone store already holds (resting
    heart rate, sleep, weight, cuff readings) and step counts walked in the day plan's bouts, which add up
    to the store's daily total."""
    r = random.Random(f"hk-daily:{life.seed}:{life.person.person_id}")
    by_time: dict[str, dict[str, dict]] = {}
    for x in series["records"]:
        by_time.setdefault(x["time"], {})[x["_metric"]] = x
    for stamp, group in sorted(by_time.items()):
        when = _local(stamp)
        if "rhr" in group:
            day = when.date()
            start = datetime.combine(day, time(0, r.randint(0, 9), r.randrange(60)))
            end = datetime.combine(day, time(23, r.randint(40, 58), r.randrange(60)))
            apple.add("HKQuantityTypeIdentifierRestingHeartRate", start, end, value=str(group["rhr"]["value"]),
                      unit="count/min", source="Apple Watch", source_version=apple._version("watch", when),
                      device=None, created=end + timedelta(minutes=r.randint(1, 30)))
        if "sleep" in group:
            x = group["sleep"]
            bed, wake = _local(x["time"]), _local(x["end_time"])
            apple.add("HKCategoryTypeIdentifierSleepAnalysis", bed, wake,
                      value="HKCategoryValueSleepAnalysisAsleepUnspecified", unit=None, source="Apple Watch",
                      source_version=apple._version("watch", wake), device=apple.device("watch", wake),
                      created=wake + timedelta(minutes=r.randint(1, 40)))
        if "weight" in group:
            apple.add("HKQuantityTypeIdentifierBodyMass", when, when, value=f"{group['weight']['value']:.1f}", unit="kg",
                      source="Health", source_version=None, device=None,
                      created=when + timedelta(seconds=r.randint(5, 120)), metadata={"HKWasUserEntered": "1"})
        if "sbp" in group and "dbp" in group:
            apple.add_blood_pressure(when, group["sbp"]["value"], group["dbp"]["value"],
                                     when + timedelta(seconds=r.randint(20, 240)))
            if "hr" in group:
                apple.add("HKQuantityTypeIdentifierHeartRate", when, when, value=str(group["hr"]["value"]),
                          unit="count/min", source="Health", source_version=None, device=None,
                          created=when + timedelta(seconds=r.randint(20, 240)), metadata={"HKWasUserEntered": "1"})
        if "steps" in group:
            plan = life.plan(when.date())
            total = int(group["steps"]["value"])
            bouts = [(t, m, int(m * WALK_SPM)) for t, m in plan.walks]
            if plan.run:
                bouts.append((plan.run[0], plan.run[1], plan.run[1] * RUN_SPM))
            walked = sum(n for _, _, n in bouts)
            if walked > total:                              # scale down to the store's total
                bouts = [(t, m, n * total // max(walked, 1)) for t, m, n in bouts]
                walked = sum(n for _, _, n in bouts)
            # What was not walked in bouts is pottering: three records over the waking day.
            rest = total - walked
            third = (plan.bed - plan.wake) / 3
            for k in range(3):
                share = rest // 3 if k < 2 else rest - 2 * (rest // 3)
                if share > 0:
                    bouts.append((plan.wake + third * k + timedelta(minutes=r.randint(0, 30)),
                                  max(1, int(third.total_seconds() // 60) - 40), share))
            for t, m, n in sorted(bouts):
                if n <= 0:
                    continue
                apple.add("HKQuantityTypeIdentifierStepCount", t, t + timedelta(minutes=m), value=str(n), unit="count",
                          source="iPhone", source_version=apple._version("phone", t), device=apple.device("phone", t),
                          created=t + timedelta(minutes=m, seconds=r.randint(5, 600)))
