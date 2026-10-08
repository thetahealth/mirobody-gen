"""Cohort engine: 60 synthetic people, their timelines, and what each visit orders.

## The cohort is prevalence-weighted, not uniformly sampled

Uniform sampling would make "abnormal rate" meaningless as a thing to calibrate against: in the
reference corpus total cholesterol is abnormal 27% of the time, triglycerides 31%, uric acid 14%,
and platelet count near 0%. These ratios are a consequence of population composition, not a
parameter of the value model — so the population is fixed first, and indicators grow out of it.

## A lab order is a "panel", not "every indicator"

Each ESL-Bench exam carries 193 indicators (it doesn't model document shape, which is fine for its
purpose). Real reports don't look like that: a check-up report prints dozens of items, a chemistry
panel a dozen or so, an ESR slip a single line. So orders here are organised by **panel** (liver
function / renal function / lipids / glucose / CBC / urinalysis, ...), with one to three panels
ordered per encounter — this is what decides whether the generated corpus's `rows_per_document`
distribution matches the measured p50=6 / p95=36.
"""

from __future__ import annotations

import math
import random
from datetime import date, timedelta

from . import profile, spec
from .model import Encounter, Event, Person, Reading
from .physiology import (
    derive, derive_second_pass, measure, normalize_differential,
    categorical_value, person_baseline, qualitative_value,
)

#: End of the corpus's time window. A literal date rather than `date.today()`: the same seed run on
#: different days must produce the same files, or regression diffing has no baseline.
CORPUS_END = date(2026, 9, 1)

# Cohort design (orders, archetypes, scripts, random incidents, regions) all lives in
# `resources/cohort.json`, hand-authored by `scripts/build_cohort.py`. This module only uses it to
# build people and holds no second copy of that definition — a table kept in two places eventually
# has one go stale, and the stale one fails silently, quietly generating the wrong people.
_COHORT = spec.cohort()
ORDERS: dict[str, list[str]] = _COHORT["orders"]
ARCHETYPES: list[dict] = _COHORT["archetypes"]
SCRIPTS: dict[str, dict] = _COHORT["scripts"]
INCIDENTS: list[dict] = _COHORT["incidents"]
REGIONS: list[str] = _COHORT["regions"]


def build_cohort(seed: int, size: int | None = None) -> list[Person]:
    """An entire cohort. Same seed, same result, always."""
    rng = random.Random(f"cohort:{seed}")
    full = sum(a["n"] for a in ARCHETYPES)
    # `--people N` must **scale proportionally across archetypes**, not just take the first N: taking
    # the first N would give an all-healthy cohort, so a smoke test would never see a chronic-disease
    # trajectory, an intervention event, or an abnormal value — exactly what it's meant to test.
    scale = 1.0 if size is None else size / full
    people: list[Person] = []
    index = 0
    for archetype in ARCHETYPES:
        count = archetype["n"] if size is None else max(1, round(archetype["n"] * scale))
        for _ in range(count):
            if size is not None and len(people) >= size:
                return people
            index += 1
            people.append(_one_person(rng, f"p{index:03d}", archetype))
    return people


def _one_person(rng: random.Random, person_id: str, archetype: dict) -> Person:
    sex = "male" if rng.random() < 0.5 else "female"
    # Iron-deficiency anaemia skews female, an epidemiological fact; without this, sex-stratified
    # reference ranges never get exercised — if both sexes used the same range there would be no
    # way to tell whether the stratification is even correct.
    if archetype["name"] == "iron_deficiency_anemia":
        sex = "female" if rng.random() < 0.85 else "male"
    age = int(rng.triangular(20, 79, 45 if archetype["name"] == "healthy" else 55))
    birth_year = CORPUS_END.year - age
    height = rng.gauss(172 if sex == "male" else 160, 6.5)

    baseline: dict[str, float] = {}
    for key in spec.indicators():
        baseline[key] = person_baseline(rng, key, sex)
    # The red-cell indices' latent variables are MCV and MCHC (each with its own CVG); MCH is
    # determined by the two of them. These three public CVGs (4.85%, 2.8%, 5.2%) are only mutually
    # consistent when MCH = MCHC x MCV. Derivation happens in physiology.derive; no extra coupling
    # of baselines is needed here.

    for key, multiplier in archetype["shift"].items():
        if key == "weight_kg":
            continue
        baseline[key] = baseline.get(key, 1.0) * multiplier

    # Weight: set from height, a target BMI and the archetype's shift, then given a slow drift over the years.
    bmi_target = rng.gauss(23.0, 2.6) * archetype["shift"].get("weight_kg", 1.0)
    weight = max(38.0, bmi_target * (height / 100) ** 2)
    span_years = rng.choice([2, 3, 3, 4, 5])
    start = CORPUS_END - timedelta(days=365 * span_years)
    drift = rng.gauss(0.0, 0.035)          # natural weight drift over the years
    anchors = tuple((start + timedelta(days=365 * y),
                     weight * (1 + drift * y)) for y in range(span_years + 1))

    events = _timeline(rng, archetype, start)
    trend = dict(archetype["trend"])

    return Person(person_id=person_id, sex=sex, birth_year=birth_year,
                  height_cm=round(height, 1), archetype=archetype["name"],
                  region=rng.choice(REGIONS), baseline=baseline, trend=trend,
                  events=events, weight_anchors=anchors)


def _timeline(rng: random.Random, archetype: dict, start: date) -> tuple[Event, ...]:
    """Timeline: the archetype's intervention event plus one to three random incidents."""
    events: list[Event] = []
    script_name = archetype["script"]
    if script_name:
        script = SCRIPTS[script_name]
        # The intervention happens around the middle of the observation window, so there are
        # follow-up points to compare on both sides — an event pushed past the last exam leaves the
        # attribution question with no observable answer.
        offset = rng.randint(200, 420)
        events.append(Event(
            name=script["name"], event_type=script["type"],
            start=start + timedelta(days=offset), duration_days=3650,
            health_effect=script["effect"], impact_level=script["impact"],
            effects={k: tuple(v) for k, v in script["effects"].items() if k != "weight_kg"},
            note="原型剧本"))
    for _ in range(rng.randint(1, 3)):
        incident = rng.choice(INCIDENTS)
        when = start + timedelta(days=rng.randint(30, (CORPUS_END - start).days - 30))
        events.append(Event(
            name=incident["name"], event_type=incident["type"], start=when,
            duration_days=incident["duration"], health_effect=incident["effect"],
            impact_level=incident["impact"],
            effects={k: tuple(v) for k, v in incident["effects"].items() if k != "weight_kg"},
            note="随机事件"))
    return tuple(sorted(events, key=lambda e: e.start))


def schedule(person: Person, rng: random.Random) -> list[tuple[date, str, list[str], str | None]]:
    """(date, encounter type, order list, package). Annual check-ups + archetype follow-ups + occasional one-offs.

    Check-ups are ordered by package tier (entry / senior / basic / standard / premium, see
    `checkup_packages` in `resources/cohort.json`), drawn per person with some yearly chance of
    upgrading (new job, getting older). About 40% of chronic-disease follow-ups are **clinic**
    visits (`clinic`: a clinic note plus a lab panel); the rest are lab-panel-only repeats.
    Hypertension follow-ups are always clinic visits, since blood pressure is measured in clinic and
    never printed on a lab report.
    """
    start = person.weight_anchors[0][0]
    out: list[tuple[date, str, list[str], str | None]] = []

    # Annual check-up: once a year, the date drifting around the same month (real check-ups also
    # cluster "around March every year").
    anniversary = rng.randint(0, 364)
    year = start
    tier = profile.choose_package(person, "checkup_center", rng)
    order = ["basic", "standard", "premium"]
    # Working-age people have some chance that their first check-up is an entry physical (new job),
    # with annual check-ups afterward.
    first_entry = 22 <= person.age_at(start) <= 45 and rng.random() < 0.25
    while year <= CORPUS_END:
        when = year + timedelta(days=anniversary % 365)
        if start <= when <= CORPUS_END:
            this = "entry" if first_entry and not out else tier
            out.append((when, "routine", [f"checkup_{this}", "ecg"], this))
            if rng.random() < 0.12 and tier in order and tier != "premium":
                tier = order[order.index(tier) + 1]
        year += timedelta(days=365)

    archetype = next(a for a in ARCHETYPES if a["name"] == person.archetype)
    if archetype["followup"]:
        step = timedelta(days=30 * archetype["interval"])
        when = start + timedelta(days=rng.randint(40, 120))
        while when <= CORPUS_END:
            orders = list(archetype["followup"])
            if orders == ["glucose"] and rng.random() < 0.3:
                orders = ["glucose_ext"]
            if rng.random() < 0.25:
                orders.append(rng.choice(["liver", "renal", "inflammation"]))
            clinic = "vitals_clinic" in orders or rng.random() < 0.4
            if clinic and "vitals_clinic" not in orders:
                orders.insert(0, "vitals_clinic")
            out.append((when, "clinic" if clinic else "follow-up", orders, None))
            when += step

    # Occasional one-offs: an ESR slip, a urinalysis, an ECG or an abdominal ultrasound report — the
    # reference corpus has plenty of single-line reports (measured rows_per_document p25=1), and
    # imaging/ECG reports each number in the dozens too.
    for _ in range(rng.randint(0, 2)):
        when = start + timedelta(days=rng.randint(10, (CORPUS_END - start).days))
        out.append((when, "specialty", [rng.choice(["inflammation", "urinalysis", "ecg", "ultrasound",
                                                    "imaging", "thyroid", "tumor", "cbc"])], None))
    # A cold sends them to a community clinic: CBC + CRP + a clinic note.
    for e in person.events:
        if e.name == "急性上呼吸道感染" and rng.random() < 0.7:
            out.append((e.start + timedelta(days=rng.randint(1, 3)), "clinic", ["vitals_clinic", "cbc", "inflammation"], None))
    return sorted(out, key=lambda x: (x[0], x[1]))


#: catalogue panel -> detection_method in mirobody's extraction contract. Anything not listed is a lab test.
DETECTION = {"vitals": "Physiological", "ecg": "Physiological", "spirometry": "Physiological",
             "arterial": "Physiological", "body_composition": "Physiological", "echo": "Imaging"}


def encounter_for(person: Person, when: date, exam_type: str, orders: list[str],
                  rng: random.Random, package: str | None = None) -> Encounter:
    """Turn one encounter into a set of readings."""
    keys: list[str] = []
    for order in orders:
        for key in ORDERS.get(order, []):          # an order with no lab items, like "ultrasound", gives an empty list
            if key not in keys:
                keys.append(key)
    # Sex-specific items: no CA125 for men, no PSA for women.
    keys = [k for k in keys
            if not (spec.indicators()[k].get("sex_specific")
                    and spec.indicators()[k]["sex_specific"] != person.sex)]

    catalogue = spec.indicators()
    primaries = [k for k in keys if not catalogue[k]["derived_from"]
                 and catalogue[k]["value_kind"] == "quantitative"]
    qualitatives = [k for k in keys if catalogue[k]["value_kind"] == "qualitative"]
    categoricals = [k for k in keys if catalogue[k]["value_kind"] == "categorical"]

    raw: dict[str, float] = {k: measure(rng, person, k, when) for k in primaries}
    normalize_differential(raw)
    values = {k: float(spec.format_value(k, v)) for k, v in raw.items()}

    for derived in (derive(values, person, when), derive_second_pass(values, person)):
        for key, value in derived.items():
            if key in keys or key in ("glb",):     # globulin feeds the A/G ratio, so compute it even when not printed
                values[key] = float(spec.format_value(key, value))

    readings: list[Reading] = []
    for key in keys:
        item = catalogue[key]
        if key in qualitatives:
            text = qualitative_value(rng, person, key, when)
            normal_text = spec.reference_text(key, person.sex)
            # Positive isn't always abnormal: a positive hepatitis B surface antibody just means vaccinated.
            abnormal = text != normal_text and not (key == "hbsab")
            readings.append(Reading(
                key=key, original_indicator=item["zh"], value=text, unit=item["unit"],
                reference_range=normal_text, status="high" if abnormal else "normal",
                canonical_value=None, unit_ucum=item["unit"], loinc=item.get("loinc"),
                value_kind="qualitative", expect_resolvable=item.get("expect_resolvable", False),
            ))
            continue
        if key in categoricals:
            readings.append(Reading(
                key=key, original_indicator=item["zh"], value=categorical_value(person, key), unit="",
                reference_range="", status="normal", canonical_value=None, unit_ucum="", loinc=item.get("loinc"),
                value_kind="categorical", expect_resolvable=item.get("expect_resolvable", False),
            ))
            continue
        if key not in values:
            continue
        value = values[key]
        lo, hi = spec.reference_bounds(key, person.sex)
        readings.append(Reading(
            key=key, original_indicator=item["zh"], value=spec.format_value(key, value),
            unit=item["unit"], reference_range=spec.reference_text(key, person.sex),
            status=spec.status_for(key, value, person.sex),
            detection_method=DETECTION.get(item["panel"], "laboratory"),
            canonical_value=value, unit_ucum=item["unit"], loinc=item.get("loinc"),
            value_kind="quantitative", expect_resolvable=item.get("expect_resolvable", False),
            ref_low=lo, ref_high=hi,
        ))

    location = {"routine": "checkup-center", "follow-up": "hospital", "clinic": "hospital",
                "specialty": "clinic"}.get(exam_type, "hospital")
    return Encounter(person_id=person.person_id, exam_date=when, exam_type=exam_type,
                     exam_location=location, panels=tuple(orders), readings=readings, package=package)


def home_stream(seed: int, person_id: str) -> random.Random:
    """The random stream that decides a person's language group and home institutions."""
    return random.Random(f"home:{seed}:{person_id}")


#: Share of each language group, overridable with `build --lang-mix`. Groups are sorted by name and
#: split [0, 1) in that order, so the default draws "en" below 0.5 exactly as 0.3.0 did, and a mix
#: gives the same cohort however it is written. A group other than zh/en changes who is in the cohort
#: (device time zone, brands, allele frequencies); its documents use the English templates.
_LANG_MIX: dict[str, float] = {"en": 0.5, "zh": 0.5}


def set_lang_mix(text: str) -> None:
    """Set the language mix from `zh:0.45,en:0.4,ja:0.15`; weights are normalised, a bare name weighs 1."""
    global _LANG_MIX
    mix: dict[str, float] = {}
    for part in text.split(","):
        name, _, weight = (s.strip() for s in part.partition(":"))
        if not name or name in mix:
            raise ValueError(f"empty or repeated language group in {text!r}")
        mix[name] = float(weight) if weight else 1.0
        if not math.isfinite(mix[name]) or mix[name] < 0:
            raise ValueError(f"weight must be a finite non-negative number: {part!r}")
    total = sum(mix.values())
    if total <= 0:
        raise ValueError(f"all weights are zero: {text!r}")
    _LANG_MIX = {name: mix[name] / total for name in sorted(mix)}


def draw_lang(rng: random.Random) -> str:
    roll = rng.random()
    acc = 0.0
    for name, weight in _LANG_MIX.items():
        acc += weight
        if roll < acc:
            return name
    return next(reversed(_LANG_MIX))  # floating-point remainder


def person_lang(seed: int, person_id: str) -> str:
    """This person's language group (zh / en / another group introduced via --lang-mix).
    `corpus.home_institutions` draws its language from the same sample of the same stream, so the
    check-up institution, chief complaints, diary, device and genomics files all agree on language —
    nobody gets a Chinese check-up and an English diary."""
    return draw_lang(home_stream(seed, person_id))


def encounters_for(person: Person, seed: int) -> list[Encounter]:
    rng = random.Random(f"enc:{seed}:{person.person_id}")
    states = profile.person_findings(person, seed)
    lang = person_lang(seed, person.person_id)
    out: list[Encounter] = []
    previous: date | None = None
    for when, exam_type, orders, package in schedule(person, rng):
        encounter = encounter_for(person, when, exam_type, orders, rng, package)
        # Events that **started, or whose effect is still ramping up,** between the previous
        # encounter and this one: a statin takes 35 days to kick in, iron supplementation 90 — an
        # event that started before the previous visit but whose effect lands in this window is also
        # an explanation for the jump. After an event ends its effect decays by half-life: one still
        # decaying at the previous visit also explains this drop.
        encounter.events_since_previous = tuple(
            e.name for e in person.events
            if e.start <= when and (previous is None or e.start > previous
                                    or e.start + timedelta(days=max([o for _, o, _ in e.effects.values()] or [0])) > previous
                                    or e.start + timedelta(days=e.duration_days + 3 * max([h or 0 for _, _, h in e.effects.values()] or [0])) > previous))
        values = {r.key: r.canonical_value for r in encounter.readings if r.canonical_value is not None}
        if exam_type == "routine":
            encounter.findings = profile.encounter_findings(person, when, states, package, values, seed)
        elif exam_type == "specialty" and "ultrasound" in orders:
            encounter.findings = [f for f in profile.encounter_findings(person, when, states, "premium", values, seed)
                                  if f.where == "abd_us"]
        elif exam_type == "specialty" and "imaging" in orders:
            encounter.findings = [f for f in profile.encounter_findings(person, when, states, "premium", values, seed)
                                  if f.where in ("chest_xray", "chest_ct")]
        encounter.complaints = profile.complaints_for(person, when, exam_type, lang, seed)
        out.append(encounter)
        previous = when
    return out
