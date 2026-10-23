"""Clinical profile: plausible issuers (on unless `build --no-clinical`).

The fiction pool names 103 of 347 Chinese hospitals 妇幼保健院 (maternal and child health hospital) and 80
中医医院 (TCM hospital). Without the profile (`--no-clinical`) a build therefore sends a 56-year-old man's
checkup to the first and prints a Western outpatient record under the second, and clinician review read
both as fake. In clinical mode such a visit moves to another institution of the same pool, chosen the
way `Institutions.draw` chooses, from a stream addressed by the visit. These are presentation rules for
review material: no facility service is inferred or recorded, and the manifest names the institution
actually printed.
"""

from __future__ import annotations

import json
import random

from .corpus import CRP_ALPHA
from .model import Encounter, Person

MATERNAL_CHILD = "妇幼保健院"
TCM = "中医医院"
#: Women of reproductive age (WHO: 15-49), and the conditions such a hospital plausibly follows for
#: them: routine women's health, anaemia and thyroid care. Statin, blood-pressure, diabetes and kidney
#: follow-up go to a general hospital.
MATERNAL_CHILD_AGE_LIMIT = 50
MATERNAL_CHILD_CONDITIONS = frozenset({"healthy", "iron_deficiency_anemia", "thyroid_disorder"})
#: Share of those women whose care includes such a hospital at all. No real count backs it (a
#: structural assumption, like STICKY_FOLLOWUP): without it every eligible woman whose usual hospital
#: was one went there for nearly every hospital visit.
MATERNAL_CHILD_SHARE = 0.3
POLICY = "clinical-issuers-v1"


def uses_maternal_child(seed: int, person: Person) -> bool:
    return random.Random(json.dumps([POLICY, seed, person.person_id, "maternal-child"])).random() < MATERNAL_CHILD_SHARE


def plausible(name: str, person: Person, year: int, exam_type: str | None, seed: int) -> bool:
    """A maternal and child health hospital only for the women above; no TCM hospital for a clinic
    visit, whose outpatient record this generator writes without TCM differentiation."""
    if MATERNAL_CHILD in name and (
            person.sex != "female" or year - person.birth_year >= MATERNAL_CHILD_AGE_LIMIT
            or person.archetype not in MATERNAL_CHILD_CONDITIONS or not uses_maternal_child(seed, person)):
        return False
    return not (exam_type == "clinic" and TCM in name)


def reselect(process, index: int, kind: str, group: str, ok, rng: random.Random,
             eligible: list[int] | None = None) -> int:
    """`index` if `ok`; otherwise another institution picked as `Institutions.draw` picks: a visited
    one by popularity, else a fresh one. `eligible` holds a required service's declared providers.
    Keeps `index` when nothing passes."""
    if ok(index):
        return index
    registry = process.registry
    if eligible is not None:
        seen = [i for i in eligible if i in process.counts and ok(i)]
        fresh = [i for i in eligible if i not in process.counts and ok(i)]
    else:
        key = (group, kind) if (group, kind) in process.fresh else (group, "hospital")
        seen = [i for i in process.counts if registry.institutions[i]["kind"] == key[1]
                and ("en" if registry.institutions[i]["language"] == "en" else "zh") == group and ok(i)]
        first = next((i for i in process.fresh[key] if i not in process.counts
                      and i not in registry.big.values() and ok(i)), None)
        fresh = [] if first is None else [first]
    total = sum(process.counts[i] for i in seen)
    if seen and (not fresh or rng.random() < total / (total + CRP_ALPHA)):
        return rng.choices(seen, weights=[process.counts[i] for i in seen])[0]
    return rng.choice(fresh) if fresh else index


def visit(seed: int, person: Person, enc: Encounter, identity: list, occurrence: int, process, index: int,
          kind: str, group: str, eligible: list[int] | None) -> int:
    """The institution this visit prints under."""
    names = process.registry.institutions

    def ok(i: int) -> bool:
        return plausible(names[i]["name"], person, enc.exam_date.year, enc.exam_type, seed)

    rng = random.Random(json.dumps([POLICY, seed, person.person_id, identity, occurrence]))
    return reselect(process, index, kind, group, ok, rng, eligible)


def homes(seed: int, person: Person, year: int, process, home: dict[str, int], group: str) -> dict[str, int]:
    """The usual checkup centre and hospital, checked at the first visit."""
    names = process.registry.institutions
    out = {}
    for kind, index in home.items():
        rng = random.Random(json.dumps([POLICY, seed, person.person_id, "home", kind]))
        out[kind] = reselect(process, index, kind, group,
                             lambda i: plausible(names[i]["name"], person, year, None, seed), rng)
    return out
