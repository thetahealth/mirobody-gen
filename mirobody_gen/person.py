"""Cohort engine: synthetic people, their timelines, and what each visit orders.

队列引擎：60 个虚拟人、他们的时间线、以及每次就诊做了哪些项目。

## 队列是按患病率配比的，不是均匀采样

均匀采样会让"异常率"这个可校准量失去意义：真实语料里总胆固醇的异常率是 27%、
甘油三酯 31%、尿酸 14%，而血小板计数接近 0。这些比例是人群构成的结果，
不是取值模型的参数。所以这里先定人群，再让指标自己长出来。

## 检验申请是"套餐"，不是"全部指标"

ESL-Bench 的每次 exam 带 193 个指标（它不关心文件形态，这没问题）。
真实报告不是这样：体检报告书印几十项，一张生化单印十几项，一张血沉单只有一行。
所以这里按**医嘱**组织（肝功能/肾功能/血脂/血糖/血常规/尿常规…），
每次就诊开一到三张单子——这直接决定了生成语料的 `rows_per_document` 分布
能不能对上实测的 p50=6 / p95=36。
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from . import profile, spec
from .model import Encounter, Event, Person, Reading
from .physiology import (
    derive, derive_second_pass, measure, normalize_differential,
    categorical_value, person_baseline, qualitative_value,
)

#: 语料的时间窗口终点。用字面日期而不是 `date.today()`：
#: 同一个 seed 在不同日期跑必须产出同一批文件，否则回归比对就没有基线。
CORPUS_END = date(2026, 9, 1)

# 队列设计（医嘱、原型、剧本、随机事件、地区）全部在 `resources/cohort.json` 里，
# 由 `scripts/build_cohort.py` 手写生成。这里只负责用它们造人，不持有第二份定义——
# 同一张表存两处，早晚有一处过时，而过时的那一处不会报错，只会悄悄生成错的人。
_COHORT = spec.cohort()
ORDERS: dict[str, list[str]] = _COHORT["orders"]
ARCHETYPES: list[dict] = _COHORT["archetypes"]
SCRIPTS: dict[str, dict] = _COHORT["scripts"]
INCIDENTS: list[dict] = _COHORT["incidents"]
REGIONS: list[str] = _COHORT["regions"]


def build_cohort(seed: int, size: int | None = None) -> list[Person]:
    """一整个队列。同 seed 必然同结果。"""
    rng = random.Random(f"cohort:{seed}")
    full = sum(a["n"] for a in ARCHETYPES)
    # `--people N` 要**按比例跨原型缩小**，不能取前 N 个：取前 N 个会得到一队健康人，
    # 冒烟测试于是永远看不到慢病轨迹、干预事件和异常值——而那才是要测的东西。
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
    # 缺铁性贫血以女性为主，这是流行病学事实；不这么做的话，性别分层的参考区间
    # 就得不到检验——男女用同一套区间时，分层写得对不对根本看不出来。
    if archetype["name"] == "iron_deficiency_anemia":
        sex = "female" if rng.random() < 0.85 else "male"
    age = int(rng.triangular(20, 79, 45 if archetype["name"] == "healthy" else 55))
    birth_year = CORPUS_END.year - age
    height = rng.gauss(172 if sex == "male" else 160, 6.5)

    baseline: dict[str, float] = {}
    for key in spec.indicators():
        baseline[key] = person_baseline(rng, key, sex)
    # 红细胞指数的潜变量是 MCV 与 MCHC（各自用自己的 CVG），MCH 由二者决定——
    # 这三个公开的 CVG（4.85%、2.8%、5.2%）只有在 MCH = MCHC × MCV 时才互相自洽。
    # 派生发生在 physiology.derive 里，这里不需要再耦合基线。

    for key, multiplier in archetype["shift"].items():
        if key == "weight_kg":
            continue
        baseline[key] = baseline.get(key, 1.0) * multiplier

    # 体重：由身高、BMI 目标与原型偏移决定，再给一条随年份缓慢变化的轨迹。
    bmi_target = rng.gauss(23.0, 2.6) * archetype["shift"].get("weight_kg", 1.0)
    weight = max(38.0, bmi_target * (height / 100) ** 2)
    span_years = rng.choice([2, 3, 3, 4, 5])
    start = CORPUS_END - timedelta(days=365 * span_years)
    drift = rng.gauss(0.0, 0.035)          # 几年间体重的自然漂移
    anchors = tuple((start + timedelta(days=365 * y),
                     weight * (1 + drift * y)) for y in range(span_years + 1))

    events = _timeline(rng, archetype, start)
    trend = dict(archetype["trend"])

    return Person(person_id=person_id, sex=sex, birth_year=birth_year,
                  height_cm=round(height, 1), archetype=archetype["name"],
                  region=rng.choice(REGIONS), baseline=baseline, trend=trend,
                  events=events, weight_anchors=anchors)


def _timeline(rng: random.Random, archetype: dict, start: date) -> tuple[Event, ...]:
    """时间线：原型的干预事件 + 一到三次随机事件。"""
    events: list[Event] = []
    script_name = archetype["script"]
    if script_name:
        script = SCRIPTS[script_name]
        # 干预发生在观察窗口的中段，这样前后都有复查点可比——
        # 事件压在最后一次检查之后，归因问题就没有可观测的答案了。
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
    """(日期, 就诊类型, 医嘱列表, 套餐)。年度体检 + 原型复查 + 偶发单项。

    体检按套餐档次开单（entry / senior / basic / standard / premium，见 `resources/cohort.json` 的 `checkup_packages`），
    档次按人抽定、逐年有一定概率升档（换了单位、年纪大了）。慢病复查里约四成是**门诊**
    （`clinic`：一份门诊病历 + 化验单），其余是只有化验单的复查；高血压复查全部走门诊
    （血压是门诊测的，不会印在检验报告单上）。
    """
    start = person.weight_anchors[0][0]
    out: list[tuple[date, str, list[str], str | None]] = []

    # 年度体检：每年一次，日子在同一个月附近浮动（真实体检也是"每年三月前后"）。
    anniversary = rng.randint(0, 364)
    year = start
    tier = profile.choose_package(person, "checkup_center", rng)
    order = ["basic", "standard", "premium"]
    # 工作年龄的人有一定概率第一次是入职体检（换了单位），之后才是年度体检
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

    # 偶发单项：一张血沉单、一张尿常规、一份心电图或腹部超声报告——真实语料里
    # "只有一行"的报告不少（实测 rows_per_document 的 p25=1），影像与心电图报告也各有几十份。
    for _ in range(rng.randint(0, 2)):
        when = start + timedelta(days=rng.randint(10, (CORPUS_END - start).days))
        out.append((when, "specialty", [rng.choice(["inflammation", "urinalysis", "ecg", "ultrasound",
                                                    "imaging", "thyroid", "tumor", "cbc"])], None))
    # 感冒那次去了社区门诊：血常规 + CRP + 门诊病历
    for e in person.events:
        if e.name == "急性上呼吸道感染" and rng.random() < 0.7:
            out.append((e.start + timedelta(days=rng.randint(1, 3)), "clinic", ["vitals_clinic", "cbc", "inflammation"], None))
    return sorted(out, key=lambda x: (x[0], x[1]))


#: 目录套餐 → mirobody 抽取契约里的 detection_method。没列的是化验。
DETECTION = {"vitals": "Physiological", "ecg": "Physiological", "spirometry": "Physiological",
             "arterial": "Physiological", "body_composition": "Physiological", "echo": "Imaging"}


def encounter_for(person: Person, when: date, exam_type: str, orders: list[str],
                  rng: random.Random, package: str | None = None) -> Encounter:
    """把一次就诊变成一组读数。"""
    keys: list[str] = []
    for order in orders:
        for key in ORDERS.get(order, []):          # "ultrasound" 这类没有检验项目的医嘱给空列表
            if key not in keys:
                keys.append(key)
    # 性别专属项目：男性不做 CA125，女性不做 PSA。
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
            if key in keys or key in ("glb",):     # 球蛋白是白球比的输入，先算不印也行
                values[key] = float(spec.format_value(key, value))

    readings: list[Reading] = []
    for key in keys:
        item = catalogue[key]
        if key in qualitatives:
            text = qualitative_value(rng, person, key, when)
            normal_text = spec.reference_text(key, person.sex)
            # 阳性不一定是异常：乙肝表面抗体阳性是接种过疫苗
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


def draw_lang(rng: random.Random) -> str:
    return "en" if rng.random() < 0.5 else "zh"


def person_lang(seed: int, person_id: str) -> str:
    """这个人的语言组（zh / en）。`corpus.home_institutions` 从同一个流的同一次抽样取语言，
    所以体检机构、主诉、日记、设备与基因文件的语言一致——一个人不会中文体检、英文写日记。"""
    return draw_lang(home_stream(seed, person_id))


def encounters_for(person: Person, seed: int) -> list[Encounter]:
    rng = random.Random(f"enc:{seed}:{person.person_id}")
    states = profile.person_findings(person, seed)
    lang = person_lang(seed, person.person_id)
    out: list[Encounter] = []
    previous: date | None = None
    for when, exam_type, orders, package in schedule(person, rng):
        encounter = encounter_for(person, when, exam_type, orders, rng, package)
        # 从上一次到这一次之间**开始或仍在起效**的事件：他汀 35 天起效、补铁 90 天起效，
        # 事件开始在上次就诊之前、效应落在这一段里，也是这段跳变的解释。
        # 事件结束后效应按半衰期衰减：上次就诊时还在衰减期内的事件，也是这段回落的解释。
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
