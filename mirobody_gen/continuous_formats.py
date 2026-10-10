"""Byte-level export shapes for the continuous streams (`continuous.py` makes the truth; this writes it).

Each writer reproduces one real artefact as closely as public evidence allows: header text, column order,
quoting, byte order mark, line endings, time representation, out-of-range encoding and identifier
formats. What each choice rests on, with its URL and how sure we are (verbatim, inferred, unsure), is in
`docs/DEVICE_FORMATS.md`; the per-device numbers and tokens are in `resources/streams.json`.

A writer returns the file's bytes and the named hazards the file carries. Nothing here draws a value:
the readings, times and gaps are decided in `continuous.py`; writers only choose what a format shows of
them (an export that backfills shows a gap the live feed did not), and format-level details that carry
no truth (record UUIDs, the hex address in an Apple device string) come from the stream they are given.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import random
import re
import uuid
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import quoteattr

from . import spec

MGDL_PER_MMOL = 18.0156
#: HealthKit's own spelling of mmol/L for glucose: the molar mass rides in the unit string.
HK_MMOL = "mmol<180.1558800000541>/L"
_LONG_DIGITS = re.compile(r"\d{8,}")


# ── Identifiers ──────────────────────────────────────────────────────────────
def _clean(text: str, longest: int = 7) -> bool:
    """A synthetic identifier must not contain a run of digits that reads as a phone or ID number: the
    privacy gate would rightly flag it, and a real export's hashed ids are not about to be mistaken
    for one either. Such a draw is redrawn. `longest` is the longest digit run allowed."""
    return not re.search(r"\d{%d,}" % (longest + 1), text)


def hex_id(r: random.Random, n: int = 64) -> str:
    while True:
        out = "".join(r.choice("0123456789abcdef") for _ in range(n))
        if _clean(out):
            return out


def uuid4(r: random.Random) -> str:
    while True:
        out = str(uuid.UUID(int=r.getrandbits(128), version=4))
        if _clean(out.replace("-", "")):
            return out


def sha_id(*parts) -> str:
    """A stable 64-hex id (Dexcom's hashed transmitter and user ids). Redrawn by salt if unclean."""
    salt = 0
    while True:
        out = hashlib.sha256(":".join(map(str, parts + (salt,))).encode()).hexdigest()
        if _clean(out):
            return out
        salt += 1


# ── Time ─────────────────────────────────────────────────────────────────────
def offset_of(tz: str) -> timedelta:
    sign = 1 if tz[0] == "+" else -1
    return sign * timedelta(hours=int(tz[1:3]), minutes=int(tz[4:6]))


def to_utc(local: datetime, tz: str) -> datetime:
    return (local - offset_of(tz)).replace(tzinfo=timezone.utc)


def iso_local(local: datetime, tz: str) -> str:
    return local.replace(microsecond=0).isoformat() + tz


def epoch_ms(local: datetime, tz: str) -> int:
    return int(to_utc(local, tz).timestamp() * 1000)


# ── Values ───────────────────────────────────────────────────────────────────
def shown(reading: dict, unit: str, low: str = "Low", high: str = "High") -> str:
    """A glucose reading as a device displays it: integer mg/dL, one-decimal mmol/L, or the device's
    out-of-range word."""
    if reading["flag"] == "low":
        return low
    if reading["flag"] == "high":
        return high
    return str(reading["mgdl"]) if unit == "mg/dL" else f"{reading['mmol']:.1f}"


def _csv_bytes(rows: list[list[str]], *, bom: bool, crlf: bool, quote_all: bool) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL if quote_all else csv.QUOTE_MINIMAL,
                   lineterminator="\r\n" if crlf else "\n")
    w.writerows(rows)
    return ("﻿" if bom else "").encode("utf-8") + buf.getvalue().encode("utf-8")


# ── Dexcom Clarity CSV ───────────────────────────────────────────────────────
def clarity_csv(sess: dict, dev: dict, name: tuple[str, str], dob: str | None, platform: str,
                plans: list, r: random.Random) -> tuple[bytes, list[str]]:
    """A Dexcom Clarity CSV export of one sensor session.

    Untimed preamble (patient, device and alert rows) then every timed row sorted by local timestamp;
    rows other than EGV and Calibration stop one field short (Clarity writes them ragged); BOM, CRLF,
    every field quoted."""
    c = spec.streams()["formats"]["clarity_csv"]
    unit = sess["unit"]
    header = list(c["header_mmol"] if unit == "mmol/L" else c["header_mgdl"])
    clar = dev["clarity"]
    source = clar["source_device"][platform]
    tid = sess["transmitter_id"]
    rows: list[list[str]] = []

    def row(ts: str, kind: str, sub: str = "", info: str = "", device: str = "", src: str = "",
            glucose: str = "", insulin: str = "", carbs: str = "", duration: str = "", rate: str = "",
            ticks: str = "", transmitter: str | None = None) -> None:
        cells = [ts, kind, sub, info, device, src, glucose, insulin, carbs, duration, rate, ticks]
        if transmitter is not None:
            cells.append(transmitter)
        rows.append(cells)

    first, last = name
    row("", "FirstName", info=first)
    row("", "LastName", info=last)
    if dob:
        row("", "DateOfBirth", info=dob)
    alerts = c["alerts_mmol"] if unit == "mmol/L" else c["alerts_mgdl"]
    devices = [(clar["device_info"], source)]
    if platform == "apple" and clar.get("watch_source") and r.random() < 0.15:
        devices.append((clar["device_info"], clar["watch_source"]))
    for info, src in devices:
        row("", "Device", device=info, src=src)
        for sub in c["alert_order"]:
            a = alerts[sub]
            row("", "Alert", sub=sub, src=src, glucose=a.get("glucose", ""), duration=a.get("duration", ""),
                rate=a.get("rate", ""))

    timed: list[tuple[str, list[str]]] = []
    hazards = ["stream.local_time_no_offset", "stream.ragged_rows", "stream.bom"]
    for x in sess["readings"]:
        if x["kind"] != "historic":
            continue
        ts = x["time"].strftime("%Y-%m-%dT%H:%M:%S")
        value = shown(x, unit)
        sub = "Low" if x["flag"] == "low" else ("High" if x["flag"] == "high" else "")
        if sub:
            hazards.append("stream.out_of_range_text")
        ticks = x["ticks"] - dev["warmup_min"] * 60 + sess["first_tick"]
        timed.append((ts, [ts, "EGV", sub, "", "", source, value, "", "", "", "", str(ticks), tid]))
    for at, value in sess.get("calibrations", []):
        ts = at.strftime("%Y-%m-%dT%H:%M:%S")
        timed.append((ts, [ts, "Calibration", "", "", "", source, value, "", "", "", "", "", tid]))
    for plan in plans:
        if plan.run and sess["start"] <= plan.run[0] <= sess["end"] and r.random() < 0.3:
            ts = plan.run[0].strftime("%Y-%m-%dT%H:%M:00")
            sub = "Light" if plan.run[1] < 30 else ("Medium" if plan.run[1] < 45 else "Heavy")
            minutes = plan.run[1]
            timed.append((ts, [ts, "Exercise", sub, "", "", source, "", "", "", f"{minutes // 60:02d}:{minutes % 60:02d}:00", "", ""]))
        for when, kind, load in plan.meals:
            if sess["start"] <= when <= sess["end"] and r.random() < 0.05:
                ts = when.strftime("%Y-%m-%dT%H:%M:00")
                timed.append((ts, [ts, "Carbs", "", "", "", source, "", "", str(int(round(load * 45))), "", "", ""]))
    timed.sort(key=lambda t: t[0])
    for _, cells in timed:
        rows.append(cells)
    if sess["unit"] == "mmol/L":
        hazards.append("stream.unit_mmol")
    out = [header] + [[str(i + 1)] + cells for i, cells in enumerate(rows)]
    return _csv_bytes(out, bom=True, crlf=True, quote_all=True), hazards


# ── Dexcom Web API v3 ────────────────────────────────────────────────────────
_TRENDS = [(3, "doubleUp"), (2, "singleUp"), (1, "fortyFiveUp"), (-1, "flat"), (-2, "fortyFiveDown"),
           (-3, "singleDown"), (-8, "doubleDown")]


def _trend(rate: float | None) -> str:
    if rate is None:
        return "notComputable"
    if rate > 8 or rate < -8:
        return "rateOutOfRange"
    if rate >= 3:
        return "doubleUp"
    if rate >= 2:
        return "singleUp"
    if rate >= 1:
        return "fortyFiveUp"
    if rate > -1:
        return "flat"
    if rate > -2:
        return "fortyFiveDown"
    if rate > -3:
        return "singleDown"
    return "doubleDown"


def dexcom_egvs(sess: dict, dev: dict, user_id: str, platform: str, tz: str, r: random.Random) -> tuple[bytes, list[str]]:
    """`GET /v3/users/self/egvs` for the session: values always in mg/dL, 39 and 401 standing for
    below and above the measuring range with `status`, `systemTime` in UTC and `displayTime` local."""
    api = dev["api"]
    hist = [x for x in sess["readings"] if x["kind"] == "historic"]
    records = []
    hazards = ["stream.utc_and_local_pair"]
    prev: list[tuple[datetime, int]] = []
    for x in hist:
        value = 39 if x["flag"] == "low" else (401 if x["flag"] == "high" else x["mgdl"])
        if x["flag"]:
            hazards.append("stream.out_of_range_sentinel")
        rate = None
        recent = [(t, v) for t, v in prev if timedelta(0) < x["time"] - t <= timedelta(minutes=16)]
        if len(recent) >= 2 and not x["flag"]:
            t0, v0 = recent[0]
            rate = round((value - v0) / ((x["time"] - t0).total_seconds() / 60), 1)
        prev = (prev + [(x["time"], value)])[-4:]
        records.append({
            "recordId": uuid4(r),
            "systemTime": to_utc(x["time"], tz).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "displayTime": iso_local(x["time"], tz),
            "transmitterId": sess["transmitter_hash"],
            "transmitterTicks": x["ticks"] - dev["warmup_min"] * 60 + sess["first_tick"],
            "value": value,
            "status": x["flag"],
            "trend": _trend(rate),
            "trendRate": rate,
            "unit": "mg/dL",
            "rateUnit": "mg/dL/min",
            "displayDevice": "iOS" if platform == "apple" else "android",
            "transmitterGeneration": api["transmitterGeneration"],
            "transmitterGenerationVariant": api["transmitterGenerationVariant"],
            "displayApp": api["displayApp"],
        })
    records.reverse()                                    # newest first, as the v2 sample and Share list them
    body = {"recordType": "egv", "recordVersion": "3.0", "userId": user_id, "records": records}
    return (json.dumps(body, indent=2) + "\n").encode("utf-8"), hazards


# ── Apple Health export.xml ──────────────────────────────────────────────────
_HK_DTD = """<!DOCTYPE HealthData [
<!-- HealthKit Export Version: 14 -->
<!ELEMENT HealthData (ExportDate,Me,(Record|Correlation|Workout|ActivitySummary|ClinicalRecord|Audiogram|VisionPrescription)*)>
<!ATTLIST HealthData
  locale CDATA #REQUIRED
>
<!ELEMENT ExportDate EMPTY>
<!ATTLIST ExportDate
  value CDATA #REQUIRED
>
<!ELEMENT Me EMPTY>
<!ATTLIST Me
  HKCharacteristicTypeIdentifierDateOfBirth                 CDATA #REQUIRED
  HKCharacteristicTypeIdentifierBiologicalSex               CDATA #REQUIRED
  HKCharacteristicTypeIdentifierBloodType                   CDATA #REQUIRED
  HKCharacteristicTypeIdentifierFitzpatrickSkinType         CDATA #REQUIRED
  HKCharacteristicTypeIdentifierCardioFitnessMedicationsUse CDATA #REQUIRED
>
<!ELEMENT Record ((MetadataEntry|HeartRateVariabilityMetadataList)*)>
<!ATTLIST Record
  type          CDATA #REQUIRED
  unit          CDATA #IMPLIED
  value         CDATA #IMPLIED
  sourceName    CDATA #REQUIRED
  sourceVersion CDATA #IMPLIED
  device        CDATA #IMPLIED
  creationDate  CDATA #IMPLIED
  startDate     CDATA #REQUIRED
  endDate       CDATA #REQUIRED
>
<!-- Note: Any Records that appear as children of a correlation also appear as top-level records in this document. -->
<!ELEMENT Correlation ((MetadataEntry|Record)*)>
<!ATTLIST Correlation
  type          CDATA #REQUIRED
  sourceName    CDATA #REQUIRED
  sourceVersion CDATA #IMPLIED
  device        CDATA #IMPLIED
  creationDate  CDATA #IMPLIED
  startDate     CDATA #REQUIRED
  endDate       CDATA #REQUIRED
>
<!ELEMENT MetadataEntry EMPTY>
<!ATTLIST MetadataEntry
  key   CDATA #REQUIRED
  value CDATA #REQUIRED
>
]>
"""


def hk_time(local: datetime, tz: str) -> str:
    return local.strftime("%Y-%m-%d %H:%M:%S ") + tz.replace(":", "")


class AppleExport:
    """One person's `apple_health_export/export.xml`: records are added as (start, xml) and written
    sorted by type then start, which is how an export groups them."""

    def __init__(self, tz: str, locale: str, sex: str, r: random.Random):
        self.tz, self.locale, self.sex, self.r = tz, locale, sex, r
        self.records: list[tuple[str, datetime, str]] = []
        h = spec.streams()["formats"]["apple_export"]
        self.watch_hw = r.choice(h["watch_hardware"])
        self.phone_hw = r.choice(h["phone_hardware"])
        self.versions = h["os_versions"]

    def _version(self, kind: str, when: datetime) -> str:
        table = self.versions[kind]
        year = str(min(max(int(k) for k in table), max(min(int(k) for k in table), when.year)))
        return table[year]

    def device(self, kind: str, when: datetime) -> str:
        sw = self._version(kind, when)
        addr = f"0x{self.r.getrandbits(36):09x}"
        if kind == "watch":
            return (f"<<HKDevice: {addr}>, name:Apple Watch, manufacturer:Apple Inc., model:Watch, "
                    f"hardware:{self.watch_hw}, software:{sw}>")
        return (f"<<HKDevice: {addr}>, name:iPhone, manufacturer:Apple Inc., model:iPhone, "
                f"hardware:{self.phone_hw}, software:{sw}>")

    def _xml(self, rtype: str, start: datetime, end: datetime, value: str | None, unit: str | None,
             source: str, source_version: str | None, device: str | None, created: datetime,
             metadata: dict | None, indent: int) -> str:
        """One `<Record>` in export.xml's attribute order (type, sourceName, sourceVersion, device, unit,
        creationDate, startDate, endDate, value), metadata entries one level deeper."""
        attrs = [("type", rtype), ("sourceName", source)]
        if source_version:
            attrs.append(("sourceVersion", source_version))
        if device:
            attrs.append(("device", device))
        if unit:
            attrs.append(("unit", unit))
        attrs += [("creationDate", hk_time(created, self.tz)), ("startDate", hk_time(start, self.tz)),
                  ("endDate", hk_time(end, self.tz))]
        if value is not None:
            attrs.append(("value", value))
        pad = " " * indent
        head = f"{pad}<Record " + " ".join(f"{k}={quoteattr(v)}" for k, v in attrs)
        if not metadata:
            return head + "/>\n"
        body = "".join(f"{pad} <MetadataEntry key={quoteattr(k)} value={quoteattr(v)}/>\n" for k, v in metadata.items())
        return head + ">\n" + body + f"{pad}</Record>\n"

    def add(self, rtype: str, start: datetime, end: datetime, *, value: str | None, unit: str | None,
            source: str, source_version: str | None, device: str | None, created: datetime,
            metadata: dict | None = None) -> None:
        self.records.append((rtype, start, self._xml(rtype, start, end, value, unit, source, source_version,
                                                     device, created, metadata, 1)))

    def add_blood_pressure(self, when: datetime, sbp: int, dbp: int, created: datetime) -> None:
        """A cuff reading typed into Health: the Correlation with its two Records inside, and the same two
        Records again at top level (the DTD's note)."""
        meta = {"HKWasUserEntered": "1"}
        children = ""
        for rtype, v in (("HKQuantityTypeIdentifierBloodPressureSystolic", sbp),
                         ("HKQuantityTypeIdentifierBloodPressureDiastolic", dbp)):
            self.add(rtype, when, when, value=str(v), unit="mmHg", source="Health", source_version=None,
                     device=None, created=created, metadata=meta)
            children += self._xml(rtype, when, when, str(v), "mmHg", "Health", None, None, created, meta, 2)
        attrs = [("type", "HKCorrelationTypeIdentifierBloodPressure"), ("sourceName", "Health"),
                 ("creationDate", hk_time(created, self.tz)), ("startDate", hk_time(when, self.tz)),
                 ("endDate", hk_time(when, self.tz))]
        xml = (" <Correlation " + " ".join(f"{k}={quoteattr(v)}" for k, v in attrs) + ">\n"
               '  <MetadataEntry key="HKWasUserEntered" value="1"/>\n' + children + " </Correlation>\n")
        self.records.append(("HKCorrelationTypeIdentifierBloodPressure", when, xml))

    def to_bytes(self, exported: datetime) -> bytes:
        sex = {"male": "HKBiologicalSexMale", "female": "HKBiologicalSexFemale"}[self.sex]
        head = ('<?xml version="1.0" encoding="UTF-8"?>\n' + _HK_DTD +
                f'<HealthData locale="{self.locale}">\n'
                f' <ExportDate value="{hk_time(exported, self.tz)}"/>\n'
                f' <Me HKCharacteristicTypeIdentifierDateOfBirth="" HKCharacteristicTypeIdentifierBiologicalSex="{sex}" '
                'HKCharacteristicTypeIdentifierBloodType="HKBloodTypeNotSet" '
                'HKCharacteristicTypeIdentifierFitzpatrickSkinType="HKFitzpatrickSkinTypeNotSet" '
                'HKCharacteristicTypeIdentifierCardioFitnessMedicationsUse="None"/>\n')
        order = {t: i for i, t in enumerate(dict.fromkeys(t for t, _, _ in sorted(self.records, key=lambda x: x[0])))}
        body = "".join(xml for _, _, xml in sorted(self.records, key=lambda x: (order[x[0]], x[1])))
        return (head + body + "</HealthData>\n").encode("utf-8")


# ── Oura API v2 heartrate ────────────────────────────────────────────────────
def oura_heartrate(samples: list[dict], tz: str) -> tuple[bytes, list[str]]:
    """`GET /v2/usercollection/heartrate` for the window: UTC timestamps with `+00:00`, epoch ms, bpm and
    Oura's `source`."""
    data = [{"timestamp": to_utc(s["time"], tz).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
             "timestamp_unix": epoch_ms(s["time"], tz), "bpm": s["bpm"], "source": s["source"]}
            for s in samples]
    return (json.dumps({"data": data, "next_token": None}, indent=2) + "\n").encode("utf-8"), \
        ["stream.utc_only", "stream.workout_burst"] if any(s["source"] == "workout" for s in samples) else ["stream.utc_only"]


# ── Zepp Life (Mi Fit) data export: HEARTRATE_AUTO ───────────────────────────
def zepp_heartrate_auto(samples: list[dict]) -> tuple[bytes, list[str]]:
    """`HEARTRATE_AUTO_<ms>.csv`: `date,time,heartRate`, device-local minutes, no offset."""
    rows = [["date", "time", "heartRate"]] + [[s["time"].strftime("%Y-%m-%d"), s["time"].strftime("%H:%M"), str(s["bpm"])]
                                              for s in samples]
    return _csv_bytes(rows, bom=False, crlf=False, quote_all=False), ["stream.local_time_no_offset"]


# ── mirobody POST /api/data batch (phone health store) ───────────────────────
def api_data_batches(records: list[dict], source: str, batch: int) -> list[bytes]:
    """`{"records": [{indicator, value, unit, time, source}]}`, at most `batch` records per call — the
    shape `devices.py` writes for the daily series."""
    out = []
    for i in range(0, len(records), batch):
        payload = {"records": [r | {"source": source} for r in records[i:i + batch]]}
        out.append((json.dumps(payload, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    return out


# ── Abbott LibreView CSV ─────────────────────────────────────────────────────
def libre_time(when: datetime, date_order: str, clock: str) -> str:
    """A LibreView timestamp in the account's date and time format: zero-padded, no seconds, no zone."""
    d = when.strftime("%m-%d-%Y") if date_order == "MDY" else when.strftime("%d-%m-%Y")
    t = when.strftime("%I:%M %p") if clock == "12h" else when.strftime("%H:%M")
    return f"{d} {t}"


def libre_value(reading: dict, unit: str, floor_ceiling: tuple[int, int]) -> str:
    """LibreView writes a reading past the sensor's range as the range limit itself, not as LO/HI."""
    lo, hi = floor_ceiling
    if reading["flag"]:
        mgdl = lo if reading["flag"] == "low" else hi
        return str(mgdl) if unit == "mg/dL" else f"{mgdl / MGDL_PER_MMOL:.1f}"
    return str(reading["mgdl"]) if unit == "mg/dL" else f"{reading['mmol']:.1f}"


def libreview_csv(blocks: list[dict], unit: str, account: dict, generated_by: str, generated_utc: datetime,
                  r: random.Random) -> tuple[bytes, list[str]]:
    """A LibreView patient export ("Glucose Data"): every session the account holds, one block per device
    serial, rows grouped by record type (0 historic, 1 scan, 5 food, 6 events) and ascending within each
    group — not chronological overall. No BOM, CRLF, a five-field title line, 19-field rows, a reading
    past the range written as the range limit."""
    f = spec.streams()["formats"]["libreview_csv"]
    header = [h.replace("{unit}", unit) for h in f["header"][account["header"]]]
    title = [f["title"][0], f["title"][1], libre_time(generated_utc, account["date"], account["clock"]) + " UTC",
             f["title"][2], generated_by]
    rows = []
    hazards = ["stream.local_time_no_offset", "stream.unsorted_rows"]
    if account["date"] == "DMY":
        hazards.append("stream.locale_date_order")
    if unit == "mmol/L":
        hazards.append("stream.unit_mmol")
    by_serial: dict[str, list[tuple[int, datetime, list[str]]]] = {}
    for b in blocks:
        sess, dev, serial = b["session"], b["device"], b["serial"]
        table = dev["range_mgdl"]
        lim = tuple(table.get(sess.get("region", "default"), table["default"]))
        cells = by_serial.setdefault(serial, [])

        def row(kind: int, when: datetime, at: int | None = None, value: str = "", extra: dict | None = None):
            c = [""] * 15
            if at is not None:
                c[at] = value
            for k, v in (extra or {}).items():
                c[k] = v
            cells.append((kind, when, [b["device_name"], serial, libre_time(when, account["date"], account["clock"]),
                                       str(kind)] + c))

        for _ in range(r.randint(2, 8)):                      # empty event rows when a sensor starts
            row(6, sess["start"] + timedelta(seconds=r.randrange(60)))
        for x in sess["readings"]:
            if x["flag"]:
                hazards.append("stream.out_of_range_clamped")
            row(0 if x["kind"] == "historic" else 1, x["time"], 0 if x["kind"] == "historic" else 1,
                libre_value(x, unit, lim))
        for when, load in b.get("food", []):
            row(5, when, extra={4: "1", 5: f"{load * 45:.1f}"})
    for serial, cells in by_serial.items():
        for _, _, c in sorted(cells, key=lambda x: (x[0], x[1])):
            rows.append(c)
    return _csv_bytes([title, header] + rows, bom=False, crlf=True, quote_all=False), hazards


def libre_reader_serial(r: random.Random) -> str:
    """A FreeStyle Libre reader serial: four letters, three digits, a dash, a letter and four digits."""
    letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    return ("".join(r.choice(letters) for _ in range(4)) + "".join(str(r.randrange(10)) for _ in range(3)) + "-" +
            r.choice(letters) + "".join(str(r.randrange(10)) for _ in range(4)))


# ── Sibionics (硅基动感) ─────────────────────────────────────────────────────
def sibionics_serial(r: random.Random, start: datetime) -> str:
    """A Sibionics sensor id: `LT`, the year and month of manufacture, four letters or digits."""
    made = start - timedelta(days=r.randint(30, 300))
    return f"LT{made:%y%m}" + "".join(r.choice("0123456789ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(4))


def sibionics_clinic_csv(sess: dict, institution: str, name: str, serial: str) -> tuple[bytes, list[str]]:
    """The hospital-side export of a sensor placed in clinic: Chinese header with the institution,
    patient and device in header cells after a full-width colon, mmol/L to one decimal, newest first,
    a reading past the range written as the range limit."""
    f = spec.streams()["formats"]["sibionics_clinic_csv"]
    header = f["header"] + [f"{label}{f['label_sep']}{value}"
                            for label, value in zip(f["header_labels"], (institution, name, serial))]
    lo, hi = sess["range"]
    rows, hazards = [], ["stream.local_time_no_offset", "stream.newest_first", "stream.metadata_in_header"]
    for x in sorted((x for x in sess["readings"] if x["kind"] == "historic"), key=lambda x: x["time"], reverse=True):
        if x["flag"]:
            hazards.append("stream.out_of_range_clamped")
            mmol = (lo if x["flag"] == "low" else hi) / MGDL_PER_MMOL
        else:
            mmol = x["mmol"]
        rows.append([f"{mmol:.1f}", x["time"].strftime("%Y-%m-%d %H:%M:%S"), "", "", ""])
    return _csv_bytes([header] + rows, bom=True, crlf=True, quote_all=False), hazards


def sibionics_app_xlsx(sess: dict, unit: str, tz: str, exported: datetime) -> tuple[bytes, list[str]]:
    """The international app's "Export all": one sheet, `Sensor Glucose`, every cell a string, oldest
    first, day-first minutes with a `GMT+H` suffix. The app names the file `.xls`; the bytes are OOXML."""
    import openpyxl

    from .render.sheet import _normalize_zip

    f = spec.streams()["formats"]["sibionics_app_xlsx"]
    hours = int(offset_of(tz).total_seconds() // 3600)
    suffix = f"GMT{'+' if hours >= 0 else '-'}{abs(hours)}"
    lo, hi = sess["range"]
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = f["sheet"]
    sheet.append([f["header"][0], f["header"][1].replace("{unit}", unit)])
    hazards = ["stream.extension_mismatch"]
    for x in (x for x in sess["readings"] if x["kind"] == "historic"):
        mgdl = (lo if x["flag"] == "low" else hi) if x["flag"] else x["mgdl"]
        if x["flag"]:
            hazards.append("stream.out_of_range_clamped")
        value = str(mgdl) if unit == "mg/dL" else f"{mgdl / MGDL_PER_MMOL:.1f}" if x["flag"] else f"{x['mmol']:.1f}"
        sheet.append([f"{x['time']:%d-%m-%Y %H:%M} {suffix}", value])
    book.properties.created = book.properties.modified = exported
    raw = io.BytesIO()
    book.save(raw)
    return _normalize_zip(raw.getvalue(), exported), hazards


# ── Medtronic CareLink Personal CSV ──────────────────────────────────────────
def carelink_csv(sess: dict, dev: dict, name: tuple[str, str], account: dict, apps: list[str], events: dict,
                 selected: tuple[datetime, datetime]) -> tuple[bytes, list[str]]:
    """CareLink's "Data Export (CSV)" for a standalone sensor: a preamble, then a Pump section (alarms,
    logbook entries, fingersticks) and a Sensor section per app installation, each under a `-------`
    separator with a trailing space and the 49-column header; rows newest first; `Index` with five decimals,
    counted from 0 straight across sections; BOM and CRLF; a blank line closes every section."""
    f = spec.streams()["formats"]["carelink_csv"]
    unit = sess["unit"]
    header = [h if unit == "mg/dL" or h in f["mgdl_only"] else h.replace("(mg/dL)", "(mmol/L)") for h in f["header_49"]]
    col = {h.split(" (")[0]: i for i, h in enumerate(header)}
    sep = account["delimiter"]
    label = dev["carelink"]["system"]

    def date_cell(when: datetime) -> str:
        if account["preamble_date"] == "US":
            return f"{when.month}/{when.day}/{when:%y} 12:00:00 AM"
        return f"{when.day}/{when.month}/{when.year} 00:00:00"

    def glucose(mgdl: int) -> str:
        return str(mgdl) if unit == "mg/dL" else f"{mgdl / MGDL_PER_MMOL:.1f}".replace(".", account["decimal"])

    lines = [sep.join(f["preamble_keys"] + [label] * len(apps)),
             sep.join(f'"{v}"' for v in (name[1], name[0], "", "", date_cell(selected[0]), date_cell(selected[1])))
             + sep + '"Serial Number"' + sep + sep.join(apps),
             "", f["notice"], ""]
    index = 0

    def section(kind: str, serial: str, rows: list[dict]) -> None:
        nonlocal index
        lines.append(sep.join(["-------", label, kind, serial, "------- "]))
        lines.append(sep.join(header))
        for row in sorted(rows, key=lambda x: x["time"], reverse=True):
            cells = [""] * len(header)
            cells[0] = f"{index}.00000".replace(".", account["decimal"])
            cells[1], cells[2] = row["time"].strftime("%Y/%m/%d"), row["time"].strftime("%H:%M:%S")
            for key, value in row["cells"].items():
                cells[col[key]] = value
            lines.append(sep.join(cells))
            index += 1
        lines.append("")

    pump = [{"time": t, "cells": {"Alarm": a}} for t, a in events["alarms"]]
    pump += [{"time": t, "cells": {"Event Marker": m}} for t, m in events["markers"]]
    pump += [{"time": t, "cells": {"BG Reading": glucose(v)}} for t, v in events["fingersticks"]]
    sensor = [{"time": x["time"], "cells": {"Sensor Glucose": glucose(x["mgdl"])}}
              for x in sess["readings"] if x["kind"] == "historic" and not x["flag"]]
    section("Pump", apps[0], pump)
    section("Sensor", apps[0], sensor)
    for other in apps[1:]:
        section("Pump", other, [])
    hazards = ["stream.local_time_no_offset", "stream.newest_first", "stream.bom", "stream.sectioned_csv"]
    if unit == "mmol/L":
        hazards.append("stream.unit_mmol")
    return ("\ufeff" + "\r\n".join(lines) + "\r\n").encode("utf-8"), hazards


def carelink_app_id(r: random.Random) -> str:
    """The id CareLink lists under "Serial Number" for a Guardian app installation."""
    alnum = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    while True:
        out = "GC" + "-".join("".join(r.choice(alnum) for _ in range(4)) for _ in range(4))
        if _clean(out):
            return out


# ── Nightscout ───────────────────────────────────────────────────────────────
#: Dexcom trend names (Share / Nightscout), numbered as share2nightscout-bridge numbers them.
NS_TRENDS = ["NONE", "DoubleUp", "SingleUp", "FortyFiveUp", "Flat", "FortyFiveDown", "SingleDown", "DoubleDown",
             "NOT COMPUTABLE", "RATE OUT OF RANGE"]
_SHARE_NAMES = {"NONE": "None", "NOT COMPUTABLE": "NotComputable", "RATE OUT OF RANGE": "RateOutOfRange"}


def trend_of(rate: float | None) -> str:
    """Rate of change in mg/dL/min → Dexcom's arrow, with the API's bands."""
    name = _trend(rate)
    return {"doubleUp": "DoubleUp", "singleUp": "SingleUp", "fortyFiveUp": "FortyFiveUp", "flat": "Flat",
            "fortyFiveDown": "FortyFiveDown", "singleDown": "SingleDown", "doubleDown": "DoubleDown",
            "notComputable": "NOT COMPUTABLE", "rateOutOfRange": "RATE OUT OF RANGE"}[name]


def rates(readings: list[dict]) -> list[float | None]:
    """mg/dL per minute over the last ~15 minutes before each reading (None without two earlier points)."""
    out, prev = [], []
    for x in readings:
        recent = [(t, v) for t, v in prev if timedelta(0) < x["time"] - t <= timedelta(minutes=16)]
        rate = None
        if len(recent) >= 2 and x["mgdl"] is not None:
            t0, v0 = recent[0]
            rate = round((x["mgdl"] - v0) / ((x["time"] - t0).total_seconds() / 60), 2)
        if x["mgdl"] is not None:
            prev = (prev + [(x["time"], x["mgdl"])])[-4:]
        out.append(rate)
    return out


def object_id(when: datetime, tz: str, counter: int, machine: str) -> str:
    """A MongoDB ObjectId: insertion second, five per-process bytes, a counter. An id whose digits run
    long enough to read as a phone number is moved to the next second (the counter alone cannot clean a
    run that the timestamp and the process bytes already make)."""
    second = int(to_utc(when, tz).timestamp())
    while True:
        out = f"{second:08x}{machine}{counter % (1 << 24):06x}"
        if _clean(out, longest=10):
            return out
        second += 1


def nightscout_entries(sess: dict, uploader: str, tz: str, r: random.Random) -> tuple[list[dict], list[str]]:
    """Every sgv entry the site holds for the session, as `/api/v1/entries.json` returns them (newest
    first), after the server's normalisation: `sysTime` in UTC, `dateString` overwritten with it,
    `utcOffset` taken from the offset the uploader sent (share2 and LibreLink-Up send UTC, so 0)."""
    u = spec.streams()["formats"]["nightscout_entries"]["uploaders"][uploader]
    hist = [x for x in sess["readings"] if x["kind"] == "historic" and not x["flag"]]
    machine = "".join(r.choice("0123456789abcdef") for _ in range(10))
    docs = []
    prev = None
    for k, (x, rate) in enumerate(zip(hist, rates(hist))):
        utc = to_utc(x["time"], tz)
        ms = int(utc.timestamp() * 1000)
        iso_utc = utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"
        doc = {"_id": object_id(x["time"] + timedelta(seconds=r.randint(20, 300)), tz, k, machine)}
        direction = trend_of(rate)
        if uploader == "share2":
            doc |= {"sgv": x["mgdl"], "date": ms, "dateString": iso_utc, "trend": NS_TRENDS.index(direction),
                    "direction": direction, "device": u["device"], "type": "sgv", "utcOffset": 0}
        elif uploader == "librelinkup":
            # Only the reading that was current at a poll carries a direction; history items have none.
            current = r.random() < 0.2
            doc |= {"type": "sgv", "sgv": x["mgdl"]}
            if current:
                doc["direction"] = direction if direction in ("SingleDown", "FortyFiveDown", "Flat", "FortyFiveUp",
                                                              "SingleUp") else "NOT COMPUTABLE"
            doc |= {"device": u["device"], "date": ms, "dateString": iso_utc, "utcOffset": 0}
        else:
            # xDrip+ sends the phone's millisecond timestamp, local dateString and sysTime (the server
            # rewrites both to UTC and appends utcOffset after them), a 5-minute delta to three decimals,
            # raw values times 1000, and "NotComputable" where the Dexcom vocabulary says NOT COMPUTABLE.
            ms += r.randrange(1000)
            iso_utc = datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms % 1000:03d}Z"
            delta = round(x["mgdl"] - prev, 3) if prev is not None else 0
            raw = round(x["mgdl"] * 1000 * math.exp(r.gauss(0, 0.03)), 5)
            doc |= {"device": u["device"][sess["family"]], "date": ms, "dateString": iso_utc, "sgv": x["mgdl"],
                    "delta": delta, "direction": direction if direction != "NOT COMPUTABLE" else "NotComputable",
                    "type": "sgv", "filtered": raw, "unfiltered": raw, "rssi": 100, "noise": 1,
                    "sysTime": iso_utc, "utcOffset": int(offset_of(tz).total_seconds() // 60)}
            docs.append(doc)
            prev = x["mgdl"]
            continue
        doc["sysTime"] = iso_utc
        docs.append(doc)
        prev = x["mgdl"]
    docs.reverse()
    hazards = ["stream.utc_only"] if uploader != "xdrip" else ["stream.utc_and_local_pair"]
    return docs, hazards


def nightscout_csv(docs: list[dict]) -> bytes:
    """`/api/v1/entries.csv`: no header; dateString, date, sgv, direction, device, each JSON-encoded
    (strings quoted, a missing value empty), rows joined by CRLF with no newline at the end."""
    def cell(v) -> str:
        return "" if v is None else json.dumps(v)

    return "\r\n".join(",".join(cell(d.get(k)) for k in ("dateString", "date", "sgv", "direction", "device"))
                        for d in docs).encode("utf-8")


# ── xDrip+ "Export CSV (SiDiary format)" ─────────────────────────────────────
def xdrip_sidiary_zip(sess: dict, exported: datetime, carbs: list[tuple[datetime, float]]) -> tuple[bytes, str, list[str]]:
    """`exportCSV<yyyyMMdd-HHmmss>.zip` holding `export<yyyyMMdd-HHmmss>.csv`: `;`-separated, LF, no BOM,
    mg/dL rounded, sensor readings, then calibrations, then treatments, each block in time order."""
    import zipfile

    f = spec.streams()["formats"]["xdrip_sidiary"]
    stamp = exported.strftime("%Y%m%d-%H%M%S")
    lines = [f["header"]]
    for x in sorted((x for x in sess["readings"] if x["kind"] == "historic" and not x["flag"]), key=lambda x: x["time"]):
        if x["mgdl"] > 13:
            lines.append(f"{x['time']:%d.%m.%Y;%H:%M;}{x['mgdl']};;;;")
    for at, value in sorted(sess.get("calibrations_mgdl", [])):
        lines.append(f"{at:%d.%m.%Y;%H:%M;};{value};;;")
    for at, grams in sorted(carbs):
        lines.append(f"{at:%d.%m.%Y;%H:%M;};;{grams:g};;")
    data = ("\n".join(lines) + "\n").encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        info = zipfile.ZipInfo(f"export{stamp}.csv", date_time=exported.timetuple()[:6])
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o600 << 16
        z.writestr(info, data)
    return buf.getvalue(), f"exportCSV{stamp}.zip", ["stream.local_time_no_offset", "stream.blocks_not_interleaved"]


# ── Follower snapshots: Dexcom Share and LibreLinkUp ─────────────────────────
def dexcom_share(sess: dict, at: datetime, tz: str) -> tuple[bytes, list[str]]:
    """`ReadPublisherLatestGlucoseValues?minutes=1440&maxCount=288` as a follower app sees it at `at`:
    compact JSON, newest first, `Date(ms)` wall/system times and `Date(ms±hhmm)` display time."""
    hist = [x for x in sess["readings"] if x["kind"] == "historic" and not x["flag"]
            and at - timedelta(hours=24) < x["time"] <= at]
    off = offset_of(tz)
    sign = "+" if off >= timedelta(0) else "-"
    hhmm = f"{sign}{abs(int(off.total_seconds())) // 3600:02d}{abs(int(off.total_seconds())) % 3600 // 60:02d}"
    out = []
    for x, rate in zip(hist, rates(hist)):
        ms = epoch_ms(x["time"], tz)
        name = trend_of(rate)
        out.append({"WT": f"Date({ms})", "ST": f"Date({ms})", "DT": f"Date({ms}{hhmm})", "Value": x["mgdl"],
                    "Trend": _SHARE_NAMES.get(name, name)})
    out.reverse()
    return json.dumps(out[:288], separators=(",", ":")).encode("utf-8"), ["stream.window_24h"]


#: A LibreLinkUp ticket's lifetime: 180 days.
TICKET_S = 180 * 86400


def _llu_time(when: datetime) -> str:
    return f"{when.month}/{when.day}/{when.year} {(when.hour % 12) or 12}:{when:%M:%S} {'AM' if when.hour < 12 else 'PM'}"


def librelinkup_graph(sess: dict, dev: dict, at: datetime, tz: str, name: tuple[str, str], unit: str,
                      country: str, r: random.Random) -> tuple[bytes, list[str]]:
    """`GET /llu/connections/{patientId}/graph` as a follower sees it at `at`: the current reading with its
    trend arrow, about twelve hours of history ascending without arrows, UTC `FactoryTimestamp` beside
    local `Timestamp` in month-first 12-hour form."""
    f = spec.streams()["formats"]["librelinkup_graph"]
    hist = [x for x in sess["readings"] if x["kind"] == "historic" and at - timedelta(hours=12) <= x["time"] <= at]
    uom = 1 if unit == "mg/dL" else 0
    lo, hi = f["target_mgdl"]

    def item(x: dict, kind: int, rate: float | None = None) -> dict:
        mgdl = x["mgdl"] if x["mgdl"] is not None else (sess["range"][0] if x["flag"] == "low" else sess["range"][1])
        value = mgdl if uom else round(mgdl / MGDL_PER_MMOL, 1)
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        color = 1 if lo <= mgdl <= hi else (4 if mgdl < lo else (2 if mgdl <= 250 else 3))
        out = {"FactoryTimestamp": _llu_time(to_utc(x["time"], tz).replace(tzinfo=None)),
               "Timestamp": _llu_time(x["time"]), "type": kind, "ValueInMgPerDl": mgdl}
        if kind == 1:
            arrow = 3 if rate is None else (5 if rate >= 2 else 4 if rate >= 1 else 3 if rate > -1 else 2 if rate > -2 else 1)
            out |= {"TrendArrow": arrow, "TrendMessage": None}
        return out | {"MeasurementColor": color, "GlucoseUnits": uom, "Value": value,
                      "isHigh": x["flag"] == "high", "isLow": x["flag"] == "low"}

    rs = rates(hist)
    current = item(hist[-1], 1, rs[-1]) if hist else None
    activated = int(to_utc(sess["start"], tz).timestamp())
    sensor = {"deviceId": "", "sn": sess["llu_sn"], "a": activated, "w": 60, "pt": dev["llu"]["pt"], "s": True, "lj": False}
    connection = {"id": uuid4(r), "patientId": uuid4(r), "country": country, "status": 2, "firstName": name[0],
                  "lastName": name[1], "targetLow": lo, "targetHigh": hi, "uom": uom, "sensor": sensor,
                  "alarmRules": f["alarm_rules"], "glucoseMeasurement": current, "glucoseItem": current,
                  "glucoseAlarm": None,
                  "patientDevice": {"did": uuid4(r), "dtid": dev["llu"]["dtid"], "v": dev["llu"]["app_version"],
                                    "ll": 60, "hl": 240, "u": activated - r.randint(86400, 86400 * 300),
                                    "fixedLowAlarmValues": {"mgdl": 60, "mmoll": 3.3}, "alarms": False,
                                    "fixedLowThreshold": 60},
                  "created": activated - r.randint(86400 * 30, 86400 * 900)}
    body = {"status": 0, "data": {"connection": connection,
                                  "activeSensors": [{"sensor": sensor, "device": connection["patientDevice"]}],
                                  "graphData": [item(x, 0) for x in hist[:-1]]},
            "ticket": {"token": synthetic_jwt(r), "expires": int(to_utc(at, tz).timestamp()) + TICKET_S,
                       "duration": TICKET_S * 1000}}
    return json.dumps(body, ensure_ascii=False).encode("utf-8"), ["stream.utc_and_local_pair", "stream.window_12h"]


def synthetic_jwt(r: random.Random) -> str:
    """A token shaped like LibreLinkUp's JWT whose payload says it is synthetic."""
    import base64

    def b64(obj) -> str:
        raw = obj if isinstance(obj, bytes) else json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return ".".join((b64({"alg": "ES256", "typ": "JWT"}), b64({"synthetic": True, "iss": "mirobody-gen"}),
                     b64(bytes(r.getrandbits(8) for _ in range(64)))))


# ── Tidepool export (web app "Export": Excel or JSON) ────────────────────────
#: Tidepool stores glucose in mmol/L, converting mg/dL with this factor and rounding to five decimals
#: half away from zero; the export multiplies back without rounding.
TIDEPOOL_FACTOR = 18.01559
TIDEPOOL_ERROR = ("Due to the size of your export, Tidepool was unable to retrieve all of your data at one time. "
                  "If your data appears incomplete, try the export again using a smaller date range.")


def _js_number(v: float) -> str:
    """A double as JavaScript prints it: shortest round-trip digits, integral values without '.0'."""
    return str(int(v)) if float(v).is_integer() else repr(float(v))


def _tidepool_stored(mgdl: float) -> float:
    q = mgdl / TIDEPOOL_FACTOR * 100000.0
    return int(q + math.copysign(0.5, q)) / 100000.0


def tidepool_records(sess: dict, tz: str, units: str, device: dict, r: random.Random,
                     uploaded: datetime) -> list[dict]:
    """The session as the export streams it: cbg records in stored order, then the upload record; only
    allow-listed keys, alphabetically; payload and annotations as JSON strings; a reading past the range is
    stored at the threshold with an out-of-range annotation."""
    lo, hi = sess["range"]
    upload_id = hex_id(r, 32)
    off = int(offset_of(tz).total_seconds() // 60)
    out = []
    for x in (x for x in sess["readings"] if x["kind"] == "historic"):
        mgdl = x["mgdl"] if not x["flag"] else (lo if x["flag"] == "low" else hi)
        stored = _tidepool_stored(mgdl)
        value = stored if units == "mmol/L" else stored * TIDEPOOL_FACTOR
        rec = {"clockDriftOffset": 0, "conversionOffset": 0, "deviceId": device["id"],
               "deviceTime": x["time"].strftime("%Y-%m-%dT%H:%M:%S"), "id": hex_id(r, 32),
               "time": to_utc(x["time"], tz).strftime("%Y-%m-%dT%H:%M:%SZ"), "timezoneOffset": off, "type": "cbg",
               "units": units, "uploadId": upload_id, "value": value}
        if x["flag"]:
            rec["annotations"] = json.dumps([{"code": "bg/out-of-range", "threshold": mgdl, "value": x["flag"]}],
                                            separators=(",", ":"))
        out.append(dict(sorted(rec.items())))
    up = {"byUser": uuid4(r), "computerTime": uploaded.strftime("%Y-%m-%dT%H:%M:%S"), "conversionOffset": 0,
          "deviceId": device["id"], "deviceManufacturers": json.dumps([device["maker"]]), "deviceModel": device["model"],
          "deviceSerialNumber": device["serial"], "deviceTags": json.dumps(["cgm"]),
          "deviceTime": uploaded.strftime("%Y-%m-%dT%H:%M:%S"), "id": upload_id,
          "time": to_utc(uploaded, tz).strftime("%Y-%m-%dT%H:%M:%SZ"), "timeProcessing": "utc-bootstrapping",
          "timezone": device["timezone"], "timezoneOffset": off, "type": "upload", "uploadId": upload_id,
          "version": device["uploader_version"]}
    out.append(dict(sorted(up.items())))
    return out


def tidepool_json(records: list[dict]) -> bytes:
    """`TidepoolExport.json`: one compact array, no whitespace, no trailing newline."""
    def enc(rec: dict) -> str:
        return "{" + ",".join(f"{json.dumps(k)}:{_js_number(v) if isinstance(v, float) else json.dumps(v)}"
                              for k, v in rec.items()) + "}"

    return ("[" + ",".join(enc(rec) for rec in records) + "]").encode("utf-8")


_XLSX_NS = ('xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
            'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"')


def _serial(when: datetime) -> float:
    """An Excel date serial (1900 system) for a naive wall-clock time, as ExcelJS computes it."""
    ms = int((when - datetime(1970, 1, 1)).total_seconds() * 1000)
    return 25569 + ms / 86400000


def _col(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        s = chr(65 + rem) + s
    return s


def tidepool_xlsx(records: list[dict], units: str, exported_utc: datetime) -> bytes:
    """`TidepoolExport.xlsx` as ExcelJS writes it for the export service: a very-hidden `EXPORT ERROR`
    sheet first, then one sheet per data type in order of first appearance (`CGM`, `Upload`); bold, frozen
    header rows; dates as Excel serials in `yyyy-mm-dd hh:mm:ss`; inline strings; the converted value
    unrounded, its display format `0` or `0.0`. The Office theme part ExcelJS adds is left out."""
    import zipfile
    from xml.sax.saxutils import escape

    f = spec.streams()["formats"]["tidepool_export"]
    mmol = units == "mmol/L"
    sheets = [("EXPORT ERROR", None)] + [(name, [rec for rec in records if rec["type"] == t])
                                         for t, name in (("cbg", "CGM"), ("upload", "Upload"))]
    head = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'

    def sheet_xml(name: str, rows: list[dict] | None) -> str:
        if rows is None:
            return (head + f'<worksheet {_XLSX_NS} mc:Ignorable="x14ac" xmlns:x14ac="http://schemas.microsoft.com/office/'
                    'spreadsheetml/2009/9/ac"><sheetFormatPr defaultRowHeight="15" outlineLevelRow="0" outlineLevelCol="0" '
                    'x14ac:dyDescent="55"/><sheetData><row r="1" spans="1:1" x14ac:dyDescent="0.25"><c r="A1" t="str"><v>'
                    + escape(TIDEPOOL_ERROR) + '</v></c></row></sheetData>' + f["page_xml"] + '</worksheet>')
        cols = f["sheets"][name]
        out = [head + f'<worksheet {_XLSX_NS} mc:Ignorable="x14ac" xmlns:x14ac="http://schemas.microsoft.com/office/'
               'spreadsheetml/2009/9/ac"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" '
               'activePane="bottomLeft" state="frozen"/><selection pane="bottomLeft" activeCell="A2" sqref="A2"/>'
               '</sheetView></sheetViews><sheetFormatPr defaultRowHeight="15" outlineLevelRow="0" outlineLevelCol="0" '
               'x14ac:dyDescent="55"/>' + f["cols_xml"][name] + "<sheetData>"]
        cells = "".join(f'<c r="{_col(i)}1" s="{4 if c["kind"] == "date" else 5 if c["kind"] == "value" else 3}" '
                        f't="str"><v>{escape(c["header"])}</v></c>' for i, c in enumerate(cols))
        out.append(f'<row r="1" spans="1:{len(cols)}" s="3" customFormat="1" x14ac:dyDescent="0.25">{cells}</row>')
        for n, rec in enumerate(rows, start=2):
            local = datetime.fromisoformat(rec["time"][:-1]) + timedelta(minutes=rec["timezoneOffset"])
            values = {"Zulu Time": datetime.fromisoformat(rec["time"][:-1]), "Local Time": local,
                      "Device Time": datetime.fromisoformat(rec["deviceTime"])}
            if "computerTime" in rec:
                values["Computer Time"] = datetime.fromisoformat(rec["computerTime"])
            parts, last = [], 0
            for i, c in enumerate(cols):
                v = values.get(c["header"], rec.get(c["field"]) if c.get("field") else None)
                if v is None:
                    continue
                ref = f"{_col(i)}{n}"
                if c["kind"] == "date":
                    parts.append(f'<c r="{ref}" s="1"><v>{_js_number(_serial(v))}</v></c>')
                elif c["kind"] == "value":
                    parts.append(f'<c r="{ref}" s="2"><v>{_js_number(v)}</v></c>')
                elif isinstance(v, (int, float)):
                    parts.append(f'<c r="{ref}"><v>{_js_number(v)}</v></c>')
                else:
                    parts.append(f'<c r="{ref}" t="str"><v>{escape(str(v))}</v></c>')
                last = i + 1
            out.append(f'<row r="{n}" spans="1:{last}" x14ac:dyDescent="0.25">' + "".join(parts) + "</row>")
        out.append("</sheetData>" + f["page_xml"] + "</worksheet>")
        return "".join(out)

    names = [n for n, _ in sheets]
    workbook = (head + f'<workbook {_XLSX_NS} mc:Ignorable="x15" xmlns:x15="http://schemas.microsoft.com/office/'
                'spreadsheetml/2010/11/main"><fileVersion appName="xl" lastEdited="5" lowestEdited="5" rupBuild="9303"/>'
                '<workbookPr defaultThemeVersion="164011" filterPrivacy="1"/><sheets>'
                + "".join(f'<sheet sheetId="{i + 1}" name="{escape(n)}" state="{"veryHidden" if i == 0 else "visible"}" '
                          f'r:id="rId{i + 3}"/>' for i, n in enumerate(names))
                + '</sheets><calcPr calcId="171027"/></workbook>')
    rels = (head + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
            'Target="styles.xml"/>'
            + "".join(f'<Relationship Id="rId{i + 3}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                      f'relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>' for i in range(len(names)))
            + "</Relationships>")
    types = (head + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" '
             'ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" '
             'ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/'
             'vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
             + "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/'
                       'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(len(names)))
             + '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.'
             'spreadsheetml.styles+xml"/><Default Extension="vml" ContentType="application/vnd.openxmlformats-'
             'officedocument.vmlDrawing"/><Override PartName="/docProps/core.xml" ContentType="application/'
             'vnd.openxmlformats-package.core-properties+xml"/><Override PartName="/docProps/app.xml" ContentType='
             '"application/vnd.openxmlformats-officedocument.extended-properties+xml"/></Types>')
    stamp = exported_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    core = (head + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            '<dc:creator>ExcelJS</dc:creator><cp:lastModifiedBy>ExcelJS</cp:lastModifiedBy>'
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{stamp}</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{stamp}</dcterms:modified></cp:coreProperties>')
    app = (head + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
           'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>Microsoft Excel'
           '</Application><DocSecurity>0</DocSecurity><ScaleCrop>false</ScaleCrop><HeadingPairs><vt:vector size="2" '
           'baseType="variant"><vt:variant><vt:lpstr>Worksheets</vt:lpstr></vt:variant><vt:variant><vt:i4>'
           f'{len(names)}</vt:i4></vt:variant></vt:vector></HeadingPairs><TitlesOfParts><vt:vector size="{len(names)}" '
           'baseType="lpstr">' + "".join(f"<vt:lpstr>{escape(n)}</vt:lpstr>" for n in names)
           + '</vt:vector></TitlesOfParts><Company></Company><LinksUpToDate>false</LinksUpToDate><SharedDoc>false'
           '</SharedDoc><HyperlinksChanged>false</HyperlinksChanged><AppVersion>16.0300</AppVersion></Properties>')
    root_rels = (head + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
                 'officeDocument" Target="xl/workbook.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.'
                 'org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/><Relationship '
                 'Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-'
                 'properties" Target="docProps/app.xml"/></Relationships>')
    styles = f["styles_mmol" if mmol else "styles_mgdl"]
    parts = [("_rels/.rels", root_rels)]
    parts += [(f"xl/worksheets/sheet{i + 1}.xml", sheet_xml(n, rows)) for i, (n, rows) in enumerate(sheets)]
    parts += [("[Content_Types].xml", types), ("docProps/app.xml", app), ("docProps/core.xml", core),
              ("xl/styles.xml", head + "\n" + styles), ("xl/_rels/workbook.xml.rels", rels),
              ("xl/workbook.xml", head + "\n" + workbook[len(head):])]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in parts:
            info = zipfile.ZipInfo(name, date_time=exported_utc.timetuple()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, text.encode("utf-8"))
    return buf.getvalue()
