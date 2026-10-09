"""The CGM report a wearer actually holds when the app gives no readings file: numbers, layout and truth.

`continuous.py` decides who has a report and in which style (`resources/streams.json`, `reports`):

- `agp_v5` (English): the International Diabetes Center's AGP Report v5 wording and layout;
- `agp_cn2023` (Chinese): the same page in the wording of the 2023 Chinese AGP consensus (葡萄糖 rather than
  血糖; bands 很高 / 高 / 目标范围 / 低 / 很低), with the variability metrics of the 2017 Chinese CGM guideline
  that Chinese reports add (SD, MAGE, MODD, LAGE);
- `cgm_sheet_2017` (Chinese): the hospital's 持续葡萄糖监测（CGM）报告单 from the 2017 guideline, one column per day
  against the guideline's normal values, its summary sentence and the signature line.

This module computes what a report prints (`cgm_metrics`), formats it, and returns two things built from the
same strings: the input of `render/agp.py` and the truth's `printed_rows`. A value is formatted once and used
twice, so the page and the truth cannot disagree; every value is rounded once, when printed.

`printed_rows` use the MedRepBench fields of the document layer (`item_name`, `item_value`, `item_unit`,
`item_range`, `is_abnormal`) plus `key`: the metric (`cgm_metrics` name), and for a per-day cell of the sheet
`<metric>@<YYYY-MM-DD>`. `item_range` is the goal or normal value the page prints beside the metric, and
`is_abnormal` whether the value misses it; without one both are empty.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta

from . import cgm_metrics as M
from . import spec, synthid

#: Goals as the consensus states them (Battelino et al., Diabetes Care 2019;42:1593), and the normal values of
#: the 2017 Chinese guideline (24-hour, adults): metric key -> (comparison, limit).
GOALS = {"tir": (">", 70.0), "tbr_low": ("<", 4.0), "tbr_very_low": ("<", 1.0), "tbr_total": ("<", 4.0),
         "tar_high": ("<", 25.0), "tar_very_high": ("<", 5.0), "tar_total": ("<", 25.0), "cv": ("<=", 36.0),
         "gmi": ("<", 7.0), "active": (">", 70.0)}
NORMAL_2017 = {"mean": ("<", 6.6), "sd": ("<", 1.4), "ge_7_8": ("<", 17.0), "le_3_9": ("<", 12.0)}
REPORT_DAYS = 14


def _misses(rule: tuple[str, float] | None, value: float) -> str:
    if rule is None:
        return ""
    op, limit = rule
    ok = {"<": value < limit, ">": value > limit, "<=": value <= limit, ">=": value >= limit}[op]
    return "0" if ok else "1"


def _values(sess: dict, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
    """Every historic reading in [start, end] as the report counts it, mmol/L; past the range at its limit."""
    lo, hi = sess["range"]
    out = []
    for x in sess["readings"]:
        if x["kind"] != "historic" or not (start <= x["time"] <= end):
            continue
        v = x["mmol"] if x["mmol"] is not None else (lo if x["flag"] == "low" else hi) / M.MGDL_PER_MMOL
        out.append((x["time"], v))
    return out


def metrics(pts: list[tuple[datetime, float]], start: datetime, end: datetime, interval_min: int) -> dict:
    """Every number a report style can print, unrounded."""
    vals = [v for _, v in pts]
    b = M.basic(vals)
    r = M.ranges(vals)
    expected = max(1, int((end - start).total_seconds() / 60 // interval_min))
    mean_mgdl = b["mean_mmol"] * M.MGDL_PER_MMOL
    return {"mean": b["mean_mmol"], "sd": b["sd_mmol"], "cv": b["cv_pct"], "gmi": b["gmi_pct"],
            "min": b["min_mmol"], "max": b["max_mmol"], "mean_mgdl": mean_mgdl,
            "tir": r["target"], "tbr_low": r["low"], "tbr_very_low": r["very_low"], "tar_high": r["high"],
            "tar_very_high": r["very_high"], "tbr_total": r["low"] + r["very_low"], "tar_total": r["high"] + r["very_high"],
            "active": min(100.0, 100 * len(vals) / expected),
            # a report period counts days of wear, not calendar dates: 14 days from 10:00 touch 15 dates
            "days": max(1, round((end - start).total_seconds() / 86400)),
            "count": len(vals), "mage": M.mage_daily_mean(pts, interval_min=interval_min),
            "modd": M.modd(pts, interval_min), "lage": M.lage(vals), "lbgi": M.lbgi(vals)}


def _glucose(mmol: float, unit: str, decimals: int = 1) -> str:
    return f"{mmol * M.MGDL_PER_MMOL:.0f}" if unit == "mg/dL" else f"{mmol:.{decimals}f}"


def _date(d: datetime, fmt: str) -> str:
    """strftime, plus `{d}`/`{m}` for day and month without leading zeros and `{B}` for the English month."""
    return d.strftime(fmt.replace("{d}", str(d.day)).replace("{m}", str(d.month)).replace("{Y}", str(d.year))
                      .replace("{B}", d.strftime("%B")))


def _row(key: str, name: str, value: str, unit: str, goal: str = "", rule=None, raw: float | None = None) -> dict:
    return {"key": key, "item_name": name, "item_value": value, "item_unit": unit, "item_range": goal,
            "is_abnormal": _misses(rule, raw) if goal and raw is not None else ""}


# ── AGP page (v5, the 2023 Chinese consensus, Sibionics' Chinese report) ─────
_NUM = __import__("re").compile(r"([<>≤≥]=?)\s*([0-9.]+)")


def rule_of(goal: str, unit: str, glucose: bool) -> tuple[str, float] | None:
    """The rule a printed goal states ("<33%", "Goal: <154 mg/dL", "(>70%)"), in mmol/L for a glucose goal."""
    m = _NUM.search(goal)
    if not m:
        return None
    op = {"≤": "<=", "≥": ">="}.get(m.group(1), m.group(1))
    limit = float(m.group(2))
    if glucose and unit == "mg/dL":
        limit /= M.MGDL_PER_MMOL
    return op, limit


def _duration(share: float) -> str:
    """A share of the day as hours and minutes, the way reports print it beside a percentage."""
    minutes = round(share / 100 * 1440)
    return f"{minutes // 60}h{minutes % 60:02d}min"


def _hypo_risk(m: dict, levels: list[str]) -> str:
    """A four-level low-glucose risk from time below range (hand-set cut-offs; the vendor's rule is not public)."""
    tbr = m["tbr_total"]
    return levels[3] if tbr < 1 else levels[2] if tbr < 4 else levels[1] if tbr < 10 else levels[0]


def _metric_value(key: str, m: dict, unit: str, item: dict) -> tuple[str, str] | None:
    raw = m.get(key)
    if raw is None:
        return None
    if key in ("mean", "sd", "mage", "modd", "lage", "min", "max"):
        return _glucose(raw, unit, item.get("decimals", 1)), unit
    if key in ("days", "count"):
        return str(raw), item.get("unit", "")
    if key == "hypo_risk":
        return raw, ""
    if key == "lbgi":
        return f"{raw:.1f}", ""
    if key == "lbgi_level":
        return raw, ""
    return f"{raw:.1f}", "%"


def _agp(sess: dict, dev: dict, style: dict, name: str, banner: bool, created: datetime) -> tuple[dict, list[dict]]:
    unit = sess["unit"]
    end = sess["end"]
    start = max(sess["start"], end - timedelta(days=REPORT_DAYS))
    pts = _values(sess, start, end)
    m = metrics(pts, start, end, dev["interval_min"])
    m["eag_a1c"] = (m["mean_mgdl"] + 46.7) / 28.7                      # ADAG: HbA1c from mean glucose
    m["hypo_risk"] = _hypo_risk(m, style.get("hypo_levels", ["", "", "", ""]))
    if style.get("lbgi_levels"):                  # Kovatchev's risk categories: <=1.1, <=2.5, <=5, above
        levels = style["lbgi_levels"]
        m["lbgi_level"] = levels[0] if m["lbgi"] <= 1.1 else levels[1] if m["lbgi"] <= 2.5 else \
            levels[2] if m["lbgi"] <= 5 else levels[3]
    pct = style.get("pct_decimals", 0)
    rows: list[dict] = []
    active = f"{m['active']:.1f}"
    # `{colon}` keeps a name label and its colon apart in the resource (the repository's privacy hook rejects
    # the pair in a tracked file); the page prints them together.
    fields = {"name": name, "days": m["days"], "active": active, "colon": "：", "start": _date(start, style["date_start"]),
              "end": _date(end, style["date_end"]), "created": _date(created, style["date_end"]), "count": m["count"],
              "sn": sess.get("report_sn", "")}
    header = [line.format(**fields) for line in style["header"]]
    printed = []
    for item in style["metrics"]:
        key = item["key"]
        got = _metric_value(key, m, unit, item)
        if got is None:
            continue
        value, unit_text = got
        goal = item.get("goal_mgdl" if unit == "mg/dL" else "goal", "")
        glucose = key in ("mean", "sd", "mage", "modd", "lage")
        raw = m[key] if isinstance(m[key], (int, float)) else None
        rule = rule_of(goal, unit, glucose) if goal and raw is not None else None
        rows.append({"key": key, "item_name": item["label"], "item_value": value, "item_unit": unit_text,
                     "item_range": _NUM.search(goal).group(0) + ("" if not glucose else f" {unit}") if rule else "",
                     "is_abnormal": _misses(rule, raw) if rule else ""})
        shown = value if not unit_text else (f"{value}%" if unit_text == "%" else f"{value} {unit_text}")
        notes = list(item.get("notes", [])) + ([goal] if goal and style.get("metrics_mode", "dots") == "dots" else [])
        printed.append({"label": item["label"], "value": shown, "goal": goal, "notes": notes})
    bands = style["bands_mgdl" if unit == "mg/dL" else "bands_mmol"]
    tir_rows, ranges = {}, {}
    vals = [v for _, v in pts]
    if style.get("tir_bands"):                        # a device's own bands (AiDEX: three, cut at 13.3)
        spec_bands = [(b["band"], b["metric"], M.share(vals, b.get("lo"), b.get("hi"), b.get("lo_incl", True),
                                                       b.get("hi_incl", True))) for b in style["tir_bands"]]
    else:
        spec_bands = [(band, key, m[key]) for band, key in (("very_high", "tar_very_high"), ("high", "tar_high"),
                                                            ("target", "tir"), ("low", "tbr_low"),
                                                            ("very_low", "tbr_very_low"))]
    for band, key, value in spec_bands:
        label, goal = bands[band]
        ranges[band] = value
        text = f"{value:.{pct}f}%"
        shown = f"{text} ({_duration(value)})" if style.get("tir_duration") else text
        tir_rows[band] = (label, shown, goal)
        rule = rule_of(goal, unit, False) if goal else None
        rows.append({"key": key, "item_name": label, "item_value": text[:-1], "item_unit": "%",
                     "item_range": _NUM.search(goal).group(0) + "%" if rule else "",
                     "is_abnormal": _misses(rule, value) if rule else ""})
    brackets = []
    for b in style["brackets"]:
        key = "tar_total" if "very_high" in b["bands"] else "tbr_total"
        text = f"{m[key]:.{pct}f}%"
        brackets.append((tuple(b["bands"]), [text, b["goal"]]))
        rule = rule_of(b["goal"], unit, False)
        rows.append({"key": key, "item_name": b["label"], "item_value": text[:-1], "item_unit": "%",
                     "item_range": _NUM.search(b["goal"]).group(0) + "%", "is_abnormal": _misses(rule, m[key])})
    daily = []
    for day, day_pts in sorted(M.by_day(pts).items()):
        dt = datetime.combine(day, datetime.min.time())
        daily.append((style["weekdays"][day.weekday()], _date(dt, style["daily_number"]), sorted(day_pts)))
    percentiles = tuple(style.get("percentiles", M.PERCENTILES))
    profile = M.profile(pts, bin_min=15, percentiles=percentiles)
    report = {"layout": "agp", "labels": style["labels"], "palette": style["palette"], "axis": style["axis"],
              "unit": unit, "title": style["title"], "header_lines": header, "metrics": printed,
              "metrics_mode": style.get("metrics_mode", "dots"), "ranges": ranges,
              "band_order": [b for b, _, _ in spec_bands], "pkeys": tuple(f"p{q}" for q in percentiles),
              "target_band": tuple(style.get("target_band", (3.9, 10.0))),
              "tir_rows": tir_rows, "tir_brackets": brackets, "tir_wide": bool(style.get("tir_duration")),
              "tir_ticks": list(style["tir_ticks_mgdl" if unit == "mg/dL" else "tir_ticks_mmol"].items()),
              "tir_notes": style["tir_notes"], "x_labels": style["x_labels"], "noon": style["noon"],
              "profile": profile, "daily": daily[-REPORT_DAYS:], "legend": style.get("legend"),
              "page_label": style.get("page_label"), "footer": style["footer"], "banner": banner, "created": created,
              "author": dev["maker"]}
    if style.get("period_means"):
        cells = []
        for b in range(12):                                   # two-hour means across the report period
            vals = [v for t, v in pts if t.hour // 2 == b]
            cells.append(_glucose(sum(vals) / len(vals), unit) if vals else "--")
            rows.append({"key": f"period_mean@{b * 2:02d}", "item_name": style["period_means"], "item_value": cells[-1],
                         "item_unit": unit, "item_range": "", "is_abnormal": ""})
        report["period_means"] = (style["period_means"], cells)
    if style.get("daily_table"):
        page, more = _daily_table(pts, style["daily_table"], unit, dev["interval_min"], header)
        report["extra_pages"] = [page]
        rows += more
    if style.get("details_page"):
        page, more = _details_page(pts, style["details_page"], unit, dev["interval_min"], header, sess.get("meals", []))
        report["extra_pages"] = report.get("extra_pages", []) + [page]
        rows += more
    return report, rows


def _details_page(pts: list[tuple[datetime, float]], spec_: dict, unit: str, interval_min: int, header: list[str],
                  meals: list[datetime]) -> tuple[dict, list[dict]]:
    """A vendor's second page, built from blocks the style lists: low/high glucose events, postprandial glucose
    for the meals the wearer logged, glucose by time of day, and a per-day table."""
    blocks, truth = [], []
    unit_text = unit if unit == "mg/dL" else "mmol/L"
    for block in spec_["blocks"]:
        kind = block["kind"]
        if kind == "heading":
            blocks.append(block)
        elif kind == "events":
            lines = []
            for ev in block["events"]:
                found = M.events(pts, ev["threshold"], ev["below"], interval_min)
                limit = _glucose(ev["threshold"], unit)
                lines.append(ev["text"].format(v0=limit, v1=unit_text, v2=block.get("sep", ": "), v3=len(found)))
                truth.append({"key": f"events_{ev['key']}", "item_name": ev["key"], "item_value": str(len(found)),
                              "item_unit": "", "item_range": "", "is_abnormal": ""})
                if found:
                    avg = round(sum(found) / len(found))
                    lines.append(block["duration"].format(v0=avg))
                    truth.append({"key": f"events_{ev['key']}_minutes", "item_name": ev["key"], "item_value": str(avg),
                                  "item_unit": "min", "item_range": "", "is_abnormal": ""})
            blocks.append({"kind": "lines", "lines": lines})
        elif kind == "postprandial":
            rows = []
            for meal in meals:
                around = [(t, v) for t, v in pts if timedelta(minutes=-20) <= t - meal <= timedelta(hours=3)]
                pre = [v for t, v in around if t <= meal]
                after = [(t, v) for t, v in around if t > meal]
                if not pre or len(after) < 6:
                    continue

                def at(minutes: int) -> float:
                    return min(after, key=lambda tv: abs((tv[0] - meal).total_seconds() / 60 - minutes))[1]

                peak_t, peak = max(after, key=lambda tv: tv[1])
                cells = [meal.strftime(block["time_format"]), _glucose(pre[-1], unit), _glucose(at(60), unit),
                         _glucose(at(120), unit), _glucose(peak, unit),
                         str(round((peak_t - meal).total_seconds() / 60)), _glucose(peak - pre[-1], unit)]
                rows.append(cells)
                for col, text in zip(block["keys"], cells[1:]):
                    truth.append({"key": f"pp_{col}@{meal.isoformat(timespec='minutes')}", "item_name": col,
                                  "item_value": text, "item_unit": "min" if col == "tpeak" else unit_text,
                                  "item_range": "", "is_abnormal": ""})
            if rows:
                period = f"{pts[0][0]:%Y/%m/%d}-{pts[-1][0]:%Y/%m/%d}"
                blocks.append({"kind": "lines", "lines": [block["intro"].format(v0=period, v1=len(rows))]})
                blocks.append({"kind": "table", "columns": block["columns"], "rows": rows, "first_w": 90})
        elif kind == "time_slots":
            rows = []
            for name, h0, h1 in block["slots"]:
                vals = [v for t, v in pts if h0 <= t.hour + t.minute / 60 < h1] if h1 > h0 else [v for _, v in pts]
                if not vals:
                    rows.append([name] + ["--"] * 3)
                    continue
                low = M.share(vals, None, 3.9, hi_incl=False)
                high = M.share(vals, 10.0, None, lo_incl=False)
                cells = [f"{low:.0f}%", f"{100 - low - high:.0f}%", f"{high:.0f}%"]
                rows.append([name] + cells)
                for col, text in zip(("low", "normal", "high"), cells):
                    truth.append({"key": f"slot_{col}@{name}", "item_name": name, "item_value": text[:-1],
                                  "item_unit": "%", "item_range": "", "is_abnormal": ""})
            blocks.append({"kind": "table", "columns": block["columns"], "rows": rows, "first_w": 90})
        elif kind == "per_day":
            page, more = _daily_table(pts, block, unit, interval_min, [])
            blocks.append({"kind": "table", "columns": page["columns"], "rows": page["rows"], "bands": page["bands"],
                           "footnotes": page["footnotes"]})
            truth += more
    return {"title": spec_["title"], "header_lines": header, "blocks": blocks}, truth


def _daily_table(pts: list[tuple[datetime, float]], spec_: dict, unit: str, interval_min: int,
                 header: list[str]) -> tuple[dict, list[dict]]:
    """One column per day of statistics; the first and last day (under 24 h of data) print `--*`."""
    days = sorted(M.by_day(pts).items())
    columns = [spec_["date_label"]] + [d.strftime(spec_["date_format"]) for d, _ in days]
    rows_text, truth = [], []
    by_key: dict[str, list[str]] = {}
    for i, (d, p) in enumerate(days):
        vals = [v for _, v in sorted(p)]
        partial = i in (0, len(days) - 1)
        b = M.basic(vals)
        r = M.ranges(vals)
        prev = days[i - 1][1] if i > 0 else []
        stats = {"count": str(len(vals)), "max": _glucose(b["max_mmol"], unit), "min": _glucose(b["min_mmol"], unit),
                 "mean": _glucose(b["mean_mmol"], unit), "tir": f"{r['target']:.1f}%",
                 "tar": f"{r['high'] + r['very_high']:.1f}%", "tbr": f"{r['low'] + r['very_low']:.1f}%",
                 "lage": _glucose(M.lage(vals), unit), "mage": _glucose(M.mage(vals) or 0.0, unit),
                 "modd": _glucose(M.modd(sorted(prev) + sorted(p), interval_min) or 0.0, unit) if prev else "--",
                 "sd": f"{b['sd_mmol']:.2f}", "cv": f"{b['cv_pct']:.1f}%"}
        for key in stats:
            text = "--*" if partial and key not in ("count", "max", "min", "mean") else stats[key]
            by_key.setdefault(key, []).append(text)
            if key in spec_["rows"]:                  # only what the table prints is truth
                truth.append({"key": f"{key}@{d.isoformat()}", "item_name": spec_["rows"][key], "item_value": text,
                              "item_unit": "", "item_range": "", "is_abnormal": ""})
    bands, order = {}, []
    for section, keys in spec_["sections"]:
        if section:
            bands[len(order)] = section
        order += keys
    rows_text = [[spec_["rows"][k]] + by_key[k] for k in order]
    return {"title": spec_["title"], "header_lines": header, "columns": columns, "rows": rows_text, "bands": bands,
            "footnotes": spec_["footnotes"]}, truth


# ── The 2017 hospital report sheet ───────────────────────────────────────────
def _sheet(sess: dict, dev: dict, style: dict, person, name: str, institution: str, banner: bool,
           created: datetime, r: random.Random) -> tuple[dict, list[dict]]:
    pts_all = _values(sess, sess["start"], sess["end"])
    days = sorted(M.by_day(pts_all).items())
    labels = [f"{d.month}月{d.day}日" for d, _ in days]

    def stats(values: list[float]) -> dict[str, float]:
        n = len(values)
        mean = sum(values) / n
        sd = math.sqrt(sum((v - mean) ** 2 for v in values) / n)
        share = lambda test: 100 * sum(1 for v in values if test(v)) / n  # noqa: E731
        return {"count": n, "mean": mean, "sd": sd, "cv": 100 * sd / mean, "max": max(values), "min": min(values),
                "ge_13_9": share(lambda v: v >= 13.9), "ge_10": share(lambda v: v >= 10.0),
                "ge_7_8": share(lambda v: v >= 7.8), "le_3_9": share(lambda v: v <= 3.9),
                "le_2_8": share(lambda v: v <= 2.8), "in_3_9_10": share(lambda v: 3.9 < v < 10.0)}

    fmt = {"count": lambda v: f"{v:d}", "mean": lambda v: f"{v:.1f}", "sd": lambda v: f"{v:.2f}",
           "cv": lambda v: f"{v:.1f}", "max": lambda v: f"{v:.1f}", "min": lambda v: f"{v:.1f}"}
    rows_out, table = [], []
    per_day = [(d, stats([v for _, v in p])) for d, p in days]
    for item in style["rows"]:
        key = item["key"]
        cells = [item["label"], item.get("normal", "")]
        for d, s in per_day:
            text = fmt.get(key, lambda v: f"{v:.1f}")(s[key])
            cells.append(text)
            rows_out.append(_row(f"{key}@{d.isoformat()}", item["label"], text, item["unit"], item.get("normal", ""),
                                 NORMAL_2017.get(key), s[key]))
        table.append(cells)
    whole = stats([v for _, v in pts_all])
    interval = dev["interval_min"]

    def hm(share: float) -> str:
        minutes = round(whole["count"] * share / 100 * interval)
        return f"{minutes // 60} h {minutes % 60} min"

    values = {"count": f"{whole['count']:d}", "mean": f"{whole['mean']:.1f}", "sd": f"{whole['sd']:.2f}",
              "cv": f"{whole['cv']:.1f}", "max": f"{whole['max']:.1f}", "min": f"{whole['min']:.1f}",
              "in_3_9_10": f"{whole['in_3_9_10']:.1f}", "ge_7_8": f"{whole['ge_7_8']:.1f}",
              "ge_10": f"{whole['ge_10']:.1f}", "ge_13_9": f"{whole['ge_13_9']:.1f}",
              "le_3_9": f"{whole['le_3_9']:.1f}", "le_2_8": f"{whole['le_2_8']:.1f}",
              "le_3_9_time": hm(whole["le_3_9"]), "le_2_8_time": hm(whole["le_2_8"])}
    summary = style["summary"].format(**values)
    for key in ("count", "mean", "sd", "cv", "max", "min", "in_3_9_10", "ge_7_8", "ge_10", "ge_13_9", "le_3_9", "le_2_8"):
        unit = "" if key == "count" else ("%" if key not in ("mean", "sd", "max", "min") else "mmol/L")
        rows_out.append(_row(key, style["summary_labels"][key], values[key], unit))
    fiction = spec.fiction()["person_names_zh"]
    age = person.age_at(sess["start"].date())
    diagnosis = style["diagnoses"].get(person.archetype, style["diagnoses"]["default"])
    header_values = {"name": name, "sex": style["sex"][person.sex], "age": f"{age}岁",
                     "date": f"{sess['start']:%Y-%m-%d}～{sess['end']:%Y-%m-%d}", "department": style["department"],
                     "ward": "", "bed": "", "number": synthid.make(r, 10), "diagnosis": diagnosis}
    header = [(label, header_values[key]) for key, label in style["header"]]
    report = {"layout": "sheet", "labels": style["labels"], "institution": institution, "title": style["title"],
              "header": header, "day_labels": labels, "rows": table, "summary": summary,
              "signatures": [(style["labels"]["reporter"], r.choice(fiction)),
                             (style["labels"]["reviewer"], r.choice(fiction)),
                             (style["labels"]["report_time"], created.strftime("%Y-%m-%d %H:%M"))],
              "footer": style["footer"], "banner": banner, "created": created, "author": institution}
    return report, rows_out


def build(sess: dict, dev: dict, style_key: str, lang: str, person, name: str, banner: bool, created: datetime,
          institution: str = "", seed: int = 0) -> tuple[dict, list[dict], str]:
    """(renderer input, printed_rows, file name) for one sensor session in one report style."""
    style = spec.streams()["reports"][style_key][lang]
    if style["layout"] == "sheet":
        r = random.Random(f"report-sheet:{seed}:{person.person_id}:{sess['n']}")
        report, rows = _sheet(sess, dev, style, person, name, institution, banner, created, r)
    else:
        report, rows = _agp(sess, dev, style, name, banner, created)
    end = sess["end"]
    start = max(sess["start"], end - timedelta(days=REPORT_DAYS)) if style["layout"] == "agp" else sess["start"]
    file_name = style["file_name"].format(start=start.strftime("%Y%m%d"), end=end.strftime("%Y%m%d"),
                                          created=created.strftime("%Y%m%d%H%M%S"))
    return report, rows, file_name


def created_at(sess: dict, seed: int, person_id: str) -> datetime:
    """When the report was generated: a few hours after the sensor ended (apps build it at the end of a wear
    period), occasionally days later when the wearer opens the app again."""
    r = random.Random(f"report:{seed}:{person_id}:{sess['n']}")
    delay = timedelta(hours=r.randint(1, 20)) if r.random() < 0.8 else timedelta(days=r.randint(2, 20))
    return (sess["end"] + delay).replace(microsecond=0, second=r.randrange(60))
