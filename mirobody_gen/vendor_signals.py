"""Vendor-cloud push payloads: the third data channel for the same synthetic person.

厂商云端推送：同一个虚拟人的第三条数据通道。

mirobody 的 `collect/providers/<vendor>` 端点（`kernel/decoders/{apple,garmin,oura,whoop}.py`）
接收的是**厂商自己 API 的原始对象**——不是手机健康库批量那个形状，字段名、单位、
时区表示各家都不一样：Garmin 的 `startTimeOffsetInSeconds` 是相对本地午夜的秒数、
Oura 的 `bedtime_start` 是 ISO 8601 带 offset、Whoop 的能量是 kilojoule、
Apple 的 `HKCategoryTypeIdentifierSleepAnalysis` 根本没有数字只有分期名。

这四个厂商的解码器**不复用** `devices.py` 的 crosswalk 表：mirobody 为他们写了
专门的 byte-level 解析器（`decoders/samples/*/records.json` 就是验收用例）。要规模化
测这些解析器，唯一的办法是让同一个虚拟人**同时**：
  - 交一份化验单（文件层，`corpus.py`）
  - 批量同步手机健康库（`/api/data`，`devices.py`）
  - 直接挂一块 Garmin / Oura / Whoop（本模块）

三个通道对同一人生理曲线独立观测，才能问出"不同厂商对同一天的 deep sleep 是否
落到同一行"这种解码一致性问题。

与 `devices.py` 的分工：那边是对公开 crosswalk 表照抄，这边是对
`decoders/samples/*` 的验收 input 照抄——两条链路的字段名来自不同的上游文档。
"""

from __future__ import annotations

import json
import pathlib
import random
from datetime import date, datetime, timedelta, timezone

from . import model, physiology
from .person import CORPUS_END

#: 一人至多挂几块厂商云设备。智表+戒指的组合在真实世界常见（一块 Garmin 运动、
#: 一枚 Oura 睡眠）,三块以上罕见。
VENDOR_ADOPTION = {"garmin": 0.30, "oura": 0.08, "whoop": 0.05}

#: 厂商 → 期望落到的 catalogue metric 前缀。真值文件里按这个对账:
#: 输入谁的字段、预期 mirobody 落到哪个 catalog 指标。
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
    """把 devices.series_for 的 record 流按本地日期分桶，供按日组装厂商对象。"""
    buckets: dict[date, dict[str, list[dict]]] = {}
    for r in series["records"]:
        day = date.fromisoformat(r["time"][:10])
        buckets.setdefault(day, {}).setdefault(r["_metric"], []).append(r)
    return buckets


def _local_ms(day: date, tz_off: str, hour: int = 0, minute: int = 0) -> int:
    """本地某时刻的 unix ms。tz_off 是 '+08:00' 样式。"""
    sign = 1 if tz_off[0] == "+" else -1
    oh, om = int(tz_off[1:3]), int(tz_off[4:6])
    local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)
    return int((local - timedelta(seconds=sign * (oh * 3600 + om * 60))).timestamp() * 1000)


def _tz_str(off: str) -> str:
    """'+08:00' → 'UTC+08:00'。各厂商文档的时区字段写法不同，这里按需组装。"""
    return f"UTC{off}"


# ── Apple HealthKit records(JSON 形式,等 export.xml 时另说)────────────────
# 对照 mirobody/kernel/decoders/samples/apple/records.json 的 input 形状。
def _apple_records(person: model.Person, days: dict[date, dict[str, list[dict]]],
                   tz: str, rng: random.Random) -> list[dict]:
    out: list[dict] = []
    apple_off = f"+{tz[1:]}" if tz.startswith("+") else tz
    src_watch, src_phone = "Watch", "iPhone"
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
            # 血压在 Apple 里走 Correlation;XML reader 只解一次、跳过 child record。
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


# ── Garmin Health API(dailies / sleeps / bodyComps / activities / pulseOx)──────
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
        # sleeps(用 devices.py 的 sleep span,Garmin 给睡眠分期 + levels map)
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
        # pulseOx:高血压/呼吸事件期间增加监测(简化:仅慢病人群隔天一次)
        if person.archetype in ("hypertension", "osa") and rng.random() < 0.3:
            out.append({"data_type": "pulseOx",
                        "payload": {"summaryId": f"g-o-{day.isoformat()}",
                                    "startTimeInSeconds": _local_ms(day, tz_off, 8) // 1000,
                                    "onDemand": False,
                                    "timeOffsetSpo2Values": {str(60 * i): int(rng.uniform(94, 99))
                                                              for i in range(3)}}})
    return out


# ── Oura Ring v2 API(daily_activity / sleep / daily_spo2 / daily_stress)─────────
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


# ── Whoop v2 API(cycle / workouts / recovery)─────────────────────────────────
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
            # workouts:活动量大的日子才记一次,对标样本里 zone 的分布
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


# ── 总装 ─────────────────────────────────────────────────────────────────────

BUILDERS = {"apple": _apple_records, "garmin": _garmin_records,
            "oura": _oura_records, "whoop": _whoop_records}

#: 一个人的厂商采用集合(确定性:同人同 seed 同集合)。高血压 + 高血压倾向的人更可能挂设备。
def adopted_vendors(person: model.Person, seed: int) -> list[str]:
    rng = random.Random(f"vendors:{seed}:{person.person_id}")
    base = {v: p for v, p in VENDOR_ADOPTION.items()}
    if person.archetype == "hypertension":
        base["garmin"] += 0.25; base["oura"] += 0.08; base["whoop"] += 0.10
    # 队列里没有"运动人群"原型;hypertension 是唯一明确会主动挂设备的,其余按渗透率走。
    return [v for v, p in base.items() if rng.random() < p]


def write_all(out_dir: pathlib.Path, people: list[model.Person], seed: int,
              langs: dict[str, str], series: dict[str, dict]) -> int:
    """为每个采用厂商设备的虚拟人输出原始 push payload。

    每人每厂商一个 JSON 文件(payload + input 的数组,可直接喂对应 decoder),
    真值写在 `vendor_signals.jsonl`。
    返回生成的文件数。"""
    from . import devices

    root = out_dir / "vendor_signals"
    total_files = 0
    with (out_dir / "vendor_signals.jsonl").open("w", encoding="utf-8") as fh:
        for person in people:
            adopted = adopted_vendors(person, seed)
            if not adopted:
                continue
            lang = langs.get(person.person_id, "zh")
            tz = devices.POPULATION.get(lang, devices._DEFAULT_POPULATION)["tz"]
            s = series.get(person.person_id)
            if s is None:
                # 挂云厂商的人必然戴表/戴戒指,而且这块专用设备(iPhone 同步的健康库)
                # 才能提供 steps/sleep/rhr——小米/华为路由的字段集没有 sleep。
                s = devices.series_for(person, seed, lang,
                                       force_wearable=True, force_vendor="apple")
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
                # 真值:把每条 record 的 data_type 与期望 catalogue metric 前缀拍平。
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
