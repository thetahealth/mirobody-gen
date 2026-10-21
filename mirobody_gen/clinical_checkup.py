"""Clinical profile: checkup content (`build --clinical`).

Clinician review read four details of generated checkups as fake: a drawn ultrasound fan with a dark
blob beside the report, a kidney or thyroid finding that leaves the other side undescribed, ECG
intervals printed as a lab table, and a serum H. pylori antibody beside a urea breath test. In
clinical mode the ultrasound report is text only, a one-sided finding is followed by the other
side's normal description (`contralateral` in narratives.json), ECG parameters stay with the tracing,
and a checkup that runs the breath test does not order the antibody. The first three change only
what is printed; the antibody's draw is kept, so the visit's other results match a default build.
"""

from __future__ import annotations

from datetime import date

from . import spec
from .model import Finding, MissingResult, Person, Reading
from .profile import _SIDES

BREATH_TEST = "hp_breath"
#: Serum tests a checkup does not order beside the urea breath test.
DUPLICATES_BREATH_TEST = frozenset({"hp_ab"})
#: Book panels printed with their own report (the ECG tracing) instead of in the lab tables.
OFF_TABLE = frozenset({"ecg"})


def contralateral(finding: Finding, hits: list[Finding], lang: str) -> str:
    """The other side's normal description after the organ's only finding, if that finding names a side."""
    template = (spec.narratives()["findings"][finding.id].get("contralateral") or {}).get(lang)
    if not template or len(hits) != 1 or finding.params.get("side") not in _SIDES["zh"]:
        return ""
    other = 1 - _SIDES["zh"].index(finding.params["side"])
    text = template.format(side=_SIDES["zh"][other], side_en=_SIDES["en"][other])
    return text if lang == "zh" else " " + text


def breath_test_printed(person: Person, when: date, package: str | None) -> bool:
    """Whether a checkup book prints the urea breath test, filtered as `book.aux_blocks` filters."""
    n = spec.narratives()
    aux = n["aux"][BREATH_TEST]
    return (BREATH_TEST in n["packages"][package or "premium"]["aux"]
            and not (aux.get("sex") and aux["sex"] != person.sex) and aux.get("min_age", 0) <= person.age_at(when))


def orders(ordered_keys: tuple[str, ...], readings: list[Reading], missing: list[MissingResult],
           person: Person, when: date, exam_type: str, package: str | None):
    """The checkup's orders without the serology that repeats its breath test."""
    if exam_type != "routine" or not breath_test_printed(person, when, package):
        return ordered_keys, readings, missing
    return (tuple(k for k in ordered_keys if k not in DUPLICATES_BREATH_TEST),
            [r for r in readings if r.key not in DUPLICATES_BREATH_TEST],
            [m for m in missing if m.key not in DUPLICATES_BREATH_TEST])
