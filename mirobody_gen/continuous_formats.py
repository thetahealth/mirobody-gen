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
def _clean(text: str) -> bool:
    """A synthetic identifier must not contain a run of digits that reads as a phone or ID number: the
    privacy gate would rightly flag it, and a real export's hashed ids are not about to be mistaken
    for one either. Such a draw is redrawn."""
    return not _LONG_DIGITS.search(text)


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
