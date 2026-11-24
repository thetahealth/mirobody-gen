"""Consumer health-app screenshots (`kind: app_screenshot`): one smart-scale page under each of its tabs.

thetahealth/mirobody#301: three screenshots of one smart-scale page, with 体重, 脂肪 or 肌肉 selected, broke a
pipeline that expects every value to have its name beside it. The chart's points have no name but the
selected tab, which only colour and an underline mark; the summary card repeats a goal, a starting weight
and the latest weighing on every tab. This draws that page (`resources/app_screens.json`) for a person who
weighs in often, from their own device series and physiology, once per tab.

Truth per screenshot:

* `readings`: the chart's points, each a dated reading of the selected tab's metric (body weight, body fat
  mass, muscle mass), coded from the catalogue's body-composition vocabulary, never a lab analyte;
* `screen`: the tabs, the active one, and the title, which on the `weight` variant says 体重曲线 whatever
  tab is selected (as on the reported app);
* `screen_context`: the summary card, identical on every tab. The goal and starting weights are not
  measurements at all; the current weight, body fat percentage and BMI repeat the latest weighing and
  belong once to an upload of all three screenshots.

The layout reproduces a field report, so only `--surface fuzz` builds draw it.
"""

from __future__ import annotations

import math
import pathlib
import random
from datetime import date

from PIL import Image, ImageDraw

from . import physiology, spec, surface
from .model import Person
from .render import degrade
from .render.hand import printed_face

POINTS = 7
#: Day-to-day repeatability of a home scale's fat percentage (hand-set): a laboratory's analytical CV
#: would swing it, and the masses derived from it, by kilograms between weigh-ins
SCALE_CV = 0.015
W, H = 900, 1500
INK, MUTED, ACCENT = (20, 20, 20), (120, 120, 120), (30, 60, 160)


def drawn(seed: int, person: Person, series: dict) -> bool:
    """Whether this person uploads the screenshots: a frequent weigher with a chart's worth of weigh-ins,
    in a build whose mode draws field-tier shapes."""
    if not surface.allowed([{"tier": "field", "share": 0}]):
        return False
    weighs = sum(r["_metric"] == "weight" for r in series["records"])
    return (series["habits"]["weigh_rate"] >= 0.5 and weighs >= POINTS
            and random.Random(f"app:{seed}:{person.person_id}").random() < spec.surface()["fuzz_rate"])


def _page(seed: int, person: Person, series: dict) -> dict:
    """The numbers on the page: the last weigh-ins with fat and muscle that day, and the summary card.

    Like a bioimpedance scale, it estimates one thing, the fat percentage, and derives the rest from the
    weight: fat mass is weight times that percentage, and muscle mass the person's own share of the rest."""
    weighs = sorted((r["time"][:10], r["value"]) for r in series["records"] if r["_metric"] == "weight")
    points = []
    for day, weight in weighs[-POINTS:]:
        when = date.fromisoformat(day)
        fat = physiology.expected(person, "body_fat", when)
        lean = physiology.expected(person, "weight", when) * (1 - fat / 100)
        muscle_share = physiology.expected(person, "muscle_mass", when) / lean
        fat_pct = fat * math.exp(random.Random(f"app:{seed}:{person.person_id}:{day}").gauss(0, SCALE_CV))
        fat_mass = weight * fat_pct / 100
        points.append({"date": day, "weight": round(weight, 1), "fat_mass": round(fat_mass, 1),
                       "muscle_mass": round((weight - fat_mass) * muscle_share, 1), "body_fat": round(fat_pct, 1)})
    r = random.Random(f"app-card:{seed}:{person.person_id}")
    baseline = round(weighs[0][1], 1)
    floor = 18.5 * (person.height_cm / 100) ** 2
    last = points[-1]
    # A goal is never under a BMI of 18.5: a person without 4 kg to lose above it aims to gain instead
    if baseline - floor >= 4:
        goal = round(max(baseline - r.uniform(4, 12), floor))
    else:
        goal = round(max(baseline, floor) + r.uniform(2, 5))
    return {"points": points, "baseline": baseline, "goal": float(goal),
            "bmi": round(last["weight"] / (person.height_cm / 100) ** 2, 1),
            "title_variant": r.choice(["weight", "neutral"])}


def _draw(page: dict, tab: str, lang: str, banner: bool) -> Image.Image:
    a = spec.app_screens()
    labels = {t["key"]: t[lang] for t in a["tabs"]}
    card, last = a["card"], page["points"][-1]
    im = Image.new("RGB", (W, H), (250, 250, 245))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 110], fill=(255, 255, 255))
    title = a["titles"][page["title_variant"]][lang]
    d.text((W // 2, 60), title, font=printed_face(40), fill=INK, anchor="mm")
    d.rounded_rectangle([30, 140, W - 30, 440], radius=24, fill=(255, 255, 255))
    cells = [(card["baseline"][lang], f"{page['baseline']:.1f} kg"), (card["goal"][lang], f"{page['goal']:.1f} kg"),
             (card["current"][lang], f"{last['weight']:.1f} kg"), (card["body_fat"][lang], f"{last['body_fat']:.1f} %"),
             (card["bmi"][lang], f"{page['bmi']:.1f}"), (card["measured"][lang], last["date"][5:].replace("-", "/"))]
    for k, (label, value) in enumerate(cells):
        cx, cy = 30 + (W - 60) * (k % 3 * 2 + 1) // 6, 225 if k < 3 else 360
        d.text((cx, cy - 35), label, font=printed_face(28), fill=MUTED, anchor="mm")
        d.text((cx, cy + 15), value, font=printed_face(40), fill=INK, anchor="mm")
    for k, (key, label) in enumerate(labels.items()):
        cx, selected = W * (2 * k + 1) // 6, key == tab
        d.text((cx, 510), label, font=printed_face(36 if selected else 32), fill=ACCENT if selected else MUTED,
               anchor="mm")
        if selected:
            d.rectangle([cx - 40, 540, cx + 40, 546], fill=ACCENT)
    values = [p[tab] for p in page["points"]]
    top, bottom, left, right = 640, 1180, 70, W - 50
    d.rectangle([left - 40, top - 60, right + 30, bottom + 110], fill=(255, 255, 240))
    d.text((left - 20, top - 30), "kg", font=printed_face(26), fill=MUTED)
    lo, hi = min(values) - 3, max(values) + 3
    xy = [(left + 30 + (right - left - 60) * k / (len(values) - 1), bottom - (bottom - top) * (v - lo) / (hi - lo))
          for k, v in enumerate(values)]
    d.line(xy, fill=ACCENT, width=4)
    for (x, y), v, p in zip(xy, values, page["points"]):
        d.ellipse([x - 7, y - 7, x + 7, y + 7], outline=ACCENT, width=3, fill=(255, 255, 255))
        d.text((x, y - 30), f"{v:.1f}", font=printed_face(26), fill=INK, anchor="mm")
        d.text((x, bottom + 40), p["date"][5:].replace("-", "/"), font=printed_face(24), fill=MUTED, anchor="mm")
        d.text((x, bottom + 72), p["date"][:4], font=printed_face(24), fill=MUTED, anchor="mm")
    if banner:
        d.text((W // 2, H - 60), "SYNTHETIC SAMPLE", font=printed_face(24), fill=(170, 170, 170), anchor="mm")
    return im


def _truth(page: dict, tab: str, lang: str) -> dict:
    a, catalogue = spec.app_screens(), spec.indicators()
    label = next(t[lang] for t in a["tabs"] if t["key"] == tab)
    item = catalogue[tab]
    printed, readings = [], []
    for k, p in enumerate(page["points"]):
        value = f"{p[tab]:.1f}"
        printed.append({"item_name": label, "item_value": value, "item_unit": "kg", "item_range": "", "is_abnormal": "",
                        "readings": [k], "alternatives": {}, "readable": True, "unreadable_fields": [], "hazards": [],
                        "table": 0, "section": a["titles"][page["title_variant"]][lang]})
        readings.append({"key": tab, "loinc": item["loinc"], "canonical_value": p[tab], "value_text": value,
                         "unit_ucum": "kg", "value_kind": "quantitative", "status": "", "observed": p["date"],
                         "role": "export", "expect_resolvable": item["expect_resolvable"], "printed_row": k,
                         "result_method": None, "source": "consumer_app"})
    card, last = a["card"], page["points"][-1]
    context = [
        {"label": card["baseline"][lang], "value": f"{page['baseline']:.1f}", "unit": "kg", "role": "baseline", "key": "weight"},
        {"label": card["goal"][lang], "value": f"{page['goal']:.1f}", "unit": "kg", "role": "goal", "key": "weight"},
        {"label": card["current"][lang], "value": f"{last['weight']:.1f}", "unit": "kg", "role": "latest",
         "key": "weight", "loinc": catalogue["weight"]["loinc"], "observed": last["date"]},
        {"label": card["body_fat"][lang], "value": f"{last['body_fat']:.1f}", "unit": "%", "role": "latest",
         "key": "body_fat", "loinc": catalogue["body_fat"]["loinc"], "observed": last["date"]},
        {"label": card["bmi"][lang], "value": f"{page['bmi']:.1f}", "unit": "kg/m2", "role": "latest",
         "key": "bmi", "loinc": catalogue["bmi"]["loinc"], "observed": last["date"]},
        {"label": card["measured"][lang], "value": last["date"][5:].replace("-", "/"), "unit": "", "role": "measured_at"},
    ]
    return {"printed_rows": printed, "readings": readings, "screen_context": context,
            "screen": {"tabs": [t[lang] for t in a["tabs"]], "active_tab": label,
                       "title": a["titles"][page["title_variant"]][lang], "title_variant": page["title_variant"]}}


def records(seed: int, person: Person, series: dict, lang: str, files_root: pathlib.Path,
            out_dir: pathlib.Path, banner: bool) -> list[dict]:
    """One screenshot per tab, written as PNG, with its record."""
    lang = spec.doc_lang(lang)
    page = _page(seed, person, series)
    out = []
    for tab in (t["key"] for t in spec.app_screens()["tabs"]):
        doc_id = f"{person.person_id}_app_{tab}"
        image = _draw(page, tab, lang, banner)
        path = files_root / person.person_id / f"{doc_id}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(degrade.encode([image], "png", doc_id))
        out.append({
            "file": str(path.relative_to(out_dir)), "synthetic": True, "doc_id": doc_id, "person_id": person.person_id,
            "kind": "app_screenshot", "encounter_dates": [], "exam_type": "device_app", "package": None,
            "family": None, "institution": None, "issuer_kind": "consumer_app", "format": "png", "source_format": "png",
            "tier": "T6", "scene": None, "severity": None, "ops": [], "annotations": [], "dpi": None,
            "image_size": list(image.size), "language": "zh-Hans" if lang == "zh" else "en", "page_count": 1,
            "hazards": [], "hazard_count": 0, "split": "main", "coding_context": "device",
            **_truth(page, tab, lang)})
    return out
