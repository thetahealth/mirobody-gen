"""Hand-authored indicator catalogue -> `resources/indicators.json`.

    python3 scripts/build_indicators.py            # print + reconcile against the corpus distribution
    python3 scripts/build_indicators.py --write    # write resources/indicators.json

The only part of the spec **not distilled from the corpus**, and the source of truth for
value legality.

## Where each field comes from

| Field | Source | Why |
|---|---|---|
| Reference range | PRC health industry standards WS/T 404.1-.8 (chemistry), WS/T 405 (hematology), and standard clinical ranges | Public standards. Using reference values observed in the real corpus would treat one hospital's one instrument as ground truth |
| CVI / CVG | Westgard / EFLM biological-variation database | Sets how much a value should jitter on repeat testing of the same person. Without it, a longitudinal series is either frozen or bouncing randomly |
| Unit | Common UCUM notation | Dialect variants (fL/fl, /L vs /l, mu vs u) are applied by the render layer from the observed distribution in `resources/layout.json`, not enumerated here |
| LOINC | Queried against `mirobody.engine.resolve` at build time | Not to "copy the answer," but to **record which names resolve and which don't**: the ones that don't resolve are material for abstention in evaluation |
| Name variants | Hand-written (includes Traditional Chinese, English, abbreviations, `#`/`%` suffixes) | The printed name is the extraction key; an indicator's several printed forms are part of layout diversity |

## The corpus's only role here: reconciliation, not sourcing values

`--compare` prints the hand-written ranges side by side with the observed p05/p50/p95 from
`library/synth_spec.json`. A large gap between the two means **one of them is wrong** --
maybe a range was mis-transcribed, maybe the corpus side mixes units (an observed
"hematocrit p50=3.0" turned out to be % and L/L mixed together). This is calibration, not a
data pipeline: no generated value is ever sourced from this reconciliation table.
"""

from __future__ import annotations

import argparse
import json
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
RESOURCES = REPO / "mirobody_gen" / "resources"

# Sources for the reference ranges. **Every entry must be something citable**: a standard
# number, a guideline name, or "manufacturer's package insert."
#
# The first version had just one `COMMON = "standard clinical range"`, tagging 57 of 98
# entries, 54 of them without even a comment. The 2026-09-22 review was right: "standard
# clinical range" isn't a citable source -- it doesn't matter for the privacy argument (the
# hand-written ranges never came from the corpus anyway), but for clinical validity and
# paper reproducibility it says nothing at all.
WS404 = "WS/T 404（临床常用生化检验项目参考区间）"
WS405 = "WS/T 405—2012（血细胞分析参考区间）"
LIPID = "中国成人血脂异常防治指南（2016年修订版）"
DIABETES = "中国2型糖尿病防治指南（2020年版）"
HYPERTENSION = "中国高血压防治指南（2018年修订版）"
OBESITY = "中国成人超重和肥胖症预防控制指南（BMI/腰围切点）"
LABMANUAL = "全国临床检验操作规程（第4版）"
VENDOR = "厂商试剂说明书区间（随检测系统而变，生成时按此口径印出）"
ECGTEXT = "临床心电图学通用正常值"
GOLD = "中华医学会《慢性阻塞性肺疾病诊治指南（2021 年修订版）》与 GOLD 2024：FEV1/FVC < 0.70 为持续气流受限"
ATS = "中华医学会《肺功能检查指南——肺量计检查》与 ATS/ERS 2022 解读标准：占预计值 ≥ 80% 为正常"
PWV = "《中国高血压防治指南（2018 年修订版）》靶器官损害判据：baPWV ≥ 1400 cm/s、ABI < 0.9；《中国动脉硬化检测临床应用专家共识》"
ECHO = "中国成年人超声心动图检查测量指南（2016）"
KDIGO = "KDIGO 2012 慢性肾脏病评估与管理指南（UACR < 30 mg/g）"
TBS = "Bethesda（TBS）2014 宫颈细胞学报告系统；《中国子宫颈癌筛查指南（2023）》：NILM 为未见上皮内病变或恶性细胞"
PULSEOX = "WHO《Pulse Oximetry Training Manual》：健康成人静息 SpO₂ 95%–100%"
DERIVED = "由恒等式定义"
#: Legacy constant kept only for the 8th positional argument at 98 call sites; its value is
#: ignored. See the note in add().
COMMON = "（弃用：出处见 REFERENCE_SOURCES）"

#: The **population center value** for one-sided reference ranges.
#:
#: An upper-bound-only item (total cholesterol <5.18) can't get its center from "upper bound
#: times a fixed factor": the population median for total cholesterol sits at 4.6 (89% of the
#: upper bound), while CRP's median sits at 1.5 (19% of its upper bound). Using one factor for
#: both would either push half the population right up against the upper bound or make
#: everyone implausibly low. These values come from the same guidelines/textbooks as the
#: reference ranges, describing the population distribution.
TYPICAL_CENTER: dict[str, float] = {
    # Lipids: population distribution from the Chinese guideline on adult dyslipidemia
    "chol": 4.6, "tg": 1.1, "ldl": 2.8, "hdl": 1.35, "lpa": 120,
    # Liver and biliary
    "dbil": 3.5, "tba": 4.0,
    # Inflammation: skewed distribution, median well below the upper bound
    "crp": 1.5, "hscrp": 0.9, "hcy": 10.0,
    # Thyroid antibodies, tumor markers: most people sit near the low end
    "tpoab": 9.0, "afp": 2.8, "cea": 1.8, "psa": 0.9, "ca125": 12.0,
    # Cardiac enzymes
    "ckmb": 10.0,
    # Renal: eGFR has only a lower bound; healthy adults sit at 95-110
    "egfr": 102.0,
    # Microscopy: real reports are overwhelmingly 0-2/HP; the range midpoint would run systematically high
    "urine_rbc": 0.6, "urine_wbc": 1.2,
    # 2026-09-29 addition
    "ca199": 8.0, "ca153": 9.0, "ca724": 1.5, "cyfra211": 1.4, "nse": 9.0, "scc": 0.6, "fpsa": 0.25,
    "pg1": 95.0, "pg2": 8.0, "ddimer": 0.25, "umalb": 6.0, "uacr": 8.0, "hstni": 3.0, "ntprobnp": 45.0,
    "ogtt2h": 6.0, "tgab": 15.0, "rf": 8.0, "aso": 60.0, "ccp": 5.0, "nonhdl": 3.2, "hp_ab": 0.0,
    "fvc_pct": 98.0, "fev1_fvc": 82.0, "fev1_pct": 96.0, "bapwv_l": 1250.0, "bapwv_r": 1250.0,
    "visceral_fat": 6.0, "la": 30.0, "ea": 1.3, "spo2": 98.0,
}

#: Reference-range source for every indicator. A missing key fails the build -- better than
#: defaulting to a generic value, because a default would make "never decided on a source"
#: look identical to "genuinely a generic range."
REFERENCE_SOURCES: dict[str, str] = {
    # Anthropometry and vital signs
    "height": LABMANUAL, "weight": LABMANUAL, "bmi": OBESITY, "waist": OBESITY,
    "sbp": HYPERTENSION, "dbp": HYPERTENSION, "pulse": ECGTEXT, "resp": LABMANUAL, "temp": LABMANUAL,
    # Complete blood count
    "wbc": WS405, "rbc": WS405, "hgb": WS405, "hct": WS405, "mcv": WS405, "mch": WS405,
    "mchc": WS405, "rdw": LABMANUAL, "plt": WS405, "mpv": LABMANUAL, "pdw": LABMANUAL,
    "pct": LABMANUAL, "neut_pct": WS405, "lymph_pct": WS405, "mono_pct": WS405,
    "eos_pct": WS405, "baso_pct": WS405, "neut_abs": WS405, "lymph_abs": WS405,
    "mono_abs": WS405, "eos_abs": WS405, "baso_abs": WS405,
    # Liver and renal function, electrolytes
    "alt": WS404, "ast": WS404, "alp": WS404, "ggt": WS404, "tp": WS404, "alb": WS404,
    "glb": DERIVED, "ag_ratio": DERIVED, "tbil": WS404, "dbil": WS404, "ibil": DERIVED,
    "tba": LABMANUAL, "urea": WS404, "crea": WS404, "ua": WS404, "cysc": VENDOR,
    "egfr": DERIVED, "k": WS404, "na": WS404, "cl": WS404, "ca": WS404, "phos": WS404,
    "mg": WS404, "ldh": WS404, "ck": WS404, "ckmb": VENDOR,
    # Glucose and lipids
    "glu": DIABETES, "hba1c": DIABETES, "ga": VENDOR,
    "chol": LIPID, "tg": LIPID, "hdl": LIPID, "ldl": LIPID,
    "apoa1": LIPID, "apob": LIPID, "lpa": LIPID,
    # Thyroid and hormones
    "tsh": VENDOR, "ft3": VENDOR, "ft4": VENDOR, "tt3": VENDOR, "tt4": VENDOR,
    "tpoab": VENDOR,
    # Inflammation, anemia, vitamins
    "crp": LABMANUAL, "hscrp": LABMANUAL, "esr": LABMANUAL, "hcy": VENDOR,
    "vitd": VENDOR, "ferritin": VENDOR, "serum_iron": LABMANUAL, "b12": VENDOR,
    "folate": VENDOR,
    # Tumor markers: all vendor cutoffs, no national standard range
    "afp": VENDOR, "cea": VENDOR, "psa": VENDOR, "ca125": VENDOR,
    # Urinalysis
    "urine_sg": LABMANUAL, "urine_ph": LABMANUAL, "urine_pro": LABMANUAL,
    "urine_glu": LABMANUAL, "urine_bld": LABMANUAL, "urine_ket": LABMANUAL,
    "urine_nit": LABMANUAL, "urine_rbc": LABMANUAL, "urine_wbc": LABMANUAL,
    # ECG
    "pr_interval": ECGTEXT, "qrs_duration": ECGTEXT, "qtc": ECGTEXT, "qrs_axis": ECGTEXT,
    # 2026-09-29 addition: filled in from the optional-item catalogue in the Expert Consensus
    # on Basic Health Check-up Items (2022) and mainstream packages
    "spo2": PULSEOX,
    "hbsag": LABMANUAL, "hbsab": LABMANUAL, "hbeag": LABMANUAL, "hbeab": LABMANUAL, "hbcab": LABMANUAL,
    "hcvab": LABMANUAL,
    "ca199": VENDOR, "ca153": VENDOR, "ca724": VENDOR, "cyfra211": VENDOR, "nse": VENDOR, "scc": VENDOR,
    "fpsa": VENDOR, "fpsa_ratio": DERIVED, "pg1": VENDOR, "pg2": VENDOR, "pgr": DERIVED,
    "pt": LABMANUAL, "aptt": LABMANUAL, "tt": LABMANUAL, "fib": LABMANUAL, "inr": LABMANUAL, "ddimer": VENDOR,
    "abo": LABMANUAL, "rh": LABMANUAL,
    "umalb": KDIGO, "uacr": KDIGO, "b2mg": VENDOR,
    "hstni": VENDOR, "ntprobnp": VENDOR,
    "insulin": VENDOR, "cpep": VENDOR, "ogtt2h": DIABETES,
    "tgab": VENDOR, "tg_protein": VENDOR,
    "rf": VENDOR, "aso": VENDOR, "ccp": VENDOR, "igg": LABMANUAL, "iga": LABMANUAL, "igm": LABMANUAL,
    "c3": LABMANUAL, "c4": LABMANUAL,
    "amy": WS404, "lps": VENDOR, "che": LABMANUAL, "pa": LABMANUAL, "nonhdl": LIPID,
    "fobt": LABMANUAL, "hp_ab": VENDOR,
    "fvc": ATS, "fvc_pct": ATS, "fev1": ATS, "fev1_fvc": GOLD, "fev1_pct": ATS,
    "bapwv_l": PWV, "bapwv_r": PWV, "abi_l": PWV, "abi_r": PWV,
    "body_fat": VENDOR, "visceral_fat": VENDOR, "muscle_mass": VENDOR, "bmr": VENDOR,
    "lvef": ECHO, "lvedd": ECHO, "ivs": ECHO, "la": ECHO, "ea": ECHO,
    "hpv16": VENDOR, "hpv18": VENDOR, "hpv_other": VENDOR, "tct": TBS,
}

#: The indicator catalogue.
#:
#: Each entry = (key, Chinese name, English name, abbreviation, unit, decimal places,
#: reference range, range source, CVI%, CVG%, panel, note).
#: Reference-range shapes:
#:   ("range", lo, hi)              sex-independent
#:   ("range_sex", (male lo, male hi), (female lo, female hi))
#:   ("upper", hi)                  upper bound only (`<=x` / `<x`)
#:   ("lower", lo)                  lower bound only (`>x`)
#:   ("qualitative", normal_value)   qualitative item
#:   None                           this item is not usually printed with a reference value
#:
#: CVI/CVG come from the Westgard database; unavailable ones are None, and generation falls
#: back to a conservative jitter estimated from the range width.
INDICATORS: list[dict] = []


def add(key, zh, en, abbr, unit, decimals, ref, _legacy_src, cvi, cvg, panel,
        *, variants=(), kind="quantitative", derived=None, sex=None, note="",
        positive_rate=None, positive_values=None, categories=None):
    # REFERENCE_SOURCES is the only place a source is recognized. The 8th positional argument
    # stays so the 98 call sites don't need to change, but its value is ignored -- writing the
    # source in two places means one of them eventually goes stale.
    if key not in REFERENCE_SOURCES:
        raise KeyError(f"indicator {key} has no reference-range source declared in REFERENCE_SOURCES")
    INDICATORS.append({
        "key": key, "zh": zh, "en": en, "abbr": abbr, "unit": unit, "decimals": decimals,
        "reference": ref, "reference_source": REFERENCE_SOURCES[key], "cvi": cvi, "cvg": cvg,
        "panel": panel, "name_variants": list(variants), "value_kind": kind,
        "derived_from": derived, "sex_specific": sex, "note": note,
        # Qualitative items: population positive rate and what gets printed when positive
        # (default +/++/weakly positive); categorical items: values and weights
        "positive_rate": positive_rate, "positive_values": positive_values, "categories": categories,
    })


# ── Anthropometry and vital signs ──────────────────────────────
add("height", "身高", "Height", "", "cm", 1, None, COMMON, None, None, "vitals",
    variants=("身長", "Height", "身高(cm)"))
add("weight", "体重", "Body weight", "", "kg", 1, None, COMMON, None, None, "vitals",
    variants=("體重", "Body Weight", "体重(kg)"))
add("bmi", "体重指数", "Body mass index", "BMI", "kg/m²", 1, ("range", 18.5, 23.9), COMMON,
    None, None, "vitals", variants=("身体质量指数", "BMI", "体质指数"),
    derived="weight / (height/100)**2")
add("waist", "腰围", "Waist circumference", "", "cm", 1, ("range_sex", (0, 90), (0, 85)), COMMON,
    None, None, "vitals", variants=("腰圍",),
    # Waist circumference must be derived from BMI, not sampled from the reference range. That
    # range is "<=90"; the 0 lower bound is a placeholder, not a real floor -- sampling from the
    # midpoint would pair a 45 cm waist with a 104 kg body weight. The linear approximation is
    # anchored at two points: BMI 22 -> male 80 / female 74 cm; BMI 31 -> male 105 / female 99 cm.
    derived="2.8 * bmi + (19 if male else 13)")
add("sbp", "收缩压", "Systolic blood pressure", "SBP", "mmHg", 0, ("range", 90, 139), COMMON,
    None, None, "vitals", variants=("收縮壓", "Systolic Blood Pressure", "血压收缩压"))
add("dbp", "舒张压", "Diastolic blood pressure", "DBP", "mmHg", 0, ("range", 60, 89), COMMON,
    None, None, "vitals", variants=("舒張壓", "Diastolic Blood Pressure"))
add("pulse", "心率", "Heart rate", "HR", "次/分", 0, ("range", 60, 100), COMMON,
    None, None, "vitals", variants=("脉搏", "脈搏", "Heart Rate", "Pulse"))
add("resp", "呼吸", "Respiratory rate", "RR", "次/分", 0, ("range", 12, 20), COMMON,
    None, None, "vitals", variants=("呼吸频率",))
# Temperature only appears on the clinic-note vitals line (T 36.5C). CVI is hand-set to 0.5%:
# physiological temperature variation is about +-0.2C, and the default 5% would produce a
# 34.8C outpatient.
add("temp", "体温", "Body temperature", "T", "℃", 1, ("range", 36.0, 37.2), COMMON,
    0.5, 0.5, "vitals", variants=("體溫", "Temperature", "体温(℃)"))

# ── Complete blood count, WS/T 405 ───────────────────────────────
add("wbc", "白细胞计数", "White blood cell count", "WBC", "10^9/L", 2,
    ("range", 3.5, 9.5), WS405, 11.4, 21.3, "cbc",
    variants=("白细胞", "白細胞計數", "白细胞总数", "WBC", "白细胞计数(WBC)"))
add("rbc", "红细胞计数", "Red blood cell count", "RBC", "10^12/L", 2,
    ("range_sex", (4.3, 5.8), (3.8, 5.1)), WS405, 3.2, 6.3, "cbc",
    variants=("红细胞", "紅細胞計數", "RBC"))
add("hgb", "血红蛋白", "Hemoglobin", "HGB", "g/L", 0,
    ("range_sex", (130, 175), (115, 150)), WS405, 2.85, 6.8, "cbc",
    variants=("血色素", "血紅蛋白", "血紅素", "Hemoglobin", "HGB", "Hb"),
    derived="rbc * mch",
    note="繁体『血紅素』在台湾就是血红蛋白，但折叠成简体『血红素』后会被索引答成 HbA1c——"
         "这是 mirobody 已知的坑，刻意留作素材")
add("hct", "红细胞压积", "Hematocrit", "HCT", "%", 1,
    ("range_sex", (40, 50), (35, 45)), WS405, 2.7, 6.41, "cbc",
    variants=("血细胞比容", "红细胞比容", "HCT", "Hct"), derived="rbc * mcv / 10")
add("mcv", "平均红细胞体积", "Mean corpuscular volume", "MCV", "fL", 1,
    ("range", 82, 100), WS405, 1.4, 4.85, "cbc", variants=("MCV",))
add("mch", "平均红细胞血红蛋白量", "Mean corpuscular hemoglobin", "MCH", "pg", 1,
    ("range", 27, 34), WS405, 1.6, 5.2, "cbc", variants=("平均血红蛋白量", "MCH"),
    derived="mchc * mcv / 1000")
add("mchc", "平均红细胞血红蛋白浓度", "Mean corpuscular hemoglobin concentration", "MCHC",
    "g/L", 0, ("range", 316, 354), WS405, 1.7, 2.8, "cbc",
    variants=("平均血红蛋白浓度", "MCHC"))
add("rdw", "红细胞分布宽度", "Red cell distribution width", "RDW-CV", "%", 1,
    ("range", 11.5, 14.5), COMMON, 3.5, 5.7, "cbc", variants=("RDW", "RDW-CV"))
add("plt", "血小板计数", "Platelet count", "PLT", "10^9/L", 0,
    ("range", 125, 350), WS405, 9.1, 21.9, "cbc",
    variants=("血小板", "血小板總數", "PLT"))
add("mpv", "平均血小板体积", "Mean platelet volume", "MPV", "fL", 1,
    ("range", 7.6, 13.2), COMMON, 4.3, 8.0, "cbc", variants=("血小板平均体积", "MPV"))
add("pdw", "血小板分布宽度", "Platelet distribution width", "PDW", "fL", 1,
    ("range", 9.0, 17.0), COMMON, None, None, "cbc", variants=("PDW",))
add("pct", "血小板压积", "Plateletcrit", "PCT", "%", 3,
    ("range", 0.108, 0.282), COMMON, None, None, "cbc", variants=("血小板比积", "PCT"))
add("neut_pct", "中性粒细胞百分比", "Neutrophil percentage", "NEUT%", "%", 1,
    ("range", 40, 75), WS405, 12.0, 20.0, "cbc",
    variants=("中性粒细胞%", "中性细胞比率", "NEUT%", "中性粒细胞比率"),
    note="与『中性粒细胞绝对值』同名不同单位 → 不同 LOINC（26511-6 vs 26499-4）")
add("lymph_pct", "淋巴细胞百分比", "Lymphocyte percentage", "LYMPH%", "%", 1,
    ("range", 20, 50), WS405, 10.9, 20.0, "cbc",
    variants=("淋巴细胞%", "淋巴细胞比率", "LYM%"))
add("mono_pct", "单核细胞百分比", "Monocyte percentage", "MONO%", "%", 1,
    ("range", 3, 10), WS405, 17.8, 27.0, "cbc", variants=("单核细胞%", "MONO%"))
add("eos_pct", "嗜酸性粒细胞百分比", "Eosinophil percentage", "EO%", "%", 1,
    ("range", 0.4, 8.0), WS405, 21.0, 60.0, "cbc", variants=("嗜酸性粒细胞%", "EOS%"))
add("baso_pct", "嗜碱性粒细胞百分比", "Basophil percentage", "BASO%", "%", 1,
    ("range", 0, 1), WS405, 25.0, 70.0, "cbc", variants=("嗜碱性粒细胞%", "BASO%"))
add("neut_abs", "中性粒细胞绝对值", "Neutrophil count", "NEUT#", "10^9/L", 2,
    ("range", 1.8, 6.3), WS405, 16.0, 28.0, "cbc",
    variants=("中性粒细胞#", "中性粒细胞数", "NEUT#"), derived="wbc * neut_pct/100")
add("lymph_abs", "淋巴细胞绝对值", "Lymphocyte count", "LYMPH#", "10^9/L", 2,
    ("range", 1.1, 3.2), WS405, 10.4, 23.0, "cbc",
    variants=("淋巴细胞#", "淋巴细胞数", "LYM#"), derived="wbc * lymph_pct/100")
add("mono_abs", "单核细胞绝对值", "Monocyte count", "MONO#", "10^9/L", 2,
    ("range", 0.1, 0.6), WS405, 17.0, 26.0, "cbc",
    variants=("单核细胞#", "MONO#"), derived="wbc * mono_pct/100")
add("eos_abs", "嗜酸性粒细胞绝对值", "Eosinophil count", "EO#", "10^9/L", 2,
    ("range", 0.02, 0.52), WS405, 21.0, 60.0, "cbc",
    variants=("嗜酸性粒细胞#", "EOS#"), derived="wbc * eos_pct/100")
add("baso_abs", "嗜碱性粒细胞绝对值", "Basophil count", "BASO#", "10^9/L", 2,
    ("range", 0, 0.06), WS405, 25.0, 70.0, "cbc",
    variants=("嗜碱性粒细胞#", "BASO#"), derived="wbc * baso_pct/100")

# ── Liver function, WS/T 404.1 / .2 / .4 ─────────────────────────
add("alt", "丙氨酸氨基转移酶", "Alanine aminotransferase", "ALT", "U/L", 0,
    ("range_sex", (9, 50), (7, 40)), WS404, 19.4, 41.6, "chemistry",
    variants=("谷丙转氨酶", "穀丙轉氨酶", "血清丙氨酸氨基转移酶", "ALT", "GPT"))
add("ast", "天门冬氨酸氨基转移酶", "Aspartate aminotransferase", "AST", "U/L", 0,
    ("range_sex", (15, 40), (13, 35)), WS404, 12.3, 23.1, "chemistry",
    variants=("谷草转氨酶", "AST", "GOT", "天冬氨酸氨基转移酶"))
add("alp", "碱性磷酸酶", "Alkaline phosphatase", "ALP", "U/L", 0,
    ("range_sex", (45, 125), (35, 100)), WS404, 6.45, 26.1, "chemistry",
    variants=("鹼性磷酸酶", "ALP", "AKP"))
add("ggt", "γ-谷氨酰基转移酶", "Gamma-glutamyl transferase", "GGT", "U/L", 0,
    ("range_sex", (10, 60), (7, 45)), WS404, 13.4, 42.15, "chemistry",
    variants=("谷氨酰转肽酶", "γ-谷氨酰转肽酶", "GGT", "γ-GT"))
add("tp", "总蛋白", "Total protein", "TP", "g/L", 1,
    ("range", 65, 85), WS404, 2.75, 4.7, "chemistry", variants=("總蛋白", "血清总蛋白", "TP"))
add("alb", "白蛋白", "Albumin", "ALB", "g/L", 1,
    ("range", 40, 55), WS404, 3.2, 4.75, "chemistry", variants=("白蛋白", "ALB", "Alb"))
add("glb", "球蛋白", "Globulin", "GLB", "g/L", 1,
    ("range", 20, 40), DERIVED, None, None, "chemistry",
    variants=("球蛋白", "GLB"), derived="tp - alb")
add("ag_ratio", "白球比", "Albumin/globulin ratio", "A/G", "", 2,
    ("range", 1.2, 2.4), DERIVED, None, None, "chemistry",
    variants=("白球比值", "A/G", "白蛋白/球蛋白"), derived="alb / glb")
add("tbil", "总胆红素", "Total bilirubin", "TBIL", "μmol/L", 1,
    ("range_sex", (0, 26.0), (0, 21.0)), WS404, 21.8, 28.4, "chemistry",
    variants=("總膽紅素", "血清总胆红素", "TBIL", "T-BIL"),
    note="WS/T 404.4—2018 表 1：男≤26.0、女≤21.0、不分性别≤23.0")
add("dbil", "直接胆红素", "Direct bilirubin", "DBIL", "μmol/L", 1,
    ("upper", 8.0), WS404, 36.8, 43.2, "chemistry",
    variants=("结合胆红素", "DBIL", "D-BIL"), derived="tbil × 直接胆红素占比",
    note="WS/T 404.4 按检测系统分层：罗氏≤8.0，贝克曼≤4.0")
add("ibil", "间接胆红素", "Indirect bilirubin", "IBIL", "μmol/L", 1,
    ("range", 0, 18.0), DERIVED, None, None, "chemistry",
    variants=("非结合胆红素", "IBIL", "I-BIL"), derived="tbil - dbil")
add("tba", "总胆汁酸", "Total bile acid", "TBA", "μmol/L", 1,
    ("upper", 10.0), COMMON, None, None, "chemistry", variants=("胆汁酸", "TBA"))

# ── Renal function, WS/T 404.5 ────────────────────────────────────
add("urea", "尿素", "Urea", "BUN", "mmol/L", 2,
    ("range_sex", (3.1, 8.0), (2.6, 7.5)), WS404, 12.1, 18.7, "chemistry",
    variants=("血清尿素", "尿素氮", "BUN", "Urea"))
add("crea", "肌酐", "Creatinine", "CREA", "μmol/L", 0,
    ("range_sex", (57, 97), (41, 73)), WS404, 5.95, 14.7, "chemistry",
    variants=("肌酸酐", "血清肌酐", "CREA", "Cr", "Scr"))
add("ua", "尿酸", "Uric acid", "UA", "μmol/L", 0,
    ("range_sex", (208, 428), (155, 357)), WS404, 8.6, 17.5, "chemistry",
    variants=("血清尿酸", "尿酸", "UA"))
add("cysc", "胱抑素C", "Cystatin C", "CysC", "mg/L", 2,
    ("range", 0.59, 1.03), COMMON, None, None, "chemistry", variants=("半胱氨酸蛋白酶抑制剂C",))
add("egfr", "估算肾小球滤过率", "Estimated GFR", "eGFR", "mL/min/1.73m²", 0,
    ("lower", 90), DERIVED, None, None, "chemistry",
    variants=("eGFR", "肾小球滤过率估算值"), derived="CKD-EPI 2021(crea, age, sex)")

# ── Glucose and lipids ─────────────────────────────────────────────
add("glu", "空腹血糖", "Fasting plasma glucose", "FPG", "mmol/L", 2,
    ("range", 3.9, 6.1), COMMON, 5.6, 7.5, "glucose",
    variants=("葡萄糖", "血糖", "空腹葡萄糖", "GLU", "FBG", "飯前血糖"))
add("hba1c", "糖化血红蛋白", "Glycated hemoglobin", "HbA1c", "%", 1,
    ("range", 4.0, 6.0), COMMON, 1.9, 5.7, "glucose",
    variants=("糖化血色素", "HbA1c", "糖化血红蛋白A1c"))
add("ga", "糖化血清白蛋白", "Glycated albumin", "GA", "%", 1,
    ("range", 11.0, 16.0), COMMON, None, None, "glucose", variants=("糖化白蛋白", "GA"))
add("chol", "总胆固醇", "Total cholesterol", "TC", "mmol/L", 2,
    ("upper", 5.18), COMMON, 5.95, 15.3, "lipid",
    variants=("胆固醇", "總膽固醇", "血清总胆固醇", "TC", "CHOL", "Cholesterol, Total"))
add("tg", "甘油三酯", "Triglycerides", "TG", "mmol/L", 2,
    ("upper", 1.70), COMMON, 19.9, 32.7, "lipid",
    variants=("三酰甘油", "甘油三脂", "TG", "Triglyceride"))
add("hdl", "高密度脂蛋白胆固醇", "HDL cholesterol", "HDL-C", "mmol/L", 2,
    ("lower", 1.04), COMMON, 7.3, 21.2, "lipid",
    variants=("高密度脂蛋白", "HDL-C", "HDL Cholesterol"))
add("ldl", "低密度脂蛋白胆固醇", "LDL cholesterol", "LDL-C", "mmol/L", 2,
    ("upper", 3.37), COMMON, 7.8, 20.4, "lipid",
    variants=("低密度脂蛋白", "LDL-C", "LDL Cholesterol"),
    derived="Friedewald: chol - hdl - tg/2.2")
add("apoa1", "载脂蛋白A1", "Apolipoprotein A1", "ApoA1", "g/L", 2,
    ("range", 1.20, 1.60), COMMON, 6.5, 13.4, "lipid", variants=("APOA1", "ApoA-I"))
add("apob", "载脂蛋白B", "Apolipoprotein B", "ApoB", "g/L", 2,
    ("range", 0.60, 1.10), COMMON, 6.9, 22.8, "lipid", variants=("APOB", "ApoB100"))
add("lpa", "脂蛋白(a)", "Lipoprotein(a)", "Lp(a)", "mg/L", 0,
    ("upper", 300), COMMON, 8.0, 85.0, "lipid", variants=("脂蛋白a", "LP(a)"))

# ── Electrolytes, WS/T 404.3 / .6 ────────────────────────────────
add("k", "钾", "Potassium", "K", "mmol/L", 2, ("range", 3.5, 5.3), WS404, 4.6, 5.6,
    "chemistry", variants=("血清钾", "K+", "钾离子"))
add("na", "钠", "Sodium", "Na", "mmol/L", 1, ("range", 137, 147), WS404, 0.6, 0.7,
    "chemistry", variants=("血清钠", "Na+", "钠离子"))
add("cl", "氯", "Chloride", "Cl", "mmol/L", 1, ("range", 99, 110), WS404, 1.2, 1.5,
    "chemistry", variants=("血清氯", "Cl-", "氯离子"))
add("ca", "钙", "Calcium", "Ca", "mmol/L", 2, ("range", 2.11, 2.52), WS404, 2.1, 2.5,
    "chemistry", variants=("血清钙", "总钙", "Ca"))
add("phos", "无机磷", "Phosphorus", "P", "mmol/L", 2, ("range", 0.85, 1.51), WS404,
    8.2, 9.4, "chemistry", variants=("磷", "血清磷"))
add("mg", "镁", "Magnesium", "Mg", "mmol/L", 2, ("range", 0.75, 1.02), WS404, 3.6, 6.4,
    "chemistry", variants=("血清镁",))

# ── Cardiac enzymes, WS/T 404.7 ───────────────────────────────────
add("ldh", "乳酸脱氢酶", "Lactate dehydrogenase", "LDH", "U/L", 0,
    ("range", 120, 250), WS404, 8.6, 14.7, "chemistry", variants=("LDH", "乳酸去氢酶"))
add("ck", "肌酸激酶", "Creatine kinase", "CK", "U/L", 0,
    ("range_sex", (50, 310), (40, 200)), WS404, 22.8, 40.0, "chemistry",
    variants=("CK", "肌酸磷酸激酶", "CPK"))
add("ckmb", "肌酸激酶同工酶", "Creatine kinase MB", "CK-MB", "U/L", 1,
    ("upper", 25), COMMON, None, None, "chemistry", variants=("CK-MB", "肌酸激酶同工酶MB"))

# ── Thyroid ─────────────────────────────────────────────────────────
add("tsh", "促甲状腺激素", "Thyroid stimulating hormone", "TSH", "mIU/L", 3,
    ("range", 0.27, 4.20), COMMON, 19.3, 24.6, "thyroid",
    variants=("促甲状腺素", "TSH", "超敏促甲状腺激素"))
add("ft3", "游离三碘甲状腺原氨酸", "Free triiodothyronine", "FT3", "pmol/L", 2,
    ("range", 3.10, 6.80), COMMON, 7.9, 17.6, "thyroid", variants=("游离T3", "FT3"))
add("ft4", "游离甲状腺素", "Free thyroxine", "FT4", "pmol/L", 2,
    ("range", 12.0, 22.0), COMMON, 5.7, 12.1, "thyroid", variants=("游离T4", "FT4"))
add("tt3", "总三碘甲状腺原氨酸", "Total triiodothyronine", "TT3", "nmol/L", 2,
    ("range", 1.30, 3.10), COMMON, 8.7, 17.6, "thyroid", variants=("总T3", "TT3"))
add("tt4", "总甲状腺素", "Total thyroxine", "TT4", "nmol/L", 1,
    ("range", 66, 181), COMMON, 4.9, 10.9, "thyroid", variants=("总T4", "TT4"))
add("tpoab", "甲状腺过氧化物酶抗体", "Thyroid peroxidase antibody", "TPOAb", "IU/mL", 1,
    ("upper", 34), COMMON, None, None, "thyroid", variants=("抗TPO抗体", "TPOAb"))

# ── Inflammation and others ───────────────────────────────────────
add("crp", "C反应蛋白", "C-reactive protein", "CRP", "mg/L", 2,
    ("upper", 8.0), COMMON, 42.2, 76.3, "inflammation", variants=("CRP", "C-反应蛋白"))
add("hscrp", "超敏C反应蛋白", "High-sensitivity CRP", "hs-CRP", "mg/L", 2,
    ("upper", 3.0), COMMON, 42.2, 76.3, "inflammation", variants=("hsCRP", "超敏C-反应蛋白"))
add("esr", "红细胞沉降率", "Erythrocyte sedimentation rate", "ESR", "mm/h", 0,
    ("range_sex", (0, 15), (0, 20)), COMMON, None, None, "inflammation",
    variants=("血沉", "血沉速率", "ESR"))
add("hcy", "同型半胱氨酸", "Homocysteine", "Hcy", "μmol/L", 1,
    ("upper", 15.0), COMMON, 8.4, 25.0, "chemistry", variants=("Hcy", "血浆同型半胱氨酸"))
add("vitd", "25-羟基维生素D", "25-hydroxyvitamin D", "25(OH)D", "ng/mL", 1,
    ("range", 30, 100), COMMON, 12.1, 25.0, "vitamin",
    variants=("维生素D", "25-OH-VD", "25羟基维生素D"),
    note="实测 mirobody 解析不出这个名字——刻意留作考弃权的素材")
add("ferritin", "铁蛋白", "Ferritin", "FER", "ng/mL", 1,
    ("range_sex", (30, 400), (13, 150)), COMMON, 14.2, 15.0, "anemia",
    variants=("血清铁蛋白", "Ferritin"))
add("serum_iron", "血清铁", "Serum iron", "Fe", "μmol/L", 1,
    ("range_sex", (11.6, 31.3), (9.0, 30.4)), COMMON, 26.5, 23.2, "anemia",
    variants=("铁", "血清铁"))
add("b12", "维生素B12", "Vitamin B12", "VB12", "pmol/L", 0,
    ("range", 145, 569), COMMON, 14.5, 32.0, "anemia", variants=("VitB12", "钴胺素"))
add("folate", "叶酸", "Folate", "FA", "nmol/L", 1,
    ("range", 7.0, 46.4), COMMON, 24.0, 37.0, "anemia", variants=("血清叶酸",))

# ── Tumor markers (sex association kept deliberately, for the audit to check demographic conflicts) ──
add("afp", "甲胎蛋白", "Alpha-fetoprotein", "AFP", "ng/mL", 2,
    ("upper", 7.0), COMMON, None, None, "tumor", variants=("AFP",))
add("cea", "癌胚抗原", "Carcinoembryonic antigen", "CEA", "ng/mL", 2,
    ("upper", 5.0), COMMON, None, None, "tumor", variants=("CEA",),
    note="实测 mirobody 解析不出——留作考弃权的素材")
add("psa", "前列腺特异性抗原", "Prostate specific antigen", "PSA", "ng/mL", 2,
    ("upper", 4.0), COMMON, None, None, "tumor", variants=("总PSA", "tPSA"), sex="male")
add("ca125", "糖类抗原125", "Cancer antigen 125", "CA125", "U/mL", 1,
    ("upper", 35.0), COMMON, None, None, "tumor", variants=("CA-125",), sex="female")

# ── Urinalysis (mostly qualitative) ───────────────────────────────
add("urine_sg", "尿比重", "Urine specific gravity", "SG", "", 3,
    ("range", 1.003, 1.030), COMMON, None, None, "urinalysis", variants=("比重", "SG"))
add("urine_ph", "尿酸碱度", "Urine pH", "pH", "", 1,
    ("range", 4.5, 8.0), COMMON, None, None, "urinalysis", variants=("酸碱度", "尿PH", "pH"))
add("urine_pro", "尿蛋白", "Urine protein", "PRO", "", 0,
    ("qualitative", "阴性"), COMMON, None, None, "urinalysis",
    variants=("蛋白质", "尿蛋白定性", "PRO"), kind="qualitative")
add("urine_glu", "尿葡萄糖", "Urine glucose", "GLU", "", 0,
    ("qualitative", "阴性"), COMMON, None, None, "urinalysis",
    variants=("尿糖", "葡萄糖(尿)", "U-GLU"), kind="qualitative",
    note="实测 mirobody 解析不出『尿葡萄糖』——留作考弃权的素材")
add("urine_bld", "尿潜血", "Urine occult blood", "BLD", "", 0,
    ("qualitative", "阴性"), COMMON, None, None, "urinalysis",
    variants=("隐血", "潜血", "BLD"), kind="qualitative")
add("urine_ket", "尿酮体", "Urine ketone", "KET", "", 0,
    ("qualitative", "阴性"), COMMON, None, None, "urinalysis",
    variants=("酮体", "KET"), kind="qualitative")
add("urine_nit", "亚硝酸盐", "Nitrite", "NIT", "", 0,
    ("qualitative", "阴性"), COMMON, None, None, "urinalysis",
    variants=("尿亚硝酸盐", "NIT"), kind="qualitative")
add("urine_rbc", "镜检红细胞", "Urine RBC (microscopy)", "U-RBC", "/HP", 1,
    ("range", 0, 3), COMMON, None, None, "urinalysis", variants=("尿红细胞", "红细胞(镜检)"))
add("urine_wbc", "镜检白细胞", "Urine WBC (microscopy)", "U-WBC", "/HP", 1,
    ("range", 0, 5), COMMON, None, None, "urinalysis", variants=("尿白细胞", "白细胞(镜检)"))

# ── ECG values ─────────────────────────────────────────────────────
add("pr_interval", "PR间期", "PR interval", "PR", "ms", 0,
    ("range", 120, 200), COMMON, None, None, "ecg", variants=("P-R间期",))
add("qrs_duration", "QRS时限", "QRS duration", "QRS", "ms", 0,
    ("range", 60, 110), COMMON, None, None, "ecg", variants=("QRS波时限",))
add("qtc", "QTc间期", "QTc interval", "QTc", "ms", 0,
    ("range", 350, 440), COMMON, None, None, "ecg", variants=("QTC间期", "校正QT间期"))
add("qrs_axis", "QRS电轴", "QRS axis", "", "°", 0,
    ("range", -30, 90), COMMON, None, None, "ecg", variants=("心电轴",))


# ── 2026-09-29 addition: mainstream check-up packages and the expert-consensus optional-item catalogue ──
# Vital signs
add("spo2", "血氧饱和度", "Oxygen saturation", "SpO2", "%", 0, ("range", 95, 100), COMMON, 0.5, 0.5, "vitals",
    variants=("血氧", "SpO₂", "指脉氧"))
# Hepatitis B panel / hepatitis C (qualitative; positive rate by population)
add("hbsag", "乙肝表面抗原", "Hepatitis B surface antigen", "HBsAg", "", 0, ("qualitative", "阴性"), COMMON, None, None,
    "hepatitis", variants=("HBsAg", "乙型肝炎表面抗原", "乙型肝炎表面抗原(HK)"), kind="qualitative",
    positive_rate=0.06, positive_values=[["阳性", 1]])
add("hbsab", "乙肝表面抗体", "Hepatitis B surface antibody", "HBsAb", "", 0, ("qualitative", "阴性"), COMMON, None, None,
    "hepatitis", variants=("Anti-HBs", "乙型肝炎表面抗體"), kind="qualitative",
    positive_rate=0.55, positive_values=[["阳性", 1]], note="接种过疫苗的人为阳性，阳性不是异常")
add("hbeag", "乙肝e抗原", "Hepatitis B e antigen", "HBeAg", "", 0, ("qualitative", "阴性"), COMMON, None, None,
    "hepatitis", variants=("HBeAg",), kind="qualitative", positive_rate=0.02, positive_values=[["阳性", 1]])
add("hbeab", "乙肝e抗体", "Hepatitis B e antibody", "HBeAb", "", 0, ("qualitative", "阴性"), COMMON, None, None,
    "hepatitis", variants=("Anti-HBe",), kind="qualitative", positive_rate=0.05, positive_values=[["阳性", 1]])
add("hbcab", "乙肝核心抗体", "Hepatitis B core antibody", "HBcAb", "", 0, ("qualitative", "阴性"), COMMON, None, None,
    "hepatitis", variants=("Anti-HBc",), kind="qualitative", positive_rate=0.08, positive_values=[["阳性", 1]])
add("hcvab", "丙肝抗体", "Hepatitis C antibody", "HCV-Ab", "", 0, ("qualitative", "阴性"), COMMON, None, None,
    "hepatitis", variants=("Anti-HCV", "丙型肝炎抗體"), kind="qualitative", positive_rate=0.005, positive_values=[["阳性", 1]])
# Tumor markers (extended)
add("ca199", "糖类抗原19-9", "Cancer antigen 19-9", "CA19-9", "U/mL", 2, ("upper", 37.0), COMMON, 16.0, 102.0, "tumor",
    variants=("CA199", "CA 19-9", "糖類抗原19-9"))
add("ca153", "糖类抗原15-3", "Cancer antigen 15-3", "CA15-3", "U/mL", 2, ("upper", 25.0), COMMON, 6.2, 62.9, "tumor",
    variants=("CA153", "CA 15-3"), sex="female")
add("ca724", "糖类抗原72-4", "Cancer antigen 72-4", "CA72-4", "U/mL", 2, ("upper", 6.9), COMMON, 20.0, None, "tumor",
    variants=("CA724",))
add("cyfra211", "细胞角蛋白19片段", "Cytokeratin 19 fragment", "CYFRA21-1", "ng/mL", 2, ("upper", 3.3), COMMON, 10.0, None, "tumor",
    variants=("CYFRA 21-1", "非小细胞肺癌相关抗原"))
add("nse", "神经元特异性烯醇化酶", "Neuron-specific enolase", "NSE", "ng/mL", 2, ("upper", 16.3), COMMON, 10.0, None, "tumor",
    variants=("NSE",))
add("scc", "鳞状细胞癌抗原", "Squamous cell carcinoma antigen", "SCC", "ng/mL", 2, ("upper", 1.5), COMMON, 12.0, None, "tumor",
    variants=("SCC-Ag", "鳞状上皮细胞癌抗原"))
add("fpsa", "游离前列腺特异性抗原", "Free PSA", "fPSA", "ng/mL", 3, ("upper", 1.0), COMMON, None, None, "tumor",
    variants=("f-PSA", "游离PSA"), sex="male", derived="psa × 个体游离比例（0.08–0.45）")
add("fpsa_ratio", "游离PSA/总PSA比值", "Free/total PSA ratio", "f/tPSA", "", 2, ("lower", 0.16), COMMON, None, None, "tumor",
    variants=("fPSA/tPSA", "F/T"), sex="male", derived="fpsa / psa")
add("pg1", "胃蛋白酶原Ⅰ", "Pepsinogen I", "PGⅠ", "ng/mL", 1, ("lower", 70.0), COMMON, 8.0, 30.0, "tumor",
    variants=("PGI", "胃蛋白酶原I"))
add("pg2", "胃蛋白酶原Ⅱ", "Pepsinogen II", "PGⅡ", "ng/mL", 1, ("upper", 20.0), COMMON, 9.0, 35.0, "tumor",
    variants=("PGII", "胃蛋白酶原II"))
add("pgr", "胃蛋白酶原比值", "Pepsinogen I/II ratio", "PGR", "", 2, ("lower", 3.0), COMMON, None, None, "tumor",
    variants=("PGⅠ/PGⅡ", "PGI/PGII"), derived="pg1 / pg2")
# Coagulation panel + D-dimer
add("pt", "凝血酶原时间", "Prothrombin time", "PT", "s", 1, ("range", 11.0, 14.0), COMMON, 2.4, 6.8, "coagulation",
    variants=("PT", "血浆凝血酶原时间"))
add("inr", "国际标准化比值", "International normalised ratio", "INR", "", 2, ("range", 0.8, 1.2), COMMON, 2.4, 6.8, "coagulation",
    variants=("INR", "PT-INR"))
add("aptt", "活化部分凝血活酶时间", "Activated partial thromboplastin time", "APTT", "s", 1, ("range", 25.0, 37.0), COMMON, 2.7, 8.6, "coagulation",
    variants=("APTT", "部分凝血活酶时间"))
add("tt", "凝血酶时间", "Thrombin time", "TT", "s", 1, ("range", 14.0, 21.0), COMMON, 3.0, 8.0, "coagulation",
    variants=("TT",))
add("fib", "纤维蛋白原", "Fibrinogen", "FIB", "g/L", 2, ("range", 2.0, 4.0), COMMON, 10.7, 15.8, "coagulation",
    variants=("FIB", "Fbg", "纤维蛋白原含量"))
add("ddimer", "D-二聚体", "D-dimer", "D-D", "mg/L FEU", 2, ("upper", 0.5), COMMON, 23.0, 28.0, "coagulation",
    variants=("D-Dimer", "DD", "D二聚体"))
# Blood type (categorical)
add("abo", "ABO血型", "ABO blood group", "ABO", "", 0, None, COMMON, None, None, "blood_type",
    variants=("血型", "ABO"), kind="categorical", categories=[["A", 28], ["B", 24], ["O", 41], ["AB", 7]])
add("rh", "Rh(D)血型", "Rh(D) type", "Rh", "", 0, None, COMMON, None, None, "blood_type",
    variants=("RhD", "Rh血型"), kind="categorical", categories=[["阳性", 99], ["阴性", 1]])
# Early renal damage
add("umalb", "尿微量白蛋白", "Urine microalbumin", "mALB", "mg/L", 1, ("upper", 20.0), COMMON, 36.0, 55.0, "renal_early",
    variants=("尿微量清蛋白", "U-mALB"))
add("uacr", "尿白蛋白/肌酐比值", "Urine albumin/creatinine ratio", "UACR", "mg/g", 1, ("upper", 30.0), COMMON, 30.0, 50.0, "renal_early",
    variants=("ACR", "尿微量白蛋白肌酐比"))
add("b2mg", "β2-微球蛋白", "Beta-2 microglobulin", "β2-MG", "mg/L", 2, ("range", 1.0, 3.0), COMMON, 5.9, 15.0, "renal_early",
    variants=("β2微球蛋白", "B2-MG"))
# Cardiac markers
add("hstni", "超敏肌钙蛋白I", "High-sensitivity troponin I", "hs-cTnI", "ng/L", 1, ("upper", 26.2), COMMON, 9.7, 57.0, "cardiac",
    variants=("hs-TnI", "肌钙蛋白I"))
add("ntprobnp", "N末端脑钠肽前体", "NT-proBNP", "NT-proBNP", "pg/mL", 0, ("upper", 125.0), COMMON, 30.0, 40.0, "cardiac",
    variants=("NT-proBNP", "氨基末端B型利钠肽原"))
# Glucose metabolism
add("insulin", "空腹胰岛素", "Fasting insulin", "INS", "μIU/mL", 2, ("range", 2.6, 24.9), COMMON, 21.0, 58.0, "glucose",
    variants=("胰岛素", "FINS"))
add("cpep", "C肽", "C-peptide", "C-P", "ng/mL", 2, ("range", 1.1, 4.4), COMMON, 9.3, 30.0, "glucose",
    variants=("C-肽", "空腹C肽"))
add("ogtt2h", "餐后2小时血糖", "2-hour post-load glucose", "2hPG", "mmol/L", 2, ("upper", 7.8), COMMON, None, None, "glucose",
    variants=("OGTT 2h", "糖负荷后2小时血糖", "餐后两小时血糖"), derived="glu × 个体餐后系数（1.05–1.9）")
# Thyroid (extended)
add("tgab", "甲状腺球蛋白抗体", "Thyroglobulin antibody", "TgAb", "IU/mL", 1, ("upper", 115.0), COMMON, 12.0, 60.0, "thyroid",
    variants=("TGAb", "抗甲状腺球蛋白抗体"))
add("tg_protein", "甲状腺球蛋白", "Thyroglobulin", "Tg", "ng/mL", 2, ("range", 3.5, 77.0), COMMON, 8.0, 40.0, "thyroid",
    variants=("TG", "甲状腺球蛋白(Tg)"))
# Rheumatology and immunology
add("rf", "类风湿因子", "Rheumatoid factor", "RF", "IU/mL", 1, ("upper", 20.0), COMMON, 8.5, 40.0, "immune",
    variants=("RF", "類風濕因子"))
add("aso", "抗链球菌溶血素O", "Antistreptolysin O", "ASO", "IU/mL", 0, ("upper", 200.0), COMMON, 10.0, 40.0, "immune",
    variants=("ASO", "抗O"))
add("ccp", "抗环瓜氨酸肽抗体", "Anti-CCP antibody", "抗CCP", "U/mL", 1, ("upper", 25.0), COMMON, 10.0, 40.0, "immune",
    variants=("Anti-CCP", "抗CCP抗体"))
add("igg", "免疫球蛋白G", "Immunoglobulin G", "IgG", "g/L", 2, ("range", 7.0, 16.0), COMMON, 4.5, 16.5, "immune",
    variants=("IgG",))
add("iga", "免疫球蛋白A", "Immunoglobulin A", "IgA", "g/L", 2, ("range", 0.7, 4.0), COMMON, 5.4, 36.0, "immune",
    variants=("IgA",))
add("igm", "免疫球蛋白M", "Immunoglobulin M", "IgM", "g/L", 2, ("range", 0.4, 2.3), COMMON, 5.9, 47.0, "immune",
    variants=("IgM",))
add("c3", "补体C3", "Complement C3", "C3", "g/L", 2, ("range", 0.9, 1.8), COMMON, 5.2, 15.0, "immune",
    variants=("C3",))
add("c4", "补体C4", "Complement C4", "C4", "g/L", 2, ("range", 0.1, 0.4), COMMON, 8.9, 33.0, "immune",
    variants=("C4",))
# Chemistry (extended)
add("amy", "淀粉酶", "Amylase", "AMY", "U/L", 0, ("range", 35, 135), COMMON, 8.7, 28.0, "chemistry",
    variants=("血淀粉酶", "AMS", "澱粉酶"))
add("lps", "脂肪酶", "Lipase", "LPS", "U/L", 0, ("range", 13, 60), COMMON, 23.0, 40.0, "chemistry",
    variants=("LIP", "血脂肪酶"))
add("che", "胆碱酯酶", "Cholinesterase", "CHE", "U/L", 0, ("range", 5000, 12000), COMMON, 5.4, 16.5, "chemistry",
    variants=("ChE", "假性胆碱酯酶"))
add("pa", "前白蛋白", "Prealbumin", "PA", "mg/L", 0, ("range", 200, 400), COMMON, 10.9, 20.0, "chemistry",
    variants=("PAB", "前清蛋白"))
add("nonhdl", "非高密度脂蛋白胆固醇", "Non-HDL cholesterol", "non-HDL-C", "mmol/L", 2, ("upper", 4.1), COMMON, None, None, "lipid",
    variants=("Non-HDL-C", "非HDL胆固醇"), derived="chol - hdl")
# Stool, H. pylori
add("fobt", "便潜血", "Faecal occult blood", "FOBT", "", 0, ("qualitative", "阴性"), COMMON, None, None, "stool",
    variants=("大便隐血", "粪便隐血试验", "大便隱血"), kind="qualitative", positive_rate=0.04, positive_values=[["阳性", 3], ["弱阳性", 1]])
add("hp_ab", "幽门螺杆菌抗体", "Helicobacter pylori antibody", "Hp-Ab", "", 0, ("qualitative", "阴性"), COMMON, None, None, "hp",
    variants=("HP抗体", "幽门螺旋杆菌抗体"), kind="qualitative", positive_rate=0.40, positive_values=[["阳性", 1]])
# Spirometry
add("fvc", "用力肺活量", "Forced vital capacity", "FVC", "L", 2, ("range_sex", (2.8, 5.5), (2.0, 4.2)), COMMON, 3.0, 12.0, "spirometry",
    variants=("FVC",))
add("fvc_pct", "用力肺活量占预计值", "FVC % predicted", "FVC%pred", "%", 0, ("lower", 80), COMMON, 3.0, 8.0, "spirometry",
    variants=("FVC%", "肺活量占预计值百分比"))
add("fev1_fvc", "一秒率", "FEV1/FVC", "FEV1/FVC", "%", 1, ("lower", 70.0), COMMON, 3.0, 6.0, "spirometry",
    variants=("FEV1%", "一秒率(FEV1/FVC)"))
add("fev1", "第一秒用力呼气容积", "FEV1", "FEV1", "L", 2, None, COMMON, None, None, "spirometry",
    variants=("FEV1.0", "一秒量"), derived="fvc × fev1_fvc / 100")
add("fev1_pct", "一秒量占预计值", "FEV1 % predicted", "FEV1%pred", "%", 0, ("lower", 80), COMMON, None, None, "spirometry",
    variants=("FEV1%pred",), derived="fvc_pct × fev1_fvc / 82")
# Arterial stiffness
add("bapwv_l", "左侧臂踝脉搏波传导速度", "baPWV (left)", "baPWV-L", "cm/s", 0, ("upper", 1400), COMMON, 6.0, 12.0, "arterial",
    variants=("左baPWV", "L-baPWV"))
add("bapwv_r", "右侧臂踝脉搏波传导速度", "baPWV (right)", "baPWV-R", "cm/s", 0, ("upper", 1400), COMMON, None, None, "arterial",
    variants=("右baPWV", "R-baPWV"), derived="bapwv_l × 个体左右差（0.94–1.06）")
add("abi_l", "左侧踝臂指数", "ABI (left)", "ABI-L", "", 2, ("range", 0.9, 1.3), COMMON, 4.0, 6.0, "arterial",
    variants=("左ABI", "L-ABI"))
add("abi_r", "右侧踝臂指数", "ABI (right)", "ABI-R", "", 2, ("range", 0.9, 1.3), COMMON, 4.0, 6.0, "arterial",
    variants=("右ABI", "R-ABI"))
# Body composition (bioelectrical impedance)
add("body_fat", "体脂率", "Body fat percentage", "PBF", "%", 1, ("range_sex", (10.0, 20.0), (18.0, 28.0)), COMMON, 3.0, 20.0, "body_composition",
    variants=("体脂百分比", "身體脂肪百分比", "Body Fat"))
add("visceral_fat", "内脏脂肪等级", "Visceral fat level", "VFL", "", 0, ("upper", 9), COMMON, 5.0, 30.0, "body_composition",
    variants=("内脏脂肪指数", "VFA level"))
add("muscle_mass", "骨骼肌量", "Skeletal muscle mass", "SMM", "kg", 1, ("range_sex", (28.0, 45.0), (18.0, 30.0)), COMMON, 2.0, 12.0, "body_composition",
    variants=("肌肉量", "骨骼肌"))
add("bmr", "基础代谢率", "Basal metabolic rate", "BMR", "kcal/d", 0, ("range_sex", (1400, 1900), (1100, 1500)), COMMON, 2.0, 10.0, "body_composition",
    variants=("基础代谢", "BMR(kcal)"))
# Echocardiography
add("lvef", "左室射血分数", "LV ejection fraction", "LVEF", "%", 0, ("range", 55, 75), COMMON, 5.0, 6.0, "echo",
    variants=("EF", "射血分数"))
add("lvedd", "左室舒张末期内径", "LV end-diastolic diameter", "LVEDd", "mm", 0, ("range_sex", (45, 55), (40, 50)), COMMON, 3.0, 6.0, "echo",
    variants=("LVIDd", "左室舒张末内径"))
add("ivs", "室间隔厚度", "Interventricular septum", "IVS", "mm", 1, ("range", 6.0, 11.0), COMMON, 5.0, 10.0, "echo",
    variants=("IVSd", "室间隔"))
add("la", "左房内径", "Left atrium diameter", "LA", "mm", 0, ("upper", 35), COMMON, 4.0, 8.0, "echo",
    variants=("LAD", "左房前后径"))
add("ea", "二尖瓣E/A比值", "Mitral E/A ratio", "E/A", "", 2, ("lower", 1.0), COMMON, 8.0, 15.0, "echo",
    variants=("E/A", "二尖瓣血流E/A"))
# Cervical cancer screening (female)
add("hpv16", "HPV16型", "HPV type 16", "HPV16", "", 0, ("qualitative", "阴性"), COMMON, None, None, "cervical",
    variants=("HPV 16", "人乳头瘤病毒16型"), kind="qualitative", sex="female", positive_rate=0.02, positive_values=[["阳性", 1]])
add("hpv18", "HPV18型", "HPV type 18", "HPV18", "", 0, ("qualitative", "阴性"), COMMON, None, None, "cervical",
    variants=("HPV 18",), kind="qualitative", sex="female", positive_rate=0.01, positive_values=[["阳性", 1]])
add("hpv_other", "其他12型高危HPV", "Other high-risk HPV (12 types)", "HPV-HR", "", 0, ("qualitative", "阴性"), COMMON, None, None, "cervical",
    variants=("高危型HPV(其他)", "HPV其他高危型"), kind="qualitative", sex="female", positive_rate=0.08, positive_values=[["阳性", 1]])
add("tct", "液基薄层细胞学检查", "Liquid-based cytology (TCT)", "TCT", "", 0, ("qualitative", "NILM"), COMMON, None, None, "cervical",
    variants=("TCT", "宫颈细胞学", "宫颈液基细胞学"), kind="qualitative", sex="female", positive_rate=0.06,
    positive_values=[["ASC-US", 8], ["LSIL", 2], ["ASC-H", 1]])

# Hong Kong / Taiwan spellings: common printed forms of the same indicator in Traditional-script reports
EXTRA_VARIANTS = {
    "hgb": ["血色素", "血紅素"], "tg": ["三酸甘油酯", "三酸甘油脂"], "crea": ["肌酸酐"], "alt": ["谷丙轉氨酶", "GPT"],
    "ast": ["谷草轉氨酶", "GOT"], "chol": ["總膽固醇"], "hdl": ["高密度膽固醇"], "ldl": ["低密度膽固醇"],
    "glu": ["血糖(空腹)", "空腹血糖(HK)"], "ua": ["尿酸(UA)"], "wbc": ["白血球"], "plt": ["血小板"], "rbc": ["紅血球"],
    "hba1c": ["糖化血色素"], "urea": ["尿素氮(BUN)", "BUN"],
}
for item in INDICATORS:
    for v in EXTRA_VARIANTS.get(item["key"], []):
        if v not in item["name_variants"]:
            item["name_variants"].append(v)


# ── Build ────────────────────────────────────────────────────────
class ResolverUnavailable(RuntimeError):
    """The resolver won't load or won't run. **This is not the same thing as "this name
    doesn't resolve."**

    The first version collapsed both into `(None, False)`, so when `scripts/numbers.py`
    shadowed the standard-library `numbers` and crashed numpy, all 98 LOINC fields silently
    zeroed out and the spec was written anyway -- the output just looked like "mirobody's
    vocabulary is poor," not like a bug.
    **An environment failure must never be allowed to look like a measurement.**
    """


def resolver() -> object:
    """Get the resolver; raise if it can't be obtained rather than returning a stand-in that
    resolves nothing."""
    try:
        from mirobody.engine import resolve
    except Exception as e:                       # noqa: BLE001 - an environment issue must surface as-is
        raise ResolverUnavailable(f"failed to import mirobody.engine: {type(e).__name__}: {e}") from e
    try:
        probe = resolve("血红蛋白")               # a name that must always resolve, used to validate the environment
    except Exception as e:                       # noqa: BLE001
        raise ResolverUnavailable(f"resolve() call failed: {type(e).__name__}: {e}") from e
    if not getattr(probe, "resolved", False):
        raise ResolverUnavailable(
            "the probe name could not even be resolved, which means the vocabulary isn't "
            "installed properly (missing LFS data?), not that these indicator names truly don't resolve")
    return resolve


def resolve_loinc(resolve, name: str) -> tuple[str | None, bool]:
    """What this printed name resolves to. **Only** here does "doesn't resolve" get recorded as False."""
    try:
        result = resolve(name)
    except Exception:                            # noqa: BLE001 - a single name's failure just means it doesn't resolve
        return None, False
    return (result.loinc or None), bool(result.resolved)


def compare_with_corpus() -> None:
    """Hand-written ranges vs. the corpus's observed quantiles. For calibration, not a data source."""
    path = REPO / "library" / "synth_spec.json"
    if not path.is_file():
        print("(no library/synth_spec.json, skipping reconciliation)")
        return
    observed = json.loads(path.read_text(encoding="utf-8"))["indicators"]
    print(f"\n{'indicator':<22}{'hand-written range':<22}{'corpus p05/p50/p95':<28}{'flag'}")
    checked = flagged = 0
    for item in INDICATORS:
        names = [item["zh"], *item["name_variants"]]
        hit = next((observed[n] for n in names if n in observed), None)
        if not hit or not hit.get("numeric") or hit["numeric"].get("n", 0) < 5:
            continue
        ref = item["reference"]
        if not ref or ref[0] not in ("range", "range_sex", "upper", "lower"):
            continue
        checked += 1
        num = hit["numeric"]
        # This must be branches, not a dict literal: a dict literal would evaluate every
        # branch, so ("upper", 8.0) would also try ref[2][0] and raise TypeError on the spot.
        if ref[0] == "range":
            lo, hi = ref[1], ref[2]
        elif ref[0] == "range_sex":
            lo, hi = min(ref[1][0], ref[2][0]), max(ref[1][1], ref[2][1])
        elif ref[0] == "upper":
            lo, hi = 0.0, ref[1]
        else:
            lo, hi = ref[1], ref[1] * 3
        p50 = num["p50"]
        # Flag it when the median falls outside the range width by more than 1.5x: this isn't a
        # verdict, just a prompt to take a look.
        width = max(hi - lo, 1e-9)
        off = p50 < lo - 1.5 * width or p50 > hi + 1.5 * width
        flagged += off
        span = "{}–{}".format(lo, hi)
        seen_str = "{}/{}/{}".format(num["p05"], p50, num["p95"])
        mark = "<- way off, take a look" if off else ""
        print("{:<22}{:<22}{:<28}{}".format(item["zh"], span, seen_str, mark))
    print(f"\n{checked} items matched, of which {flagged} have a median clearly off from the hand-written range.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--compare", action="store_true", help="reconcile against the corpus's observed quantiles")
    ap.add_argument("--no-resolver", action="store_true",
                    help="build anyway when the resolver is unavailable (LOINC left blank); default is to error out")
    args = ap.parse_args()

    try:
        resolve = resolver()
    except ResolverUnavailable as e:
        if not args.no_resolver:
            raise SystemExit(
                f"resolver unavailable: {e}\n"
                f"This would make expect_resolvable false for all {len(INDICATORS)} items and write "
                f"that into the spec.\n"
                f"Rerun with mirobody's interpreter:\n"
                f"  ../mirobody/.venv/bin/python scripts/build_indicators.py --write\n"
                f"To build without a resolver anyway, pass --no-resolver (LOINC fields will be left blank).")
        print(f"(--no-resolver: {e}; LOINC fields left blank)")
        resolve = None

    resolved = unresolved = 0
    for item in INDICATORS:
        loinc, ok = resolve_loinc(resolve, item["zh"]) if resolve else (None, False)
        item["loinc"] = loinc
        item["expect_resolvable"] = ok
        resolved += ok
        unresolved += not ok

    panels: dict[str, int] = {}
    for item in INDICATORS:
        panels[item["panel"]] = panels.get(item["panel"], 0) + 1
    derived = sum(1 for i in INDICATORS if i["derived_from"])
    with_cvi = sum(1 for i in INDICATORS if i["cvi"])

    print(f"{len(INDICATORS)} indicators · {len(panels)} panels · {derived} identity-derived · "
          f"{with_cvi} with CVI")
    print(f"LOINC resolution: {resolved} resolved, {unresolved} unresolved"
          f" ({resolved / len(INDICATORS):.0%})")
    print("Panel composition: " + " · ".join(f"{k}:{v}" for k, v in sorted(panels.items())))
    if unresolved:
        print("Unresolved (material for abstention in evaluation):",
              ", ".join(i["zh"] for i in INDICATORS if not i["expect_resolvable"]))

    if args.compare:
        compare_with_corpus()

    if args.write:
        payload = {
            "_vocabulary_fields": ["zh", "en", "abbr", "unit", "name_variants",
                                   "value_kind", "panel", "panels"],
            "_source": "public-standard",
            "_note": (
                "参考区间来自 WS/T 404.1–.8 与 WS/T 405 及通行临床区间；"
                "生物学变异 CVI/CVG 来自 Westgard/EFLM 公开数据库；"
                "LOINC 为构建时向 mirobody.engine.resolve 查询所得，记录的是"
                "『这个打印名解析得出与否』，供评测区分召回与弃权。"
                "本文件不含任何来自真实语料的取值。"
            ),
            "_provenance": {
                "script": "scripts/build_indicators.py",
                "indicators": len(INDICATORS),
                "loinc_resolved": resolved,
                "reference_sources": sorted({i["reference_source"] for i in INDICATORS
                                             if i["reference_source"]}),
            },
            "indicators": INDICATORS,
            "panels": panels,
            "typical_center": TYPICAL_CENTER,
        }
        out = RESOURCES / "indicators.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
