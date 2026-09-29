"""Clinical audit: do the generated values hold up on their own? Non-zero exit on findings.

临床审计：生成出来的数值自己站不站得住。非零退出码即有问题。

    mirobody-gen audit-clinical out/<build>/manifest.jsonl
    mirobody-gen audit-clinical --selftest       # 用内置的正反例验证审计本身

## 为什么审计要单独写一遍

生成器是用机理模型**算**出这些值的；审计是用**恒等式反查**。两边共用一行代码，
这道检查就只能确认生成器自己的盲区——这个项目为此付过代价（第一次隐私审计报告
"0 人名泄漏"，而库里正躺着一个真实医生姓名，因为审计调用的是过滤时用的同一个谓词）。

所以这里不 import 生成器（`mirobody_gen` 的非 audit 模块）里的东西，恒等式按检验医学的定义重新写：

| 检查 | 恒等式 | 容差 |
|---|---|---|
| BMI | 体重 / 身高² | ±0.2 |
| MCV | 红细胞压积×10 / 红细胞计数 | ±2 fL |
| MCH | 血红蛋白 / 红细胞计数 | ±1 pg |
| MCHC | 血红蛋白 / (压积/100) | ±8 g/L |
| 球蛋白 | 总蛋白 − 白蛋白 | ±0.6 g/L |
| 白球比 | 白蛋白 / 球蛋白 | ±0.06 |
| 间接胆红素 | 总胆红素 − 直接胆红素 | ±0.6 μmol/L |
| 分类绝对值 | 白细胞 × 百分比/100 | ±0.12 ×10⁹/L |
| 分类百分比之和 | = 100 | ±1.5 |
| LDL（未直接测定时） | 总胆固醇 − HDL − 甘油三酯/2.2 | ±0.25 mmol/L |

容差不是随手写的：它是**印刷有效位数**决定的。血红蛋白印成整数、压积印成一位小数，
两者相除的结果本来就带着 ±0.5% 的量化误差，MCHC 因此允许 ±8 g/L。
把容差收得比有效位数还紧，只会让审计报一堆"错误"，而每一条都是四舍五入。

## 另外四类检查

- **标记与参考范围一致**：印了 `↑` 就必须真的超上限。这是真实语料里的高频陷阱
  （`flag.contradicts_reference`），但那是**别人的报告**里的错；我们自己生成的必须自洽，
  除非那一份文件**显式注入**了这个陷阱——注入过的会在 manifest 里写明，审计据此放行。
- **人口学冲突**：男性不做 CA125、女性不做 PSA；年龄与参考区间分组要对得上。
- **生理硬边界**：钠 1141 mmol/L 这种值在真实语料的聚合里出现过（单位混了），
  但它不该从我们这里出去。硬边界按"人活着时可能的极值"设，比参考区间宽得多。
- **纵向合理性**：看的是超出参考变化值 RCV 的**比例**（理论约 5%），不是逐对报错；
  逐对只抓超过 3×RCV 且时间线上无事件解释的。
- **诊断与值互相解释得通**：值连续达到诊断标准却没有对应诊断，是漏诊；
  反过来（有诊断但指标从未异常）只报告不判定——控制良好的慢病本就如此。
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

PACKAGE = pathlib.Path(__file__).resolve().parents[1]
RESOURCES = PACKAGE / "resources"
#: 源码检出的根目录。只有需要检出才有的东西（参考集、缓存、产物）才用它；安装后的包里没有这些。
REPO = PACKAGE.parent

#: 分析变异。真实实验室的 CVA 因项目而异，这里取一个统一的保守值：
#: RCV 用它只是为了判"这个跳变需不需要解释"，不是为了复现某台仪器的精密度。
CVA_DEFAULT = 3.0

#: (键, 说明, 依赖的键, 计算式, 容差)
IDENTITIES: list[tuple[str, str, tuple[str, ...], object, float]] = [
    ("bmi", "体重指数 = 体重 / 身高²", ("weight", "height"),
     lambda v: v["weight"] / (v["height"] / 100) ** 2, 0.2),
    ("mcv", "平均红细胞体积 = 压积×10 / 红细胞计数", ("hct", "rbc"),
     lambda v: v["hct"] * 10 / v["rbc"], 2.0),
    ("mch", "平均红细胞血红蛋白量 = 血红蛋白 / 红细胞计数", ("hgb", "rbc"),
     lambda v: v["hgb"] / v["rbc"], 1.0),
    ("mchc", "平均红细胞血红蛋白浓度 = 血红蛋白 / (压积/100)", ("hgb", "hct"),
     lambda v: v["hgb"] / (v["hct"] / 100), 8.0),
    ("glb", "球蛋白 = 总蛋白 − 白蛋白", ("tp", "alb"),
     lambda v: v["tp"] - v["alb"], 0.6),
    ("ag_ratio", "白球比 = 白蛋白 / 球蛋白", ("alb", "glb"),
     lambda v: v["alb"] / v["glb"], 0.06),
    ("ibil", "间接胆红素 = 总胆红素 − 直接胆红素", ("tbil", "dbil"),
     lambda v: v["tbil"] - v["dbil"], 0.6),
    ("neut_abs", "中性粒细胞绝对值 = 白细胞 × 百分比/100", ("wbc", "neut_pct"),
     lambda v: v["wbc"] * v["neut_pct"] / 100, 0.12),
    ("lymph_abs", "淋巴细胞绝对值 = 白细胞 × 百分比/100", ("wbc", "lymph_pct"),
     lambda v: v["wbc"] * v["lymph_pct"] / 100, 0.12),
    ("mono_abs", "单核细胞绝对值 = 白细胞 × 百分比/100", ("wbc", "mono_pct"),
     lambda v: v["wbc"] * v["mono_pct"] / 100, 0.12),
    ("eos_abs", "嗜酸性粒细胞绝对值 = 白细胞 × 百分比/100", ("wbc", "eos_pct"),
     lambda v: v["wbc"] * v["eos_pct"] / 100, 0.12),
    ("baso_abs", "嗜碱性粒细胞绝对值 = 白细胞 × 百分比/100", ("wbc", "baso_pct"),
     lambda v: v["wbc"] * v["baso_pct"] / 100, 0.12),
    ("nonhdl", "非HDL胆固醇 = 总胆固醇 − HDL", ("chol", "hdl"), lambda v: v["chol"] - v["hdl"], 0.02),
    ("fpsa_ratio", "游离/总 PSA", ("fpsa", "psa"), lambda v: v["fpsa"] / v["psa"], 0.03),
    ("pgr", "胃蛋白酶原比值 = PGⅠ / PGⅡ", ("pg1", "pg2"), lambda v: v["pg1"] / v["pg2"], 0.15),
    ("fev1", "一秒量 = 用力肺活量 × 一秒率", ("fvc", "fev1_fvc"), lambda v: v["fvc"] * v["fev1_fvc"] / 100, 0.05),
]

#: 生理硬边界：活人身上可能出现的极值，比参考区间宽得多。
#: 越过它的值说明生成器算错了或单位串了，不是"病得重"。
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
    # 2026-09-29 扩充
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

#: 允许没有硬边界的指标，以及为什么。
#:
#: 这张豁免表存在的理由是：`test_every_quantitative_indicator_has_a_hard_limit`
#: 要求每个定量指标都有边界。腰围 44.2 cm 配 104 kg 的体重能溜出去，
#: 正是因为 waist 当时不在 HARD_LIMITS 里——**缺一项是静默的**，
#: 检查表不完备时，"0 findings" 既可能是没问题，也可能是没查。
NO_HARD_LIMIT: dict[str, str] = {
    "ga": "糖化白蛋白的极值缺公认上界",
    "tba": "总胆汁酸在淤胆时可以极高，没有有意义的上界",
    "apoa1": "载脂蛋白极值少见报道", "apob": "同上", "lpa": "同上",
    "ferritin": "急性期反应物，炎症时可达数万",
    "serum_iron": "中毒时极高", "b12": "补充后可极高", "folate": "同上",
    "vitd": "大剂量补充可极高",
    "afp": "肿瘤标志物无上界", "cea": "同上", "psa": "同上", "ca125": "同上",
    "tpoab": "抗体滴度无上界",
    "urine_sg": "尿比重范围窄且已由参考区间约束",
    "urine_ph": "同上", "urine_rbc": "镜检计数无上界", "urine_wbc": "同上",
    "pr_interval": "心电数值另有专门口径", "qrs_duration": "同上",
    "qtc": "同上", "qrs_axis": "同上（可为负）",
    # 2026-09-29 扩充的肿瘤标志物与抗体、急性期反应物：无上界
    "ca199": "肿瘤标志物无上界", "ca153": "同上", "ca724": "同上", "cyfra211": "同上", "nse": "同上", "scc": "同上",
    "fpsa": "同上", "ddimer": "血栓时可极高", "umalb": "肾病综合征时可极高", "uacr": "同上",
    "hstni": "心梗时可高数千倍", "ntprobnp": "心衰时可极高", "tgab": "抗体滴度无上界", "tg_protein": "肿瘤时可极高",
    "rf": "同上", "aso": "同上", "ccp": "同上",
}

#: 性别专属项目。
SEX_ONLY = {"psa": "male", "fpsa": "male", "fpsa_ratio": "male", "ca125": "female", "ca153": "female",
            "hpv16": "female", "hpv18": "female", "hpv_other": "female", "tct": "female"}

#: 百分比之和必须接近 100 的那一组。
DIFFERENTIAL = ("neut_pct", "lymph_pct", "mono_pct", "eos_pct", "baso_pct")


class Finding:
    def __init__(self, kind: str, where: str, detail: str):
        self.kind, self.where, self.detail = kind, where, detail

    def __str__(self) -> str:
        return f"[{self.kind}] {self.where}: {self.detail}"


def numeric_rows(record: dict) -> dict[str, float]:
    """这份文件里可用于恒等式检查的数值行：key → 数值。"""
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
            findings.append(Finding("恒等式", where, f"{label}：分母为 0"))
            continue
        actual = values[key]
        if abs(actual - expected) > tolerance:
            # 注入过"值被改坏"类陷阱的文件，恒等式本来就该对不上——那是故意的。
            if injected & {"value.missing_decimal_point", "value.decimal_comma",
                           "value.space_in_number", "ocr.other", "unit.ocr_corrupted"}:
                continue
            findings.append(Finding(
                "恒等式", where,
                f"{label}：印的是 {actual:g}，按式子应为 {expected:.4g}"
                f"（差 {abs(actual - expected):.4g}，容差 {tolerance}）"))
    present = [k for k in DIFFERENTIAL if k in values]
    if len(present) == len(DIFFERENTIAL):
        total = sum(values[k] for k in present)
        if abs(total - 100) > 1.5:
            findings.append(Finding("恒等式", where, f"白细胞分类百分比之和 {total:.2f}，应为 100"))
    return findings


def check_limits(record: dict, values: dict[str, float]) -> list[Finding]:
    where = record.get("file", "?")
    findings = []
    for key, value in values.items():
        bounds = HARD_LIMITS.get(key)
        if bounds and not (bounds[0] <= value <= bounds[1]):
            findings.append(Finding(
                "生理边界", where,
                f"{key} = {value:g}，超出活人可能范围 {bounds[0]}–{bounds[1]}"))
    return findings


def check_demographics(record: dict, values: dict[str, float]) -> list[Finding]:
    where = record.get("file", "?")
    sex = (record.get("person") or {}).get("sex")
    findings = []
    for key, only in SEX_ONLY.items():
        if key in values and sex and sex != only:
            findings.append(Finding("人口学", where, f"{sex} 出现了 {only} 专属项目 {key}"))
    age = (record.get("person") or {}).get("age")
    if isinstance(age, (int, float)) and not (0 < age < 120):
        findings.append(Finding("人口学", where, f"年龄 {age} 不合理"))
    return findings


def check_flags(record: dict) -> list[Finding]:
    """印了 ↑ 就得真的超上限——除非这份文件显式注入了"标记与参考矛盾"这个陷阱。"""
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
                                    f"{row.get('key')} 标为 high，但 {value:g} ≤ 上限 {hi:g}"))
        if status == "low" and isinstance(lo, (int, float)) and value >= lo:
            findings.append(Finding("标记", where,
                                    f"{row.get('key')} 标为 low，但 {value:g} ≥ 下限 {lo:g}"))
    return findings


def check_longitudinal(records: list[dict], cvi: dict[str, float],
                       decimals: dict[str, int] | None = None,
                       derived: set[str] | None = None) -> list[Finding]:
    """纵向合理性。**看的是超出率，不是逐对报错。**

    第一版逐对判：相邻两次的变化超过参考变化值 RCV 就报一条。在 601 份记录上报了 586 条，
    而这恰恰是 RCV 的定义决定的——RCV = 1.96×√2×CV 是差值的 **95% 界**，
    按构造就该有约 5% 的相邻对超出它。把一条预期的统计尾巴逐个列出来，
    审计就变成了噪声发生器，真正的问题会淹在里面。

    改成两条：

    * **逐对**只抓**离谱**的：超过 3×RCV 且时间线上没有事件解释。那已经不是生物学变异，
      是生成器算错了或单位串了。
    * **总体**看超出率：短间隔（<120 天）的相邻对里，超过 RCV 的比例应当在 5% 附近。
      明显偏高说明在注入无法解释的跳变，明显偏低说明抖动给小了、纵向序列假得像直线。

    长间隔（≥120 天）只统计不判定：RCV 描述的是稳态下的短期复测，
    一年之隔的两次之间还有季节、体重、年龄的真实漂移，用 RCV 去卡它是用错了工具。
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
                    continue          # 计算量的变异是传播来的，见 load_derived()
                cv = cvi.get(key)
                if not cv:
                    continue
                # 印刷分辨率以内的差异不作数。嗜碱性粒细胞印一位小数，真值 0.05
                # 时会在 0.0 与 0.1 之间跳——那是取整，不是生物学变化，
                # 而按比例算它是 100%（或除零）。
                step = 10 ** -decimals.get(key, 2)
                if abs(value - previous[1]) <= 1.5 * step:
                    continue
                # 两个值都贴着零时（嗜碱性粒细胞 0.1 vs 0.5），对数比被量化步长主导：
                # 0.1 的真值可以是 0.05–0.15，比值因此在 3–10 之间乱跳。离零不到 5 步的不判。
                if min(value, previous[1]) < 5 * step:
                    continue
                rcv = 2.77 * math.sqrt(cv ** 2 + CVA_DEFAULT ** 2) / 100
                # 在**对数尺度**上比，不要用 (新-旧)/旧。
                # 后者在旧值偏小时会炸：直接胆红素 0.9→4.6 算出 411%，而反过来
                # 4.6→0.9 只有 80%——同一对数据，换个顺序就换一个结论。
                # 对数比是对称的，也是检验医学对高变异项目的通行做法。
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
                        f"{key} 从 {previous[1]:g} 变到 {value:g}"
                        f"（对数比 {change:.2f}，超过 3×ln(1+RCV) {3 * threshold:.2f}），"
                        f"时间线上没有事件解释"))

    if short_pairs >= 50:
        rate = short_exceed / short_pairs
        print(f"（纵向：短间隔相邻对 {short_pairs} 组，超 RCV 的占 {rate:.1%}，"
              f"理论值约 5%；长间隔 {long_pairs} 组，超 {long_exceed / max(long_pairs, 1):.1%}，"
              f"只统计不判定）")
        if not 0.01 <= rate <= 0.15:
            findings.append(Finding(
                "纵向", "全体",
                f"短间隔超 RCV 的比例 {rate:.1%} 落在 [1%, 15%] 之外——"
                f"偏高说明在注入无法解释的跳变，偏低说明纵向序列抖得太少"))
    return findings


def _days_between(a: str, b: str) -> int | None:
    from datetime import date

    try:
        return abs((date.fromisoformat(b) - date.fromisoformat(a)).days)
    except ValueError:
        return None


def load_decimals() -> dict[str, int]:
    """指标键 → 印刷小数位。用来判断"这点差异是不是只是取整"。"""
    path = RESOURCES / "indicators.json"
    if not path.is_file():
        return {}
    spec = json.loads(path.read_text(encoding="utf-8"))
    return {i["key"]: i.get("decimals", 2) for i in spec["indicators"]}


def load_criteria() -> list[dict]:
    """诊断标准。见 `scripts/build_cohort.py` 的 DIAGNOSTIC_CRITERIA。"""
    path = RESOURCES / "cohort.json"
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("diagnostic_criteria", [])


def check_diagnosis_coherence(records: list[dict], criteria: list[dict]) -> list[Finding]:
    """值与诊断集合必须互相解释得通。

    这一类抓的是一种在两个现成数据集里都存在的不自洽：文件上印着 HbA1c 7.2%，
    而这个人的诊断集合里没有糖尿病——纸面上就是一个没被诊断的糖尿病人。
    ESL-Bench 的值由 LLM 生成、不受诊断约束；Synthea 的值是模块 JSON 里
    `{low, high}` 均匀抽样，与它自己给的 Condition 无关。我们不该继承这个。

    两个方向都查，但**严厉程度不同**：

    * 值达标而无诊断 → **findings**。这是漏诊，纸面上说不通。
    * 有诊断而值从未异常 → **只报告不判定**。控制良好的慢病本来就可以指标全正常
      （降压药吃着、血压 128/82），把它判成错误等于要求"有病的人必须看起来有病"。

    `persistence` 要求连续多次就诊都达标才算数。写 1 会被单次噪声刷屏——
    参考区间本身是 95% 区间，健康人偶尔越界是常态，不是漏诊。
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
                        f"连续 {hits} 次满足「{rule['condition']['display']}」的诊断标准"
                        f"（{rule['source'].split('：')[-1]}），但诊断集合里没有它"))
                    break
            if code in have and hits == 0:
                missing_the_other_way += 1

    if missing_the_other_way:
        print(f"（诊断：有 {missing_the_other_way} 例带诊断但相关指标从未达标——"
              f"控制良好的慢病本就如此，只报告不判定）")
    return findings


def _compare(value, op: str, threshold: float) -> bool:
    if value is None:
        return False
    return {">=": value >= threshold, ">": value > threshold,
            "<=": value <= threshold, "<": value < threshold}.get(op, False)


def load_derived() -> set[str]:
    """由恒等式算出来的指标键。

    它们要**排除在 RCV 检查之外**：公开的 CVI 描述的是直接测定法的变异，
    而 Friedewald 算出来的 LDL 继承的是总胆固醇、HDL、甘油三酯三次测量的传播方差，
    必然大于 LDL 自己的 CVI。拿后者去卡前者，是用错了数——
    报出来的每一条都不是缺陷，而是"计算量本来就比直接测定抖"。
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


# ── 自检：审计自己会不会漏 ───────────────────────────────────────
#: 审计的第一个用户是审计自己。一份干净记录必须 0 findings，
#: 一份**每种错都犯一次**的记录必须每种都被抓到。没有这个自检，
#: "审计通过"既可能意味着数据没问题，也可能意味着审计什么都没查。
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
        {"key": "bmi", "canonical_value": 27.0},          # 恒等式：应为 22.0
        {"key": "hgb", "canonical_value": 140.0},
        {"key": "hct", "canonical_value": 42.0},
        {"key": "mchc", "canonical_value": 300.0},        # 恒等式：应为 333
        {"key": "na", "canonical_value": 1141.0},         # 生理边界
        {"key": "psa", "canonical_value": 1.2},           # 人口学：女性做了 PSA
        {"key": "glu", "canonical_value": 5.0, "status": "high",
         "ref_low": 3.9, "ref_high": 6.1},                # 标记：high 但没超上限
        {"key": "neut_pct", "canonical_value": 60.0},
        {"key": "lymph_pct", "canonical_value": 30.0},
        {"key": "mono_pct", "canonical_value": 6.0},
        {"key": "eos_pct", "canonical_value": 3.0},
        {"key": "baso_pct", "canonical_value": 9.0},      # 分类之和 108
    ],
}


def selftest() -> int:
    clean = audit([CLEAN])
    print(f"干净记录：{len(clean)} 处 findings" + ("" if not clean else " ← 不该有"))
    for f in clean:
        print("   ", f)

    broken = audit([BROKEN])
    kinds = {f.kind for f in broken}
    print(f"\n坏记录：{len(broken)} 处 findings，覆盖 {sorted(kinds)}")
    for f in broken:
        print("   ", f)

    expected = {"恒等式", "生理边界", "人口学", "标记"}
    missing = expected - kinds
    ok = not clean and not missing
    print(f"\n自检{'通过' if ok else '未通过'}"
          + (f"：漏掉了 {sorted(missing)}" if missing else ""))
    return 0 if ok else 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest", nargs="?", help="out/<build>/manifest.jsonl")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        raise SystemExit(selftest())
    if not args.manifest:
        ap.error("要么给一个 manifest，要么 --selftest")

    path = pathlib.Path(args.manifest)
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    findings = audit(records)
    by_kind: dict[str, int] = {}
    for f in findings:
        by_kind[f.kind] = by_kind.get(f.kind, 0) + 1
        print(f)
    print(f"\n{len(records)} 份记录 · {len(findings)} 处 findings"
          + (f"（{', '.join(f'{k}:{v}' for k, v in by_kind.items())}）" if findings else ""))
    sys.exit(1 if findings else 0)


if __name__ == "__main__":
    main()
