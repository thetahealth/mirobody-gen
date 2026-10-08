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

from dataclasses import dataclass, field
from datetime import date


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
