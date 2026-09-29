"""Value model: what a person's indicator reads on a given day (interval centre, baseline, trend, events, noise, identities).

取值模型：一个人在某一天的某个指标印出来是多少。

一条读数 =
`参考区间中心 × 个体基线（CVG）× 慢病趋势 × 事件效应 × 个体内变异（CVI）× 分析变异（CVA）`，
然后按印刷有效位数取整。**派生指标不走这条路**，它们由恒等式从已取整的原值算出来
（见 `derive`），这样报告上印出来的数字彼此自洽——`audit/clinical.py` 正是这么反查的。

三件刻意如此的事：

1. **取值不来自语料。** 中心来自 `resources/indicators.json` 的参考区间（公开标准），
   抖动来自 Westgard 的生物学变异。真实语料只在事后对账时出现（`build_indicators --compare`）。
2. **先取整再派生。** 若先算 MCHC 再取整，印出来的 `血红蛋白/压积` 与印出来的 MCHC 会差
   一个量化误差，审计就会报一堆并非错误的"恒等式不符"。真实仪器也是先出整数再算派生量。
3. **事件效应是可计算的真值。** 归因问题（"为什么 LDL 降了"）的答案是注入的那条事件本身，
   不需要事后解释模型。
"""

from __future__ import annotations

import math
import random
from datetime import date

from . import spec

#: 没有 CVI 数据时的保守个体内变异（%）。宁可抖得小一点，也不要凭空造出"病情波动"。
DEFAULT_CVI = 5.0
#: 分析变异（%）。所有项目取同一个值：它在这里只负责"复查不会一模一样"，
#: 不承担复现某台仪器精密度的责任。
CVA = 3.0
#: 例外：体温的 3% 是 ±1℃——门诊病历上会印出 35.7℃ 与 38.5℃ 的健康人。体温计的精度是 0.1℃ 量级。
CVA_OVERRIDE = {"temp": 0.3}
#: 有物理上界的量：饱和度、比值百分数、踝臂指数。对数正态噪声会越过上界，真实仪器不会。
CEILING = {"spo2": 100.0, "fev1_fvc": 100.0, "fvc_pct": 140.0, "abi_l": 1.6, "abi_r": 1.6}

#: 没有参考区间的指标，中心值从这里取（身高体重另有处理）。
FALLBACK_CENTER: dict[str, float] = {
    "height": 168.0, "weight": 65.0,
}


def center_of(key: str, sex: str) -> float:
    """参考区间的"正常人落点"。

    区间型取中点。单侧的（总胆固醇 <5.18、HDL >1.04）取 spec 里手写的人群中心值——
    第一版用的是"上限 × 0.55"这样的固定系数，那是错的：总胆固醇的人群中位数占上限的
    89%，CRP 只占 19%，同一个系数不可能同时对。中心值查不到时才退回固定系数，
    并且这种情况会在 `--stats` 的异常率里露出来。
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
    """个体基线偏移（相对倍数），来自个体间变异 CVG。一个人一辈子不变。

    与测量噪声同理走对数正态，并把 z 截断在 ±2 以内：正态尾巴会造出"天生血钠 180"
    这种人，而那种值应该来自疾病或事件，不该来自个体差异。
    """
    item = spec.indicators()[key]
    cvg = item.get("cvg") or item.get("cvi") or DEFAULT_CVI
    sigma = cvg / 100
    z = max(-2.0, min(2.0, rng.gauss(0, 1)))
    return math.exp(sigma * z - sigma ** 2 / 2)


def event_factor(person, key: str, when: date) -> tuple[float, list[str]]:
    """事件在这一天对这个指标的合计效应，以及是哪些事件。

    单个事件的形状：起效前为 0，起效期内线性爬升到满幅，事件结束后按半衰期衰减。
    多个事件**相加**（不是相乘）：两件都让血糖升高的事，合起来不该变成平方关系。
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
                decay = 1.0                      # 长期习惯：不衰减
            else:
                decay = 0.5 ** (after_end / max(decay_days, 1))
        contribution = magnitude * ramp * decay
        if abs(contribution) > 1e-6:
            factor += contribution
            names.append(event.name)
    return max(factor, 0.05), names


def trend_factor(person, key: str, when: date) -> float:
    """慢病进展：按年复利。`trend[key]=0.06` 表示这个指标每年涨 6%。"""
    rate = person.trend.get(key)
    if not rate:
        return 1.0
    # 以"这个人被纳入队列的那一年"为起点，而不是出生年：慢病是从某个时点开始进展的。
    years = max(0.0, (when - person.events[0].start).days / 365.25) if person.events else 0.0
    return (1.0 + rate) ** years


def weight_at(person, when: date) -> float:
    """按锚点线性插值的体重。体重是 BMI 的输入，所以它必须先于 BMI 存在。"""
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


def measure(rng: random.Random, person, key: str, when: date) -> float:
    """一次测量。"""
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
    # **对数正态**，不是加性正态。直接胆红素的 CVI 是 36.8%，加性模型下
    # `base × (1 + 0.37ε)` 在 ε=-2.6 时给出 0.04 倍基线——一个趋近于零的胆红素。
    # 生物学量是非负的、右偏的，乘性噪声才是它的形状。
    # 减去 σ²/2 是为了保持均值仍等于 base（否则中心会被系统性抬高）。
    value = base * math.exp(rng.gauss(0, sigma) - sigma ** 2 / 2)
    return min(max(value, 0.0), CEILING.get(key, math.inf))


def derive(values: dict[str, float], person, when: date) -> dict[str, float]:
    """由恒等式算出派生指标。输入必须是**已按印刷位数取整**的原值。

    顺序有依赖：球蛋白要先于白球比，分类百分比要先于绝对值。
    """
    out: dict[str, float] = {}

    def has(*keys: str) -> bool:
        return all(k in values for k in keys)

    if has("weight", "height"):
        out["bmi"] = values["weight"] / (values["height"] / 100) ** 2
    # 血液学的因果方向：独立变量是**红细胞计数、平均体积、平均血红蛋白浓度**，
    # MCH = MCHC×MCV，血红蛋白与压积再由它们相乘得到。
    #
    # 为什么 MCHC 是原生而 MCH 是派生：MCHC 的个体间变异只有 2.8%、个体内 1.7%，
    # 是三者里最紧的——它紧正是因为血红蛋白与压积同向走。把 MCH 当原生、
    # MCHC 当派生，MCHC 的波动就变成 MCH 与 MCV 两个独立噪声之比，必然宽于 1.7%，
    # 于是每隔几百份就会飘出一个 403 g/L（上限 400）。改成这个方向之后，
    # 印出来的 MCHC 波动**就是**它自己的 CVI。
    #
    # 第一版把血红蛋白与压积当成独立变量各自抽样，结果 MCHC 落在 300–424 之间
    # （真实区间 316–354），审计直接判出"活人不可能"。根因不是抖动给大了，
    # 是**独立变量选错了**：MCHC 的 CVI 只有 1.7%，正因为血红蛋白与压积是同向走的。
    # 两个 CVI 各 3% 的量独立相除，得到的比值波动必然大于 1.7%。
    if has("mchc", "mcv"):
        out["mch"] = values["mchc"] * values["mcv"] / 1000
    if has("rbc") and "mch" in out:
        out["hgb"] = values["rbc"] * out["mch"]
    if has("rbc", "mcv"):
        out["hct"] = values["rbc"] * values["mcv"] / 10
    if has("tp", "alb"):
        out["glb"] = values["tp"] - values["alb"]
    # 直接胆红素是总胆红素的**组分**，不是独立测量：它必须由总胆红素乘一个比例得到。
    # 独立抽的后果是 dbil 可以超过 tbil，于是印出 −2.1 的间接胆红素。
    # 这是"独立变量选错了"的第二例（第一例是红细胞指数）。
    # 比例用个体基线定，范围掐在 0.08–0.45：健康人的直接胆红素约占总量两到三成。
    if has("tbil"):
        fraction = min(0.45, max(0.08, 0.25 * person.baseline.get("dbil", 1.0)))
        out["dbil"] = values["tbil"] * fraction
        out["ibil"] = values["tbil"] - out["dbil"]
    if has("chol", "hdl", "tg"):
        # Friedewald。甘油三酯 > 4.5 mmol/L 时这个式子不成立，真实实验室会改用直接法，
        # 所以这里也改成"直接测"——把它当恒等式算下去会印出一个负的 LDL。
        if values["tg"] <= 4.5:
            out["ldl"] = max(values["chol"] - values["hdl"] - values["tg"] / 2.2, 0.3)
    if has("crea"):
        out["egfr"] = ckd_epi_2021(values["crea"], person.age_at(when), person.sex)
    if has("chol", "hdl"):
        out["nonhdl"] = values["chol"] - values["hdl"]
    if has("psa"):
        # 游离 PSA 是总 PSA 的一个组分，比例按人定（0.08–0.45）；良性时比例高
        out["fpsa"] = values["psa"] * _person_factor(person, "fpsa", 0.08, 0.45)
    if has("glu"):
        # 餐后 2 小时血糖 = 空腹 × 个体餐后系数；糖代谢受损的人系数大
        out["ogtt2h"] = values["glu"] * _person_factor(person, "ogtt2h", 1.05, 1.9)
    if has("fvc", "fev1_fvc"):
        out["fev1"] = values["fvc"] * values["fev1_fvc"] / 100
    if has("fvc_pct", "fev1_fvc"):
        out["fev1_pct"] = values["fvc_pct"] * values["fev1_fvc"] / 82
    if has("bapwv_l"):
        out["bapwv_r"] = values["bapwv_l"] * _person_factor(person, "bapwv_r", 0.94, 1.06)
    return out


def _person_factor(person, key: str, lo: float, hi: float) -> float:
    """一个人一辈子不变的个体系数（游离 PSA 占比、左右侧差异这类）。"""
    return random.Random(f"factor:{person.person_id}:{key}").uniform(lo, hi)


def derive_second_pass(values: dict[str, float], person=None) -> dict[str, float]:
    """依赖前一轮派生结果的那些（白球比要等球蛋白，腰围要等 BMI）。"""
    out: dict[str, float] = {}
    if person is not None and "bmi" in values:
        # 见 resources/indicators.json 的 derived_from：按 BMI 线性近似，男女截距不同。
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
    """CKD-EPI 2021（无种族项）。肌酐由 μmol/L 换算成 mg/dL。"""
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
    """白细胞分类百分比强制合到 100。

    不这么做的话，五个独立抽样的百分比合起来是 97 或 104，而真实报告永远是 100——
    这是最容易被忽略、又最容易被审计抓到的一处不自洽。
    归一后把残差补给中性粒细胞（占比最大，补上去看不出来）。
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
    """定性项：按目录里的人群阳性率（默认 3%）出阳性，受事件影响时更容易；阳性时怎么印也由目录定
    （尿常规是 +/++/弱阳性，抗体类是"阳性"，宫颈细胞学是 ASC-US/LSIL）。

    黏性：乙肝表面抗体、幽门螺杆菌抗体这类结果一个人多年不变，所以阳性与否按人抽一次，
    不按每次就诊抽——否则同一个人每年的乙肝五项会来回翻转。"""
    item = spec.indicators()[key]
    sticky = random.Random(f"qual:{person.person_id}:{key}")
    base_rate = item.get("positive_rate")
    if base_rate is None:
        base_rate = 0.03
        roll = rng.random()                      # 尿常规这类随状态变的：每次就诊抽
    else:
        roll = sticky.random()                   # 抗体这类不变的：按人抽
    factor, _ = event_factor(person, key, when)
    positive_chance = base_rate + max(0.0, factor - 1.0) * 0.5
    if roll < positive_chance:
        values = item.get("positive_values") or [["+", 6], ["++", 2], ["弱阳性", 1]]
        return sticky.choices([v for v, _ in values], weights=[w for _, w in values])[0]
    ref = item.get("reference")
    return str(ref[1]) if ref and ref[0] == "qualitative" else "阴性"


def categorical_value(person, key: str) -> str:
    """分类项（血型）：一个人一辈子一个值。"""
    item = spec.indicators()[key]
    rng = random.Random(f"cat:{person.person_id}:{key}")
    values = item["categories"]
    return rng.choices([v for v, _ in values], weights=[w for _, w in values])[0]
