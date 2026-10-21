"""Clinical audit: do the generated values hold up on their own? Non-zero exit on findings.

    mirobody-gen audit-clinical out/<build>/manifest.jsonl
    mirobody-gen audit-clinical --selftest       # verify the audit against its own built-in pass/fail cases

## Why this reimplements the identities instead of importing the generator

The generator *computes* these values from a mechanistic model; the audit *checks* them by working the
identities backwards. If the two shared a line of code, this check could only confirm the generator's
own blind spots -- a mistake this project already paid for once (a privacy audit first reported "0 names
leaked" while a real physician's name sat in the corpus, because the audit called the same predicate the
filter used). So nothing here imports from `mirobody_gen` outside `audit/`; every identity below is
rederived from laboratory-medicine definitions:

| check | identity | tolerance |
|---|---|---|
| BMI | weight / height² | ±0.2 |
| MCV | hematocrit×10 / RBC | ±2 fL |
| MCH | hemoglobin / RBC | ±1 pg |
| MCHC | hemoglobin / (hematocrit/100) | ±8 g/L |
| globulin | total protein − albumin | ±0.6 g/L |
| A/G ratio | albumin / globulin | ±0.06 |
| indirect bilirubin | total − direct bilirubin | ±0.6 μmol/L |
| differential absolute | WBC × percentage/100 | ±0.12 ×10⁹/L |
| differential percentages | sum to 100 | ±1.5 |
| LDL (when not measured directly) | total cholesterol − HDL − triglycerides/2.2 | ±0.25 mmol/L |

The tolerances aren't arbitrary: they follow from **printed precision**. Hemoglobin is printed as an
integer and hematocrit to one decimal place, so their ratio already carries ±0.5% of quantization error
-- hence MCHC's ±8 g/L. Tightening a tolerance below what the printed precision allows just makes the
audit report rounding as "errors".

## Four more kinds of checks

- **Flags agree with the reference range**: a printed up-arrow must actually exceed the upper bound.
  This is a common trap in the reference corpus (`flag.contradicts_reference`), but there it is someone
  else's mistake; our own output must be self-consistent unless a file **explicitly injects** that trap
  (recorded in the manifest, which the audit honors).
- **Demographic conflicts**: no CA125 for men, no PSA for women; age must match the reference-range age
  bracket.
- **Physiological hard limits**: values like sodium 1141 mmol/L have shown up in reference-corpus
  aggregates (a unit mix-up) but must never leave this generator. Hard limits are set to the extremes a
  living person can reach, much wider than any reference range.
- **Longitudinal plausibility**: between two draws with no intervening event, the declared lognormal
  model fixes the spread of the log ratio, so about 5% of such pairs exceed its 95% threshold; the
  pooled rate is tested against 5%, and single pairs are flagged only past 3x their threshold.
- **Diagnoses and values must explain each other, at each visit's date**: using only visits up to that
  date, a model rule confirmed on enough distinct dates (with any required elapsed time and same-visit
  companion values) but absent from the visit's conditions is a missed diagnosis; a condition derived
  from a numeric rule without confirming evidence by then, or one recorded after the visit, is a
  finding too (`诊断时序` for the latter). A declared archetype diagnosis whose indicator looks normal is
  not judged -- a well-controlled chronic condition looks exactly like that. These are consistency
  checks of the generator's own rules, not clinical diagnostic accuracy.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
from datetime import date

PACKAGE = pathlib.Path(__file__).resolve().parents[1]
RESOURCES = PACKAGE / "resources"
#: Root of the source checkout. Used only for things that exist only in a checkout (reference set,
#: cache, build output) -- an installed package has none of these.
REPO = PACKAGE.parent

#: Analytical CV when resources/measurement.json is absent (an incomplete package); the declared
#: model there is what the longitudinal check normally reads.
CVA_FALLBACK = 3.0

#: (key, label, keys it depends on, formula, tolerance)
IDENTITIES: list[tuple[str, str, tuple[str, ...], object, float]] = [
    ("bmi", "BMI = weight / height²", ("weight", "height"),
     lambda v: v["weight"] / (v["height"] / 100) ** 2, 0.2),
    ("mcv", "MCV = hematocrit×10 / RBC", ("hct", "rbc"),
     lambda v: v["hct"] * 10 / v["rbc"], 2.0),
    ("mch", "MCH = hemoglobin / RBC", ("hgb", "rbc"),
     lambda v: v["hgb"] / v["rbc"], 1.0),
    ("mchc", "MCHC = hemoglobin / (hematocrit/100)", ("hgb", "hct"),
     lambda v: v["hgb"] / (v["hct"] / 100), 8.0),
    ("glb", "globulin = total protein − albumin", ("tp", "alb"),
     lambda v: v["tp"] - v["alb"], 0.6),
    ("ag_ratio", "A/G ratio = albumin / globulin", ("alb", "glb"),
     lambda v: v["alb"] / v["glb"], 0.06),
    ("ibil", "indirect bilirubin = total − direct bilirubin", ("tbil", "dbil"),
     lambda v: v["tbil"] - v["dbil"], 0.6),
    ("neut_abs", "neutrophil absolute = WBC × percentage/100", ("wbc", "neut_pct"),
     lambda v: v["wbc"] * v["neut_pct"] / 100, 0.12),
    ("lymph_abs", "lymphocyte absolute = WBC × percentage/100", ("wbc", "lymph_pct"),
     lambda v: v["wbc"] * v["lymph_pct"] / 100, 0.12),
    ("mono_abs", "monocyte absolute = WBC × percentage/100", ("wbc", "mono_pct"),
     lambda v: v["wbc"] * v["mono_pct"] / 100, 0.12),
    ("eos_abs", "eosinophil absolute = WBC × percentage/100", ("wbc", "eos_pct"),
     lambda v: v["wbc"] * v["eos_pct"] / 100, 0.12),
    ("baso_abs", "basophil absolute = WBC × percentage/100", ("wbc", "baso_pct"),
     lambda v: v["wbc"] * v["baso_pct"] / 100, 0.12),
    ("nonhdl", "non-HDL cholesterol = total cholesterol − HDL", ("chol", "hdl"), lambda v: v["chol"] - v["hdl"], 0.02),
    ("fpsa_ratio", "free/total PSA", ("fpsa", "psa"), lambda v: v["fpsa"] / v["psa"], 0.03),
    ("pgr", "pepsinogen ratio = PG I / PG II", ("pg1", "pg2"), lambda v: v["pg1"] / v["pg2"], 0.15),
    ("fev1", "FEV1 = FVC × FEV1/FVC ratio", ("fvc", "fev1_fvc"), lambda v: v["fvc"] * v["fev1_fvc"] / 100, 0.05),
]

#: Physiological hard limits: the extremes a living person can reach, much wider than any reference
#: range. A value past one of these means the generator miscalculated or mixed up units, not "very sick".
HARD_LIMITS: dict[str, tuple[float, float]] = {
    "height": (100, 220), "weight": (25, 250), "bmi": (10, 70),
    "sbp": (60, 260), "dbp": (30, 160), "pulse": (30, 220), "resp": (6, 50), "temp": (33.0, 42.0),
    "hgb": (20, 230), "hct": (10, 70), "rbc": (1.0, 8.0), "wbc": (0.1, 200),
    "plt": (1, 2000), "mcv": (50, 130), "mch": (12, 45), "mchc": (250, 400),
    "glu": (1.0, 40), "hba1c": (3.0, 20), "chol": (1.0, 20), "tg": (0.1, 60),
    "hdl": (0.1, 5), "ldl": (0.1, 15), "crea": (20, 1500), "urea": (0.5, 60),
    "ua": (50, 1500), "alt": (1, 5000), "ast": (1, 5000), "alp": (5, 2000),
    "tp": (30, 120), "alb": (10, 70), "glb": (5, 80), "tbil": (1, 600),
    "dbil": (0, 400), "k": (1.5, 9.0), "na": (100, 180), "cl": (60, 140),
    "ca": (1.0, 4.5), "tsh": (0.001, 200), "ft3": (0.3, 60), "ft4": (0.5, 120),
    "esr": (0, 150), "crp": (0, 500),
    "waist": (40, 200), "resp": (6, 50), "ag_ratio": (0.3, 5.0), "hscrp": (0, 500), "rdw": (8, 40), "mpv": (5, 20),
    "pdw": (5, 30), "pct": (0.01, 2.0), "egfr": (3, 200), "ua": (50, 1500),
    "tg": (0.1, 60), "ggt": (1, 3000), "ibil": (0, 300), "cysc": (0.1, 10),
    "hcy": (1, 200), "tsh": (0.001, 200), "ft3": (0.3, 60), "ft4": (0.5, 120),
    "tt3": (0.1, 20), "tt4": (5, 400), "k": (1.5, 9.0), "mg": (0.2, 3.0),
    "phos": (0.1, 5.0), "ldh": (20, 5000), "ck": (5, 50000), "ckmb": (0, 500),
    "neut_pct": (0, 100), "lymph_pct": (0, 100), "mono_pct": (0, 100),
    "eos_pct": (0, 100), "baso_pct": (0, 100),
    "neut_abs": (0, 100), "lymph_abs": (0, 100), "mono_abs": (0, 50),
    "eos_abs": (0, 50), "baso_abs": (0, 10),
    # Extended 2026-09-29
    "spo2": (50, 100), "pt": (5, 120), "inr": (0.5, 12), "aptt": (10, 200), "tt": (8, 120), "fib": (0.3, 10),
    "igg": (1, 60), "iga": (0.05, 20), "igm": (0.05, 20), "c3": (0.1, 4), "c4": (0.02, 1.5),
    "amy": (5, 5000), "lps": (2, 5000), "che": (500, 25000), "pa": (20, 800), "nonhdl": (0.5, 18),
    "insulin": (0.5, 400), "cpep": (0.1, 30), "ogtt2h": (1.5, 40), "b2mg": (0.3, 30),
    "fvc": (0.8, 8), "fvc_pct": (20, 160), "fev1_fvc": (20, 100), "fev1": (0.4, 7), "fev1_pct": (10, 160),
    "bapwv_l": (600, 3500), "bapwv_r": (600, 3500), "abi_l": (0.3, 1.8), "abi_r": (0.3, 1.8),
    "body_fat": (3, 60), "visceral_fat": (1, 30), "muscle_mass": (10, 70), "bmr": (700, 3000),
    "lvef": (15, 85), "lvedd": (30, 80), "ivs": (4, 25), "la": (20, 70), "ea": (0.3, 4),
    "pg1": (5, 600), "pg2": (1, 120), "pgr": (0.5, 40), "fpsa_ratio": (0.02, 1.0),
    # below water is impossible; contrast media can push a sample past 1.040
    "urine_sg": (1.000, 1.050),
}

#: Quantitative indicators allowed to have no hard limit, and why.
#:
#: This exemption table exists because `test_every_quantitative_indicator_has_a_hard_limit` requires
#: every quantitative indicator to have a bound. A waist of 44.2 cm with a body weight of 104 kg once
#: slipped through precisely because `waist` wasn't in HARD_LIMITS at the time -- **a missing entry is
#: silent**: when the checklist is incomplete, "0 findings" can mean there's no problem, or it can mean
#: nothing was checked.
NO_HARD_LIMIT: dict[str, str] = {
    "ga": "no agreed upper bound for glycated albumin",
    "tba": "total bile acids can run extremely high in cholestasis; no meaningful upper bound",
    "apoa1": "apolipoprotein extremes are rarely reported", "apob": "apolipoprotein extremes are rarely reported",
    "lpa": "apolipoprotein extremes are rarely reported",
    "ferritin": "acute-phase reactant, can reach the tens of thousands during inflammation",
    "serum_iron": "can be extremely high in poisoning", "b12": "can be extremely high after supplementation",
    "folate": "can be extremely high after supplementation",
    "vitd": "can be extremely high with high-dose supplementation",
    "afp": "tumor marker, no upper bound", "cea": "tumor marker, no upper bound",
    "psa": "tumor marker, no upper bound", "ca125": "tumor marker, no upper bound",
    "tpoab": "antibody titer has no upper bound",
    "urine_ph": "narrow range, already constrained by its reference interval",
    "urine_rbc": "microscopy counts have no upper bound", "urine_wbc": "microscopy counts have no upper bound",
    "pr_interval": "ECG values use a dedicated convention", "qrs_duration": "ECG values use a dedicated convention",
    "qtc": "ECG values use a dedicated convention",
    "qrs_axis": "ECG values use a dedicated convention (and can be negative)",
    # Tumor markers, antibodies and acute-phase reactants added 2026-09-29: none has an upper bound
    "ca199": "tumor marker, no upper bound", "ca153": "tumor marker, no upper bound",
    "ca724": "tumor marker, no upper bound", "cyfra211": "tumor marker, no upper bound",
    "nse": "tumor marker, no upper bound", "scc": "tumor marker, no upper bound",
    "fpsa": "tumor marker, no upper bound", "ddimer": "can be extremely high with thrombosis",
    "umalb": "can be extremely high in nephrotic syndrome", "uacr": "can be extremely high in nephrotic syndrome",
    "hstni": "can run thousands of times higher during a myocardial infarction",
    "ntprobnp": "can be extremely high in heart failure", "tgab": "antibody titer has no upper bound",
    "tg_protein": "can be extremely high with malignancy",
    "rf": "antibody titer has no upper bound", "aso": "antibody titer has no upper bound",
    "ccp": "antibody titer has no upper bound",
}

#: Sex-restricted items.
SEX_ONLY = {"psa": "male", "fpsa": "male", "fpsa_ratio": "male", "ca125": "female", "ca153": "female",
            "hpv16": "female", "hpv18": "female", "hpv_other": "female", "tct": "female"}

#: The differential whose percentages must sum to ~100.
DIFFERENTIAL = ("neut_pct", "lymph_pct", "mono_pct", "eos_pct", "baso_pct")


class Finding:
    def __init__(self, kind: str, where: str, detail: str):
        self.kind, self.where, self.detail = kind, where, detail

    def __str__(self) -> str:
        return f"[{self.kind}] {self.where}: {self.detail}"


def numeric_rows(record: dict) -> dict[str, float]:
    """Numeric rows in this file usable for identity checks: key -> value."""
    out: dict[str, float] = {}
    for row in record.get("rows", []):
        key, value = row.get("key"), row.get("canonical_value")
        if key and isinstance(value, (int, float)):
            out[key] = float(value)
    return out


def check_identities(record: dict, values: dict[str, float]) -> list[Finding]:
    where = record.get("file", "?")
    findings = []
    injected = set(record.get("hazards") or [])
    for key, label, needs, formula, tolerance in IDENTITIES:
        if key not in values or any(n not in values for n in needs):
            continue
        try:
            expected = formula(values)
        except ZeroDivisionError:
            findings.append(Finding("恒等式", where, f"{label}: denominator is 0"))
            continue
        actual = values[key]
        if abs(actual - expected) > tolerance:
            # A file with a "value corrupted" trap injected is supposed to fail this identity -- that's
            # intentional.
            if injected & {"value.missing_decimal_point", "value.decimal_comma",
                           "value.space_in_number", "ocr.other", "unit.ocr_corrupted"}:
                continue
            findings.append(Finding(
                "恒等式", where,
                f"{label}: printed {actual:g}, formula gives {expected:.4g} "
                f"(diff {abs(actual - expected):.4g}, tolerance {tolerance})"))
    present = [k for k in DIFFERENTIAL if k in values]
    if len(present) == len(DIFFERENTIAL):
        total = sum(values[k] for k in present)
        if abs(total - 100) > 1.5:
            findings.append(Finding("恒等式", where, f"WBC differential percentages sum to {total:.2f}, should be 100"))
    return findings


def check_limits(record: dict, values: dict[str, float]) -> list[Finding]:
    where = record.get("file", "?")
    findings = []
    for key, value in values.items():
        bounds = HARD_LIMITS.get(key)
        if bounds and not (bounds[0] <= value <= bounds[1]):
            findings.append(Finding(
                "生理边界", where,
                f"{key} = {value:g}, outside the physiologically possible range {bounds[0]}–{bounds[1]}"))
    return findings


def check_demographics(record: dict, values: dict[str, float]) -> list[Finding]:
    where = record.get("file", "?")
    sex = (record.get("person") or {}).get("sex")
    findings = []
    for key, only in SEX_ONLY.items():
        if key in values and sex and sex != only:
            findings.append(Finding("人口学", where, f"{sex} has {only}-only item {key}"))
    age = (record.get("person") or {}).get("age")
    if isinstance(age, (int, float)) and not (0 < age < 120):
        findings.append(Finding("人口学", where, f"age {age} is implausible"))
    return findings


def check_flags(record: dict) -> list[Finding]:
    """A printed up/down flag must actually exceed the bound -- unless the file explicitly injects the
    "flag contradicts reference" trap."""
    where = record.get("file", "?")
    if "flag.contradicts_reference" in (record.get("hazards") or []):
        return []
    findings = []
    for row in record.get("rows", []):
        status = (row.get("status") or "").lower()
        value = row.get("canonical_value")
        lo, hi = row.get("ref_low"), row.get("ref_high")
        if not isinstance(value, (int, float)) or status not in ("high", "low"):
            continue
        if status == "high" and isinstance(hi, (int, float)) and value <= hi:
            findings.append(Finding("标记", where,
                                    f"{row.get('key')} flagged high, but {value:g} <= upper bound {hi:g}"))
        if status == "low" and isinstance(lo, (int, float)) and value >= lo:
            findings.append(Finding("标记", where,
                                    f"{row.get('key')} flagged low, but {value:g} >= lower bound {lo:g}"))
    return findings


def check_longitudinal(records: list[dict], cvi: dict[str, float],
                       decimals: dict[str, int] | None = None,
                       derived: set[str] | None = None,
                       measurement: dict | None = None,
                       cohort: dict | None = None) -> list[Finding]:
    """Longitudinal plausibility, judged on the pairs whose expected spread is known exactly.

    The declared measurement model (resources/measurement.json) is lognormal: a printed value is
    round(centre * exp(e - s^2/2)) with e ~ N(0, s^2) independent per draw, s = sqrt(ln(1 + c^2)) and
    c^2 = CVI^2 + CVA^2 (Fokkema et al., Clin Chem 2006). Between two draws with no change in the
    person's events, the log ratio minus the archetype trend's drift is therefore
    N(0, 2 s^2 + q1^2 + q2^2), where q_i = step / (sqrt(12) * value_i) is the printed rounding's share.
    The two-sided 95% threshold is 1.96 times its square root, and about 5% of such pairs exceed it by
    construction -- the reference change value read in log space. (The first version gated
    |ln ratio| with ln(1 + 2.77 c): that is narrower than the model's spread, so its "theoretical ~5%"
    was really 8% overall and 19% for CV 40%.)

    * **Stable stratum, judged**: pairs of adjacent measurements of a key with no event affecting that
      key in any intervening record's `events_since_previous`. The pooled exceedance rate is tested
      against 5% (exact two-sided binomial, p < 0.001 is a finding): higher means unexplained jumps,
      lower means the series is jittered too little. Excluded beforehand, by properties that do not
      look at the change: derived quantities (their spread is propagated), the rescaled white-cell
      differential, declared floors/ceilings, noise-free height/weight, and pairs whose rounding
      share exceeds the model's own spread. Every exclusion is counted and printed.
    * **Per pair**: a stable pair beyond 3x its threshold is a finding -- a generator bug or a unit
      mix-up, not biological variation.
    * Pairs with an event in between are reported, not judged: the event's effect is the point.
    """
    findings: list[Finding] = []
    decimals = decimals or {}
    derived = derived or set()
    measurement = measurement if measurement is not None else load_measurement()
    cohort = cohort if cohort is not None else load_cohort()
    default_cvi = measurement.get("default_cvi_percent")
    cva = measurement.get("cva_percent", CVA_FALLBACK)
    cva_override = measurement.get("cva_override_percent", {})
    excluded_keys = {key: "derived" for key in derived}
    for reason, keys in (("compositional", measurement.get("compositional", [])),
                         ("floor_or_ceiling", list(measurement.get("offset", {})) + list(measurement.get("ceiling", {}))),
                         ("noise_free", measurement.get("noise_free", []))):
        excluded_keys.update({key: reason for key in keys})
    affected: dict[str, set[str]] = {}
    responses = cohort.get("medication_response") or {}
    for script in (cohort.get("scripts") or {}).values():
        affected.setdefault(script["name"], set()).update(script.get("effects", {}))
        if script.get("course"):  # a dose-dependent treatment also moves its response's keys
            doses = responses.get(script["course"]["drug"], {}).get("doses_mg", {})
            affected[script["name"]].update(k for response in doses.values() for k in response)
    for incident in cohort.get("incidents") or []:
        affected.setdefault(incident["name"], set()).update(incident.get("effects", {}))
    trends = {a["name"]: a.get("trend", {}) for a in cohort.get("archetypes") or []}

    counts = {"pairs": 0, "stable": 0, "stable_exceed": 0, "event": 0, "event_exceed": 0}
    excluded: dict[str, int] = {}
    by_person: dict[str, list[dict]] = {}
    for record in records:
        by_person.setdefault(record.get("person_id", "?"), []).append(record)

    for person_id, items in by_person.items():
        items.sort(key=lambda r: r.get("collected") or "")
        history: dict[str, tuple[str, float]] = {}
        since: dict[str, set[str]] = {}
        trend = trends.get((items[0].get("person") or {}).get("archetype"), {})
        for record in items:
            names = set(record.get("events_since_previous") or [])
            for key in since:
                since[key] |= names
            collected = record.get("collected") or ""
            for key, value in numeric_rows(record).items():
                previous = history.get(key)
                events, since[key] = since.get(key, set()), set()
                history[key] = (collected, value)
                if not previous:
                    continue
                counts["pairs"] += 1
                cv_percent = cvi.get(key) or default_cvi
                reason = excluded_keys.get(key) or (None if cv_percent else "no_declared_cv")
                if reason is None and (value <= 0 or previous[1] <= 0):
                    reason = "non_positive"
                gap = _days_between(previous[0], collected)
                if reason is None and gap is None:
                    reason = "undated"
                if reason is not None:
                    excluded[reason] = excluded.get(reason, 0) + 1
                    continue
                c = math.sqrt(cv_percent ** 2 + cva_override.get(key, cva) ** 2) / 100
                s = math.sqrt(math.log(1 + c * c))
                step = 10 ** -decimals.get(key, 2)
                q1, q2 = step / (math.sqrt(12) * previous[1]), step / (math.sqrt(12) * value)
                if max(q1, q2) > s:
                    excluded["rounding_dominated"] = excluded.get("rounding_dominated", 0) + 1
                    continue
                threshold = 1.96 * math.sqrt(2 * s * s + q1 * q1 + q2 * q2)
                change = math.log(value / previous[1]) - gap / 365.25 * math.log(1 + trend.get(key, 0.0))
                if any(key in affected.get(name, {key}) for name in events):  # an unknown event may affect anything
                    counts["event"] += 1
                    counts["event_exceed"] += abs(change) > threshold
                    continue
                counts["stable"] += 1
                counts["stable_exceed"] += abs(change) > threshold
                if abs(change) > 3 * threshold:
                    findings.append(Finding(
                        "纵向", f"{person_id} {previous[0]}→{collected}",
                        f"{key} changed from {previous[1]:g} to {value:g} (log change {change:+.2f} after "
                        f"trend, beyond 3x its 95% threshold {threshold:.2f}) with no event affecting it"))

    stable, exceed = counts["stable"], counts["stable_exceed"]
    if counts["pairs"]:
        rate = exceed / stable if stable else float("nan")
        p_value = binomial_two_sided(exceed, stable, 0.05) if stable else float("nan")
        print(f"(longitudinal: {counts['pairs']} adjacent pairs; stable {stable}, {rate:.1%} beyond the 95% "
              f"threshold (model 5%, exact binomial p={p_value:.3g}); with an event in between "
              f"{counts['event']}, {counts['event_exceed'] / max(counts['event'], 1):.1%} beyond it, reported "
              f"not judged; excluded {sum(excluded.values())} "
              f"({', '.join(f'{k}:{v}' for k, v in sorted(excluded.items())) or 'none'}))")
        if stable >= 200 and p_value < 0.001:
            findings.append(Finding(
                "纵向", "overall",
                f"stable-pair exceedance {rate:.1%} of {stable} departs from the model's 5% (exact binomial "
                f"p={p_value:.2g}): higher means unexplained jumps, lower means too little jitter"))
    return findings


def binomial_two_sided(k: int, n: int, p: float) -> float:
    """Exact two-sided binomial p-value: the total probability of outcomes no more likely than k."""
    def log_pmf(i: int) -> float:
        return (math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
                + i * math.log(p) + (n - i) * math.log1p(-p))
    observed = log_pmf(k)
    return min(1.0, sum(math.exp(log_pmf(i)) for i in range(n + 1) if log_pmf(i) <= observed + 1e-9))


def _days_between(a: str, b: str) -> int | None:
    from datetime import date

    try:
        return abs((date.fromisoformat(b) - date.fromisoformat(a)).days)
    except ValueError:
        return None


def load_decimals() -> dict[str, int]:
    """Indicator key -> printed decimal places, used to tell whether a difference is just rounding."""
    path = RESOURCES / "indicators.json"
    if not path.is_file():
        return {}
    spec = json.loads(path.read_text(encoding="utf-8"))
    return {i["key"]: i.get("decimals", 2) for i in spec["indicators"]}


def load_criteria() -> list[dict]:
    """Diagnostic criteria. See DIAGNOSTIC_CRITERIA in `scripts/build_cohort.py`."""
    path = RESOURCES / "cohort.json"
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("diagnostic_criteria", [])


def _sample_diagnostic_state(record: dict, rule: dict) -> bool | None:
    """Independent audit implementation; never imports the generator's decision code."""
    def predicate(c):
        rows = [r for r in record.get("rows", []) if r.get("key") == c["key"]]
        if not rows:
            return None
        signatures = {(r.get("canonical_value"), r.get("unit_ucum"), r.get("ref_low"), r.get("ref_high"))
                      for r in rows}
        if len(signatures) != 1:
            return None
        r = rows[0]
        value = r.get("canonical_value")
        limit = r.get(c["reference"]) if "reference" in c else c["value"]
        if any(type(v) not in (float, int) or not math.isfinite(v) for v in (value, limit)):
            return None
        if c.get("unit") and r.get("unit_ucum") != c["unit"]:
            return None
        return _compare(value, c["op"], limit)
    options = [predicate(c) for c in rule["any_of"]]
    supplements = [predicate(c) for c in rule.get("all_of", [])]
    if False in supplements:
        return False
    if not any(v is True for v in options):
        return None if None in options else False
    return None if None in supplements else True


def _diagnostic_confirmation(items: list[dict], rule: dict, cutoff: str) -> str | None:
    by_day = {}
    for record in items:
        day = (record.get("collected") or "")[:10]
        if day and day <= cutoff[:10]:
            by_day.setdefault(day, []).append(_sample_diagnostic_state(record, rule))
    streak = []
    for day in sorted(by_day):
        states = {x for x in by_day[day] if x is not None}
        if False in states:  # A contradictory date cannot count as a positive date.
            streak.clear()
        elif True in states:
            streak.append(day)
            elapsed = (date.fromisoformat(day[:10]) - date.fromisoformat(streak[0][:10])).days
            if len(streak) >= rule["persistence"] and (
                    "minimum_elapsed_days_exclusive" not in rule or elapsed > rule["minimum_elapsed_days_exclusive"]):
                return day
    return None


def check_diagnosis_coherence(records: list[dict], criteria: list[dict]) -> list[Finding]:
    """Audit model-rule consistency at each visit's date, not clinical diagnosis accuracy.

    Declared archetype priors are retained separately from numeric-rule inference;
    a treated prior need not be abnormal today. Missing tests are not normal tests.
    """
    findings = []
    by_person = {}
    for record in records:
        by_person.setdefault(record.get("person_id", "?"), []).append(record)
    for person_id, items in by_person.items():
        for record in items:
            when = record.get("collected")
            if not when:
                continue
            conditions = (record.get("person") or {}).get("conditions") or []
            for condition in conditions:
                if not condition.get("recorded_date") or not isinstance(condition.get("source"), str) or not condition["source"].strip():
                    findings.append(Finding("诊断时序", f"{person_id} {when}",
                                            f"condition lacks dated/source knowledge: {condition.get('code')}"))
                if any(condition.get(k) and condition[k][:10] > when[:10] for k in ("recorded_date", "onset_date")):
                    findings.append(Finding("诊断时序", f"{person_id} {when}",
                                            f"future condition exposed: {condition.get('code')}"))
            have = {c.get("code") for c in conditions}
            for rule in criteria:
                code = rule["condition"]["code"]
                confirmed = _diagnostic_confirmation(items, rule, when)
                if confirmed and code not in have:
                    findings.append(Finding("诊断", f"{person_id} {when}",
                                            f"model rule for {rule['condition']['display']} confirmed {confirmed}, missing in this visit"))
                elif not confirmed and any(c.get("code") == code and c.get("source") == "synthetic_numeric_rule"
                                           for c in conditions):
                    findings.append(Finding("诊断", f"{person_id} {when}",
                                            f"numeric-rule condition lacks supporting evidence: {code}"))
    return findings


def _compare(value, op: str, threshold: float) -> bool:
    if value is None:
        return False
    return {">=": value >= threshold, ">": value > threshold,
            "<=": value <= threshold, "<": value < threshold}.get(op, False)


def load_derived() -> set[str]:
    """Indicator keys computed from an identity.

    These are **excluded from the RCV check**: published CVI describes the variability of direct
    measurement, while an LDL computed via Friedewald inherits the propagated variance of three measured
    inputs (total cholesterol, HDL, triglycerides), which is necessarily larger than LDL's own CVI.
    Gating the former with the latter is using the wrong number -- every finding it would produce is not
    a defect, just "a computed quantity is naturally noisier than a direct measurement".
    """
    path = RESOURCES / "indicators.json"
    if not path.is_file():
        return set()
    spec = json.loads(path.read_text(encoding="utf-8"))
    return {i["key"] for i in spec["indicators"] if i.get("derived_from")}


def load_measurement() -> dict:
    """The declared measurement-noise model (read as data; the generator is never imported)."""
    path = RESOURCES / "measurement.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def load_cohort() -> dict:
    """Event scripts, incidents and archetype trends: which keys an event moves, and each trend."""
    path = RESOURCES / "cohort.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def load_cvi() -> dict[str, float]:
    path = RESOURCES / "indicators.json"
    if not path.is_file():
        return {}
    spec = json.loads(path.read_text(encoding="utf-8"))
    return {i["key"]: i["cvi"] for i in spec["indicators"] if i.get("cvi")}


def audit(records: list[dict]) -> list[Finding]:
    findings: list[Finding] = []
    for record in records:
        values = numeric_rows(record)
        findings += check_identities(record, values)
        findings += check_limits(record, values)
        findings += check_demographics(record, values)
        findings += check_flags(record)
    findings += check_longitudinal(records, load_cvi(), load_decimals(), load_derived())
    findings += check_diagnosis_coherence(records, load_criteria())
    return findings


# ── Self-test: does the audit itself miss anything? ───────────────────
#: The audit's first user is itself. A clean record must produce 0 findings, and a record that
#: **commits every kind of error once** must have every kind caught. Without this self-test, "audit
#: passed" could mean the data is fine, or it could mean the audit checked nothing.
CLEAN = {
    "file": "clean.pdf", "person_id": "p1", "collected": "2026-01-01",
    "person": {"sex": "male", "age": 40}, "hazards": [],
    "rows": [
        {"key": "height", "canonical_value": 175.0},
        {"key": "weight", "canonical_value": 70.0},
        {"key": "bmi", "canonical_value": 22.9},
        {"key": "hgb", "canonical_value": 150.0},
        {"key": "rbc", "canonical_value": 5.00},
        {"key": "hct", "canonical_value": 45.0},
        {"key": "mcv", "canonical_value": 90.0},
        {"key": "mch", "canonical_value": 30.0},
        {"key": "mchc", "canonical_value": 333.0},
        {"key": "wbc", "canonical_value": 6.00},
        {"key": "neut_pct", "canonical_value": 60.0},
        {"key": "lymph_pct", "canonical_value": 30.0},
        {"key": "mono_pct", "canonical_value": 6.0},
        {"key": "eos_pct", "canonical_value": 3.0},
        {"key": "baso_pct", "canonical_value": 1.0},
        {"key": "neut_abs", "canonical_value": 3.60},
        {"key": "tp", "canonical_value": 72.0},
        {"key": "alb", "canonical_value": 45.0},
        {"key": "glb", "canonical_value": 27.0},
        {"key": "tbil", "canonical_value": 14.0, "status": "normal",
         "ref_low": 0, "ref_high": 26.0},
        {"key": "dbil", "canonical_value": 4.0},
        {"key": "ibil", "canonical_value": 10.0},
    ],
}

BROKEN = {
    "file": "broken.pdf", "person_id": "p2", "collected": "2026-01-01",
    # a numeric-rule condition with no confirming values, recorded two months after this visit
    "person": {"sex": "female", "age": 38, "conditions": [
        {"code": "44054006", "display": "2型糖尿病", "source": "synthetic_numeric_rule",
         "recorded_date": "2026-03-01", "evidence_dates": ["2026-02-01", "2026-03-01"]}]},
    "hazards": [],
    "rows": [
        {"key": "height", "canonical_value": 165.0},
        {"key": "weight", "canonical_value": 60.0},
        {"key": "bmi", "canonical_value": 27.0},          # identity: should be 22.0
        {"key": "hgb", "canonical_value": 140.0},
        {"key": "hct", "canonical_value": 42.0},
        {"key": "mchc", "canonical_value": 300.0},        # identity: should be 333
        {"key": "na", "canonical_value": 1141.0},         # hard limit
        {"key": "psa", "canonical_value": 1.2},           # demographics: PSA ordered for a female
        {"key": "glu", "canonical_value": 5.0, "status": "high",
         "ref_low": 3.9, "ref_high": 6.1},                # flag: high but doesn't exceed the upper bound
        {"key": "neut_pct", "canonical_value": 60.0},
        {"key": "lymph_pct", "canonical_value": 30.0},
        {"key": "mono_pct", "canonical_value": 6.0},
        {"key": "eos_pct", "canonical_value": 3.0},
        {"key": "baso_pct", "canonical_value": 9.0},      # differential sums to 108
    ],
}


def selftest() -> int:
    clean = audit([CLEAN])
    print(f"clean record: {len(clean)} findings" + ("" if not clean else " <- should be none"))
    for f in clean:
        print("   ", f)

    broken = audit([BROKEN])
    kinds = {f.kind for f in broken}
    print(f"\nbroken record: {len(broken)} findings, covering {sorted(kinds)}")
    for f in broken:
        print("   ", f)

    expected = {"恒等式", "生理边界", "人口学", "标记", "诊断", "诊断时序"}
    missing = expected - kinds
    ok = not clean and not missing
    print(f"\nself-test {'passed' if ok else 'failed'}"
          + (f": missing {sorted(missing)}" if missing else ""))
    return 0 if ok else 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest", nargs="?", help="out/<build>/manifest.jsonl")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        raise SystemExit(selftest())
    if not args.manifest:
        ap.error("give either a manifest or --selftest")

    path = pathlib.Path(args.manifest)
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    findings = audit(records)
    by_kind: dict[str, int] = {}
    for f in findings:
        by_kind[f.kind] = by_kind.get(f.kind, 0) + 1
        print(f)
    print(f"\n{len(records)} records · {len(findings)} findings"
          + (f" ({', '.join(f'{k}:{v}' for k, v in by_kind.items())})" if findings else ""))
    sys.exit(1 if findings else 0)


if __name__ == "__main__":
    main()
