"""Medication instructions: one dosing intent, spelled in a prescriber's dialect.

The simulation fixes what a plan means: how many administrations per period and how much each time.
The `sig` axes of `resources/surface.json` decide how a Chinese instruction spells it (每日/每天/一日
before a daily count, spaces around the count, 每次/一次 before the dose), each choice with a
provenance tier, under the same `--surface` mode as names. Resources write every instruction in the
canonical spelling, the first choice of each axis; `parse` reads it back into an `Intent` and `write`
spells that intent in a plan's dialect, so every spelling carries the same truth.

`pairs` is a parser regression table: per intent, the canonical spelling and one variant per
single-axis change, including weekly and every-other-day schedules no cohort plan uses.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

from . import spec, surface

_DOSE = r"(?P<dose>\d+(?:\.\d+)?)(?P<unit>mg|微克|片)"
_SCHEDULED = re.compile(rf"^每日(?P<count>\d)次，每次{_DOSE}(?P<tail>，.+)?$")
_AS_NEEDED = re.compile(rf"^需要时每次{_DOSE}，每日不超过(?P<count>\d)次$")
_UCUM = {"mg": "mg", "微克": "ug", "片": "{tbl}"}


@dataclass(frozen=True)
class Intent:
    count: int                  # administrations per period; for as-needed use, the most allowed
    dose: str                   # amount per administration, as printed
    unit: str                   # printed unit: mg / 微克 / 片
    period: int = 1
    period_unit: str = "d"      # d / wk
    as_needed: bool = False
    tail: str = ""              # instruction after the dose (，随餐服用)

    @property
    def axes(self) -> tuple[str, ...]:
        """The axes this intent's spelling depends on."""
        if self.as_needed:
            return "day_word", "count_spacing", "dose_word"
        if (self.period, self.period_unit) == (2, "d"):
            return "every_other_day", "dose_word"
        return ("day_word" if self.period_unit == "d" else "week_word"), "count_spacing", "dose_word"

    def truth(self) -> dict:
        daily = (self.period, self.period_unit) == (1, "d") and not self.as_needed
        return {"timing": {"count": self.count, "period": self.period, "period_unit": self.period_unit,
                           "as_needed": self.as_needed},
                "slots_per_day": self.count if daily else None,
                "dose_per_slot": {"value": float(self.dose) if "." in self.dose else int(self.dose),
                                  "unit": _UCUM[self.unit]}}


def parse(text: str) -> Intent:
    """The intent behind a canonical instruction."""
    if m := _SCHEDULED.match(text):
        return Intent(int(m["count"]), m["dose"], m["unit"], tail=m["tail"] or "")
    if m := _AS_NEEDED.match(text):
        return Intent(int(m["count"]), m["dose"], m["unit"], as_needed=True)
    raise ValueError(f"not a canonical instruction: {text!r}")


def write(intent: Intent, form: dict[str, str]) -> str:
    sp = form.get("count_spacing", "")
    count = f"{sp}{intent.count}{sp}次"
    dose = f"{form['dose_word']}{intent.dose}{intent.unit}"
    if intent.as_needed:
        return f"需要时{dose}，{form['day_word']}不超过{count}{intent.tail}"
    if intent.axes[0] == "every_other_day":
        if intent.count != 1:                    # the axis spells "once every other day" whole
            raise ValueError(f"every-other-day spellings say once, not {intent.count} times")
        return f"{form['every_other_day']}，{dose}{intent.tail}"
    return f"{form[intent.axes[0]]}{count}，{dose}{intent.tail}"


def _choices() -> dict[str, list[dict]]:
    return {axis: surface.allowed(a["choices"]) for axis, a in spec.surface()["sig"]["axes"].items()}


def draw(rng: random.Random) -> dict[str, str] | None:
    """A plan's dialect: one drawn choice per axis this mode allows; `None` when the layer is off."""
    if surface.MODE == "off":
        return None
    return {axis: surface.pick(rng, choices)["word"] for axis, choices in _choices().items() if choices}


def spell(entry: dict, form: dict[str, str]) -> dict[str, str]:
    """Rewrite a plan or change's canonical `dose_text_zh` in `form` and add its intent's truth.
    Returns the part of the form the spelling used."""
    intent = parse(entry["dose_text_zh"])
    entry.update(dose_text_zh=write(intent, form), **intent.truth())
    return {axis: form[axis] for axis in intent.axes}


#: Schedules the regression table spells besides the cohort's own plans.
EXTRA = (Intent(2, "1", "片"), Intent(3, "1", "片"), Intent(2, "1", "片", period_unit="wk"),
         Intent(1, "1", "片", period=2))


def pairs() -> list[dict]:
    """Per intent, the canonical spelling and one variant per single-axis change this mode allows.
    An intent with an axis the mode cannot spell (every other day, outside `fuzz`) is left out."""
    choices = _choices()
    intents = dict.fromkeys([parse(p["dose_text_zh"]) for p in spec.cohort()["medication_plans"].values()]
                            + list(EXTRA))
    rows = []
    for n, intent in enumerate(intents):
        if not all(choices[a] for a in intent.axes):
            continue
        base = {axis: choices[axis][0]["word"] for axis in intent.axes}
        variants = [("base", base)] + [(f"{axis}:{c['word'].replace(' ', '␠')}", {**base, axis: c["word"]})
                                       for axis in intent.axes for c in choices[axis][1:]]
        rows += [{"pair_id": f"sig{n:03d}", "variant": variant, "text": write(intent, form),
                  "sig_form": form, **intent.truth()} for variant, form in variants]
    return rows
