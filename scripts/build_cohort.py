"""手写的队列设计 → `resources/cohort.json`。

    python3 scripts/build_cohort.py --write

这份是**临床设计**，不是代码细节：哪几种人、各占多少、慢病怎么进展、干预什么时候起效、
一次就诊开哪些单子。它放在 spec 里而不是埋在 `generator/person.py` 里，有两个理由：

1. 要能被不读 Python 的人审。"他汀让 LDL 降 38%、4–6 周起效"是一条可以被质疑的临床主张，
   它应该躺在一个能指着看的文件里。
2. 生成物里会出现这些名字（`iron_deficiency_anemia`、`checkup-center`），
   而隐私闸门的豁免只认 spec 里声明过的公共词汇——不声明的话，
   它们会被当成"可能是从真实语料搬来的字符串"报出来。那个报警是对的：
   闸门不该替任何未经声明的字符串背书。

`generator/person.py` 从这里读，不再自己持有一份。
"""

from __future__ import annotations

import argparse
import json
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
RESOURCES = REPO / "mirobody_gen" / "resources"


#: 医嘱 → 指标键。子集是手写的，因为"肾功能五项"不是一个可以从套餐名推出来的东西。
#:
#: 2026-09-29 审查后分了档：真实体检报告不会在基础套餐里印载脂蛋白、脂蛋白(a)、胱抑素 C、
#: 糖化白蛋白这些项目，也不会印呼吸频率（那是病房和门诊的生命体征）。
#: `checkup_basic / standard / premium` 是三档套餐的检验部分；科室与辅助检查在
#: `resources/narratives.json` 的 `packages` 里，同名对应。
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
    "glucose_ext": ["glu", "hba1c", "ga"],           # 内分泌门诊偶尔加做糖化白蛋白
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
    "vitals_clinic": ["temp", "pulse", "resp", "spo2", "sbp", "dbp", "weight"],   # 门诊病历的体格检查行
    "ecg": ["pulse", "pr_interval", "qrs_duration", "qtc", "qrs_axis"],
    "vitamin": ["vitd", "hcy"],
    # ── 2026-09-29 扩充：主流套餐里常见、原目录没有的组 ──
    # 出处：《健康体检基本项目专家共识（2022）》的"1+X"备选项目目录、各大体检中心公开套餐目录、
    # 日本人間ドック基本检查项目、Quest/Labcorp 公开样例报告。
    "liver_full": ["alt", "ast", "alp", "ggt", "tp", "alb", "glb", "ag_ratio",
                   "tbil", "dbil", "ibil", "tba", "che", "pa"],
    "pancreas": ["amy", "lps"],
    "renal_early": ["umalb", "uacr", "b2mg"],
    "glucose_full": ["glu", "hba1c", "insulin", "cpep"],
    "glucose_ogtt": ["glu", "ogtt2h", "hba1c"],            # 内分泌门诊的糖耐量
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
#: 五档套餐的检验部分。`entry` 是入职体检（用人单位不得要求乙肝项目，所以没有肝炎标志物），
#: `senior` 是老年人健康体检（国家基本公共卫生服务规范第三版的"健康体检"项目），
#: 其余三档是体检中心自费套餐的常见配置。
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

#: 原型 = (人数, 基线偏移, 年趋势, 常规复查的医嘱, 复查间隔月数, 事件剧本名)
#:
#: 基线偏移是**相对参考区间中心的倍数**：`{"glu": 1.25}` 表示这个人的空腹血糖
#: 比一般人高 25%。趋势是每年的相对变化。
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
     # 偏移打在**独立变量**上：缺铁是小细胞低色素性贫血，MCV 与 MCH 降、
     # 红细胞计数轻度降，于是血红蛋白与压积由恒等式自然跟着降。
     # 直接压血红蛋白是压不出自洽的血常规的。
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

#: 事件剧本。每条 = (名称, 类型, 效应, 健康影响, 强度)
#: 效应：指标键 → (相对幅度, 起效天数, 半衰期天数或 None)
SCRIPTS: dict[str, dict] = {
    "metformin": {
        "name": "开始二甲双胍", "type": "medication", "effect": "positive", "impact": "high",
        "effects": {"glu": (-0.18, 30, None), "hba1c": (-0.14, 90, None),
                    "weight_kg": (-0.03, 90, None)},
    },
    "statin": {
        "name": "开始他汀治疗", "type": "medication", "effect": "positive", "impact": "high",
        # LDL 的阶跃在 4–6 周内完成，这是他汀的药效学事实，也是"起效延迟"这个
        # 参数存在的理由：当天开药、当天复查是看不到变化的。
        "effects": {"ldl": (-0.38, 35, None), "chol": (-0.26, 35, None),
                    "tg": (-0.12, 35, None), "alt": (0.15, 60, 180)},
    },
    "iron": {
        "name": "开始补铁治疗", "type": "medication", "effect": "positive", "impact": "medium",
        # 同理：作用在 MCV/MCH/RBC 上，血红蛋白与压积跟着涨。
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

#: 每个人都可能遇到的一次性事件（与原型无关）。
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

#: 原型 → 诊断（SNOMED CT）。
#:
#: 这一层是为了接种子层留的**接缝**：PySynthea / Synthea 直接给 SNOMED 诊断，
#: 到时候这张表换成"诊断 → 指标偏移"的反向查表即可，原型这个概念会退役。
#: 现在先正着写，是因为我们自己的队列是从原型出发的。
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

#: 诊断标准：**值达到什么程度，就必须有对应的诊断**。
#:
#: 这是临床审计第五类检查的依据。它抓的是一种两边都犯过的不自洽：
#: 文件上印着 HbA1c 7.2%，而这个人的诊断集合里没有糖尿病——那在纸面上就是
#: 一个没被诊断的糖尿病人。ESL-Bench（LLM 生成值，不受诊断约束）与 Synthea
#: （模块里 {low,high} 均匀抽样）各自都会产生这种病历。
#:
#: `persistence` 是需要连续几次就诊都满足才算数。写 1 会让审计被单次噪声刷屏——
#: 参考区间本身就是 95% 区间，健康人偶尔越界是常态，不是漏诊。
#: 阈值全部取自各自的诊断指南，不是我拍的。
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
        # 这些名字会出现在 manifest 里，所以要声明成公共词汇，否则隐私闸门会（正确地）
        # 把它们当成来历不明的字符串报出来。
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
    print(f"原型 {len(ARCHETYPES)} 种 · 共 {total} 人 · 医嘱 {len(ORDERS)} 套 · "
          f"剧本 {len(SCRIPTS)} 个 · 随机事件 {len(INCIDENTS)} 个")
    for a in ARCHETYPES:
        print(f"  {a['name']:<24}{a['n']:>3} 人  复查 {'/'.join(a['followup']) or '（无）':<16}"
              f"剧本 {a['script'] or '（无）'}")
    if args.write:
        out = RESOURCES / "cohort.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n已写出 {out}")


if __name__ == "__main__":
    main()
