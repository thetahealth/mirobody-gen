"""Device data: phone health-store batches for the same synthetic person.

mirobody's `POST /api/data` accepts batched records from a phone health store
(`{"records": [{indicator, value, unit, time, source}]}`, up to 500 per call). `source` is the
vendor tag (apple_health / huawei / xiaomi / health_connect) and `indicator` is that vendor's own
field name (`HKQuantityTypeIdentifierBodyMass`, `com.huawei.instantaneous.body_weight`, ...), which
mirobody maps to LOINC via `res/crosswalks/<vendor>.tsv`. Records here use that same table's field
names, so a generated batch can be posted as-is; the truth records which LOINC each one should
resolve to.

This series shares the physiology model with the document layer: weight comes from
`physiology.weight_at`, blood pressure from `physiology.measure` (same person, same event timeline
as what a clinic visit would measure), and resting heart rate and steps follow the same event
effects (starting to run, a month of overtime, a cold). That lets "weight on the scale" reconcile
with "weight on the check-up report", and a home blood-pressure cuff with a clinic reading — the
only way to test at scale that mirobody resolves `Body weight` and `bodyMass` to the same LOINC,
29463-7.

Each person has exactly one phone platform (vendor drawn per person); most people with hypertension
also own a home blood-pressure cuff.
"""

from __future__ import annotations

import json
import math
import pathlib
import random
from datetime import datetime, timedelta

from . import physiology
from .model import Person
from .person import CORPUS_END

#: Vendor -> (source value, each metric's vendor field name). Field names copied verbatim from
#: mirobody's res/crosswalks/<vendor>.tsv (2026-09-29). Huawei's blood_pressure type maps to two
#: codes and mirobody won't guess between them, so blood pressure is sent under the catalogue
#: indicator names (systolicPressures) instead.
VENDORS = {
    "apple": {"source": "apple_health", "fields": {
        "weight": ("HKQuantityTypeIdentifierBodyMass", "kg"),
        "rhr": ("HKQuantityTypeIdentifierRestingHeartRate", "count/min"),
        "hr": ("HKQuantityTypeIdentifierHeartRate", "count/min"),
        "steps": ("HKQuantityTypeIdentifierStepCount", "count"),
        "sbp": ("HKQuantityTypeIdentifierBloodPressureSystolic", "mmHg"),
        "dbp": ("HKQuantityTypeIdentifierBloodPressureDiastolic", "mmHg"),
        "sleep": ("HKCategoryTypeIdentifierSleepAnalysis:asleepUnspecified", "min")}},
    "huawei": {"source": "huawei", "fields": {
        "weight": ("com.huawei.instantaneous.body_weight", "kg"),
        "rhr": ("com.huawei.instantaneous.resting_heart_rate:bpm", "bpm"),
        "hr": ("com.huawei.instantaneous.heart_rate:bpm", "bpm"),
        "steps": ("com.huawei.continuous.steps.delta", "count"),
        "sbp": ("systolicPressures", "mmHg"),
        "dbp": ("diastolicPressures", "mmHg")}},
    "xiaomi": {"source": "xiaomi", "fields": {
        "weight": ("com.xiaomi.micloud.fit.weight", "kg"),
        "hr": ("com.xiaomi.micloud.fit.heart_rate.bpm", "bpm"),
        "steps": ("com.xiaomi.micloud.fit.step_count.delta", "count")}},
    "health_connect": {"source": "health_connect", "fields": {
        "weight": ("WeightRecord", "kg"),
        "rhr": ("RestingHeartRateRecord", "bpm"),
        "hr": ("HeartRateRecord", "bpm"),
        "steps": ("StepsRecord", "count"),
        "sbp": ("systolicPressures", "mmHg"),
        "dbp": ("diastolicPressures", "mmHg"),
        "sleep": ("SleepSessionRecord", "min")}},
}
LOINC = {"weight": "29463-7", "rhr": "40443-4", "hr": "8867-4", "steps": "55423-8", "sbp": "8480-6",
         "dbp": "8462-4", "sleep": "93832-4"}
#: Each language group's demographic profile: device time zone and brand share. This is a
#: population attribute, not document wording — a group introduced by `--lang-mix` must have a row
#: here, or that slice of the cohort loses its devices (KeyError) or gets another country's time
#: zone. Sourced: Japan's Apple Watch share is roughly 60% (MMRI 2024 wearables survey); the zh row
#: follows CAICT's 2023 wearables shipment breakdown.
POPULATION = {
    "zh": {"tz": "+08:00", "vendors": {"huawei": 45, "apple": 30, "xiaomi": 20, "health_connect": 5}},
    "en": {"tz": "+00:00", "vendors": {"apple": 60, "health_connect": 30, "huawei": 5, "xiaomi": 5}},
    "ja": {"tz": "+09:00", "vendors": {"apple": 62, "health_connect": 28, "huawei": 4, "xiaomi": 6}},
}
#: An unknown group defaults to the English-speaking world's profile: conservative and reproducible.
_DEFAULT_POPULATION = POPULATION["en"]
BATCH = 500


def _tz(lang: str) -> str:
    return POPULATION.get(lang, _DEFAULT_POPULATION)["tz"]


def series_for(person: Person, seed: int, lang: str) -> dict:
    """A person's device records, deterministic in seed and person.

    This is the person's only device series: devices.jsonl, the home logs, the handwritten logs and the
    vendor-cloud payloads all read it, so a day's reading is the same number on every channel."""
    rng = random.Random(f"device:{seed}:{person.person_id}")
    vw = POPULATION.get(lang, _DEFAULT_POPULATION)["vendors"]
    vendor = rng.choices(list(vw), weights=list(vw.values()))[0]
    fields = VENDORS[vendor]["fields"]
    start = person.weight_anchors[0][0]
    end = min(person.weight_anchors[-1][0], CORPUS_END)
    tz = _tz(lang)
    # Habits: how often they weigh in, whether they wear a watch, whether they own a cuff.
    weigh_rate = rng.choice([0.15, 0.3, 0.5, 0.9])
    wearable = rng.random() < 0.6
    cuff = person.archetype == "hypertension" and rng.random() < 0.85 or rng.random() < 0.15
    base_rhr = rng.gauss(64, 6) * (0.93 if person.archetype == "healthy" and rng.random() < 0.3 else 1.0)
    base_steps = rng.gauss(6500, 1800)
    base_sleep = rng.gauss(430, 35)
    records: list[dict] = []
    day = start
    while day <= end:
        stamp = f"{day.isoformat()}T{rng.randint(6, 8):02d}:{rng.randint(0, 59):02d}:00{tz}"
        if "weight" in fields and rng.random() < weigh_rate:
            w = physiology.weight_at(person, day) + rng.gauss(0, 0.35)
            records.append(_rec("weight", fields, round(w, 1), stamp))
        if wearable:
            pulse_factor, _ = physiology.event_factor(person, "pulse", day)
            uri = any(e.name == "急性上呼吸道感染" and 0 <= (day - e.start).days <= e.duration_days for e in person.events)
            if "rhr" in fields:
                rhr = base_rhr * pulse_factor * (1.10 if uri else 1.0) + rng.gauss(0, 2.2)
                records.append(_rec("rhr", fields, int(round(rhr)), stamp))
            if "steps" in fields:
                weekend = day.weekday() >= 5
                running = any(e.name == "开始规律跑步" and e.start <= day for e in person.events)
                overtime = any(e.name == "连续加班一个月" and 0 <= (day - e.start).days <= 30 for e in person.events)
                steps = base_steps * (0.8 if weekend else 1.0) * (1.4 if running else 1.0) * (0.75 if overtime else 1.0) \
                    * (0.3 if uri else 1.0) * math.exp(rng.gauss(0, 0.35))
                records.append(_rec("steps", fields, int(max(0, steps)), f"{day.isoformat()}T23:59:00{tz}"))
            if "sleep" in fields and rng.random() < 0.9:
                overtime = any(e.name == "连续加班一个月" and 0 <= (day - e.start).days <= 30 for e in person.events)
                minutes = base_sleep - (50 if overtime else 0) + rng.gauss(0, 40)
                bed = datetime.combine(day - timedelta(days=1), datetime.min.time()) + timedelta(hours=23, minutes=rng.randint(0, 90))
                wake = bed + timedelta(minutes=max(180, minutes))
                records.append({**_rec("sleep", fields, int(max(180, minutes)), bed.isoformat() + tz),
                                "end_time": wake.isoformat() + tz})
        if cuff and rng.random() < (0.7 if person.archetype == "hypertension" else 0.1):
            for hour in ((7, 21) if rng.random() < 0.5 else (7,)):
                r = random.Random(f"bp:{seed}:{person.person_id}:{day}:{hour}")
                sbp = physiology.measure(r, person, "sbp", day) * (1.0 if hour < 12 else 0.97)
                dbp = physiology.measure(r, person, "dbp", day)
                t = f"{day.isoformat()}T{hour:02d}:{r.randint(0, 40):02d}:00{tz}"
                records.append(_rec("sbp", fields, int(round(sbp)), t))
                records.append(_rec("dbp", fields, int(round(dbp)), t))
                records.append(_rec("hr", fields, int(round(base_rhr * 1.08 + r.gauss(0, 3))), t))
        day += timedelta(days=1)
    # `baseline` is never written out: the continuous streams (`continuous.py`) read it for the days and
    # vendors that carry no daily record (Xiaomi has no resting-heart-rate field).
    return {"vendor": vendor, "source": VENDORS[vendor]["source"], "records": records,
            "habits": {"weigh_rate": weigh_rate, "wearable": wearable, "cuff": cuff},
            "baseline": {"rhr": base_rhr, "steps": base_steps, "sleep_min": base_sleep, "tz": tz}}


#: When a vendor has no field for a metric (Xiaomi Health has no blood-pressure type), a cuff app
#: writes the record into the health store under the catalogue indicator name instead — mirobody's
#: `/api/data` accepts an indicator that is already a catalogue name.
FALLBACK_FIELDS = {"sbp": ("systolicPressures", "mmHg"), "dbp": ("diastolicPressures", "mmHg"),
                   "hr": ("heartRates", "bpm"), "rhr": ("restingHeartRates", "bpm"),
                   "sleep": ("dailyTotalSleepTime", "min"), "steps": ("steps", "count"), "weight": ("bodyMasss", "kg")}


def _rec(metric: str, fields: dict, value, stamp: str) -> dict:
    field, unit = fields.get(metric) or FALLBACK_FIELDS[metric]
    return {"indicator": field, "value": value, "unit": unit, "time": stamp, "_metric": metric}


def write_all(out_dir: pathlib.Path, people: list[Person], seed: int, langs: dict[str, str]) -> int:
    """One to several batch files per person (<=500 records, ready to POST), plus `devices.jsonl` truth."""
    root = out_dir / "devices"
    root.mkdir(parents=True, exist_ok=True)
    total = 0
    with (out_dir / "devices.jsonl").open("w", encoding="utf-8") as fh:
        for person in people:
            s = series_for(person, seed, langs.get(person.person_id, "zh"))
            recs = s["records"]
            if not recs:
                continue
            files = []
            for i in range(0, len(recs), BATCH):
                chunk = recs[i:i + BATCH]
                payload = {"records": [{k: v for k, v in r.items() if not k.startswith("_")} | {"source": s["source"]}
                                       for r in chunk]}
                path = root / person.person_id / f"{s['vendor']}_batch{i // BATCH + 1:02d}.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
                files.append(str(path.relative_to(out_dir)))
            total += len(recs)
            truth = [{"indicator": r["indicator"], "metric": r["_metric"], "loinc": LOINC[r["_metric"]],
                      "value": r["value"], "unit": r["unit"], "time": r["time"]} for r in recs]
            fh.write(json.dumps({"person_id": person.person_id, "synthetic": True, "vendor": s["vendor"],
                                 "source": s["source"], "files": files, "habits": s["habits"],
                                 "n_records": len(recs), "records": truth}, ensure_ascii=False) + "\n")
    return total


def bp_log_windows(series: dict, rng: random.Random) -> list[list[dict]]:
    """Material for a home blood-pressure log: 7-14 consecutive days of readings, grouped by day."""
    by_day: dict[str, list[dict]] = {}
    for r in series["records"]:
        if r["_metric"] in ("sbp", "dbp", "hr"):
            by_day.setdefault(r["time"][:10], []).append(r)
    days = sorted(by_day)
    if len(days) < 7:
        return []
    out = []
    for _ in range(rng.randint(1, 2)):
        n = rng.randint(7, 14)
        start = rng.randint(0, max(0, len(days) - n))
        out.append([{"day": d, "records": by_day[d]} for d in days[start:start + n]])
    return out
