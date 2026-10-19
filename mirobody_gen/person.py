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
import copy
import dataclasses
import json
import random
from datetime import date, timedelta

from . import clinical, physiology, profile, spec
from .model import DoseStep, Encounter, Event, MedicationCourse, MissingResult, Person, Reading
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
#: Lunar New Year's Day by year, read from cohort.json (sourced in scripts/build_cohort.py).
LUNAR_NEW_YEAR: dict[int, date] = {int(y): date.fromisoformat(d) for y, d in _COHORT["lunar_new_year"].items()}

ENCOUNTER_STREAM_POLICY = "encounter-streams-v2"


def build_cohort(seed: int, size: int | None = None) -> list[Person]:
    """An entire cohort. Same seed, same result, always."""
    rng = random.Random(f"cohort:{seed}")
    counts = archetype_counts(size)
    people: list[Person] = []
    index = 0
    for archetype in ARCHETYPES:
        for _ in range(counts[archetype["name"]]):
            index += 1
            people.append(_one_person(rng, f"p{index:03d}", archetype))
    return people


def archetype_counts(size: int | None) -> dict[str, int]:
    """Exactly `size` seats apportioned across archetypes by their prevalence weight `n`.

    `--people N` must **scale proportionally across archetypes**, not just take the first N: taking
    the first N would give an all-healthy cohort, so a smoke test would never see a chronic-disease
    trajectory, an intervention event, or an abnormal value — exactly what it's meant to test. The
    earlier rule (`max(1, round(n * scale))`, then truncating in table order) returned 60 people for
    61 and 99 for 100, and for 7/8 dropped whichever archetypes came last in the table.

    Largest-remainder apportionment (ties broken by table order) gives each archetype within one
    seat of its exact quota and the full table back when `size` equals its total. When there are at
    least as many seats as archetypes, each archetype gets one, taken from the archetype with the
    most seats; fewer seats than archetypes cannot cover them all and are not asked to.
    """
    weights = {a["name"]: a["n"] for a in ARCHETYPES}
    full = sum(weights.values())
    if size is None:
        return dict(weights)
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise ValueError(f"cohort size must be a positive integer, got {size!r}")
    quota = {name: n * size / full for name, n in weights.items()}
    seats = {name: int(q) for name, q in quota.items()}
    order = list(weights)
    by_remainder = sorted(order, key=lambda name: (-(quota[name] - seats[name]), order.index(name)))
    for name in by_remainder[:size - sum(seats.values())]:
        seats[name] += 1
    if size >= len(order):
        for name in order:
            if seats[name] == 0:
                # the donor holds the most seats; on a tie, the one furthest above its quota
                donor = max(order, key=lambda d: (seats[d], seats[d] - quota[d], -order.index(d)))
                seats[donor] -= 1
                seats[name] = 1
    assert sum(seats.values()) == size
    return seats


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

    events = _timeline(rng, archetype, start, birth_year)
    trend = dict(archetype["trend"])

    return Person(person_id=person_id, sex=sex, birth_year=birth_year,
                  height_cm=round(height, 1), archetype=archetype["name"],
                  region=rng.choice(REGIONS), baseline=baseline, trend=trend,
                  events=events, weight_anchors=anchors)


def _age_at(birth_year: int, when: date) -> int:
    """Same formula as `Person.age_at`, usable before a `Person` exists."""
    return when.year - birth_year - ((when.month, when.day) < (7, 1))


def _eligible_incidents(birth_year: int, when: date) -> list[dict]:
    """Incidents whose `age_range` (inclusive) covers this person's age on this date. An incident
    with no `age_range` is open to anyone."""
    age = _age_at(birth_year, when)
    return [i for i in INCIDENTS if i.get("age_range", (0, 200))[0] <= age <= i.get("age_range", (0, 200))[1]]


def _spring_festival_near(rng: random.Random, candidate: date, start: date, end: date) -> date:
    """Snap a candidate date onto the Lunar New Year closest to it, with a few days of jitter for
    "the feast starts a little before or after New Year's Day" -- not an arbitrary day of the year."""
    anchor = min(LUNAR_NEW_YEAR.values(), key=lambda d: abs((d - candidate).days))
    when = anchor + timedelta(days=rng.randint(-2, 4))
    return max(start, min(end, when))


def _timeline(rng: random.Random, archetype: dict, start: date, birth_year: int) -> tuple[Event, ...]:
    """Timeline: the archetype's intervention event plus one to three random incidents."""
    events: list[Event] = []
    script_name = archetype["script"]
    if script_name:
        script = SCRIPTS[script_name]
        # The intervention happens around the middle of the observation window, so there are
        # follow-up points to compare on both sides — an event pushed past the last exam leaves the
        # attribution question with no observable answer.
        offset = rng.randint(200, 420)
        begin = start + timedelta(days=offset)
        # A dose-dependent treatment starts as one continuous step at the script's dose, fully taken
        # and never stopped; titration, stops and adherence are explicit course edits (with_course).
        course = (MedicationCourse(script["course"]["drug"], (DoseStep(begin, script["course"]["dose_mg"]),))
                  if script.get("course") else None)
        events.append(Event(
            name=script["name"], event_type=script["type"],
            start=begin, duration_days=3650,
            health_effect=script["effect"], impact_level=script["impact"],
            # Cohort resources call this weight_kg; event truth uses the catalogue key so
            # documents, scales and attribution all refer to the same indicator.
            effects={"weight" if k == "weight_kg" else k: tuple(v)
                     for k, v in script["effects"].items()},
            note="原型剧本", course=course))
    for _ in range(rng.randint(1, 3)):
        when = start + timedelta(days=rng.randint(30, (CORPUS_END - start).days - 30))
        # Age-gate before drawing, not after: a draw that lands on "献血 400mL" at age 63 should
        # redraw from what's actually plausible at that age, not silently waive the gate.
        incident = rng.choice(_eligible_incidents(birth_year, when))
        if incident["name"] == "春节假期饮食":
            when = _spring_festival_near(rng, when, start, CORPUS_END)
        events.append(Event(
            name=incident["name"], event_type=incident["type"], start=when,
            duration_days=incident["duration"], health_effect=incident["effect"],
            impact_level=incident["impact"],
            effects={"weight" if k == "weight_kg" else k: tuple(v)
                     for k, v in incident["effects"].items()},
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
    # Clone without consuming the routine stream. The incident root must not
    # depend on later routine-schedule draw counts (for example the horizon).
    event_stream = random.Random()
    event_stream.setstate(rng.getstate())
    event_root = event_stream.getrandbits(128)
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
    # An event's list position or an added/deleted future incident must not decide
    # whether/when an earlier incident gets a visit. Fork once from the stable
    # initial routine-schedule state, then address each incident independently.
    occurrences: dict[tuple, int] = {}
    for e in person.events:
        if e.name != "急性上呼吸道感染":
            continue
        identity = (e.name, e.event_type, e.start.isoformat())
        occurrence = occurrences.get(identity, 0)
        occurrences[identity] = occurrence + 1
        event_rng = random.Random(json.dumps([ENCOUNTER_STREAM_POLICY, "incident", event_root,
                                             person.person_id, identity, occurrence]))
        if event_rng.random() < 0.7:
            out.append((e.start + timedelta(days=event_rng.randint(1, 3)), "clinic", ["vitals_clinic", "cbc", "inflammation"], None))
    return sorted(out, key=lambda x: (x[0], x[1]))


#: catalogue panel -> detection_method in mirobody's extraction contract. Anything not listed is a lab test.
DETECTION = {"vitals": "Physiological", "ecg": "Physiological", "spirometry": "Physiological",
             "arterial": "Physiological", "body_composition": "Physiological", "echo": "Imaging"}


def encounter_for(person: Person, when: date, exam_type: str, orders: list[str],
                  rng: random.Random, package: str | None = None, *, direct_ldl: Reading | None = None) -> Encounter:
    """Turn one encounter into a set of readings."""
    keys: list[str] = []
    for order in orders:
        # Existing narrative/image procedures intentionally have no analyte list.
        if order not in ORDERS and order not in ("ultrasound", "imaging"):
            raise ValueError(f"Unknown order panel: {order}")
        for key in ORDERS.get(order, []):          # an order with no lab items, like "ultrasound", gives an empty list
            if key not in keys:
                keys.append(key)
    ordered_keys = tuple(keys)
    if direct_ldl is not None:
        if ("ldl" not in keys or direct_ldl.key != "ldl" or direct_ldl.result_method != "direct_assay"
                or not isinstance(direct_ldl.source, str) or not direct_ldl.source.strip()
                or direct_ldl.value_kind != "quantitative"
                or direct_ldl.unit != "mmol/L" or direct_ldl.unit_ucum != "mmol/L"
                or type(direct_ldl.canonical_value) not in (float, int)
                or not math.isfinite(direct_ldl.canonical_value) or direct_ldl.canonical_value < 0):
            raise ValueError("direct_ldl requires an ordered, finite, sourced direct-assay result in mmol/L")
        try:
            if float(direct_ldl.value) != direct_ldl.canonical_value:
                raise ValueError("direct_ldl text and numeric result disagree")
        except (TypeError, ValueError) as exc:
            raise ValueError("direct_ldl text and numeric result disagree") from exc
    # Preserve excluded orders as explicit non-results rather than deleting the orders.
    keys = [k for k in keys
            if not (spec.indicators()[k].get("sex_specific")
                    and spec.indicators()[k]["sex_specific"] != person.sex)]

    catalogue = spec.indicators()
    missing = [MissingResult(k, catalogue[k]["unit"], "not_applicable_sex_specific",
                             {"sex": person.sex, "applicable_sex": catalogue[k]["sex_specific"]})
               for k in ordered_keys if k not in keys]
    primaries = [k for k in keys if not catalogue[k]["derived_from"]
                 and catalogue[k]["value_kind"] == "quantitative"]
    if "chol" in keys and "ldl" not in keys:
        primaries.append("ldl")  # the modelled LDL state supports a derived TC even when not printed
    qualitatives = [k for k in keys if catalogue[k]["value_kind"] == "qualitative"]
    categoricals = [k for k in keys if catalogue[k]["value_kind"] == "categorical"]

    raw: dict[str, float] = {k: measure(rng, person, k, when) for k in primaries}
    normalize_differential(raw)
    values = {k: float(spec.format_value(k, v)) for k, v in raw.items()}

    def record_derived(derived: dict[str, float]) -> None:
        for key, value in derived.items():
            if key in keys or key in ("glb",):     # globulin feeds the A/G ratio, so compute it even when not printed
                values[key] = float(spec.format_value(key, value))

    # Evaluate each pass only after recording the preceding pass's rounded results. Eagerly
    # constructing a tuple of both calls leaves BMI/globulin/free PSA absent from pass two.
    record_derived(derive(values, person, when))
    record_derived(derive_second_pass(values, person))
    # The laboratory reports LDL as the Friedewald calculation from TC, HDL and TG: without all
    # three, or above TG 4.5 mmol/L where Friedewald is not used, it is not reported (with its
    # reason below) even though the modelled LDL still determines TC. A direct assay is not
    # modelled; no value is invented from missing inputs or an invalid formula.
    if "ldl" in values and (any(k not in values for k in ("chol", "hdl", "tg")) or values["tg"] > 4.5):
        del values["ldl"]

    readings: list[Reading] = []
    for key in keys:
        item = catalogue[key]
        if key == "ldl" and direct_ldl is not None:
            readings.append(copy.deepcopy(direct_ldl))
            continue
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
            details = {"declared_derivation": item.get("derived_from")}
            reason, method, source = "result_unavailable", "not_modelled", "synthetic_generator"
            if key == "ldl":
                method, source = "calculated_friedewald", "synthetic_calculation"
                absent = [k for k in ("chol", "hdl", "tg") if k not in values]
                if absent:
                    reason, details = "calculation_inputs_missing", {"missing_inputs": absent, "direct_result_supplied": False}
                elif values["tg"] > 4.5:
                    reason, details = "tg_above_calculation_limit", {
                        "tg": values["tg"], "tg_text": spec.format_value("tg", values["tg"]), "tg_unit": "mmol/L",
                        "limit": 4.5, "direct_result_supplied": False}
            missing.append(MissingResult(key, item["unit"], reason, details, method, source))
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
            result_method="calculated_friedewald" if key == "ldl" else None,
            source="synthetic_calculation" if key == "ldl" else "synthetic_generator",
        ))

    if clinical.ENABLED:
        from . import clinical_checkup

        ordered_keys, readings, missing = clinical_checkup.orders(ordered_keys, readings, missing, person, when,
                                                                  exam_type, package)
    location = {"routine": "checkup-center", "follow-up": "hospital", "clinic": "hospital",
                "specialty": "clinic"}.get(exam_type, "hospital")
    encounter = Encounter(person_id=person.person_id, exam_date=when, exam_type=exam_type,
                          exam_location=location, panels=tuple(orders), readings=readings, package=package,
                          ordered_keys=ordered_keys, missing_results=missing)
    encounter.validate_order_results()
    return encounter


def _changing(event: Event, previous: date | None, when: date) -> bool:
    """Whether the event's effect changes between the previous encounter and this one, so it can
    explain a difference between them: it starts in between; it is still ramping up after the previous
    visit (a statin takes weeks to kick in, iron supplementation 90 days); it has ended by this visit
    and is still decaying after the previous one (its effect falls by half-life once it ends); or a
    treatment course changes dose, adherence or stops in between. An event whose effect is flat across
    the whole interval -- a treatment years into steady use -- does not explain it. The first visit
    lists every event that has started by then."""
    if event.start > when:
        return False
    if previous is None or event.start > previous:
        return True
    # Laboratory HbA1c keeps moving for its history window after the glycaemic state settles.
    lag = {k: physiology.HBA1C_WINDOW_DAYS if k == "hba1c" else 0 for k in event.effects}
    onset = max([o + lag[k] for k, (_, o, _) in event.effects.items()] or [0])
    if event.start + timedelta(days=onset) > previous:
        return True
    end = event.start + timedelta(days=event.duration_days)
    tails = [3 * h + lag[k] for k, (_, _, h) in event.effects.items() if h is not None]  # None: never decays
    if tails and end <= when and end + timedelta(days=max(tails)) > previous:
        return True
    return _course_changing(event, previous, when)


def _course_changing(event: Event, previous: date, when: date) -> bool:
    """A dose change, adherence change or stop after `previous` (or still ramping at it) up to `when`."""
    if event.course is None:
        return False
    response = spec.cohort()["medication_response"][event.course.drug]
    ramp = timedelta(days=max(response["onset_days"], response["offset_days"]))
    return any(change <= when and change + ramp > previous for change in event.course.change_dates())


def with_course(person: Person, event_name: str, steps: list[DoseStep], stop: date | None = None) -> Person:
    """`person` with the named treatment's exposure course replaced: dose steps (dose, adherence, from a
    date on) and an optional stop. The first step must start on the event's own start date. A stop ends
    the event there, so its fixed effects decay from that day too."""
    events, found = [], False
    for event in person.events:
        if event.name == event_name:
            found = True
            if event.course is None:
                raise ValueError(f"{event_name} has no dose-dependent course")
            if steps[0].start != event.start:
                raise ValueError("the first dose step must start on the treatment's start date")
            course = MedicationCourse(event.course.drug, tuple(steps), stop)
            duration = (stop - event.start).days if stop is not None else event.duration_days
            event = dataclasses.replace(event, course=course, duration_days=duration)
        events.append(event)
    if not found:
        raise ValueError(f"{person.person_id} has no event {event_name}")
    return dataclasses.replace(person, events=tuple(events))


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
    """Default v2 schedule with independently addressed measurement streams.

    This intentionally changes draws from the legacy shared stream. A seed must
    always be interpreted with its policy/code version; archived v1 outputs are
    comparison evidence, not recalibrated/overwritten by this function.
    """
    schedule_rng = random.Random(f"enc:{seed}:{person.person_id}")
    states = profile.person_findings(person, seed)
    lang = person_lang(seed, person.person_id)
    out: list[Encounter] = []
    previous: date | None = None
    occurrences: dict[tuple, int] = {}
    for when, exam_type, orders, package in schedule(person, schedule_rng):
        # Noise is owned by this scheduled visit, not by the full schedule's
        # future draw count or by earlier visits' order counts. Exact duplicate
        # slots retain multiplicity through a local occurrence number.
        identity = (when.isoformat(), exam_type, tuple(orders), package)
        occurrence = occurrences.get(identity, 0)
        occurrences[identity] = occurrence + 1
        visit_rng = random.Random(json.dumps([ENCOUNTER_STREAM_POLICY, "observation", seed,
                                             person.person_id, identity, occurrence]))
        encounter = encounter_for(person, when, exam_type, orders, visit_rng, package)
        encounter.events_since_previous = tuple(e.name for e in person.events if _changing(e, previous, when))
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
        from .manifest import _conditions_of

        encounter.conditions = _conditions_of(person, out, when)
        previous = when
    return out
