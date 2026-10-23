"""Clinical profile: complete result tables (on unless `build --no-clinical`).

Clinician review of generated scenarios read slips as fake when results had no unit, no reference range
or no abnormal flag, when the patient's name and sex sat among the result rows, and when a Simplified
Chinese slip printed Traditional names. Layout families drawn from the reference corpus carry each of
these at its real document rate, and so do the incident hazards (artifacts measured on OCR output of
real scans). In clinical mode an institution is drawn only if its template keeps all of them in every
revision, and incidents stay off the page. `--no-clinical` builds and `--pairs` keep both for robustness
studies.
"""

from __future__ import annotations

import copy
import random

from . import hazards, layout
from .person import CORPUS_END

#: Traditional characters of the resource name variants that `layout._TRAD` lacks, so `script_of`
#: files 身長, 收縮壓 or 總膽固醇 as Simplified. Listed here so this profile does not wait for that
#: table to be completed.
TRADITIONAL = frozenset(layout._TRAD) | frozenset("圍壓張時溫澱濕稱穀紅細縮總脈膽見轉長間隱風飯體鹼")


def traditional(text: str) -> bool:
    return any(ch in TRADITIONAL for ch in text)


def complete(f: layout.Family) -> bool:
    """A unit beside every result (its own column or glued to the value), a reference-range column,
    a printed abnormal flag, subject fields outside the result table, and no Traditional character on
    a page that is not Traditional. Traditional-script templates are left out: the zh cohort is a
    Simplified-script population."""
    printed = "".join(f.headers.values()) + f.flag_high + f.flag_low + f.flag_normal + "".join(f.date_labels)
    return (f.language != "zh-Hant" and f.unit_at in ("column", "value") and f.flag_at != "none"
            and "reference" in f.columns and not f.subject_in_table and not traditional(printed))


def complete_institutions(registry: layout.Registry) -> frozenset[int]:
    """Institutions whose template is complete in every revision a visit can print (an institution
    keeps one template per revision, `layout.revised`, and revisions start in 2018)."""
    years = range(2018, CORPUS_END.year + 1)
    return frozenset(i for i in range(len(registry.institutions))
                     if complete(registry.family(i))
                     and all(complete(layout.revised(registry.family(i), y)[0]) for y in years))


def same_script(variants: list[str], script: str) -> list[str]:
    """Name variants that print in the page's script."""
    return [v for v in variants if script != "zh-Hans" or not traditional(v)]


def skip_incidents(doc, rng: random.Random) -> None:
    """Draw the incidents on a copy, so the document stream advances as in a `--no-clinical` build and
    the page keeps none of them."""
    hazards.inject(copy.deepcopy(doc), rng)
