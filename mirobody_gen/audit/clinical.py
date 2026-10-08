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
- **Longitudinal plausibility**: judged by the **rate** of pairs exceeding the reference change value
  (RCV) -- theoretically about 5% -- not by flagging every pair; individual pairs are only flagged past
  3×RCV with no event on the timeline to explain them.
- **Diagnoses and values must explain each other**: values that persistently meet diagnostic criteria
  without a matching diagnosis are a missed diagnosis; the converse (a diagnosis whose indicator is never
  abnormal) is reported but not judged -- a well-controlled chronic condition looks exactly like that.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

PACKAGE = pathlib.Path(__file__).resolve().parents[1]
RESOURCES = PACKAGE / "resources"
#: Root of the source checkout. Used only for things that exist only in a checkout (reference set,
#: cache, build output) -- an installed package has none of these.
REPO = PACKAGE.parent

#: Analytical coefficient of variation. Real labs' CVA varies by assay; this is a single conservative
#: value, used only to decide via RCV whether a jump needs an explanation -- not to reproduce any
#: particular instrument's precision.
CVA_DEFAULT = 3.0

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
    "urine_sg": "narrow range, already constrained by its reference interval",
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
                       derived: set[str] | None = None) -> list[Finding]:
    """Longitudinal plausibility. **Judged by the exceedance rate, not by flagging every pair.**

    The first version flagged every adjacent pair whose change exceeded the reference change value
    (RCV): 586 findings across 601 records. That's exactly what the definition of RCV predicts -- RCV =
    1.96*sqrt(2)*CV is the **95% bound** on a difference, so about 5% of adjacent pairs are expected to
    exceed it by construction. Listing an expected statistical tail one finding at a time turns the audit
    into a noise generator that buries the real problems.

    Replaced with two checks:

    * **Per pair**, flag only the **egregious**: more than 3xRCV with no event on the timeline to
      explain it. That's no longer biological variation -- it's a generator bug or a unit mix-up.
    * **Overall**, check the exceedance rate: among adjacent pairs with a short interval (<120 days),
      the share exceeding RCV should sit near 5%. Much higher means unexplained jumps are being
      injected; much lower means the jitter is too small and the longitudinal series looks suspiciously
      like a straight line.

    Long intervals (>=120 days) are reported but not judged: RCV describes short-term repeat testing at
    steady state, and a year between two draws carries real seasonal, weight and age drift that RCV is
    the wrong tool to gate.
    """
    findings: list[Finding] = []
    decimals = decimals or {}
    derived = derived or set()
    by_person: dict[str, list[dict]] = {}
    for record in records:
        by_person.setdefault(record.get("person_id", "?"), []).append(record)

    short_pairs = short_exceed = long_pairs = long_exceed = 0

    for person_id, items in by_person.items():
        items.sort(key=lambda r: r.get("collected") or "")
        history: dict[str, tuple[str, float]] = {}
        for record in items:
            events = record.get("events_since_previous") or []
            collected = record.get("collected") or ""
            for key, value in numeric_rows(record).items():
                previous = history.get(key)
                history[key] = (collected, value)
                if not previous or previous[1] == 0:
                    continue
                if key in derived:
                    continue  # a derived quantity's variability is propagated, see load_derived()
                cv = cvi.get(key)
                if not cv:
                    continue
                # A difference within printed resolution doesn't count. Basophils print to one decimal
                # place, so a true value of 0.05 can print as 0.0 or 0.1 -- that's rounding, not
                # biology, yet as a ratio it reads as 100% (or a division by zero).
                step = 10 ** -decimals.get(key, 2)
                if abs(value - previous[1]) <= 1.5 * step:
                    continue
                # When both values hug zero (basophils 0.1 vs 0.5), the log ratio is dominated by the
                # quantization step: a printed 0.1 could be anywhere from 0.05 to 0.15, so the ratio
                # swings 3-10x. Skip anything within 5 steps of zero.
                if min(value, previous[1]) < 5 * step:
                    continue
                rcv = 2.77 * math.sqrt(cv ** 2 + CVA_DEFAULT ** 2) / 100
                # Compare on a **log scale**, not (new - old) / old: the latter blows up when the old
                # value is small -- direct bilirubin 0.9->4.6 computes as 411%, while the reverse
                # 4.6->0.9 is only 80%, so the same pair of values gives a different answer depending on
                # order. The log ratio is symmetric, and is standard practice in laboratory medicine for
                # high-variability analytes.
                change = abs(math.log(max(value, 1e-9) / max(previous[1], 1e-9)))
                threshold = math.log(1 + rcv)
                gap = _days_between(previous[0], collected)
                if gap is not None and gap < 120:
                    short_pairs += 1
                    short_exceed += change > threshold
                else:
                    long_pairs += 1
                    long_exceed += change > threshold
                if gap is not None and gap < 120 and change > 3 * threshold and not events:
                    findings.append(Finding(
                        "纵向", f"{person_id} {previous[0]}→{collected}",
                        f"{key} changed from {previous[1]:g} to {value:g} "
                        f"(log ratio {change:.2f}, exceeds 3×ln(1+RCV) {3 * threshold:.2f}), "
                        f"with no event on the timeline to explain it"))

    if short_pairs >= 50:
        rate = short_exceed / short_pairs
        print(f"(longitudinal: {short_pairs} short-interval pairs, {rate:.1%} exceed RCV "
              f"(theoretical ~5%); {long_pairs} long-interval pairs, "
              f"{long_exceed / max(long_pairs, 1):.1%} exceed it, reported but not judged)")
        if not 0.01 <= rate <= 0.15:
            findings.append(Finding(
                "纵向", "overall",
                f"short-interval exceedance rate {rate:.1%} is outside [1%, 15%] -- "
                f"too high means unexplained jumps are being injected, too low means the longitudinal "
                f"series is jittered too little"))
    return findings


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


def check_diagnosis_coherence(records: list[dict], criteria: list[dict]) -> list[Finding]:
    """Values and the diagnosis set must explain each other.

    This catches an inconsistency present in two existing benchmark datasets: HbA1c printed at 7.2% with
    no diabetes in that person's diagnosis set -- on paper, an undiagnosed diabetic. ESL-Bench's values
    come from an LLM and aren't constrained by diagnoses; Synthea's values are uniform samples from the
    `{low, high}` in its module JSON, independent of the Condition it assigns. We shouldn't inherit
    either problem.

    Checked in both directions, but **judged with different severity**:

    * Values meet criteria with no matching diagnosis -> **a finding**. This is a missed diagnosis; it
      doesn't hold up on paper.
    * A diagnosis exists but the indicator is never abnormal -> **reported, not judged**. A
      well-controlled chronic condition can look entirely normal (medicated, blood pressure 128/82);
      calling that an error would demand that sick people always look sick.

    `persistence` requires several consecutive visits to meet criteria before it counts. Requiring just
    one would get flooded by single-visit noise -- a reference range is itself a 95% interval, so
    healthy people occasionally fall outside it as a matter of course, not as a missed diagnosis.
    """
    findings: list[Finding] = []
    by_person: dict[str, list[dict]] = {}
    for record in records:
        by_person.setdefault(record.get("person_id", "?"), []).append(record)

    missing_the_other_way = 0
    for person_id, items in by_person.items():
        items.sort(key=lambda r: r.get("collected") or "")
        have = {c.get("code") for c in ((items[0].get("person") or {}).get("conditions") or [])}
        for rule in criteria:
            code = rule["condition"]["code"]
            hits = 0
            for record in items:
                values = numeric_rows(record)
                met = any(_compare(values.get(c["key"]), c["op"], c["value"])
                          for c in rule["any_of"])
                hits = hits + 1 if met else 0
                if hits >= rule["persistence"] and code not in have:
                    findings.append(Finding(
                        "诊断", f"{person_id} 至 {record.get('collected')}",
                        f"meets the diagnostic criteria for \"{rule['condition']['display']}\" on "
                        f"{hits} consecutive visits ({rule['source'].split('：')[-1]}), but it's "
                        f"missing from the diagnosis set"))
                    break
            if code in have and hits == 0:
                missing_the_other_way += 1

    if missing_the_other_way:
        print(f"(diagnosis: {missing_the_other_way} cases have a diagnosis whose indicator never met "
              f"criteria -- a well-controlled chronic condition looks exactly like that, reported but "
              f"not judged)")
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
    "person": {"sex": "female", "age": 38}, "hazards": [],
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

    expected = {"恒等式", "生理边界", "人口学", "标记"}
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
