"""Device data: phone health-store batches for the same synthetic person.

设备数据：同一个虚拟人在"文件"之外的第二条来源。

mirobody 的 `POST /api/data` 接收手机健康库的批量记录
（`{"records": [{indicator, value, unit, time, source}]}`，一次最多 500 条），
`source` 是厂商标识（apple_health / huawei / xiaomi / health_connect），`indicator` 是厂商自己的
字段名（`HKQuantityTypeIdentifierBodyMass`、`com.huawei.instantaneous.body_weight`……），
mirobody 用 `res/crosswalks/<vendor>.tsv` 把它们编到 LOINC。这里按那张表的字段名出数据，
所以生成的批次能原样喂给它；真值里记下每条记录该落到的 LOINC。

序列与文件层共用同一套生理模型：体重来自 `physiology.weight_at`，血压来自 `physiology.measure`
（与门诊病历上测的是同一个人、同一条事件线），静息心率与步数按事件效应（开始跑步、连续加班、
感冒）走。于是"体重秤上的体重"与"体检报告上的体重"、"家用血压计"与"门诊血压"是可以对账的——
这是 mirobody 两源一码（`Body weight` 与 `bodyMass` 同为 29463-7）唯一能规模化测试的方式。

一个人只有一个手机平台（厂商按人抽定）；高血压的人多半有家用血压计。
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

#: 厂商 → (source 值, 各指标的厂商字段名)。字段名照抄 mirobody res/crosswalks/<vendor>.tsv（2026-09-29）。
#: 华为的 blood_pressure 一个类型对应两个码，那边"不猜"，所以血压按目录指标名（systolicPressures）送。
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
VENDOR_WEIGHTS = {"zh": {"huawei": 45, "apple": 30, "xiaomi": 20, "health_connect": 5},
                  "en": {"apple": 60, "health_connect": 30, "huawei": 5, "xiaomi": 5}}
BATCH = 500


def _tz(lang: str) -> str:
    return "+08:00" if lang == "zh" else "+00:00"


def series_for(person: Person, seed: int, lang: str) -> dict:
    """一个人的全部设备记录（按类型分组的日序列）。确定性：`RandomState`/`Random` 都按 seed+person。"""
    rng = random.Random(f"device:{seed}:{person.person_id}")
    vw = VENDOR_WEIGHTS[lang]
    vendor = rng.choices(list(vw), weights=list(vw.values()))[0]
    fields = VENDORS[vendor]["fields"]
    start = person.weight_anchors[0][0]
    end = min(person.weight_anchors[-1][0], CORPUS_END)
    tz = _tz(lang)
    # 习惯：称重频率、是否戴表、有没有血压计
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
    return {"vendor": vendor, "source": VENDORS[vendor]["source"], "records": records,
            "habits": {"weigh_rate": weigh_rate, "wearable": wearable, "cuff": cuff}}


#: 厂商没有这个字段时（小米健康云没有血压类型），血压计 App 把记录写进健康库用的是目录指标名——
#: mirobody 的 `/api/data` 接受"已经是目录指标名"的 indicator。
FALLBACK_FIELDS = {"sbp": ("systolicPressures", "mmHg"), "dbp": ("diastolicPressures", "mmHg"),
                   "hr": ("heartRates", "bpm"), "rhr": ("restingHeartRates", "bpm"),
                   "sleep": ("dailyTotalSleepTime", "min"), "steps": ("steps", "count"), "weight": ("bodyMasss", "kg")}


def _rec(metric: str, fields: dict, value, stamp: str) -> dict:
    field, unit = fields.get(metric) or FALLBACK_FIELDS[metric]
    return {"indicator": field, "value": value, "unit": unit, "time": stamp, "_metric": metric}


def write_all(out_dir: pathlib.Path, people: list[Person], seed: int, langs: dict[str, str]) -> int:
    """每人一到几个批次文件（≤500 条，可直接 POST）+ `devices.jsonl` 真值。"""
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
    """家庭血压记录表的素材：连续 7–14 天的血压记录，按日分组。"""
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
