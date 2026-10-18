"""Clinical profile: coherent red-cell indices (`build --clinical`).

By default haemoglobin is the product RBC x MCV x MCHC of three independently drawn indices, each
with a 3% analytical CV, so its spread is the sum of theirs (CV about 10.6% against haemoglobin's
own 7.4%): seed 7 printed men up to 195 g/L and MCHC above 360 g/L in 12 of 286 blood counts. In
clinical mode haemoglobin is drawn with its own between- and within-subject variation and the red
cell count follows from it, the analytical CV of haemoglobin, MCV and MCHC is that of a current
analyser, and the archetype's shifts, trends and events still reach haemoglobin through the three
indices. The noise comes from a stream of its own, so the visit's other draws are unchanged.
"""

from __future__ import annotations

import math
import random
from datetime import date

from . import physiology, spec

#: Analytical CVs (%) on a current haematology analyser; the default applies 3% to every analyte.
ANALYTICAL_CV = {"hgb": 1.0, "mcv": 1.0, "mchc": 1.0}


def coherent(raw: dict[str, float], person, when: date) -> None:
    """Replace the drawn red cell count by haemoglobin / MCH, haemoglobin drawn on its own."""
    if not all(k in raw for k in ("rbc", "mcv", "mchc")):
        return
    shift = next((a["shift"] for a in spec.cohort()["archetypes"] if a["name"] == person.archetype), {})
    level = physiology.center_of("hgb", person.sex) * person.baseline.get("hgb", 1.0)
    for key in ("rbc", "mcv", "mchc"):
        level *= (shift.get(key, 1.0) * physiology.trend_factor(person, key, when)
                  * physiology.event_factor(person, key, when)[0])
    cv = math.sqrt(spec.indicators()["hgb"]["cvi"] ** 2 + ANALYTICAL_CV["hgb"] ** 2) / 100
    sigma = math.sqrt(math.log(1 + cv ** 2))
    z = random.Random(f"hgb:{person.person_id}:{when.isoformat()}").gauss(0, 1)
    hgb = level * math.exp(sigma * z - sigma ** 2 / 2)
    raw["rbc"] = hgb / (raw["mchc"] * raw["mcv"] / 1000)
