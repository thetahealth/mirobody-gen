"""Internal data model: Person, Event, Encounter, Reading, Complaint, Finding.

生成器的内部模型：人、事件、就诊、读数。

**渲染层只认这四个东西。** 它们可以来自两个源：

* `mirobody_gen.person` —— 本仓的队列引擎（按患病率配比、机理取值）；
* 外部虚拟人数据集（如 ESL-Bench，arXiv:2604.02834）——只要按这四个类型装进来，渲染层照常工作。

共用同一个模型，是为了让"同一个虚拟人，结构化入库 vs 文件入库"这个消融
（docs/zh-CN/plan.md §6.1）只是换一个数据源，而不是换一套渲染器。

字段名刻意向两边靠拢：`Reading` 的字段与 mirobody 提取契约同名
（`original_indicator / value / unit / reference_range / status / detection_method`），
`Encounter` 的字段与 ESL-Bench 的 exam 记录同名（`exam_date / exam_type / exam_location`）。
中间层不自创第三套词汇——真值要能被两边直接读懂，否则评分就要靠映射，而映射会悄悄出错。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Event:
    """时间线上的一个事件，以及它对哪些指标做了什么。

    效应用三个数刻画：`magnitude`（相对基线的比例变化，正为升）、
    `onset_days`（起效延迟，他汀不会当天就把 LDL 压下去）、
    `decay_days`（效应衰减到一半所需天数，`None` 表示持续不衰减）。

    这三个数就是归因问题的**可计算答案**：问"为什么 LDL 降了"，答案是这条事件，
    而不是某个模型的解释。ESL-Bench 用同样的思路（真值来自生成过程），
    所以这里的字段名跟着它走：`event_type / health_effect / impact_level`。
    """

    name: str
    event_type: str            # health_event / diet_change / exercise_change / medication / long_term_habit
    start: date
    duration_days: int
    health_effect: str         # positive / negative / neutral
    impact_level: str          # low / medium / high
    #: 指标键 → (相对幅度, 起效天数, 半衰期天数或 None)
    effects: dict[str, tuple[float, int, int | None]] = field(default_factory=dict)
    note: str = ""


@dataclass(frozen=True)
class Person:
    """一个虚拟人。**真值的根**：指标由这里派生，不是反过来。"""

    person_id: str
    sex: str                   # male / female
    birth_year: int
    height_cm: float
    archetype: str
    region: str
    #: 指标键 → 个体基线偏移（相对倍数）。来自个体间变异 CVG，一个人一辈子不变。
    baseline: dict[str, float] = field(default_factory=dict)
    #: 指标键 → 每年的趋势（相对倍数/年）。慢病进展走这里。
    trend: dict[str, float] = field(default_factory=dict)
    events: tuple[Event, ...] = ()
    #: 体重是会变的，所以不像身高那样固定：(日期, 公斤) 的锚点，中间线性插值。
    weight_anchors: tuple[tuple[date, float], ...] = ()

    def age_at(self, when: date) -> int:
        return when.year - self.birth_year - ((when.month, when.day) < (7, 1))


@dataclass
class Reading:
    """一份报告上的一行。字段名与 mirobody 的提取契约一一对应。

    `original_indicator` 是**打印名**——版式层会从 spec 的 name_variants 里挑一种写法，
    所以同一个 `key` 在不同报告上可能印成"血红蛋白"、"Hemoglobin"或"血紅素"。
    评分按 `key` 对齐，提取按 `original_indicator` 找，两者的差正是这套语料要测的东西。
    """

    key: str
    original_indicator: str
    value: str
    unit: str
    reference_range: str
    status: str                # normal / high / low（与提取契约一致）
    detection_method: str = "laboratory"
    #: 评分用的附加列，不印在报告上。
    canonical_value: float | None = None
    unit_ucum: str = ""
    loinc: str | None = None
    value_kind: str = "quantitative"
    expect_resolvable: bool = False
    ref_low: float | None = None
    ref_high: float | None = None
    #: 这一行可不可读。注入的陷阱让某一行读不出来时，它移出召回分母、进入"必须弃权"集合。
    readable: bool = True


@dataclass(frozen=True)
class Complaint:
    """一条主诉/症状。`text` 是印出来或写进日记的表面；`icpc3` 是 mirobody 症状轴的期望答案，
    `expect` 是那边应给的结果（coded / no-match / refused / needs-input）——弃权也是答案。"""

    text: str
    symptom_id: str
    icpc3: str | None
    expect: str
    duration: str = ""


@dataclass(frozen=True)
class Finding:
    """一条具名的检查所见（脂肪肝、甲状腺结节……）。印在哪、印成什么、mirobody 该编成什么码。"""

    id: str
    where: str                 # 科室或辅助检查 id
    item: str | None           # 条目 / 器官 id
    surface: str               # 诊断表面（编码用）
    icpc3: str | None          # ICPC-3 D 轴期望码；None = 期望弃权
    severity: str | None = None
    params: dict = field(default_factory=dict)
    since: date | None = None


@dataclass
class Encounter:
    """一次就诊/体检。字段名与 ESL-Bench 的 exam 记录一致。

    `exam_type`：routine（体检报告书）/ follow-up（复查化验单）/ clinic（门诊病历 + 化验单）/
    specialty（单项检查）。`package` 只在 routine 有（entry / senior / basic / standard / premium）。
    """

    person_id: str
    exam_date: date
    exam_type: str
    exam_location: str         # hospital / clinic / checkup-center / lab
    panels: tuple[str, ...]
    readings: list[Reading] = field(default_factory=list)
    #: 从上一次就诊到这一次之间开始的事件（纵向审计用它判断跳变有没有解释）。
    events_since_previous: tuple[str, ...] = ()
    package: str | None = None
    complaints: list[Complaint] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
