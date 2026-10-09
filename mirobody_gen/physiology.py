"""Value model: what a person's indicator reads on a given day (interval centre, baseline, trend, events, noise, identities).

One reading =
`reference-interval centre x individual baseline (CVG) x chronic-disease trend x event effects x
within-subject variation (CVI) x analytical variation (CVA)`,
rounded to the printed number of significant digits. **Derived indicators skip this path** — they
are computed from already-rounded primary values via identities (see `derive`), so the numbers
printed on a report stay mutually consistent, which is exactly what `audit/clinical.py` checks for.

Three choices here are deliberate:

1. **Values don't come from a reference corpus.** The centre comes from the reference intervals in
   `resources/indicators.json` (public standards); the jitter comes from Westgard biological
   variation. The reference corpus only appears later, for reconciliation (`build_indicators --compare`).
2. **Round before deriving.** Computing MCHC first and rounding after would leave the printed
   hemoglobin/hematocrit and the printed MCHC off by a quantization error, and the audit would flag
   a pile of "identity doesn't hold" cases that aren't real bugs. Real instruments also emit integers
   first and compute derived quantities afterward.
3. **Event effects are a computable truth.** The answer to an attribution question ("why did LDL
   drop") is the injected event itself — no post-hoc explanatory model is needed.
"""

from __future__ import annotations

import math
import random
from datetime import date

from . import spec

#: Conservative within-subject variation (%) when no CVI is on file. Better to under-jitter than to
#: invent "disease fluctuation" out of nothing.
DEFAULT_CVI = 5.0
#: Analytical variation (%). The same value for every item: its only job here is to keep a repeat
#: measurement from being identical, not to reproduce any particular instrument's precision.
CVA = 3.0
#: Exception: temperature's 3% is +/-1 degC -- a clinic note would then print 35.7 and 38.5 degC for
#: healthy people. A thermometer's actual precision is on the order of 0.1 degC.
CVA_OVERRIDE = {"temp": 0.3}
#: Quantities with a physical ceiling: saturation, ratio percentages, ankle-brachial index.
#: Lognormal noise can cross the ceiling; real instruments can't.
CEILING = {"spo2": 100.0, "fev1_fvc": 100.0, "fvc_pct": 140.0, "abi_l": 1.6, "abi_r": 1.6}

#: Center value for indicators with no reference interval (height and weight are handled separately).
FALLBACK_CENTER: dict[str, float] = {
    "height": 168.0, "weight": 65.0,
}


def center_of(key: str, sex: str) -> float:
    """Where a "normal" person falls in the reference interval.

    A two-sided interval takes its midpoint. A one-sided one (total cholesterol <5.18, HDL >1.04)
    takes the hand-curated population centre from spec instead — the first version used a fixed
    factor ("upper bound x 0.55"), which was wrong: the population median sits at 89% of the upper
    bound for total cholesterol but only 19% for CRP, so one factor can't fit both. Falling back to
    the fixed factor only happens when no centre is on file, and that case shows up in `--stats`'s
    abnormal rate.
    """
    typical = spec.typical_centers().get(key)
    if typical is not None:
        return float(typical)
    lo, hi = spec.reference_bounds(key, sex)
    if lo is not None and hi is not None:
        return (lo + hi) / 2
    if hi is not None:
        return hi * 0.55
    if lo is not None:
        return lo * 1.35
    return FALLBACK_CENTER.get(key, 1.0)


def person_baseline(rng: random.Random, key: str, sex: str) -> float:
    """Individual baseline offset (relative multiplier), from between-subject variation (CVG). Fixed
    for a person's whole life.

    Lognormal, like measurement noise, with z clamped to +/-2: a normal tail would produce someone
    with a "natural sodium of 180", and a value like that should come from disease or an event, not
    from individual variation.
    """
    item = spec.indicators()[key]
    cvg = item.get("cvg") or item.get("cvi") or DEFAULT_CVI
    sigma = cvg / 100
    z = max(-2.0, min(2.0, rng.gauss(0, 1)))
    return math.exp(sigma * z - sigma ** 2 / 2)


def event_factor(person, key: str, when: date) -> tuple[float, list[str]]:
    """The combined effect of events on this indicator on this day, and which events contributed.

    A single event's shape: zero before onset, ramping linearly to full magnitude during onset, then
    decaying by half-life after the event ends. Multiple events **add** rather than multiply: two
    things that each raise blood glucose shouldn't combine into a squared relationship.
    """
    factor = 1.0
    names: list[str] = []
    for event in person.events:
        effect = event.effects.get(key)
        if not effect:
            continue
        magnitude, onset_days, decay_days = effect
        elapsed = (when - event.start).days
        if elapsed < 0:
            continue
        ramp = 1.0 if onset_days <= 0 else min(1.0, elapsed / onset_days)
        after_end = elapsed - event.duration_days
        decay = 1.0
        if after_end > 0:
            if decay_days is None:
                decay = 1.0                      # a long-term habit: never decays
            else:
                decay = 0.5 ** (after_end / max(decay_days, 1))
        contribution = magnitude * ramp * decay
        if abs(contribution) > 1e-6:
            factor += contribution
            names.append(event.name)
    return max(factor, 0.05), names


def trend_factor(person, key: str, when: date) -> float:
    """Chronic-disease progression: compounded yearly. `trend[key]=0.06` means this indicator rises 6% a year."""
    rate = person.trend.get(key)
    if not rate:
        return 1.0
    # Measured from the year this person entered the cohort, not their birth year: chronic-disease
    # progression starts at some point in time, not at birth.
    years = max(0.0, (when - person.events[0].start).days / 365.25) if person.events else 0.0
    return (1.0 + rate) ** years


def weight_at(person, when: date) -> float:
    """Weight linearly interpolated between anchors. Weight feeds BMI, so it must exist before BMI does."""
    anchors = person.weight_anchors
    if not anchors:
        return FALLBACK_CENTER["weight"]
    if when <= anchors[0][0]:
        return anchors[0][1]
    if when >= anchors[-1][0]:
        return anchors[-1][1]
    for (d0, w0), (d1, w1) in zip(anchors, anchors[1:]):
        if d0 <= when <= d1:
            span = max((d1 - d0).days, 1)
            return w0 + (w1 - w0) * ((when - d0).days / span)
    return anchors[-1][1]


def expected(person, key: str, when: date) -> float:
    """A person's indicator on a day without measurement noise: the centre `measure` draws around.
    Home glucose logs and the CGM read it, so a meter, a sensor and a laboratory share one level."""
    base = center_of(key, person.sex) * person.baseline.get(key, 1.0)
    return base * trend_factor(person, key, when) * event_factor(person, key, when)[0]


def measure(rng: random.Random, person, key: str, when: date) -> float:
    """One measurement."""
    if key == "height":
        return person.height_cm
    if key == "weight":
        return weight_at(person, when)

    item = spec.indicators()[key]
    base = center_of(key, person.sex) * person.baseline.get(key, 1.0)
    base *= trend_factor(person, key, when)
    base *= event_factor(person, key, when)[0]

    cvi = item.get("cvi") or DEFAULT_CVI
    sigma = math.sqrt(cvi ** 2 + CVA_OVERRIDE.get(key, CVA) ** 2) / 100
    # **Lognormal**, not additive normal. Direct bilirubin's CVI is 36.8%; under an additive model
    # `base * (1 + 0.37*eps)` gives 0.04x baseline at eps=-2.6 — a bilirubin collapsing toward zero.
    # Biological quantities are non-negative and right-skewed, which is the shape multiplicative
    # noise has. Subtracting sigma^2/2 keeps the mean equal to base (otherwise the centre would be
    # systematically inflated).
    value = base * math.exp(rng.gauss(0, sigma) - sigma ** 2 / 2)
    return min(max(value, 0.0), CEILING.get(key, math.inf))


def derive(values: dict[str, float], person, when: date) -> dict[str, float]:
    """Compute derived indicators from identities. Inputs must already be **rounded to printed precision**.

    Order matters: globulin must come before the A/G ratio, differential percentages before absolutes.
    """
    out: dict[str, float] = {}

    def has(*keys: str) -> bool:
        return all(k in values for k in keys)

    if has("weight", "height"):
        out["bmi"] = values["weight"] / (values["height"] / 100) ** 2
    # Hematology's causal direction: the independent variables are **red cell count, mean cell
    # volume and mean cell hemoglobin concentration**; MCH = MCHC x MCV, and hemoglobin and
    # hematocrit are then computed by multiplying through.
    #
    # Why MCHC is primary and MCH is derived: MCHC has the tightest variation of the three (2.8%
    # between-subject, 1.7% within-subject) — tight precisely because hemoglobin and hematocrit move
    # together. Treating MCH as primary and MCHC as derived would turn MCHC's spread into the ratio
    # of two independently noisy quantities (MCH and MCV), necessarily wider than 1.7%, throwing an
    # occasional 403 g/L (above the 400 ceiling) every few hundred records. Deriving in this
    # direction instead makes the printed MCHC spread **equal to** its own CVI.
    #
    # The first version sampled hemoglobin and hematocrit as independent variables, which put MCHC
    # between 300 and 424 (real range 316-354) and the audit correctly flagged it as physiologically
    # impossible. The root cause wasn't too much jitter — it was **choosing the wrong independent
    # variables**: MCHC's CVI is only 1.7% precisely because hemoglobin and hematocrit move together.
    # Independently dividing two quantities with 3% CVI each necessarily produces a ratio wider than 1.7%.
    if has("mchc", "mcv"):
        out["mch"] = values["mchc"] * values["mcv"] / 1000
    if has("rbc") and "mch" in out:
        out["hgb"] = values["rbc"] * out["mch"]
    if has("rbc", "mcv"):
        out["hct"] = values["rbc"] * values["mcv"] / 10
    if has("tp", "alb"):
        out["glb"] = values["tp"] - values["alb"]
    # Direct bilirubin is a **fraction** of total bilirubin, not an independent measurement: it must
    # be total bilirubin times a ratio. Sampling it independently lets dbil exceed tbil, printing a
    # negative indirect bilirubin of -2.1. This is the second case of "chose the wrong independent
    # variable" (the first was the red-cell indices). The fraction is fixed per person via their
    # baseline and clamped to 0.08-0.45: a healthy person's direct bilirubin runs about 20-30% of the total.
    if has("tbil"):
        fraction = min(0.45, max(0.08, 0.25 * person.baseline.get("dbil", 1.0)))
        out["dbil"] = values["tbil"] * fraction
        out["ibil"] = values["tbil"] - out["dbil"]
    if has("chol", "hdl", "tg"):
        # Friedewald. The formula breaks down above a triglyceride level of 4.5 mmol/L, where a real
        # lab would switch to a direct assay — so this does too, rather than carrying the identity
        # through to a negative LDL.
        if values["tg"] <= 4.5:
            out["ldl"] = max(values["chol"] - values["hdl"] - values["tg"] / 2.2, 0.3)
    if has("crea"):
        out["egfr"] = ckd_epi_2021(values["crea"], person.age_at(when), person.sex)
    if has("chol", "hdl"):
        out["nonhdl"] = values["chol"] - values["hdl"]
    if has("psa"):
        # Free PSA is a fraction of total PSA, fixed per person (0.08-0.45); the fraction runs higher in benign cases
        out["fpsa"] = values["psa"] * _person_factor(person, "fpsa", 0.08, 0.45)
    if has("glu"):
        # 2-hour postprandial glucose = fasting x a per-person postprandial factor; larger in impaired glucose metabolism
        out["ogtt2h"] = values["glu"] * _person_factor(person, "ogtt2h", 1.05, 1.9)
    if has("fvc", "fev1_fvc"):
        out["fev1"] = values["fvc"] * values["fev1_fvc"] / 100
    if has("fvc_pct", "fev1_fvc"):
        out["fev1_pct"] = values["fvc_pct"] * values["fev1_fvc"] / 82
    if has("bapwv_l"):
        out["bapwv_r"] = values["bapwv_l"] * _person_factor(person, "bapwv_r", 0.94, 1.06)
    return out


def _person_factor(person, key: str, lo: float, hi: float) -> float:
    """A per-person factor fixed for life (free-PSA fraction, left/right asymmetry, that sort of thing)."""
    return random.Random(f"factor:{person.person_id}:{key}").uniform(lo, hi)


def derive_second_pass(values: dict[str, float], person=None) -> dict[str, float]:
    """Indicators that depend on the first pass's results (A/G ratio needs globulin, waist needs BMI)."""
    out: dict[str, float] = {}
    if person is not None and "bmi" in values:
        # See derived_from in resources/indicators.json: a linear approximation from BMI, with a
        # different intercept per sex.
        out["waist"] = 2.8 * values["bmi"] + (19 if person.sex == "male" else 13)
    if "alb" in values and values.get("glb"):
        out["ag_ratio"] = values["alb"] / values["glb"]
    if values.get("psa") and "fpsa" in values:
        out["fpsa_ratio"] = values["fpsa"] / values["psa"]
    if values.get("pg2") and "pg1" in values:
        out["pgr"] = values["pg1"] / values["pg2"]
    for pct_key, abs_key in (("neut_pct", "neut_abs"), ("lymph_pct", "lymph_abs"),
                             ("mono_pct", "mono_abs"), ("eos_pct", "eos_abs"),
                             ("baso_pct", "baso_abs")):
        if "wbc" in values and pct_key in values:
            out[abs_key] = values["wbc"] * values[pct_key] / 100
    return out


def ckd_epi_2021(creatinine_umol_l: float, age: int, sex: str) -> float:
    """CKD-EPI 2021 (race-free). Creatinine converted from umol/L to mg/dL."""
    scr = creatinine_umol_l / 88.4
    kappa = 0.7 if sex == "female" else 0.9
    alpha = -0.241 if sex == "female" else -0.302
    egfr = (142
            * min(scr / kappa, 1) ** alpha
            * max(scr / kappa, 1) ** -1.200
            * 0.9938 ** age
            * (1.012 if sex == "female" else 1.0))
    return egfr


def normalize_differential(values: dict[str, float]) -> None:
    """Force the white-cell differential percentages to sum to exactly 100.

    Without this, five independently sampled percentages sum to 97 or 104, while a real report
    always sums to 100 — the easiest inconsistency to overlook and the easiest for an audit to
    catch. After normalising, the residual is folded into neutrophils (the largest share, where it
    goes unnoticed).
    """
    keys = ["neut_pct", "lymph_pct", "mono_pct", "eos_pct", "baso_pct"]
    if not all(k in values for k in keys):
        return
    total = sum(values[k] for k in keys)
    if total <= 0:
        return
    for k in keys:
        values[k] = values[k] * 100 / total


def qualitative_value(rng: random.Random, person, key: str, when: date) -> str:
    """A qualitative item: comes back positive at the catalogue's population positive rate (3% by
    default), more readily under an active event; how a positive result is printed is also set by
    the catalogue (urinalysis uses +/++/a weak-positive grade, antibody panels print a plain
    positive/negative, cervical cytology uses ASC-US/LSIL).

    Sticky: results like hepatitis B surface antibody or H. pylori antibody stay fixed for a person
    over many years, so whether they're positive is drawn once per person, not per encounter —
    otherwise the same person's hepatitis panel would flip back and forth year to year."""
    item = spec.indicators()[key]
    sticky = random.Random(f"qual:{person.person_id}:{key}")
    base_rate = item.get("positive_rate")
    if base_rate is None:
        base_rate = 0.03
        roll = rng.random()                      # state-dependent items like urinalysis: draw per encounter
    else:
        roll = sticky.random()                   # fixed items like antibody panels: draw per person
    factor, _ = event_factor(person, key, when)
    positive_chance = base_rate + max(0.0, factor - 1.0) * 0.5
    if roll < positive_chance:
        values = item.get("positive_values") or [["+", 6], ["++", 2], ["弱阳性", 1]]
        return sticky.choices([v for v, _ in values], weights=[w for _, w in values])[0]
    ref = item.get("reference")
    return str(ref[1]) if ref and ref[0] == "qualitative" else "阴性"


def categorical_value(person, key: str) -> str:
    """A categorical item (blood type): one fixed value for a person's whole life."""
    item = spec.indicators()[key]
    rng = random.Random(f"cat:{person.person_id}:{key}")
    values = item["categories"]
    return rng.choices([v for v, _ in values], weights=[w for _, w in values])[0]
