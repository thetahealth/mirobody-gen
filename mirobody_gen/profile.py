"""Profile layer: named findings per person, chief complaints per visit, check-up package choice.

Truth comes from the generation process here too, same as the indicator layer (docs/zh-CN/plan.md
section 3.3):

* **Findings are drawn once per person and advance year by year.** A cyst, stone, nodule or cavity,
  once it appears, stays (`sticky`) and only slowly grows or escalates in severity (fatty liver
  mild -> moderate); a lifestyle intervention can walk fatty liver back down to mild. So one person's
  check-up reports across several years tell **the same story**, not an independent draw each year —
  when mirobody reconciles a longitudinal history, "last year's 6mm thyroid nodule is 7mm this year"
  is a decidable question.
* **Chief complaints follow the archetype and events.** Someone with diabetes reports thirst and
  fatigue before starting metformin, occasional numbness after; the two weeks of a cold bring cough
  and sore throat; a healthy person has no complaints at a check-up (a check-up report has no
  complaints section at all). Every complaint's surface form is matched against mirobody's symptom
  vocabulary in `resources/complaints.json`, so what code it should resolve to — or whether it should
  be an abstention — is written into the truth.

Incidence rates have nothing to do with mirobody; they only aim for human-like magnitude, as noted in spec.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from . import spec
from .model import Complaint, Finding, Person

_dl = spec.doc_lang


def _narr() -> dict:
    return spec.narratives()


def _comp() -> dict:
    return spec.complaints()


# ── findings ────────────────────────────────────────────────────
def _eligible(fdef: dict, person: Person, age: int) -> bool:
    if fdef.get("sex") and fdef["sex"] != person.sex:
        return False
    if fdef.get("min_age") and age < fdef["min_age"]:
        return False
    if fdef.get("max_age") and age > fdef["max_age"] and not fdef["rate"].get("after_max_age"):
        return False
    return True


def _rate(fdef: dict, person: Person, age: int) -> float:
    r = fdef["rate"]
    if fdef.get("max_age") and age > fdef["max_age"]:
        rate = r.get("after_max_age", 0.0)
    else:
        rate = r.get("base", 0.0) + r.get("age", 0.0) * max(0, age - 30)
    rate *= r.get("sex", {}).get(person.sex, 1.0)
    rate *= r.get("archetype", {}).get(person.archetype, 1.0)
    return min(rate, 0.97)


_SIDES = {"zh": ["左", "右"], "en": ["left", "right"]}
_LOBES = {"zh": ["左", "右"], "en": ["left", "right"]}
_LUNG_LOBES = {"zh": ["上", "中", "下"], "en": ["upper", "middle", "lower"]}
_ECHO = {"zh": ["低", "等", "囊实性混合"], "en": ["hypoechoic", "isoechoic", "mixed cystic-solid"]}
_WALLS = {"zh": ["前", "后"], "en": ["anterior", "posterior"]}
_DENSITY = {"zh": ["磨玻璃", "实性"], "en": ["ground-glass", "solid"]}
_TEETH = ["16", "26", "36", "46", "17", "27", "37", "47", "14", "24", "34", "44"]


def _params(fdef: dict, rng: random.Random) -> dict:
    """Slots to fill into a finding's text: side, size, echogenicity, tooth position, etc. Drawn
    once and reused every year after (only the size is allowed to grow)."""
    p: dict = {}
    side = rng.randrange(2)
    p["side"], p["side_en"] = _SIDES["zh"][side], _SIDES["en"][side]
    p["lobe"], p["lobe_en"] = _LOBES["zh"][side], _LOBES["en"][side]
    lung = rng.randrange(3)
    p["lobe_lung"], p["lobe_lung_en"] = _LUNG_LOBES["zh"][lung], _LUNG_LOBES["en"][lung]
    echo = rng.choices([0, 1, 2], weights=[70, 20, 10])[0]
    p["echo"], p["echo_en"] = _ECHO["zh"][echo], _ECHO["en"][echo]
    wall = rng.randrange(2)
    p["wall"], p["wall_en"] = _WALLS["zh"][wall], _WALLS["en"][wall]
    dens = rng.choices([0, 1], weights=[55, 45])[0]
    p["density"], p["density_en"] = _DENSITY["zh"][dens], _DENSITY["en"][dens]
    p["tirads"] = rng.choices(["2", "3", "3", "4A"], weights=[30, 45, 15, 10])[0]
    p["clock"] = str(rng.randint(1, 12))
    p["tooth"] = rng.choice(_TEETH)
    if fdef.get("size_mm"):
        lo, hi = fdef["size_mm"]
        p["mm0"] = round(rng.uniform(lo, hi), 0)
    if fdef.get("t_score"):
        lo, hi = fdef["t_score"]
        p["t"] = round(rng.uniform(lo, hi), 1)
    return p


class FindingState:
    """One of a person's findings: when it started, its parameters, whether it's sticky."""

    def __init__(self, fid: str, fdef: dict, since: date, params: dict, sticky: bool):
        self.fid, self.fdef, self.since, self.params, self.sticky = fid, fdef, since, params, sticky

    def at(self, person: Person, when: date, rng: random.Random) -> Finding | None:
        if self.sticky and when < self.since:
            return None
        years = max(0.0, (when - self.since).days / 365.25)
        params = dict(self.params)
        if "mm0" in params:
            params["mm"] = int(round(params["mm0"] + 0.6 * years))     # a nodule/cyst grows roughly half a millimetre a year
        severity = None
        if self.fdef.get("severity"):
            levels = self.fdef["severity"]
            idx = min(len(levels) - 1, int(years // 2))
            # Drops back to the mildest grade after a lifestyle intervention: one decidable answer
            # to an attribution question.
            improved = any(e.event_type == "exercise_change" and e.start <= when
                           and (when - e.start).days > 120 for e in person.events)
            severity = levels[0] if improved else levels[idx]
        return Finding(id=self.fid, where=self.fdef["where"][0], item=self.fdef["where"][1],
                       surface=self.fdef["surface"], icpc3=self.fdef.get("icpc3"), severity=severity,
                       params=params, since=self.since)


def person_findings(person: Person, seed: int) -> list[FindingState]:
    """Drawn once per person. A sticky finding, once drawn, gets a start date (possibly before or
    after the observation window); a non-sticky one is redrawn at every encounter."""
    rng = random.Random(f"findings:{seed}:{person.person_id}")
    start = person.weight_anchors[0][0]
    end = person.weight_anchors[-1][0]
    age_mid = person.age_at(start + (end - start) / 2)
    out: list[FindingState] = []
    for fid, fdef in _narr()["findings"].items():
        if fdef.get("condition"):
            continue                                            # value-determined (e.g. sinus bradycardia), not drawn here
        if not _eligible(fdef, person, age_mid):
            continue
        rate = _rate(fdef, person, age_mid)
        sticky = fdef.get("sticky", False)
        if sticky:
            if rng.random() < rate:
                since = start + timedelta(days=rng.randint(-5 * 365, (end - start).days))
                out.append(FindingState(fid, fdef, since, _params(fdef, rng), True))
        else:
            out.append(FindingState(fid, fdef, start, _params(fdef, rng), False))
    return out


def _meets(values: dict[str, float], cond: dict) -> bool:
    for key, expr in cond.items():
        v = values.get(key)
        if v is None:
            return False
        op, num = expr[0], float(expr[1:])
        if op == "<" and not v < num:
            return False
        if op == ">" and not v > num:
            return False
    return True


def encounter_findings(person: Person, when: date, states: list[FindingState], package: str,
                       values: dict[str, float], seed: int) -> list[Finding]:
    """Findings that appear on this check-up report: within this package's sections/auxiliary exams, and present for this person now."""
    rng = random.Random(f"findings:{seed}:{person.person_id}:{when.isoformat()}")
    narr = _narr()
    pkg = narr["packages"][package]
    scope = set(pkg["sections"]) | set(pkg["aux"])
    age = person.age_at(when)
    out: list[Finding] = []
    seen_slots: set[tuple[str, str | None]] = set()
    for st in states:
        where, item = st.fdef["where"]
        if where not in scope:
            continue
        aux = narr["aux"].get(where)
        if aux and (aux.get("sex") and aux["sex"] != person.sex or aux.get("min_age", 0) > age):
            continue
        if not st.sticky and rng.random() >= _rate(st.fdef, person, age):
            continue
        f = st.at(person, when, rng)
        if f is None:
            continue
        # Only one finding per item slot (a cavity and a missing tooth don't both print under
        # "Teeth" at once — whichever is drawn first wins)
        if (where, item) in seen_slots and item is not None:
            continue
        seen_slots.add((where, item))
        out.append(f)
    # Findings determined by values (e.g. heart rate <60 -> sinus bradycardia)
    for fid, fdef in narr["findings"].items():
        cond = fdef.get("condition")
        if cond and fdef["where"][0] in scope and _meets(values, cond):
            out.append(Finding(id=fid, where=fdef["where"][0], item=fdef["where"][1],
                               surface=fdef["surface"], icpc3=fdef.get("icpc3")))
    return out


# ── check-up packages ──────────────────────────────────────────
def choose_package(person: Person, kind: str, rng: random.Random) -> str:
    """A person's check-up tier: institution-type weights x age (older people buy deeper packages
    more often) x archetype (chronic-disease people skew standard)."""
    weights = dict(_narr()["package_weights"].get(kind, _narr()["package_weights"]["hospital"]))
    age = person.age_at(person.weight_anchors[0][0])
    if age >= 50:
        weights["premium"] *= 1.8
        weights["basic"] *= 0.7
    if age >= 65:
        # Free senior health check-ups offered by community health centres: fewer items, once a year
        weights["senior"] = weights.get("senior", 0.0) + 0.6
    if person.archetype != "healthy":
        weights["standard"] *= 1.3
    names = list(weights)
    return rng.choices(names, weights=[weights[n] for n in names])[0]


# ── chief complaints ───────────────────────────────────────────
def complaint_surface(symptom_id: str, lang: str, rng: random.Random) -> Complaint:
    comp = _comp()
    if symptom_id in comp["symptoms"]:
        s = comp["symptoms"][symptom_id]
        if lang == "zh":
            text = rng.choice(s["zh"])
            code = s.get("zh_codes", {}).get(text, s["icpc3"])
            return Complaint(text=text, symptom_id=symptom_id, icpc3=code, expect="coded")
        pool = s["en"] or ([s["en_preferred"]] if s.get("en_preferred") else [])
        if pool:
            text = rng.choice(pool)
            return Complaint(text=text, symptom_id=symptom_id, icpc3=s["icpc3"], expect="coded")
        # No confirmed English surface form: the Chinese vocabulary entry has no preferred English
        # wording either — fall back to a made-up no-match surface form.
        return Complaint(text=symptom_id.replace("_", " "), symptom_id=symptom_id, icpc3=None, expect="no-match")
    f = comp["frontier"][symptom_id]
    pool = f["zh"] if lang == "zh" else (f["en"] or f["zh"])
    return Complaint(text=rng.choice(pool), symptom_id=symptom_id, icpc3=None, expect=f["expect"])


def complaints_for(person: Person, when: date, exam_type: str, lang: str, seed: int) -> list[Complaint]:
    """This encounter's chief complaints. A check-up has none; a clinic/follow-up visit draws 0-2
    based on archetype and events."""
    if exam_type == "routine":
        return []
    rng = random.Random(f"cc:{seed}:{person.person_id}:{when.isoformat()}")
    comp = _comp()
    pools = comp["pools"]
    arch = pools["archetype"].get(person.archetype, pools["archetype"]["healthy"])
    scripted = [e for e in person.events if e.note == "原型剧本"]
    after = bool(scripted) and scripted[0].start <= when
    candidates = list(arch["after"] if after else arch["before"])
    for e in person.events:
        if e.note == "随机事件" and e.start - timedelta(days=3) <= when <= e.start + timedelta(days=e.duration_days + 7):
            candidates += pools["incident"].get(e.name, [])
    none_rate = arch["none_rate"] if not candidates else min(arch["none_rate"], 0.5)
    if not candidates or rng.random() < none_rate:
        return []
    n = 1 if rng.random() < 0.6 else 2
    picked = rng.sample(candidates, min(n, len(set(candidates))))
    # Complaint wording is the document layer (spec.doc_lang); the symptom vocabulary's zh/en choice
    # is decided directly by lang in complaint_surface, with ja routed to the en pool — the two
    # layers agree.
    durations = comp["phrasing"][_dl(lang)]["durations"]
    out = []
    for i, sid in enumerate(dict.fromkeys(picked)):
        c = complaint_surface(sid, lang, rng)
        out.append(Complaint(text=c.text, symptom_id=c.symptom_id, icpc3=c.icpc3, expect=c.expect,
                             duration=rng.choice(durations) if i == 0 else ""))
    return out
