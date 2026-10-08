"""Hand-authored cohort design -> `resources/cohort.json`.

    python3 scripts/build_cohort.py --write

This is **clinical design**, not code detail: which archetypes exist, their relative
frequencies, how each chronic condition progresses, when an intervention takes effect, what
a visit orders. It lives in the spec rather than buried in `generator/person.py` for two
reasons:

1. It must be reviewable by someone who doesn't read Python. "A statin drops LDL by 38%,
   taking effect in 4-6 weeks" is a falsifiable clinical claim; it belongs in a file anyone
   can point at.
2. Names such as `iron_deficiency_anemia` and `checkup-center` show up in generated output,
   and the privacy gate's exemption only recognizes public vocabulary declared in the spec --
   anything undeclared gets flagged as a possible string lifted from the real corpus. That
   flag is correct: the gate should never vouch for an undeclared string on its own.

`generator/person.py` reads from here rather than keeping its own copy.
"""

from __future__ import annotations

import argparse
import json
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
RESOURCES = REPO / "mirobody_gen" / "resources"


#: Order set -> indicator keys. Hand-written because "five-item renal panel" isn't something
#: derivable from a package name.
#:
#: Tiered after a 2026-09-29 review: a real check-up report would not print apolipoprotein,
#: lipoprotein(a), cystatin C or glycated albumin in a basic package, nor respiratory rate
#: (that's an inpatient/outpatient vital sign, not a check-up one). `checkup_basic / standard
#: / premium` are the lab portion of the three package tiers; departments and imaging/other
#: investigations are under `packages` in `resources/narratives.json`, matched by name.
ORDERS: dict[str, list[str]] = {
    "cbc": ["wbc", "rbc", "hgb", "hct", "mcv", "mch", "mchc", "rdw", "plt", "mpv",
            "pdw", "pct", "neut_pct", "lymph_pct", "mono_pct", "eos_pct", "baso_pct",
            "neut_abs", "lymph_abs", "mono_abs", "eos_abs", "baso_abs"],
    "liver_basic": ["alt", "ast", "ggt", "tbil", "dbil", "ibil"],
    "liver": ["alt", "ast", "alp", "ggt", "tp", "alb", "glb", "ag_ratio",
              "tbil", "dbil", "ibil", "tba"],
    "renal_basic": ["urea", "crea", "ua"],
    "renal": ["urea", "crea", "ua", "cysc", "egfr"],
    "electrolyte": ["k", "na", "cl", "ca"],
    "electrolyte_full": ["k", "na", "cl", "ca", "phos", "mg"],
    "lipid_basic": ["chol", "tg", "hdl", "ldl"],
    "lipid": ["chol", "tg", "hdl", "ldl", "apoa1", "apob"],
    "lipid_full": ["chol", "tg", "hdl", "ldl", "nonhdl", "apoa1", "apob", "lpa"],
    "glucose_basic": ["glu"],
    "glucose": ["glu", "hba1c"],
    "glucose_ext": ["glu", "hba1c", "ga"],           # endocrinology occasionally adds glycated albumin
    "thyroid_basic": ["tsh", "ft3", "ft4"],
    "thyroid": ["tsh", "ft3", "ft4", "tt3", "tt4", "tpoab"],
    "urinalysis": ["urine_sg", "urine_ph", "urine_pro", "urine_glu", "urine_bld",
                   "urine_ket", "urine_nit", "urine_rbc", "urine_wbc"],
    "inflammation": ["crp", "hscrp", "esr"],
    "inflammation_hs": ["hscrp"],
    "anemia": ["ferritin", "serum_iron", "b12", "folate"],
    "cardiac": ["ldh", "ck", "ckmb"],
    "tumor": ["afp", "cea", "psa", "ca125"],
    "vitals": ["height", "weight", "bmi", "waist", "sbp", "dbp", "pulse"],
    "vitals_clinic": ["temp", "pulse", "resp", "spo2", "sbp", "dbp", "weight"],   # the clinic-note vitals line
    "ecg": ["pulse", "pr_interval", "qrs_duration", "qtc", "qrs_axis"],
    "vitamin": ["vitd", "hcy"],
    # ── 2026-09-29 addition: groups common in mainstream packages, missing from the original set ──
    # Sources: the "1+X" optional-item catalogue in the Expert Consensus on Basic Health
    # Check-up Items (2022), published package catalogues from major check-up centres, the
    # basic items of Japan's ningen dock, and public sample reports from Quest/Labcorp.
    "liver_full": ["alt", "ast", "alp", "ggt", "tp", "alb", "glb", "ag_ratio",
                   "tbil", "dbil", "ibil", "tba", "che", "pa"],
    "pancreas": ["amy", "lps"],
    "renal_early": ["umalb", "uacr", "b2mg"],
    "glucose_full": ["glu", "hba1c", "insulin", "cpep"],
    "glucose_ogtt": ["glu", "ogtt2h", "hba1c"],            # the endocrinology glucose-tolerance test
    "thyroid_full": ["tsh", "ft3", "ft4", "tt3", "tt4", "tpoab", "tgab", "tg_protein"],
    "coagulation": ["pt", "inr", "aptt", "tt", "fib"],
    "coagulation_full": ["pt", "inr", "aptt", "tt", "fib", "ddimer"],
    "blood_type": ["abo", "rh"],
    "hepatitis": ["hbsag", "hbsab", "hbeag", "hbeab", "hbcab"],
    "hepatitis_full": ["hbsag", "hbsab", "hbeag", "hbeab", "hbcab", "hcvab"],
    "immune": ["rf", "aso", "ccp", "igg", "iga", "igm", "c3", "c4"],
    "cardiac_full": ["ldh", "ck", "ckmb", "hstni", "ntprobnp"],
    "tumor_full": ["afp", "cea", "ca199", "ca724", "cyfra211", "nse", "scc", "psa", "fpsa", "fpsa_ratio",
                   "ca125", "ca153", "pg1", "pg2", "pgr"],
    "stool": ["fobt"],
    "hp": ["hp_ab"],
    "cervical": ["hpv16", "hpv18", "hpv_other", "tct"],
    "spirometry": ["fvc", "fvc_pct", "fev1", "fev1_pct", "fev1_fvc"],
    "arterial": ["bapwv_l", "bapwv_r", "abi_l", "abi_r"],
    "body_composition": ["body_fat", "visceral_fat", "muscle_mass", "bmr"],
    "echo": ["lvedd", "ivs", "la", "lvef", "ea"],
}
#: The lab portion of the five package tiers. `entry` is a pre-employment exam (employers may
#: not require hepatitis B testing, so no hepatitis markers); `senior` is the older-adult
#: exam (the "health examination" items in the National Basic Public Health Service Norms,
#: 3rd edition); the remaining three tiers are common configurations of self-pay check-up
#: center packages.
CHECKUP_PACKAGES: dict[str, list[str]] = {
    "entry": ["vitals", "cbc", "urinalysis", "liver_basic", "renal_basic", "glucose_basic", "blood_type"],
    "senior": ["vitals", "cbc", "urinalysis", "liver_basic", "renal_basic", "glucose_basic", "lipid_basic"],
    "basic": ["vitals", "cbc", "urinalysis", "liver_basic", "renal_basic", "glucose_basic", "lipid_basic"],
    "standard": ["vitals", "cbc", "urinalysis", "liver", "renal_basic", "glucose", "lipid", "electrolyte",
                 "thyroid_basic", "tumor", "inflammation_hs", "hepatitis"],
    "premium": ["vitals", "body_composition", "cbc", "blood_type", "coagulation_full", "urinalysis", "stool",
                "liver_full", "renal", "renal_early", "pancreas", "glucose_full", "lipid_full", "electrolyte_full",
                "thyroid_full", "tumor_full", "inflammation_hs", "vitamin", "anemia", "cardiac_full", "immune",
                "hepatitis_full", "hp", "cervical", "spirometry", "arterial", "echo"],
}
for _tier, _groups in CHECKUP_PACKAGES.items():
    ORDERS[f"checkup_{_tier}"] = [k for g in _groups for k in ORDERS[g] if k not in
                                  {k2 for g2 in _groups[:_groups.index(g)] for k2 in ORDERS[g2]}]
ORDERS["checkup"] = ORDERS["checkup_standard"]

#: Archetype = (headcount, baseline shift, annual trend, routine follow-up orders, follow-up
#: interval in months, event script name).
#:
#: The baseline shift is a **multiplier on the reference-range center**: `{"glu": 1.25}` means
#: this person's fasting glucose runs 25% above the typical value. Trend is the relative
#: change per year.
ARCHETYPES: list[dict] = [
    {"name": "healthy", "n": 18, "shift": {}, "trend": {},
     "followup": [], "interval": 0, "script": None},
    {"name": "prediabetes_to_t2dm", "n": 10,
     "shift": {"glu": 1.22, "hba1c": 1.18, "tg": 1.4, "weight_kg": 1.15, "insulin": 1.5, "visceral_fat": 1.4,
               "body_fat": 1.12},
     "trend": {"glu": 0.05, "hba1c": 0.045},
     "followup": ["glucose"], "interval": 6, "script": "metformin"},
    {"name": "dyslipidemia_statin", "n": 8,
     "shift": {"chol": 1.35, "ldl": 1.45, "tg": 1.6, "hdl": 0.82, "bapwv_l": 1.08},
     "trend": {}, "followup": ["lipid"], "interval": 6, "script": "statin"},
    {"name": "iron_deficiency_anemia", "n": 5,
     # The shift is applied to the **independent variables**: iron deficiency is a
     # microcytic, hypochromic anemia, so MCV and MCH drop along with a mild fall in red
     # cell count; hemoglobin and hematocrit then follow from the identities that derive
     # them. Pushing hemoglobin down directly cannot produce a self-consistent CBC.
     "shift": {"rbc": 0.90, "mcv": 0.82, "mchc": 0.93, "ferritin": 0.25, "serum_iron": 0.45},
     "trend": {}, "followup": ["cbc", "anemia"], "interval": 4, "script": "iron"},
    {"name": "thyroid_disorder", "n": 5,
     "shift": {"tsh": 3.2, "ft4": 0.78, "ft3": 0.85, "tpoab": 4.0, "tgab": 3.0},
     "trend": {}, "followup": ["thyroid"], "interval": 6, "script": "levothyroxine"},
    {"name": "ckd_progression", "n": 4,
     "shift": {"crea": 1.45, "urea": 1.5, "cysc": 1.5, "ua": 1.25, "umalb": 3.0, "uacr": 3.0, "b2mg": 1.6},
     "trend": {"crea": 0.09, "urea": 0.07, "cysc": 0.08},
     "followup": ["renal", "electrolyte"], "interval": 6, "script": None},
    {"name": "fatty_liver", "n": 5,
     "shift": {"alt": 2.1, "ast": 1.5, "ggt": 1.9, "tg": 1.5, "weight_kg": 1.2, "visceral_fat": 1.5,
               "body_fat": 1.15, "insulin": 1.4},
     "trend": {}, "followup": ["liver"], "interval": 6, "script": "lifestyle"},
    {"name": "hypertension", "n": 5,
     "shift": {"sbp": 1.18, "dbp": 1.15, "bapwv_l": 1.15, "ivs": 1.08, "ea": 0.85, "umalb": 1.5, "la": 1.06},
     "trend": {"bapwv_l": 0.01}, "followup": ["vitals_clinic"], "interval": 6, "script": "antihypertensive"},
]

#: Event scripts. Each entry = (name, type, effect direction, health impact, strength).
#: Effects: indicator key -> (relative magnitude, days to onset, half-life in days or None)
SCRIPTS: dict[str, dict] = {
    "metformin": {
        "name": "开始二甲双胍", "type": "medication", "effect": "positive", "impact": "high",
        "effects": {"glu": (-0.18, 30, None), "hba1c": (-0.14, 90, None),
                    "weight_kg": (-0.03, 90, None)},
    },
    "statin": {
        "name": "开始他汀治疗", "type": "medication", "effect": "positive", "impact": "high",
        # The LDL step completes over 4-6 weeks -- a pharmacodynamic fact about statins, and
        # the reason the "onset delay" parameter exists at all: a same-day prescription and
        # same-day recheck would show no change.
        "effects": {"ldl": (-0.38, 35, None), "chol": (-0.26, 35, None),
                    "tg": (-0.12, 35, None), "alt": (0.15, 60, 180)},
    },
    "iron": {
        "name": "开始补铁治疗", "type": "medication", "effect": "positive", "impact": "medium",
        # Same logic: the effect acts on MCV/MCH/RBC, and hemoglobin and hematocrit rise with them.
        "effects": {"rbc": (0.08, 60, None), "mchc": (0.05, 75, None),
                    "mcv": (0.12, 90, None), "ferritin": (1.6, 90, None),
                    "serum_iron": (0.8, 45, None)},
    },
    "levothyroxine": {
        "name": "左甲状腺素滴定", "type": "medication", "effect": "positive", "impact": "high",
        "effects": {"tsh": (-0.62, 45, None), "ft4": (0.24, 45, None), "ft3": (0.12, 45, None)},
    },
    "lifestyle": {
        "name": "生活方式干预（减重 + 戒酒）", "type": "exercise_change",
        "effect": "positive", "impact": "medium",
        "effects": {"alt": (-0.35, 90, None), "ggt": (-0.30, 90, None),
                    "tg": (-0.25, 60, None), "weight_kg": (-0.08, 180, None)},
    },
    "antihypertensive": {
        "name": "开始降压治疗", "type": "medication", "effect": "positive", "impact": "high",
        "effects": {"sbp": (-0.12, 21, None), "dbp": (-0.10, 21, None)},
    },
}

#: One-off events anyone might encounter (independent of archetype).
INCIDENTS: list[dict] = [
    {"name": "急性上呼吸道感染", "type": "health_event", "effect": "negative", "impact": "medium",
     "duration": 10,
     "effects": {"wbc": (0.55, 1, 14), "neut_pct": (0.28, 1, 14), "lymph_pct": (-0.22, 1, 14),
                 "crp": (6.0, 1, 12), "hscrp": (5.0, 1, 12), "esr": (1.2, 3, 21)}},
    {"name": "献血 400mL", "type": "health_event", "effect": "neutral", "impact": "low",
     "duration": 1,
     "effects": {"rbc": (-0.09, 3, 90), "ferritin": (-0.35, 7, 150)}},
    {"name": "连续加班一个月", "type": "long_term_habit", "effect": "negative", "impact": "low",
     "duration": 30,
     "effects": {"sbp": (0.05, 7, 45), "tg": (0.18, 14, 60), "pulse": (0.06, 7, 30)}},
    {"name": "春节假期饮食", "type": "diet_change", "effect": "negative", "impact": "low",
     "duration": 7,
     "effects": {"tg": (0.35, 2, 30), "ua": (0.18, 3, 30), "glu": (0.08, 2, 21),
                 "weight_kg": (0.02, 7, 60)}},
    {"name": "开始规律跑步", "type": "exercise_change", "effect": "positive", "impact": "medium",
     "duration": 400,
     "effects": {"hdl": (0.14, 90, None), "tg": (-0.18, 60, None), "pulse": (-0.10, 60, None),
                 "weight_kg": (-0.05, 120, None)}},
]

#: Archetype -> diagnosis (SNOMED CT).
#:
#: This layer is a **seam left for a future seeding layer**: PySynthea / Synthea emit SNOMED
#: diagnoses directly, and this table can then flip into a "diagnosis -> indicator shift"
#: lookup, retiring the archetype concept. It's written forward for now because our own
#: cohort starts from archetypes.
ARCHETYPE_CONDITIONS: dict[str, list[dict]] = {
    "healthy": [],
    "prediabetes_to_t2dm": [{"code": "44054006", "display": "2型糖尿病"}],
    "dyslipidemia_statin": [{"code": "55822004", "display": "高脂血症"}],
    "iron_deficiency_anemia": [{"code": "87522002", "display": "缺铁性贫血"}],
    "thyroid_disorder": [{"code": "40930008", "display": "甲状腺功能减退症"}],
    "ckd_progression": [{"code": "709044004", "display": "慢性肾脏病"}],
    "fatty_liver": [{"code": "197321007", "display": "脂肪肝"}],
    "hypertension": [{"code": "38341003", "display": "高血压"}],
}

#: Diagnostic criteria: **once a value crosses this line, the matching diagnosis must exist**.
#:
#: This backs the clinical audit's fifth check class. It catches an inconsistency both ESL-Bench
#: and Synthea have independently produced: a chart printing HbA1c 7.2% for a person whose
#: diagnosis set has no diabetes -- an undiagnosed diabetic on paper. ESL-Bench generates
#: values with no diagnostic constraint; Synthea's modules sample {low, high} uniformly.
#:
#: `persistence` is how many consecutive visits must meet the criterion before it counts.
#: Setting it to 1 would flood the audit with single-visit noise -- a reference range is
#: itself a 95% interval, so healthy people occasionally fall outside it; that's normal, not
#: a missed diagnosis. All thresholds come from their respective diagnostic guidelines, not
#: from guesswork.
DIAGNOSTIC_CRITERIA: list[dict] = [
    {"condition": {"code": "44054006", "display": "2型糖尿病"},
     "any_of": [{"key": "hba1c", "op": ">=", "value": 6.5},
                {"key": "glu", "op": ">=", "value": 7.0}],
     "persistence": 2,
     "source": "中国2型糖尿病防治指南（2020年版）：空腹血糖≥7.0 mmol/L 或 HbA1c≥6.5%"},
    {"condition": {"code": "38341003", "display": "高血压"},
     "any_of": [{"key": "sbp", "op": ">=", "value": 140},
                {"key": "dbp", "op": ">=", "value": 90}],
     "persistence": 3,
     "source": "中国高血压防治指南（2018）：非同日三次≥140/90 mmHg"},
    {"condition": {"code": "709044004", "display": "慢性肾脏病"},
     "any_of": [{"key": "egfr", "op": "<", "value": 60}],
     "persistence": 2,
     "source": "KDIGO：eGFR<60 持续≥3 个月"},
    {"condition": {"code": "87522002", "display": "贫血"},
     "any_of": [{"key": "hgb", "op": "<", "value": 110}],
     "persistence": 2,
     "source": "WHO 贫血标准（成人女性<120、男性<130；此处取更保守的 110 以免误报）"},
    {"condition": {"code": "40930008", "display": "甲状腺功能减退症"},
     "any_of": [{"key": "tsh", "op": ">=", "value": 10.0}],
     "persistence": 2,
     "source": "临床甲减：TSH≥10 mIU/L 且 FT4 降低"},
    {"condition": {"code": "55822004", "display": "高脂血症"},
     "any_of": [{"key": "chol", "op": ">=", "value": 6.2},
                {"key": "ldl", "op": ">=", "value": 4.1}],
     "persistence": 2,
     "source": "中国成人血脂异常防治指南（2016）：TC≥6.2 或 LDL-C≥4.1 mmol/L 为升高"},
]

REGIONS = ["华北", "华东", "华南", "西南", "东北", "西北", "华中"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    payload = {
        "_source": "hand-authored",
        "_note": (
            "队列构成、疾病轨迹、干预剧本与检验医嘱。全部手写：患病率配比来自流行病学常识，"
            "药效幅度与起效时间来自各自药物的药效学。不含任何来自真实语料的内容。"
        ),
        "_provenance": {"script": "scripts/build_cohort.py",
                        "archetypes": len(ARCHETYPES),
                        "people": sum(a["n"] for a in ARCHETYPES),
                        "orders": len(ORDERS)},
        # These names show up in the manifest, so they must be declared as public vocabulary --
        # otherwise the privacy gate will (correctly) flag them as strings of unknown origin.
        "_vocabulary_fields": ["orders", "checkup_packages", "archetypes", "scripts", "incidents", "regions",
                               "name", "type", "effect", "impact", "followup", "script",
                               "exam_locations", "detection_methods",
                               "archetype_conditions", "condition", "display", "source"],
        "orders": ORDERS,
        "checkup_packages": CHECKUP_PACKAGES,
        "archetypes": ARCHETYPES,
        "archetype_conditions": ARCHETYPE_CONDITIONS,
        "diagnostic_criteria": DIAGNOSTIC_CRITERIA,
        "scripts": SCRIPTS,
        "incidents": INCIDENTS,
        "regions": REGIONS,
        "exam_locations": ["hospital", "clinic", "checkup-center", "lab"],
        "detection_methods": ["laboratory", "Imaging", "Physiological", "Pathological", "wearable"],
    }
    total = sum(a["n"] for a in ARCHETYPES)
    print(f"{len(ARCHETYPES)} archetypes · {total} people · {len(ORDERS)} order sets · "
          f"{len(SCRIPTS)} scripts · {len(INCIDENTS)} random incidents")
    for a in ARCHETYPES:
        print(f"  {a['name']:<24}{a['n']:>3} people  follow-up {'/'.join(a['followup']) or '(none)':<16}"
              f"script {a['script'] or '(none)'}")
    if args.write:
        out = RESOURCES / "cohort.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
