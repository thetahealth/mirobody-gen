"""Internal data model: Person, Event, Encounter, Reading, Complaint, Finding.

**The rendering layer knows only these four types.** They can come from either of two sources:

* `mirobody_gen.person` — this repo's cohort engine (prevalence-weighted, mechanism-driven values);
* an external synthetic-person dataset (e.g. ESL-Bench, arXiv:2604.02834) — as long as it's loaded
  into these four types, the rendering layer works unchanged.

Sharing one model means the ablation "same synthetic person, structured ingestion vs. file ingestion"
(docs/zh-CN/plan.md §6.1) is just swapping a data source, not swapping renderers.

Field names deliberately align with both sides: `Reading`'s fields match mirobody's extraction
contract (`original_indicator / value / unit / reference_range / status / detection_method`), and
`Encounter`'s fields match ESL-Bench's exam record (`exam_date / exam_type / exam_location`). This
layer invents no third vocabulary — the truth must be directly legible to both, or scoring needs a
mapping, and mappings fail silently.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date
import math


@dataclass(frozen=True)
class DoseStep:
    """From `start` on, `dose_mg` daily of which the fraction `adherence` (0-1) is actually taken."""
    start: date
    dose_mg: float
    adherence: float = 1.0


@dataclass(frozen=True)
class MedicationCourse:
    """One drug's exposure over time: dose steps (start, increases, decreases, adherence changes) and
    an optional stop. It is the single state the lab values, the medication plan and the clinic note
    all read; its response per dose comes from `resources/cohort.json` `medication_response`."""
    drug: str
    steps: tuple[DoseStep, ...]
    stop: date | None = None

    def __post_init__(self):
        if not self.steps:
            raise ValueError("a medication course needs at least one dose step")
        for a, b in zip(self.steps, self.steps[1:]):
            if not a.start < b.start:
                raise ValueError("dose steps must have strictly increasing start dates")
        for step in self.steps:
            if not step.dose_mg > 0 or not 0.0 <= step.adherence <= 1.0:
                raise ValueError("a dose step needs a positive dose and adherence in [0, 1]")
        if self.stop is not None and not self.stop > self.steps[-1].start:
            raise ValueError("a stop must come after the last dose step")

    def step_at(self, when: date) -> DoseStep | None:
        """The step in force on `when`, or None before the start and from the stop on."""
        if when < self.steps[0].start or (self.stop is not None and when >= self.stop):
            return None
        return [s for s in self.steps if s.start <= when][-1]

    def change_dates(self) -> list[date]:
        return [s.start for s in self.steps] + ([self.stop] if self.stop is not None else [])


@dataclass(frozen=True)
class Event:
    """An event on the timeline, and what it did to which indicators.

    The effect is three numbers: `magnitude` (fractional change from baseline, positive is up),
    `onset_days` (delay before it takes effect — a statin doesn't drop LDL the same day), and
    `decay_days` (days for the effect to decay to half, `None` meaning it never decays).

    These three numbers are the **computable answer** to an attribution question: asked "why did
    LDL drop", the answer is this event, not some model's post-hoc explanation. ESL-Bench takes the
    same approach (truth comes from the generation process), so field names follow it:
    `event_type / health_effect / impact_level`.
    """

    name: str
    event_type: str            # health_event / diet_change / exercise_change / medication / long_term_habit
    start: date
    duration_days: int
    health_effect: str         # positive / negative / neutral
    impact_level: str          # low / medium / high
    #: indicator key -> (relative magnitude, onset days, half-life days or None)
    effects: dict[str, tuple[float, int, int | None]] = field(default_factory=dict)
    note: str = ""
    #: A treatment whose dose-dependent effects come from its exposure course rather than `effects`.
    course: MedicationCourse | None = None

    def __post_init__(self):
        if self.course is not None and self.course.steps[0].start != self.start:
            raise ValueError(f"{self.name}: the exposure course must start on the event's start date")


@dataclass(frozen=True)
class Person:
    """A synthetic person. **The root of truth**: indicators are derived from this, never the reverse."""

    person_id: str
    sex: str                   # male / female
    birth_year: int
    height_cm: float
    archetype: str
    region: str
    #: indicator key -> individual baseline offset (relative multiplier). From between-subject
    #: variation (CVG); fixed for a person's whole life.
    baseline: dict[str, float] = field(default_factory=dict)
    #: indicator key -> yearly trend (relative multiplier per year). Chronic-disease progression runs here.
    trend: dict[str, float] = field(default_factory=dict)
    events: tuple[Event, ...] = ()
    #: Weight changes, unlike height, so it's a set of (date, kg) anchors with linear interpolation
    #: between them.
    weight_anchors: tuple[tuple[date, float], ...] = ()
    #: Explicit source diagnosis records. Missing time/source remains unplaced,
    #: never backfilled from the latent archetype or a treatment start.
    diagnosis_history: tuple[dict, ...] = ()

    def age_at(self, when: date) -> int:
        return when.year - self.birth_year - ((when.month, when.day) < (7, 1))


@dataclass
class Reading:
    """One row on a report. Field names map one-to-one onto mirobody's extraction contract.

    `original_indicator` is the **printed name** — the layout layer picks one spelling from spec's
    name_variants, so the same `key` (e.g. hemoglobin) may be printed under a different spelling,
    script or alias on different reports. Scoring aligns on `key`; extraction looks for
    `original_indicator`; the gap between the two is exactly what this corpus is meant to test.
    """

    key: str
    original_indicator: str
    value: str
    unit: str
    reference_range: str
    status: str                # normal / high / low (matches the extraction contract)
    detection_method: str = "laboratory"
    #: Scoring-only extra columns, never printed on the report.
    canonical_value: float | None = None
    unit_ucum: str = ""
    loinc: str | None = None
    value_kind: str = "quantitative"
    expect_resolvable: bool = False
    ref_low: float | None = None
    ref_high: float | None = None
    #: Whether this row can be read at all. When an injected hazard makes it unreadable, it drops
    #: out of the recall denominator and into the "must abstain" set.
    readable: bool = True
    #: Algorithm/assay provenance is separate from the acquisition channel above.
    result_method: str | None = None
    source: str = "synthetic_generator"


@dataclass
class MissingResult:
    """An ordered analyte with no result. Never a numeric/normal Reading."""
    key: str
    unit: str
    reason_code: str
    details: dict = field(default_factory=dict)
    result_method: str = "not_modelled"
    source: str = "synthetic_generator"

    def explanation(self, en: bool) -> str:
        if self.reason_code == "tg_above_calculation_limit":
            # the TG as printed in the same document, not the float's own repr (4.60 vs 4.6)
            tg, limit = self.details.get("tg_text", self.details["tg"]), self.details["limit"]
            return (f"not resulted: TG={tg} mmol/L exceeds the existing calculation limit {limit} mmol/L; "
                    "no direct-assay result supplied") if en else (
                    f"未出结果：TG={tg} mmol/L超过现有计算法界限{limit} mmol/L；未提供直接法结果")
        if self.reason_code == "calculation_inputs_missing":
            inputs = ", ".join(self.details["missing_inputs"])
            return f"not resulted: calculation inputs missing ({inputs}); no direct-assay result supplied" if en else (
                f"未出结果：缺少计算输入（{inputs}）；未提供直接法结果")
        if self.reason_code == "not_applicable_sex_specific":
            return "not resulted: sex-specific order not applicable" if en else "未出结果：该性别限定项目不适用"
        return "not resulted: generator supplied no result; clinical cause unknown" if en else "未出结果：生成器未提供结果；临床原因未知"


def order_result_errors(ordered_keys, rows: list[dict], missing: list[dict]) -> list[str]:
    """Check the serialized order/result partition, without judging medical values."""
    errors = []
    if not isinstance(ordered_keys, (list, tuple)) or any(not isinstance(k, str) or not k for k in ordered_keys):
        return ["ordered_keys must be an explicit list of analyte keys"]
    if len(set(ordered_keys)) != len(ordered_keys):
        errors.append("duplicate ordered key")
    if not isinstance(rows, list) or not isinstance(missing, list):
        return errors + ["rows and missing_results must be lists"]
    counts = Counter()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("key"), str):
            errors.append("invalid result row")
            continue
        counts[row["key"]] += 1
        if not isinstance(row.get("value"), str) or not row["value"].strip():
            errors.append(f"{row['key']}: empty result text")
        if not isinstance(row.get("unit"), str) or not isinstance(row.get("source"), str) or not row["source"].strip():
            errors.append(f"{row['key']}: missing result unit/source")
        if row["key"] == "ldl" and row.get("result_method") not in ("calculated_friedewald", "direct_assay"):
            errors.append("ldl: missing calculation/assay method")
        if row.get("value_kind") == "quantitative":
            value = row.get("canonical_value")
            if type(value) not in (float, int) or not math.isfinite(value):
                errors.append(f"{row['key']}: non-finite/missing quantitative result")
    for item in missing:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str):
            errors.append("invalid missing-result record")
            continue
        counts[item["key"]] += 1
        if any(not isinstance(item.get(k), str) or not item[k].strip()
               for k in ("reason_code", "result_method", "source")):
            errors.append(f"{item['key']}: missing reason/method/source")
        if not isinstance(item.get("unit"), str) or not isinstance(item.get("details"), dict):
            errors.append(f"{item['key']}: missing unit/details")
    errors.extend(f"{key}: expected exactly one result or non-result, got {counts[key]}"
                  for key in ordered_keys if counts[key] != 1)
    errors.extend(f"{key}: outcome without order" for key in counts if key not in ordered_keys)
    return errors


@dataclass(frozen=True)
class Complaint:
    """A chief complaint or symptom. `text` is the surface form printed or written into a diary;
    `icpc3` is the expected answer on mirobody's symptom axis, and `expect` is what mirobody should
    return (coded / no-match / refused / needs-input) — abstaining is also a valid answer."""

    text: str
    symptom_id: str
    icpc3: str | None
    expect: str
    duration: str = ""


@dataclass(frozen=True)
class Finding:
    """A named finding on an exam (fatty liver, a thyroid nodule, ...): where it's printed, how it
    reads, and what code mirobody should resolve it to."""

    id: str
    where: str                 # department or auxiliary-exam id
    item: str | None           # item / organ id
    surface: str               # diagnosis surface form (for coding)
    icpc3: str | None          # expected ICPC-3 D-axis code; None = expected abstention
    severity: str | None = None
    params: dict = field(default_factory=dict)
    since: date | None = None


@dataclass
class Encounter:
    """One encounter or check-up. Field names match ESL-Bench's exam record.

    `exam_type`: routine (check-up report) / follow-up (repeat lab panel) / clinic (clinic note plus
    lab panel) / specialty (a single targeted exam). `package` is set only for routine (entry /
    senior / basic / standard / premium).
    """

    person_id: str
    exam_date: date
    exam_type: str
    exam_location: str         # hospital / clinic / checkup-center / lab
    panels: tuple[str, ...]
    readings: list[Reading] = field(default_factory=list)
    #: Events that started between the previous encounter and this one (the longitudinal audit uses
    #: this to judge whether a jump in values has an explanation).
    events_since_previous: tuple[str, ...] = ()
    package: str | None = None
    complaints: list[Complaint] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    #: Explicit encounter/source fact. Missing is unknown, never inferred from the issuer's name.
    department: str | None = None
    #: None = caller did not declare orders; generated encounters always declare the full set.
    ordered_keys: tuple[str, ...] | None = None
    missing_results: list[MissingResult] = field(default_factory=list)
    #: Dated knowledge available to this encounter; None means no timeline supplied.
    conditions: list[dict] | None = None

    def validate_order_results(self) -> None:
        if self.ordered_keys is None:
            if self.missing_results:
                raise ValueError("Missing results require explicitly declared orders")
            return
        errors = order_result_errors(self.ordered_keys, [asdict(r) for r in self.readings],
                                     [asdict(m) for m in self.missing_results])
        if errors:
            raise ValueError("; ".join(errors))
