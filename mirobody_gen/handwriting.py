"""Handwritten files: notebook logs, a doctor's note in a clinic booklet, a printed form filled in by hand.

手写文件：居家记录本、门诊病历本上医生的手写记录、手填的打印表格。

    mirobody-gen build --seed 7 --out out/p3 --render --handwriting

Handwriting is a way a document comes into being, alongside a printer and an export: the same person,
the same physiology and the same visits, written by a hand instead of printed by a laboratory system.
So a handwritten file has an ordinary `kind` (`home_log`, `outpatient_record`, `lab_slip`), ordinary
truth rows and readings, an ordinary capture tier (`T2` scan, `T3` phone photo) and one more record
field, `handwriting`: the writing tier, the hand, the corrections and ditto marks, a transcript of the
page, where each value was written and how legible the ink still is after capture.

## Where the values come from

* Notebook logs copy the person's own device series (`devices.series_for`): the cuff readings and the
  scale's morning weights are the same numbers the phone health-store batches carry.
* Glucose logs are a glucometer diary, which no device stream has. Fasting values come from the
  physiology model (`physiology.measure`: the person's own baseline, trend and metformin course).
  Post-meal values are solved from the ADAG relation between HbA1c and mean glucose
  (eAG mmol/L = 1.59 × HbA1c − 2.59; Nathan et al., Diabetes Care 2008;31:1473): the day's four-point
  mean equals the eAG of the person's expected HbA1c that day, with meal-to-meal and meter variation
  (σ 0.14 on the log scale, the order of the ISO 15197:2013 meter tolerance). They are recorded under
  the catalogue's plasma keys (`glu`; `ogtt2h`, LOINC 1521-4, two hours after a meal): the catalogue
  has no capillary-meter indicators, and the reading means the same thing to a reader.
* The doctor's note and the form carry the visit's own readings, the ones its printed record shows.

## Truth

A crossed-out value is not a reading; the correction beside it is (`hand.correction`; the struck value
is kept in `handwriting.corrections`). A ditto mark under a date stands for that date (`hand.ditto`): the
printed row holds the value written on that line and its reading carries the date the mark repeats.
A unit written once in the header applies to the column (`unit.in_header_or_reference_only`); none
written anywhere is `unit.missing`. Every row of a handwritten file carries `hand.written`.

## Isolation

Every random choice here comes from streams of its own (`hand:<seed>:<doc>`, `writer:<seed>:<person>`);
nothing draws from the per-person document stream or the institution process the printed files use.
With handwriting off a build is byte-identical to one made without this module; with it on, every
printed file and record is unchanged and the handwritten records follow them in `files.jsonl`.
"""

from __future__ import annotations

import dataclasses
import math
import pathlib
import random
from datetime import date, timedelta

from . import physiology, spec
from .document import Cells, Doc, DocReading, PrintedRow, Table, build_doc, group_of
from .layout import Family
from .model import Encounter, Person
from .person import CORPUS_END
from .render import degrade, hand

#: Page resolution of a handwritten sheet: the generator's scan resolution (`delivery.dpi_of_tier["T2"]`).
DPI = 200
#: The legibility floor a captured page must clear, else capture falls back to a milder scene.
#: Digit height is in delivered pixels; contrast is the luminance the ink removes (0–255) in the faintest
#: 5% of inked tiles. Set by reading pages at the threshold: below them a careful reader starts to guess
#: between 1 and 7, or loses strokes in a shadow.
MIN_DIGIT_PX = 15.0
MIN_CONTRAST = 38.0
#: Glucose log columns → catalogue key.
_GLUCOSE_KEY = {"fasting": "glu", "breakfast": "ogtt2h", "lunch": "ogtt2h", "dinner": "ogtt2h", "post": "ogtt2h"}


def _h() -> dict:
    return spec.handwriting()


def _weighted(rng: random.Random, weights: dict):
    keys = list(weights)
    return rng.choices(keys, weights=[w["weight"] if isinstance(w, dict) else w for w in weights.values()])[0]


# ── Writers ───────────────────────────────────────────────────────────
@dataclasses.dataclass
class Writer:
    tier: str
    pen: hand.Pen
    ink: str

    def describe(self) -> dict:
        return {"face": self.pen.face, "latin_face": self.pen.latin, "ink": self.ink,
                "slant_deg": round(math.degrees(math.atan(self.pen.slant)), 1),
                "digit_px": round(self.pen.digit_px, 1)}


def writer(rng: random.Random, lang: str, digit_px: float, tier: str | None = None) -> Writer:
    """A hand: its tier, face, ink and how far it wanders. `lang` is `zh` or `en`."""
    h = _h()
    tier = tier or _weighted(rng, {k: v["weight"] for k, v in h["tiers"].items()})
    t = h["tiers"][tier]
    face = _weighted(rng, t["faces"][lang])
    latin = _weighted(rng, t["faces"]["en"]) if lang == "zh" else None
    ink = _weighted(rng, h["inks"])
    return Writer(tier=tier, pen=hand.make_pen(rng, t, face, latin, tuple(h["inks"][ink]["rgb"]), digit_px), ink=ink)


# ── Values ────────────────────────────────────────────────────────────
def _expected(person: Person, key: str, day: date) -> float:
    """A person's indicator on a day without measurement noise (the centre `physiology.measure` draws around)."""
    base = physiology.center_of(key, person.sex) * person.baseline.get(key, 1.0)
    return base * physiology.trend_factor(person, key, day) * physiology.event_factor(person, key, day)[0]


def glucose_day(seed: int, person: Person, day: date, meals: list[str]) -> dict[str, float]:
    """A glucometer day: fasting plus the given post-meal readings, mmol/L to one decimal."""
    r = random.Random(f"glucose:{seed}:{person.person_id}:{day.isoformat()}")
    out = {"fasting": round(min(max(physiology.measure(r, person, "glu", day), 3.2), 22.0), 1)}
    fasting = _expected(person, "glu", day)
    eag = 1.59 * _expected(person, "hba1c", day) - 2.59
    post = max((4 * eag - fasting) / 3, fasting + 0.8)
    for meal in meals:
        out[meal] = round(min(max(post * math.exp(r.gauss(0, 0.14)), 3.5), 25.0), 1)
    return out


def _date_text(fmt: str, day: date) -> str:
    v = _h()["logs"]["en"]
    return fmt.format(y=day.year, m=day.month, d=day.day, mon=v["months_short"][day.month - 1],
                      month=v["months"][day.month - 1])


# ── A file under construction ────────────────────────────────────────
@dataclasses.dataclass
class Written:
    doc: Doc
    sheet: hand.Sheet
    writer: Writer
    scope: str                            # page: everything is handwritten / values: a printed form filled in
    paper: str
    transcript: list[str]
    encounter: Encounter
    corrections: list[dict] = dataclasses.field(default_factory=list)
    dittos: list[dict] = dataclasses.field(default_factory=list)
    printed_box: tuple[int, int, int, int] | None = None     # printed truth on the page (forms)


def _family(base: Family, family_id: str, institution: str, kind: str, language: str, fmt: str,
            columns: tuple[str, ...] = (), headers: dict | None = None) -> Family:
    return dataclasses.replace(base, family_id=family_id, institution=institution, kind=kind, language=language,
                               fmt=fmt, columns=columns, headers=headers or {}, bilingual_header=False,
                               furniture=(), watermark=False, signatures=())


def _reading(doc: Doc, p_index: int, key: str, value: str, observed: str, role: str, status: str = "") -> None:
    item = spec.indicators()[key]
    doc.printed[p_index].readings.append(len(doc.readings))
    doc.readings.append(DocReading(key=key, loinc=item.get("loinc"), canonical_value=float(value), value_text=value,
                                   unit_ucum=item["unit"], value_kind="quantitative", status=status,
                                   observed=observed, role=role,
                                   expect_resolvable=item.get("expect_resolvable", False), printed_row=p_index))


def slip(value: str, rng: random.Random) -> str:
    """The slip a hand makes and crosses out: one digit off, or two neighbouring digits swapped."""
    digits = [i for i, ch in enumerate(value) if ch.isdigit()]
    if len(digits) >= 2 and rng.random() < 0.4:
        i, j = digits[0], digits[1]
        if value[i] != value[j] and j == i + 1:
            return value[:i] + value[j] + value[i] + value[j + 1:]
    i = rng.choice(digits)
    leading = i == digits[0] and len(digits) > 1
    options = [d for d in "0123456789" if d != value[i] and not (leading and d == "0")]
    near = [d for d in options if abs(int(d) - int(value[i])) == 1]
    new = rng.choice(near if near and rng.random() < 0.6 else options)
    return value[:i] + new + value[i + 1:]


def _write_value(w: Written, text: str, x: float, baseline: float, pen: hand.Pen, row: int, field: str,
                 wrong: str | None) -> float:
    """Write a value; with `wrong`, first write the slip, strike it and write the value after it.
    Returns where the pen stopped."""
    sheet = w.sheet
    if wrong:
        box = sheet.write(wrong, x, baseline, pen, role="struck", row=row, field=field)
        sheet.strike(box, pen)
        x = box[2] + pen.digit_px * 0.6
        w.corrections.append({"row": row, "struck": wrong, "written": text})
        w.doc.mark("hand.correction", "incident", [row])
    sheet.write(text, x, baseline, pen, role="value", row=row, field=field)
    return sheet.log[-1]["end_x"]


def _banner(banner: bool) -> str | None:
    return spec.templates()["banner"] if banner else None


# ── Notebook logs ─────────────────────────────────────────────────────
def build_log(seed: int, person: Person, base: Family, lang: str, log_kind: str, days: list[dict], doc_id: str,
              banner: bool, columns: dict | None = None) -> Written:
    """A notebook page of home readings. `days` = [{"day": date, "entries": [{"time": "HH:MM", "values": {...}}]}];
    a second entry on a day is the evening reading, written under a ditto mark."""
    rng = random.Random(f"hand:{seed}:{doc_id}")
    h = _h()
    v = h["logs"][lang]
    m = h["messes"]
    lk = v[log_kind]
    title = rng.choice(lk["titles"])
    columns = dict(columns or rng.choice(lk["columns"]))
    # Six columns do not fit an A5 page at a readable size: people with that much to record use B5.
    stock = dict(h["papers"]["notebook"])
    if len(columns) >= 6:
        stock["sizes_mm"] = [max(stock["sizes_mm"], key=lambda size: size[0])]
    paper, ruling = hand.ruled_paper(rng, stock, DPI, _banner(banner))
    sheet = hand.Sheet(paper, f"{seed}:{doc_id}")
    # One person, one hand: the writer is sticky across all of a person's notebook pages.
    w8r = writer(random.Random(f"writer:{seed}:{person.person_id}"), lang, ruling.spacing * 0.46)
    pen = w8r.pen
    units = lk["units"]
    if rng.random() < m["unit_in_header"]:
        unit_mode = "header"
    elif log_kind == "weight" and rng.random() >= m["unit_nowhere"]:
        unit_mode = "value"              # "68.5kg": only weights get a unit after every entry
    else:
        unit_mode = "none"
    roles = [r for r in columns if r != "note"] + ["note"]
    value_roles = [r for r in roles if r not in ("date", "time", "note")]

    def unit_of(role: str) -> str:
        return units.get("glucose") if log_kind == "glucose" else units.get(role, "")

    header_text = {r: columns[r] for r in roles}
    title_text = title
    if unit_mode == "header":
        if log_kind == "glucose":       # one unit for the whole page, on the title line
            title_text = f"{title}({units['glucose']})" if lang == "zh" else f"{title} ({units['glucose']})"
        else:
            for r in value_roles:
                header_text[r] = f"{columns[r]}({unit_of(r)})" if lang == "zh" else f"{columns[r]} ({unit_of(r)})"

    first_day = days[0]["day"]
    family = _family(base, f"hand:{person.person_id}", "", "home", "zh-Hans" if lang == "zh" else "en",
                     "handwriting", columns=tuple(f"log_{r}" for r in roles), headers=header_text)
    doc = Doc(doc_id=doc_id, person_id=person.person_id, family=family, title=title, subject=[], dates=[],
              tables=[], banner=banner, kind="home_log")
    w = Written(doc=doc, sheet=sheet, writer=w8r, scope="page", paper="ruled_notebook",
                transcript=[_banner(banner)] if banner else [],
                encounter=Encounter(person_id=person.person_id, exam_date=first_day, exam_type="home_log",
                                    exam_location="home", panels=(log_kind,)))
    lines = ruling.rules
    lift = ruling.spacing * 0.17
    line = 0
    sheet.write(title_text, ruling.left, lines[line] - lift, pen.sized(1.12), role="title")
    w.transcript.append(title_text)
    line += 1
    if rng.random() < m["page_year"]:
        month = (v["month_line"].format(y=first_day.year, m=first_day.month) if lang == "zh" else
                 v["month_line"].format(month=v["months"][first_day.month - 1], y=first_day.year))
        sheet.write(month, ruling.left + ruling.spacing * rng.uniform(0.2, 1.2), lines[line] - lift, pen, role="title")
        w.transcript.append(month)
        line += 1

    # Every cell's text first, so the columns can be measured before anything is written.
    date_fmt = rng.choice(v["date_formats"])
    word_times = log_kind == "bp" and rng.random() < 0.35
    rows: list[dict] = []
    for d in days:
        for k, e in enumerate(d["entries"]):
            cells: dict[str, str] = {"date": _date_text(date_fmt, d["day"]) if k == 0 else "〃"}
            if "time" in columns:
                hour = int(e["time"][:2])
                cells["time"] = (lk["time_words"]["morning" if hour < 12 else "evening"] if word_times
                                 else f"{hour}:{e['time'][3:5]}")
            values = {}
            for r in value_roles:
                if r == "bp":
                    values[r] = f"{e['values']['sbp']}/{e['values']['dbp']}"
                elif r in e["values"]:
                    values[r] = str(e["values"][r])
            cells["note"] = rng.choice(lk["notes"]) if rng.random() < 0.3 else ""
            rows.append({"day": d["day"], "cells": cells, "values": values, "raw": e["values"]})
    slip_at = None
    if rng.random() < m["correction_log"]:
        i = rng.randrange(len(rows))
        candidates = [r for r in value_roles if r in rows[i]["values"]]
        if candidates:
            r = rng.choice(candidates)
            slip_at = (i, r, slip(rows[i]["values"][r], rng))

    def shown(i: int, r: str) -> str:
        """The value as written: the number, plus its unit when this page writes units after values."""
        return rows[i]["values"][r] + (unit_of(r) if unit_mode == "value" else "")

    # Fit the columns to the page: narrower gaps first, then a slightly smaller hand (twice at most: a hand
    # does not shrink to a third of its size to fit a table), then notes that do not fit are left out.
    room = ruling.right - ruling.left
    gap = ruling.spacing * 0.55
    for attempt in range(4):
        widths = {}
        for r in roles:
            texts = [header_text[r]] + [row["cells"].get(r, "") for row in rows if r != "note"]
            if r in value_roles:
                texts += [shown(i, r) for i, row in enumerate(rows) if r in row["values"]]
            widths[r] = max(sheet.measure(t, pen) for t in texts)
            if slip_at and slip_at[1] == r:
                widths[r] = max(widths[r], sheet.measure(slip_at[2], pen) + pen.digit_px * 0.6
                                + sheet.measure(shown(slip_at[0], r), pen))
        if sum(widths.values()) + gap * 1.25 * (len(roles) - 1) <= room:
            break
        if attempt == 0:
            gap = ruling.spacing * 0.4
        elif attempt < 3:
            pen = pen.sized(0.93)
    left_for_notes = room - (sum(widths.values()) - widths["note"]) - gap * 1.25 * (len(roles) - 1)
    widths["note"] = max(left_for_notes, widths["note"])
    for row in rows:
        if row["cells"]["note"] and sheet.measure(row["cells"]["note"], pen) > left_for_notes:
            row["cells"]["note"] = ""
    xs, x = {}, ruling.left + rng.uniform(0, ruling.spacing * 0.2)
    for r in roles:
        xs[r] = x
        x += widths[r] + gap * rng.uniform(0.8, 1.25)
    for r in roles:
        sheet.write(header_text[r], xs[r] + rng.uniform(-3, 3), lines[line] - lift, pen, role="label")
    w.transcript.append(" ".join(header_text[r] for r in roles))
    header_line = line
    line += 1
    if line + len(rows) > len(lines):
        raise ValueError(f"{doc_id}: {len(rows)} rows do not fit the page")
    if rng.random() < m["ruled_columns"]:
        y_top = lines[header_line] - ruling.spacing * 0.95
        y_bottom = lines[line + len(rows) - 1] + 2
        for r in roles[1:]:
            sheet.ruled_column(xs[r] - gap * 0.45, y_top, y_bottom, pen)

    for i, row in enumerate(rows):
        base_y = lines[line] - lift
        parts = []
        for r in roles:
            jitter = rng.uniform(-4, 4)
            if r in value_roles:
                if r not in row["values"]:
                    continue
                p_index = len(doc.printed)
                unit = unit_of(r) if unit_mode != "none" else ""
                doc.printed.append(PrintedRow(item_name=header_text[r], item_value=row["values"][r], item_unit=unit,
                                              item_range="", is_abnormal=""))
                observed = row["day"].isoformat()
                if r == "bp":
                    _reading(doc, p_index, "sbp", str(row["raw"]["sbp"]), observed, "export")
                    _reading(doc, p_index, "dbp", str(row["raw"]["dbp"]), observed, "export")
                    doc.mark("value.pair_in_one_cell", "content", [p_index])
                else:
                    key = _GLUCOSE_KEY.get(r, r)
                    _reading(doc, p_index, key, row["values"][r], observed, "export")
                doc.mark("hand.written", "content", [p_index])
                doc.mark({"header": "unit.in_header_or_reference_only", "value": "unit.glued_to_value",
                          "none": "unit.missing"}[unit_mode], "layout", [p_index])
                if row["cells"]["date"] == "〃":
                    w.dittos.append({"row": p_index, "column": "date", "stands_for": observed})
                    doc.mark("hand.ditto", "incident", [p_index])
                wrong = slip_at[2] if slip_at and slip_at[:2] == (i, r) else None
                end = _write_value(w, row["values"][r], xs[r] + jitter, base_y, pen, p_index, r, wrong)
                if unit_mode == "value":
                    sheet.write(unit_of(r), end + pen.digit_px * 0.05, base_y, pen, role="unit", field=r)
                parts.append(shown(i, r))
                continue
            text = row["cells"].get(r, "")
            if not text:
                continue
            if r == "date" and text == "〃":
                sheet.ditto(xs[r] + ruling.spacing * 0.3 + jitter, base_y, pen, field="date")
            else:
                sheet.write(text, xs[r] + jitter, base_y, pen, role="date" if r == "date" else "text", field=r)
            parts.append(text)
        w.transcript.append(" ".join(parts))
        line += 1
    doc.mark("meta.date_format_dialect", "layout")
    doc.tables.append(Table(columns=[f"log_{r}" for r in roles], headers=[[header_text[r] for r in roles]],
                            rows=[Cells(name="", value="", unit="", range="", flag="",
                                        raw={f"log_{r}": row["cells"].get(r, row["values"].get(r, "")) for r in roles})
                                  for row in rows]))
    return w


# ── A doctor's note ───────────────────────────────────────────────────
_VITALS = ("temp", "pulse", "resp", "bp", "spo2", "weight")


def build_note(seed: int, person: Person, enc: Encounter, family: Family, lang: str, doc_id: str,
               banner: bool) -> Written | None:
    """A clinic visit written up by hand in the patient's booklet: visit line, how the patient is, the
    vitals inline, a short examination, the diagnosis and the plan, a scribbled signature."""
    readings = {r.key: r for r in enc.readings}
    have = [k for k in _VITALS if (k == "bp" and "sbp" in readings and "dbp" in readings) or k in readings]
    if len([k for k in have if k != "weight"]) < 2:
        return None
    rng = random.Random(f"hand:{seed}:{doc_id}")
    h = _h()
    n = h["note"][lang]
    header = rng.choice(n["header"])
    colon = "：" if lang == "zh" else ":"
    labels = {role: label + colon for role, label in n["fields"].items()}
    printed = [(header, None, 16.0, 15.0)]
    field_x = {}
    for k, role in enumerate(labels):
        field_x[role] = 12.0 + 44.0 * k
        printed.append((labels[role], field_x[role], 25.0, 9.0))
    paper, ruling = hand.ruled_paper(rng, h["papers"]["booklet"], DPI, _banner(banner), header=printed)
    sheet = hand.Sheet(paper, f"{seed}:{doc_id}")
    w8r = writer(rng, lang, ruling.spacing * 0.42)
    pen = w8r.pen
    fam = dataclasses.replace(family, fmt="handwriting", furniture=(), watermark=False, signatures=())
    doc = Doc(doc_id=doc_id, person_id=person.person_id, family=fam, title=header, subject=[], dates=[],
              tables=[], banner=banner, kind="outpatient_record")
    w = Written(doc=doc, sheet=sheet, writer=w8r, scope="page", paper="clinic_booklet",
                transcript=([_banner(banner)] if banner else []) + [header], encounter=enc)

    # The booklet's printed fields, filled in.
    label_font = hand.printed_face(int(round(9 * DPI / 72)))
    field_texts = {"date": _date_text(rng.choice(n["date_formats"]), enc.exam_date),
                   "department": rng.choice(n["departments"])}
    fields_line = []
    for role, label in labels.items():
        x = hand.mm(field_x[role], DPI) + label_font.getlength(label) + 8
        sheet.write(field_texts[role], x, hand.mm(25.0, DPI), pen.sized(0.85), role="date" if role == "date" else "text",
                    field=role)
        fields_line.append(f"{label}{field_texts[role]}" if lang == "zh" else f"{label} {field_texts[role]}")
    w.transcript.append(("  " if lang == "zh" else "   ").join(fields_line))
    doc.dates = [{"label": n["fields"]["date"], "role": "collected", "printed": field_texts["date"],
                  "iso": enc.exam_date.isoformat()}]

    lift = ruling.spacing * 0.17
    lines = ruling.rules
    right = ruling.right
    texts: list[str] = []
    # The body starts on the first rule clear of the booklet's printed fields and their handwriting.
    first = next(i for i, y in enumerate(lines) if y >= hand.mm(25.0, DPI) + ruling.spacing * 1.5)
    state = {"line": first - 1, "x": ruling.left}

    def start_line() -> None:
        state["line"] += 1
        state["x"] = ruling.left + rng.uniform(0, ruling.spacing * 0.3)
        if state["line"] >= len(lines) - 1:
            raise ValueError(f"{doc_id}: the note does not fit the page")
        texts.append("")

    def put(text: str, *, role: str = "text", row: int | None = None, field: str | None = None,
            wrong: str | None = None, glue: bool = False) -> None:
        width = sheet.measure(text, pen) + (sheet.measure(wrong, pen) + pen.digit_px * 0.6 if wrong else 0)
        if not glue and state["x"] + width > right and texts[-1]:
            start_line()
        y = lines[state["line"]] - lift
        x = state["x"] if not glue else state["x"] - pen.digit_px * 0.5
        if role == "value":
            end = _write_value(w, text, x, y, pen, row, field, wrong)
        else:
            sheet.write(text, x, y, pen, role=role, row=row, field=field)
            end = sheet.log[-1]["end_x"]
        texts[-1] = (texts[-1] + text) if glue or (lang == "zh" and texts[-1] and not text[:1].isascii()) \
            else (texts[-1] + " " + text).strip()
        state["x"] = end + pen.digit_px * rng.uniform(0.45, 0.8)

    def sentence(text: str) -> None:
        """A run of words, wrapped at spaces (English) or between any two characters (Chinese)."""
        if lang == "en":
            for word in text.split(" "):
                put(word)
            return
        chunk = ""
        for ch in text:
            if chunk and state["x"] + sheet.measure(chunk + ch, pen) > right:
                put(chunk)
                start_line()
                chunk = ""
            chunk += ch
        if chunk:
            put(chunk)

    uri = any(e.name == "急性上呼吸道感染" and abs((enc.exam_date - e.start).days) <= 10 for e in person.events)
    dx_key = "uri" if uri else person.archetype
    dx = n["dx"].get(dx_key, n["dx"]["healthy"])
    start_line()
    sentence(rng.choice(n["visit"]["first" if uri else "followup"]).format(dx=dx))
    pool = n["status"].get(dx_key, []) + n["status"]["general"]
    start_line()
    sentence(("，" if lang == "zh" else " ").join(rng.sample(pool, k=rng.randint(1, 2))))

    # Vitals inline, as a doctor writes them: label, value, sometimes a unit, glued or spaced.
    start_line()
    order = [k for k in _VITALS if k in have]
    slip_key = rng.choice(order) if rng.random() < h["messes"]["correction_note"] else None
    vital_rows: list[int] = []
    for key in order:
        vs = n["vitals"][key]
        label = rng.choice(vs["labels"])
        unit = rng.choice(vs["units"])
        value = f"{readings['sbp'].value}/{readings['dbp'].value}" if key == "bp" else readings[key].value
        p_index = len(doc.printed)
        doc.printed.append(PrintedRow(item_name=label.rstrip(":："), item_value=value, item_unit=unit, item_range="",
                                      is_abnormal=""))
        observed = enc.exam_date.isoformat()
        if key == "bp":
            for k in ("sbp", "dbp"):
                _reading(doc, p_index, k, readings[k].value, observed, "current", readings[k].status)
            doc.mark("value.pair_in_one_cell", "content", [p_index])
        else:
            _reading(doc, p_index, key, value, observed, "current", readings[key].status)
        vital_rows.append(p_index)
        doc.mark("hand.written", "content", [p_index])
        wrong = slip(value, rng) if key == slip_key else None
        need = sum(sheet.measure(t, pen) for t in (label, value, unit, wrong or "")) + pen.digit_px * 2.4
        if state["x"] + need > right and texts[-1]:
            start_line()          # a label and its value stay on one line
        put(label, role="label", field=key)
        put(value, role="value", row=p_index, field=key, wrong=wrong)
        if unit:
            glued = unit in ("°C", "%", "次/分") or rng.random() < 0.5
            put(unit, role="unit", field=key, glue=glued)
            if glued:
                doc.mark("unit.glued_to_value", "layout", [p_index])
        else:
            doc.mark("unit.missing", "layout", [p_index])
    doc.mark("value.multiple_per_row", "layout", vital_rows)

    start_line()
    exam_pool = n["exam_uri"] if uri else n["exam"]
    sentence(("，" if lang == "zh" else " ").join(rng.sample(exam_pool, k=min(len(exam_pool), rng.randint(1, 2)))))
    start_line()
    put(rng.choice(n["dx_label"]), role="label")
    sentence(dx)
    start_line()
    put(rng.choice(n["plan_label"]), role="label")
    sentence(("，" if lang == "zh" else ", ").join(rng.sample(n["plans"], k=rng.randint(2, 3))))
    # A scribbled signature on the next line. No letters: a signature would be a name.
    y = lines[min(state["line"] + 1, len(lines) - 1)]
    sig_w = ruling.spacing * rng.uniform(2.2, 3.2)
    sheet.scribble((right - sig_w, y - ruling.spacing * 0.75, right, y - ruling.spacing * 0.1), pen)
    w.transcript += [t for t in texts if t]
    doc.blocks = [_narrative([t for t in texts if t])]
    doc.mark("meta.narrative_block", "incident")
    return w


def _narrative(lines: list[str]):
    from .book import Block

    return Block(kind="narrative", title="", rows=[("", t) for t in lines], section_id="handwritten_note")


# ── A printed form filled in by hand ─────────────────────────────────
_FORM_GROUPS = ("vitals", "glucose_full")


def build_form(seed: int, person: Person, enc: Encounter, family: Family, lang: str, doc_id: str,
               banner: bool) -> Written | None:
    """The institution's own printed form (its typeface, layout family, banner, rendered by `render/pdf.py`)
    with the result column printed blank and filled in by a nurse's hand."""
    import fitz
    from PIL import Image

    from . import hazards
    from .render import pdf

    groups = [g for g in _FORM_GROUPS if any(group_of(r.key) == g for r in enc.readings)]
    keep = [r for r in enc.readings if group_of(r.key) in groups and r.value_kind == "quantitative"]
    if len(keep) < 4:
        return None
    rng = random.Random(f"hand:{seed}:{doc_id}")
    f = _h()["form"][lang]
    cols = ("name", "result", "unit", "reference") if "reference" in family.columns else ("name", "result", "unit")
    headers = {c: family.headers.get(c) or f["headers"][c] for c in cols}
    form_family = dataclasses.replace(
        family, fmt="pdf", columns=cols, headers=headers, flag_at="none", flag_high="", flag_low="", flag_normal="",
        unit_at="column", previous_column=False, subject_in_table=False, bilingual_header=False, decimal_comma=False,
        name_style="native", watermark=False, font_size=max(family.font_size, 10.5), rules="grid")
    doc = build_doc(rng, doc_id, person, dataclasses.replace(enc, readings=keep[:12]), groups, form_family, {},
                    banner=banner)
    doc.title = rng.choice(f["titles"])
    doc.kind = "lab_slip"
    hazards.detect(doc)
    # Each result cell prints a placeholder as wide as a hand needs (wider where the nurse will cross a value
    # out and write the correction beside it); it is found on the page and redacted.
    rows = [c.printed for table in doc.tables for c in table.rows if c.printed is not None]
    slip_at = rng.choice(rows) if rng.random() < _h()["messes"]["correction_form"] else None
    slots: dict[int, str] = {}
    for table in doc.tables:
        for c in table.rows:
            if c.printed is not None:
                slots[c.printed] = f"Q{len(slots):02d}Q" + "M" * (11 if c.printed == slip_at else 5)
                c.value = slots[c.printed]
    reported = next((d["iso"] for d in doc.dates if d["role"] == "reported"),
                    doc.dates[0]["iso"] if doc.dates else f"{enc.exam_date.isoformat()}T09:00:00")
    data, pages = pdf.render(doc, reported)
    if pages != 1:
        return None
    k = DPI / 72
    boxes: dict[int, tuple[float, float, float, float]] = {}
    with fitz.open("pdf", data) as pdf_doc:
        page = pdf_doc[0]
        text = page.get_text()
        words = page.get_text("words")
        for p_index, token in slots.items():
            hits = page.search_for(token)
            if len(hits) != 1:
                return None
            r = hits[0]
            boxes[p_index] = (r.x0 * k, r.y0 * k, r.x1 * k, r.y1 * k)
            page.add_redact_annot(r, fill=(1, 1, 1))
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE)
        pix = page.get_pixmap(dpi=DPI, colorspace=fitz.csRGB, alpha=False)
        paper = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    printed_box = (int(min(wd[0] for wd in words) * k), int(min(wd[1] for wd in words) * k),
                   int(max(wd[2] for wd in words) * k), int(max(wd[3] for wd in words) * k))
    for p_index, token in slots.items():
        text = text.replace(token, doc.printed[p_index].item_value)
    sheet = hand.Sheet(paper, f"{seed}:{doc_id}")
    row_h = min(b[3] - b[1] for b in boxes.values())
    w8r = writer(rng, lang, row_h * 0.78)
    pen = w8r.pen
    w = Written(doc=doc, sheet=sheet, writer=w8r, scope="values", paper="printed_form",
                transcript=[line for line in text.splitlines() if line.strip()], encounter=enc, printed_box=printed_box)
    for p_index, (x0, y0, x1, y1) in boxes.items():
        value = doc.printed[p_index].item_value
        wrong = slip(value, rng) if p_index == slip_at else None
        need = sheet.measure(value, pen) + (sheet.measure(wrong, pen) + pen.digit_px * 0.6 if wrong else 0)
        room = (x1 - x0) * (0.95 if wrong else 1.1)
        p = pen.sized(room / need) if need > room else pen
        baseline = y1 - (y1 - y0) * rng.uniform(0.12, 0.22)
        _write_value(w, value, x0 + rng.uniform(0, (x1 - x0) * 0.08), baseline, p, p_index, "result", wrong)
        doc.mark("hand.written", "content", [p_index])
    return w


# ── Capture ───────────────────────────────────────────────────────────
def _capture_options(rng: random.Random, tier: str) -> list[tuple[str, str, str]]:
    """(capture tier, scene, severity): the drawn one first, then milder fall-backs for the legibility floor."""
    t = _h()["tiers"][tier]
    cap = _weighted(rng, t["capture"])
    scene = _weighted(rng, t["scenes"][cap])
    severity = _weighted(rng, t["severity"])
    options = [(cap, scene, severity)]
    if severity != "mild":
        options.append((cap, scene, "mild"))
    calm = "flatbed_scan" if cap == "T2" else "clean_photo"
    if scene != calm:
        options.append((cap, calm, "mild"))
    return options


def capture(w: Written, out_root: pathlib.Path, rel: str, rng: random.Random
            ) -> tuple[pathlib.Path, dict, dict] | None:
    """Scan or photograph the page through `render/degrade.py`, keeping the first option under which
    every written value clears the legibility floor. Returns (path, delivery record, legibility), or None
    when no option does: a page a careful reader could not read is not delivered."""
    page = w.sheet.render()
    bare = w.sheet.render(values=False)
    box = w.sheet.content_box()
    if w.printed_box:
        pb = w.printed_box
        box = (min(box[0], pb[0]), min(box[1], pb[1]), max(box[2], pb[2]), max(box[3], pb[3]))
    margins = {0: box}
    values = [e for e in w.sheet.log if e["role"] == "value"]
    tried: list[str] = []
    for cap, scene, severity in _capture_options(rng, w.writer.tier):
        seed_text = f"{w.doc.doc_id}:hand"
        shot, ops = degrade.apply_scene([page], scene, severity, seed_text, margins)
        without, ops_bare = degrade.apply_scene([bare], scene, severity, seed_text, margins)
        if ops != ops_bare:
            raise RuntimeError(f"{w.doc.doc_id}: scene {scene} drew different operators for the same seed")
        per_value = hand.value_legibility(shot[0], without[0], values, ops, page.size)
        if per_value is None:
            raise RuntimeError(f"{w.doc.doc_id}: scene {scene} moves pixels in a way legibility cannot follow")
        legible = {"min_digit_px": min(v["digit_px"] for v in per_value),
                   "min_contrast": min(v["contrast"] for v in per_value),
                   "median_contrast": sorted(v["contrast"] for v in per_value)[len(per_value) // 2]}
        tried.append(f"{scene}/{severity}")
        if legible["min_digit_px"] >= MIN_DIGIT_PX and legible["min_contrast"] >= MIN_CONTRAST:
            break
    if legible["min_digit_px"] < MIN_DIGIT_PX or legible["min_contrast"] < MIN_CONTRAST:
        return None
    legible["rejected"] = tried[:-1]
    path = out_root / f"{rel}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(degrade.encode(shot, "jpg", w.doc.title))
    delivery = {"tier": cap, "scene": scene, "severity": severity, "ops": ops, "container": "jpg", "annotations": [],
                "dpi": DPI, "image_size": list(shot[0].size)}
    return path, delivery, legible


def extra(w: Written, legible: dict) -> dict:
    """The record's `handwriting` field."""
    boxes = [{"row": e["row"], "text": e["text"], "box": e["box"]} for e in w.sheet.log
             if e["role"] == "value" and e["row"] is not None]
    return {"handwriting": {
        "tier": w.writer.tier, "scope": w.scope, "paper": w.paper, "hand": w.writer.describe(),
        "corrections": w.corrections, "dittos": w.dittos, "value_boxes": boxes,
        "page_size": [w.sheet.width, w.sheet.height], "legibility": legible, "transcript": w.transcript,
    }}


# ── Which handwritten files a person has ─────────────────────────────
def _bp_days(series: dict, rng: random.Random, second: float) -> list[dict] | None:
    by_day: dict[str, dict[str, dict]] = {}
    for r in series["records"]:
        if r["_metric"] in ("sbp", "dbp", "hr"):
            by_day.setdefault(r["time"][:10], {}).setdefault(r["time"][11:16], {})[r["_metric"]] = r["value"]
    complete = {d: [t for t in sorted(by_day[d]) if {"sbp", "dbp", "hr"} <= set(by_day[d][t])] for d in by_day}
    days = [d for d in sorted(complete) if complete[d]]
    if len(days) < 7:
        return None
    n = rng.randint(7, 10)
    start = rng.randint(0, len(days) - n)
    out, rows = [], 0
    for d in days[start:start + n]:
        times = complete[d]
        pick = times[:2] if len(times) > 1 and rows < 12 and rng.random() < second else times[:1]
        rows += len(pick)
        out.append({"day": date.fromisoformat(d),
                    "entries": [{"time": t, "values": {"sbp": by_day[d][t]["sbp"], "dbp": by_day[d][t]["dbp"],
                                                       "pulse": by_day[d][t]["hr"]}} for t in pick]})
    return out


def _weight_days(series: dict, rng: random.Random) -> list[dict] | None:
    by_day: dict[str, dict] = {}
    for r in series["records"]:
        if r["_metric"] == "weight":
            by_day.setdefault(r["time"][:10], r)
    days = sorted(by_day)
    if len(days) < 7:
        return None
    n = rng.randint(7, 14)
    start = rng.randint(0, len(days) - n)
    return [{"day": date.fromisoformat(d), "entries": [{"time": by_day[d]["time"][11:16],
                                                        "values": {"weight": by_day[d]["value"]}}]}
            for d in days[start:start + n]]


def _glucose_days(seed: int, person: Person, encounters: list[Encounter], rng: random.Random,
                  columns: dict) -> list[dict] | None:
    if not encounters:
        return None
    first = encounters[0].exam_date
    last = min(encounters[-1].exam_date, CORPUS_END - timedelta(days=20))
    if last <= first:
        return None
    start = first + timedelta(days=rng.randint(0, (last - first).days))
    meals = [c for c in ("breakfast", "lunch", "dinner", "post") if c in columns]
    out = []
    for k in range(rng.randint(7, 12)):
        day = start + timedelta(days=k)
        today = [mk for mk in meals if rng.random() < (0.7 if mk == "post" else 0.55)]
        out.append({"day": day, "entries": [{"time": "07:00", "values": glucose_day(seed, person, day, today)}]})
    return out


def render_person(seed: int, person: Person, group: str, base: Family, visits: list[tuple[int, Encounter, Family]],
                  files_root: pathlib.Path, banner: bool) -> list[tuple[Doc, pathlib.Path, dict, Encounter, dict]]:
    """Every handwritten file of one person: (doc, path, delivery, encounter, record extra).

    `group` is the person's language group and `base` any layout family of theirs (the notebook pages
    borrow its scalar fields); `visits` are (visit index, encounter, the visit's layout family) as the
    printed files were laid out. Nothing here draws from the generator's other random streams."""
    from . import devices
    from .person import person_lang

    rng = random.Random(f"hand:{seed}:{person.person_id}")
    h = _h()
    rates = h["rates"]
    lang = spec.doc_lang(group)
    jobs = []
    series = devices.series_for(person, seed, person_lang(seed, person.person_id))
    k = 0
    if series["habits"]["cuff"] and rng.random() < rates["bp_log"]:
        days = _bp_days(series, rng, h["messes"]["second_reading"])
        if days:
            k += 1
            jobs.append((f"{person.person_id}_hlog{k:02d}",
                         lambda doc_id, days=days: build_log(seed, person, base, lang, "bp", days, doc_id, banner)))
    if person.archetype == "prediabetes_to_t2dm" and rng.random() < rates["glucose_log"]:
        columns = rng.choice(h["logs"][lang]["glucose"]["columns"])
        days = _glucose_days(seed, person, [v[1] for v in visits], rng, columns)
        if days:
            k += 1
            jobs.append((f"{person.person_id}_hlog{k:02d}",
                         lambda doc_id, days=days, columns=columns:
                         build_log(seed, person, base, lang, "glucose", days, doc_id, banner, columns)))
    if series["habits"]["weigh_rate"] >= 0.5 and rng.random() < rates["weight_log"]:
        days = _weight_days(series, rng)
        if days:
            k += 1
            jobs.append((f"{person.person_id}_hlog{k:02d}",
                         lambda doc_id, days=days: build_log(seed, person, base, lang, "weight", days, doc_id, banner)))
    for idx, enc, family in visits:
        if family.language not in ("zh-Hans", "en"):
            continue                # the Chinese faces are simplified-script faces
        vlang = "zh" if family.language == "zh-Hans" else "en"
        stem = f"{person.person_id}_{enc.exam_date.isoformat()}_e{idx:02d}"
        if enc.exam_type == "clinic" and rng.random() < rates["clinic_note"]:
            jobs.append((f"{stem}hn", lambda doc_id, enc=enc, family=family, vlang=vlang:
                         build_note(seed, person, enc, family, vlang, doc_id, banner)))
        if enc.exam_type == "routine" and rng.random() < rates["form"]:
            jobs.append((f"{stem}hf", lambda doc_id, enc=enc, family=family, vlang=vlang:
                         build_form(seed, person, enc, family, vlang, doc_id, banner)))
    out = []
    for doc_id, make in jobs:
        w = make(doc_id)
        if w is None:
            continue
        shot = capture(w, files_root, f"{person.person_id}/{doc_id}", random.Random(f"hand-capture:{seed}:{doc_id}"))
        if shot is None:
            continue
        path, delivery, legible = shot
        out.append((w.doc, path, delivery, w.encounter, extra(w, legible)))
    return out
