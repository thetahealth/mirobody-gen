"""Vendor-cloud push payloads: the third data channel for the same synthetic person.

mirobody's `collect/providers/<vendor>` endpoints (`kernel/decoders/{apple,garmin,oura,whoop}.py`)
accept **each vendor's own raw API objects** — not the phone-health-store batch shape, and every
vendor's field names, units and time-zone representation differ: Garmin's
`startTimeOffsetInSeconds` is seconds relative to local midnight, Oura's `bedtime_start` is ISO 8601
with an offset, WHOOP's energy is in kilojoules, and Apple's
`HKCategoryTypeIdentifierSleepAnalysis` carries no number at all, only a sleep-stage name.

These four vendors' decoders **do not reuse** `devices.py`'s crosswalk table: mirobody wrote
dedicated byte-level parsers for them (`decoders/samples/*/records.json` are their acceptance test
cases). Testing those parsers at scale requires the same synthetic person to **simultaneously**:
  - hand in a lab report (the file layer, `corpus.py`)
  - batch-sync a phone health store (`/api/data`, `devices.py`)
  - wear a Garmin / Oura / WHOOP directly (this module)

The payloads re-shape the series devices.jsonl is written from (`devices.series_for`), so a day's
steps, resting heart rate and sleep are the same number on both channels; `adopted_vendors` decides
who has which payloads.

Division of labour with `devices.py`: that module copies a public crosswalk table verbatim; this one
copies the acceptance-test input shapes under `decoders/samples/*` verbatim — the two pipelines'
field names come from different upstream documentation.
"""

from __future__ import annotations

import json
import pathlib
import random
from datetime import date, datetime, timedelta, timezone

from . import model

#: Adoption of each cloud wearable among people who can have one (see `adopted_vendors`); a watch plus
#: a ring is common, three devices are rare.
VENDOR_ADOPTION = {"garmin": 0.30, "oura": 0.08, "whoop": 0.05}
#: The phone stores Garmin, Oura and WHOOP write into (not Huawei Health or Xiaomi).
CLOUD_STORES = ("apple", "health_connect")

#: Vendor -> expected catalogue-metric prefixes. The truth file reconciles against this: which
#: vendor field went in, and which catalogue metric mirobody should resolve it to.
EXPECTED = {
    "garmin": {
        "dailies": ["dailySteps", "dailyDistance", "dailyCaloriesActive", "dailyCaloriesBasal",
                    "dailyTotalCalories", "dailyFloors", "exerciseMinutes",
                    "dailyHeartRateMin", "dailyHeartRateMax", "dailyAvgHeartRate",
                    "dailyRestingHeartRates"],
        "sleeps": ["dailyAwakeTime", "dailyDeepSleep", "dailyLightSleep", "dailyRemSleep",
                   "dailySleepDuration", "dailyTotalSleepTime", "dailySleepEfficiency"],
        "bodyComps": ["bodyMasss", "bmis", "bodyFatPercentages", "bodyWater", "bodyBone"],
        "activities": ["heartRates", "heartRateMax", "activeCalories", "steps",
                       "walkingRunningDistances", "workoutDuration"],
        "pulseOx": ["oxygenSaturations"],
    },
    "oura": {
        "daily_activity": ["dailySteps", "dailyCaloriesActive", "dailyTotalCalories",
                           "dailyDistance", "dailyActivityScore",
                           "sedentaryTime", "restingTime"],
        "sleep": ["dailyTotalSleepTime", "sleepAnalysis_InBed", "sleepAnalysis_Awake",
                  "sleepAnalysis_Asleep(Deep)", "sleepAnalysis_Asleep(Core)",
                  "sleepAnalysis_Asleep(REM)", "sleepEfficiency", "sleepLatency",
                  "dailyHeartRateAvg", "dailyHeartRateMin", "hrvRMSSD", "respiratoryRates",
                  "sleepDisturbances", "temperatureDelta"],
        "daily_spo2": ["oxygenSaturations"],
        "daily_stress": ["stressHighDuration", "recoveryHighDuration"],
    },
    "whoop": {
        "cycle": ["activeCalories", "heartRates", "heartRateMax", "strain"],
        "workouts": ["workoutDurationLow", "workoutDurationMedium", "workoutDurationHigh",
                     "activeCalories", "walkingRunningDistances", "heartRates", "heartRateMax",
                     "altitudeGain", "altitudeChange"],
        "recovery": ["recoveryScore", "restingHeartRates", "hrvRMSSD",
                     "oxygenSaturations", "skinTemperature"],
    },
    "apple": {
        "quantity": ["heartRates", "restingHeartRates", "steps", "bodyMasss",
                     "systolicPressures", "diastolicPressures", "oxygenSaturations",
                     "bloodGlucoses", "bodyTemperatures", "respiratoryRates"],
        "sleep": ["sleepAnalysis_InBed", "sleepAnalysis_Awake",
                  "sleepAnalysis_Asleep(Deep)", "sleepAnalysis_Asleep(Core)",
                  "sleepAnalysis_Asleep(REM)"],
    },
}


def _by_day(series: dict) -> dict[date, dict[str, list[dict]]]:
    """Bucket devices.series_for's record stream by local date, for assembling vendor objects day by day."""
    buckets: dict[date, dict[str, list[dict]]] = {}
    for r in series["records"]:
        day = date.fromisoformat(r["time"][:10])
        buckets.setdefault(day, {}).setdefault(r["_metric"], []).append(r)
    return buckets


def _local_ms(day: date, tz_off: str, hour: int = 0, minute: int = 0) -> int:
    """Unix ms for a local time. `tz_off` is styled like '+08:00'."""
    sign = 1 if tz_off[0] == "+" else -1
    oh, om = int(tz_off[1:3]), int(tz_off[4:6])
    local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)
    return int((local - timedelta(seconds=sign * (oh * 3600 + om * 60))).timestamp() * 1000)


# ── Apple HealthKit records (JSON shape; export.xml is a separate concern) ─────
# Matches the input shape of mirobody/kernel/decoders/samples/apple/records.json.
def _apple_records(person: model.Person, days: dict[date, dict[str, list[dict]]],
                   tz: str, rng: random.Random) -> list[dict]:
    out: list[dict] = []
    apple_off = f"+{tz[1:]}" if tz.startswith("+") else tz
    src_watch = "Watch"
    for day, bucket in days.items():
        if "steps" in bucket:
            r = bucket["steps"][0]
            t = f"{day.isoformat()} 08:00:00 {apple_off}"
            out.append({"type": "HKQuantityTypeIdentifierStepCount",
                        "input": {"value": str(r["value"]), "unit": "count",
                                  "sourceName": src_watch, "startDate": t, "endDate": t}})
        if "rhr" in bucket:
            r = bucket["rhr"][0]
            t = f"{day.isoformat()} 07:00:00 {apple_off}"
            out.append({"type": "HKQuantityTypeIdentifierRestingHeartRate",
                        "input": {"value": str(r["value"]), "unit": "count/min",
                                  "sourceName": src_watch, "startDate": t, "endDate": t}})
        if "sbp" in bucket and "dbp" in bucket:
            sbp, dbp = bucket["sbp"][0], bucket["dbp"][0]
            t = f"{day.isoformat()} 08:00:00 {apple_off}"
            # Blood pressure goes through a Correlation in Apple's model; the XML reader parses it once and skips the child records.
            out.append({"type": "HKCorrelationTypeIdentifierBloodPressure",
                        "input": {"systolic": str(sbp["value"]), "diastolic": str(dbp["value"]),
                                  "unit": "mmHg", "sourceName": "Cuff",
                                  "startDate": t, "endDate": t}})
            out.append({"type": "HKQuantityTypeIdentifierBloodPressureSystolic",
                        "input": {"value": str(sbp["value"]), "unit": "mmHg",
                                  "sourceName": "Cuff", "startDate": t, "endDate": t}})
            out.append({"type": "HKQuantityTypeIdentifierBloodPressureDiastolic",
                        "input": {"value": str(dbp["value"]), "unit": "mmHg",
                                  "sourceName": "Cuff", "startDate": t, "endDate": t}})
        if "sleep" in bucket and rng.random() < 0.85:
            r = bucket["sleep"][0]
            start_iso = r["time"][:16].replace("T", " ") + ":00 " + apple_off
            end_iso = r["end_time"][:16].replace("T", " ") + ":00 " + apple_off
            stage = rng.choice(["HKCategoryValueSleepAnalysisInBed",
                                "HKCategoryValueSleepAnalysisAsleepCore",
                                "HKCategoryValueSleepAnalysisAsleepDeep",
                                "HKCategoryValueSleepAnalysisAsleepREM",
                                "HKCategoryValueSleepAnalysisAwake"])
            out.append({"type": "HKCategoryTypeIdentifierSleepAnalysis",
                        "input": {"value": stage, "sourceName": src_watch,
                                  "startDate": start_iso, "endDate": end_iso}})
        if "weight" in bucket:
            r = bucket["weight"][0]
            t = f"{day.isoformat()} 07:30:00 {apple_off}"
            out.append({"type": "HKQuantityTypeIdentifierBodyMass",
                        "input": {"value": f"{r['value']:.1f}", "unit": "kg",
                                  "sourceName": "Scale", "startDate": t, "endDate": t}})
    return out


# ── Garmin Health API (dailies / sleeps / bodyComps / activities / pulseOx) ────
def _garmin_records(person: model.Person, days: dict[date, dict[str, list[dict]]],
                    tz_off: str, rng: random.Random) -> list[dict]:
    out: list[dict] = []
    sign = 1 if tz_off[0] == "+" else -1
    tz_secs = sign * (int(tz_off[1:3]) * 3600 + int(tz_off[4:6]) * 60)
    for day, bucket in days.items():
        local_midnight_ms = _local_ms(day, tz_off)
        gmt_midnight_s = local_midnight_ms // 1000 - tz_secs
        # dailies
        if "steps" in bucket or "rhr" in bucket:
            steps = int(bucket["steps"][0]["value"]) if "steps" in bucket else 0
            rhr = int(bucket["rhr"][0]["value"]) if "rhr" in bucket else 0
            bmr = int(1500 + rng.gauss(0, 80))
            active = int(steps * 0.05 + rng.gauss(0, 20))
            out.append({"data_type": "dailies",
                        "payload": {"summaryId": f"g-d-{day.isoformat()}",
                                    "calendarDate": day.isoformat(),
                                    "startTimeInSeconds": gmt_midnight_s,
                                    "startTimeOffsetInSeconds": tz_secs,
                                    "activityType": "WALKING",
                                    "durationInSeconds": 86400,
                                    "steps": steps,
                                    "distanceInMeters": round(steps * 0.72, 1),
                                    "activeKilocalories": max(0, active),
                                    "bmrKilocalories": bmr,
                                    "floorsClimbed": int(rng.gauss(11, 4)) if steps else 0,
                                    "activeTimeInSeconds": int(steps * 0.4),
                                    "moderateIntensityDurationInSeconds": 1800,
                                    "vigorousIntensityDurationInSeconds": 600,
                                    "minHeartRateInBeatsPerMinute": max(40, rhr - 6),
                                    "maxHeartRateInBeatsPerMinute": rhr + 80,
                                    "averageHeartRateInBeatsPerMinute": rhr + 10,
                                    "restingHeartRateInBeatsPerMinute": rhr,
                                    "timeOffsetHeartRateSamples": [
                                        {"timestampOffsetInSeconds": 900 * i,
                                         "heartRateInBeatsPerMinute": max(40, int(rhr + rng.gauss(0, 4)))}
                                        for i in range(rng.randint(0, 6))]}})
        # sleeps (reuses devices.py's sleep span; Garmin wants sleep stages plus a levels map)
        if "sleep" in bucket:
            r = bucket["sleep"][0]
            bed = datetime.fromisoformat(r["time"][:19])
            wake = datetime.fromisoformat(r["end_time"][:19])
            total_s = int((wake - bed).total_seconds())
            deep = int(total_s * rng.uniform(0.18, 0.24))
            rem = int(total_s * rng.uniform(0.18, 0.23))
            light = total_s - deep - rem - int(rng.uniform(300, 1200))
            awake = total_s - deep - rem - light
            bed_s = int(bed.replace(tzinfo=timezone.utc).timestamp()) - tz_secs
            out.append({"data_type": "sleeps",
                        "payload": {"summaryId": f"g-s-{day.isoformat()}",
                                    "calendarDate": day.isoformat(),
                                    "startTimeInSeconds": bed_s,
                                    "durationInSeconds": total_s,
                                    "awakeDurationInSeconds": awake,
                                    "deepSleepDurationInSeconds": deep,
                                    "lightSleepDurationInSeconds": light,
                                    "remSleepInSeconds": rem,
                                    "sleepLevelsMap": {
                                        "deep": [{"startTimeInSeconds": bed_s,
                                                  "endTimeInSeconds": bed_s + deep}] if deep else [],
                                        "light": [{"startTimeInSeconds": bed_s + deep,
                                                   "endTimeInSeconds": bed_s + deep + light}] if light else [],
                                        "rem": [{"startTimeInSeconds": bed_s + deep + light,
                                                 "endTimeInSeconds": bed_s + deep + light + rem}] if rem else [],
                                        "awake": [{"startTimeInSeconds": bed_s + deep + light + rem,
                                                   "endTimeInSeconds": bed_s + total_s}] if awake else []}}})
        # bodyComps
        if "weight" in bucket and rng.random() < 0.85:
            r = bucket["weight"][0]
            w_kg = float(r["value"])
            h_m = person.height_cm / 100
            bmi = round(w_kg / (h_m * h_m), 1)
            out.append({"data_type": "bodyComps",
                        "payload": {"summaryId": f"g-b-{day.isoformat()}",
                                    "measurementTimeInSeconds": _local_ms(day, tz_off, 7) // 1000,
                                    "weightInGrams": int(w_kg * 1000),
                                    "bodyMassIndex": bmi,
                                    "bodyFatInPercent": round(rng.uniform(12, 24), 1),
                                    "bodyWaterInPercent": round(rng.uniform(50, 60), 1),
                                    "boneMassInGrams": int(w_kg * 44),
                                    "muscleMassInGrams": int(w_kg * 440)}})
        # pulseOx: more frequent during hypertension/respiratory events (simplified: chronic-disease
        # archetypes only, roughly every other day)
        if person.archetype in ("hypertension", "osa") and rng.random() < 0.3:
            out.append({"data_type": "pulseOx",
                        "payload": {"summaryId": f"g-o-{day.isoformat()}",
                                    "startTimeInSeconds": _local_ms(day, tz_off, 8) // 1000,
                                    "onDemand": False,
                                    "timeOffsetSpo2Values": {str(60 * i): int(rng.uniform(94, 99))
                                                              for i in range(3)}}})
    return out


# ── Oura Ring v2 API (daily_activity / sleep / daily_spo2 / daily_stress) ──────
def _oura_records(person: model.Person, days: dict[date, dict[str, list[dict]]],
                  tz_off: str, rng: random.Random) -> list[dict]:
    out: list[dict] = []
    for day, bucket in days.items():
        if "steps" in bucket:
            steps = int(bucket["steps"][0]["value"])
            active_cal = int(steps * 0.05)
            out.append({"data_type": "daily_activity",
                        "payload": {"id": f"o-a-{day.isoformat()}",
                                    "day": day.isoformat(),
                                    "score": rng.randint(60, 95),
                                    "steps": steps,
                                    "active_calories": active_cal,
                                    "total_calories": 1500 + active_cal,
                                    "equivalent_walking_distance": int(steps * 0.75),
                                    "high_activity_time": 600,
                                    "medium_activity_time": 1800,
                                    "low_activity_time": 7200,
                                    "sedentary_time": 30000,
                                    "resting_time": 28800}})
        if "sleep" in bucket:
            r = bucket["sleep"][0]
            bed_iso = r["time"][:19].replace("T", "T") + tz_off
            wake_iso = r["end_time"][:19] + tz_off
            bed = datetime.fromisoformat(r["time"][:19])
            wake = datetime.fromisoformat(r["end_time"][:19])
            total_s = int((wake - bed).total_seconds())
            in_bed_s = total_s + rng.randint(0, 1800)
            awake_s = in_bed_s - total_s
            deep = int(total_s * rng.uniform(0.18, 0.24))
            rem = int(total_s * rng.uniform(0.18, 0.23))
            light = total_s - deep - rem
            out.append({"data_type": "sleep",
                        "payload": {"id": f"o-s-{day.isoformat()}",
                                    "day": day.isoformat(),
                                    "bedtime_start": bed_iso,
                                    "bedtime_end": wake_iso,
                                    "type": "long_sleep",
                                    "total_sleep_duration": total_s,
                                    "time_in_bed": in_bed_s,
                                    "awake_time": awake_s,
                                    "deep_sleep_duration": deep,
                                    "light_sleep_duration": light,
                                    "rem_sleep_duration": rem,
                                    "efficiency": int(100 * total_s / in_bed_s),
                                    "latency": rng.randint(120, 900),
                                    "average_heart_rate": round(rng.uniform(52, 62), 1),
                                    "lowest_heart_rate": rng.randint(44, 54),
                                    "average_hrv": rng.randint(28, 60),
                                    "average_breath": round(rng.uniform(13, 16), 1),
                                    "restless_periods": rng.randint(0, 8),
                                    "temperature_delta": round(rng.uniform(-0.3, 0.3), 2)}})
        if rng.random() < 0.5:
            out.append({"data_type": "daily_spo2",
                        "payload": {"id": f"o-x-{day.isoformat()}",
                                    "day": day.isoformat(),
                                    "spo2_percentage": {"average": round(rng.uniform(95.5, 99.0), 1)}}})
        out.append({"data_type": "daily_stress",
                    "payload": {"id": f"o-st-{day.isoformat()}",
                                "day": day.isoformat(),
                                "stress_high": rng.randint(0, 7200),
                                "recovery_high": rng.randint(0, 7200)}})
    return out


# ── Whoop v2 API (cycle / workouts / recovery) ─────────────────────────────────
def _whoop_records(person: model.Person, days: dict[date, dict[str, list[dict]]],
                   tz_off: str, rng: random.Random) -> list[dict]:
    out: list[dict] = []
    for i, (day, bucket) in enumerate(days.items()):
        gmt_mid_ms = _local_ms(day, tz_off)
        start = datetime.fromtimestamp(gmt_mid_ms / 1000, tz=timezone.utc) + timedelta(hours=4)
        end = start + timedelta(hours=24)
        rhr = int(bucket["rhr"][0]["value"]) if "rhr" in bucket else 58
        active_kcal = int(500 + rng.gauss(0, 120))
        strain = round(max(0, min(21, 10 + rng.gauss(0, 3))), 1)
        out.append({"data_type": "cycle",
                    "payload": {"id": i + 1,
                                "user_id": 7,
                                "start": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                                "end": end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                                "score_state": "SCORED",
                                "score": {"strain": strain,
                                          "kilojoule": round(active_kcal * 4.184, 1),
                                          "average_heart_rate": rhr + 10,
                                          "max_heart_rate": rhr + 85}}})
        if rng.random() < 0.55:
            # workouts: logged only on days with real activity, matching the zone distribution in the sample data
            km = round(rng.gauss(6, 3), 1)
            zones = [60, 120, 600, 900, 1200, 720]
            out.append({"data_type": "workouts",
                        "payload": {"id": f"w-{day.isoformat()}",
                                    "start": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                                    "end": (start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                                    "sport_name": "running",
                                    "score_state": "SCORED",
                                    "score": {"average_heart_rate": 150, "max_heart_rate": 178,
                                              "kilojoule": round(km * 350, 1),
                                              "distance_meter": int(km * 1000),
                                              "altitude_gain_meter": 55, "altitude_change_meter": 5,
                                              "zone_durations": {"zone_zero_milli": zones[0] * 1000,
                                                                  "zone_one_milli": zones[1] * 1000,
                                                                  "zone_two_milli": zones[2] * 1000,
                                                                  "zone_three_milli": zones[3] * 1000,
                                                                  "zone_four_milli": zones[4] * 1000,
                                                                  "zone_five_milli": zones[5] * 1000}}}})
        out.append({"data_type": "recovery",
                    "payload": {"cycle_id": i + 1,
                                "created_at": (start + timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                                "score_state": "SCORED",
                                "score": {"recovery_score": rng.randint(40, 90),
                                          "resting_heart_rate": rhr,
                                          "hrv_rmssd_milli": round(rng.uniform(30, 60), 1),
                                          "spo2_percentage": round(rng.uniform(96, 99), 1),
                                          "skin_temp_celsius": round(rng.uniform(33.0, 34.5), 2)}}})
    return out


# ── assembly ────────────────────────────────────────────────────────────────

BUILDERS = {"apple": _apple_records, "garmin": _garmin_records,
            "oura": _oura_records, "whoop": _whoop_records}


def adopted_vendors(person: model.Person, seed: int, series: dict) -> list[str]:
    """The payload vendors of a person whose device series is `series`, deterministic in seed and person.

    An Apple Health store is also given in HealthKit JSON (`apple`). Garmin, Oura and WHOOP go only to
    someone who wears a device and whose store is one those devices write into, so the series already
    holds that device's steps, resting heart rate and sleep. The hypertension archetype adopts more."""
    out = ["apple"] if series["vendor"] == "apple" else []
    if not (series["habits"]["wearable"] and series["vendor"] in CLOUD_STORES):
        return out
    rng = random.Random(f"vendors:{seed}:{person.person_id}")
    base = {v: p for v, p in VENDOR_ADOPTION.items()}
    if person.archetype == "hypertension":
        base["garmin"] += 0.25
        base["oura"] += 0.08
        base["whoop"] += 0.10
    # The cohort has no "athletic" archetype; hypertension is the only one that clearly adopts
    # devices on purpose, everyone else follows the baseline penetration rate.
    return out + [v for v, p in base.items() if rng.random() < p]


def write_all(out_dir: pathlib.Path, people: list[model.Person], seed: int,
              langs: dict[str, str]) -> int:
    """Write one JSON file per person and vendor, ready for that vendor's decoder, and the truth in
    `vendor_signals.jsonl`. Returns the number of files."""
    from . import devices

    root = out_dir / "vendor_signals"
    total_files = 0
    with (out_dir / "vendor_signals.jsonl").open("w", encoding="utf-8") as fh:
        for person in people:
            lang = langs.get(person.person_id, "zh")
            s = devices.series_for(person, seed, lang)
            adopted = adopted_vendors(person, seed, s)
            if not adopted:
                continue
            tz = devices.POPULATION.get(lang, devices._DEFAULT_POPULATION)["tz"]
            days = _by_day(s)
            for vendor in adopted:
                builder = BUILDERS[vendor]
                rng = random.Random(f"vsig:{seed}:{person.person_id}:{vendor}")
                records = builder(person, days, tz, rng)
                if not records:
                    continue
                path = root / person.person_id / f"{vendor}.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"person_id": person.person_id, "synthetic": True,
                                            "vendor": vendor, "tz": tz, "records": records},
                                           ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
                total_files += 1
                # Truth: flatten each record's data_type together with its expected catalogue-metric prefixes.
                truth_rows = []
                for rec in records:
                    dt = rec.get("data_type") or rec.get("type")
                    expected_metrics = EXPECTED.get(vendor, {}).get(
                        dt if vendor != "apple" else ("sleep" if "SleepAnalysis" in dt else "quantity"), [])
                    truth_rows.append({"data_type": dt, "expected_metrics": expected_metrics,
                                       "input": rec.get("payload") or rec.get("input")})
                fh.write(json.dumps({"person_id": person.person_id, "synthetic": True,
                                     "vendor": vendor, "file": str(path.relative_to(out_dir)),
                                     "n_records": len(records), "records": truth_rows},
                                    ensure_ascii=False) + "\n")
    return total_files
