"""Apple Health export scenarios (`build --apple-scenarios`): a long span, a re-import, a trip abroad.

An import pipeline breaks on shapes one fresh export never has (thetahealth/mirobody#266: re-importing an
unchanged 35-day export lost a day's figures, and a failure part way through zeroed nine). For every Apple
Health person whose device series covers at least `MIN_SPAN_DAYS`, this writes under
`apple_scenarios/<person>/`:

* `long_span/export.xml` - the whole series: a year or more of daily records;
* `reimport/export_1.xml` and `reimport/export_2.xml` - two exports from the same phone, the first taken
  part way through (holding the records that had ended by then) and the second at the end. An export is
  cumulative, so the second repeats every record of the first byte for byte; merged, each day counts once;
* `trip/export.xml` - the series with a trip abroad: records on the trip's days carry the destination's
  offset while their clock times keep the person's routine.

`apple_scenarios.jsonl` holds, per person and scenario, the files, the date ranges and the figure an
importer should show for every local date (steps, resting heart rate, last weight, sleep minutes on the
wake date, blood-pressure readings), with the offset in force that day.
"""

from __future__ import annotations

import json
import pathlib
import random
from datetime import date, datetime, time, timedelta

from . import continuous, devices, spec
from . import continuous_formats as fmt
from .model import Person

MIN_SPAN_DAYS = 400
TRIP_DAYS = 12
#: Where a trip from each home offset goes: Tokyo, New York, Shanghai
DESTINATION = {"+08:00": "+09:00", "+00:00": "-05:00", "+09:00": "+08:00"}

_HK = "HKQuantityTypeIdentifier"
_SLEEP = "HKCategoryTypeIdentifierSleepAnalysis"


def daily(entries: list[dict]) -> list[dict]:
    """The figure for every local date the records touch, as an importer should bucket them: each record
    by its own local start date (sleep by its wake date), in the offset printed on it."""
    days: dict[date, dict] = {}

    def day(d: date, offset: str) -> dict:
        return days.setdefault(d, {"date": d.isoformat(), "offset": offset})

    for e in sorted(entries, key=lambda e: e["start"]):
        d = day(e["start"].date(), e["offset"])
        if e["type"] == f"{_HK}StepCount":
            d["steps"] = d.get("steps", 0) + int(e["value"])
        elif e["type"] == f"{_HK}RestingHeartRate":
            d["resting_hr"] = int(e["value"])
        elif e["type"] == f"{_HK}BodyMass":
            d["weight_kg"] = float(e["value"])
        elif e["type"] == f"{_HK}BloodPressureSystolic":
            d["bp_readings"] = d.get("bp_readings", 0) + 1
        elif e["type"] == _SLEEP:
            wake = day(e["end"].date(), e["end_offset"])
            wake["sleep_min"] = wake.get("sleep_min", 0) + int((e["end"] - e["start"]).total_seconds() // 60)
    return [days[d] for d in sorted(days)]


def _span(series: dict) -> tuple[date, date]:
    days = sorted({r["time"][:10] for r in series["records"]})
    return date.fromisoformat(days[0]), date.fromisoformat(days[-1])


def scenarios(person: Person, seed: int, lang: str) -> list[tuple[str, dict[str, bytes], dict]] | None:
    """(scenario, files by name, truth) for one person, or None without a long enough Apple Health series."""
    series = devices.series_for(person, seed, lang)
    if series["vendor"] != "apple" or not series["records"]:
        return None
    first, last = _span(series)
    if (last - first).days < MIN_SPAN_DAYS:
        return None
    life = continuous.Life(seed, person, series)
    locale = spec.streams()["formats"]["apple_export"]["locale"].get(lang, "en_US")

    def export(zones=()) -> fmt.AppleExport:
        apple = fmt.AppleExport(life.tz, locale, person.sex, random.Random(f"formats:{seed}:{person.person_id}"),
                                zones)
        continuous.apple_daily(apple, life, series)
        return apple

    def at(d: date) -> datetime:
        return datetime.combine(d, time(21, 0))

    full = export()
    cut = first + timedelta(days=(last - first).days * 3 // 5)
    r = random.Random(f"trip:{seed}:{person.person_id}")
    trip_start = first + timedelta(days=r.randint(30, (last - first).days - 30 - TRIP_DAYS))
    trip_end = trip_start + timedelta(days=TRIP_DAYS - 1)
    destination = DESTINATION[life.tz]
    trip = export(((trip_start, trip_end, destination),))
    expected = daily(full.entries)
    return [
        ("long_span", {"export.xml": full.to_bytes(at(last))},
         {"first": first.isoformat(), "last": last.isoformat(), "days": expected}),
        # A phone saves a record when it ends: the earlier export holds those ended by the time it was taken
        ("reimport", {"export_1.xml": full.document([x for x in full.records if x[3] <= at(cut)], at(cut)),
                      "export_2.xml": full.to_bytes(at(last))},
         {"exports": [{"file": "export_1.xml", "first": first.isoformat(), "last": cut.isoformat()},
                      {"file": "export_2.xml", "first": first.isoformat(), "last": last.isoformat()}],
          "overlap": {"first": first.isoformat(), "last": cut.isoformat()}, "days": expected}),
        ("trip", {"export.xml": trip.to_bytes(at(last))},
         {"home_offset": life.tz, "trip": {"first": trip_start.isoformat(), "last": trip_end.isoformat(),
                                           "offset": destination},
          "days": daily(trip.entries)}),
    ]


def write_all(out_dir: pathlib.Path, people: list[Person], seed: int, langs: dict[str, str]) -> int:
    """Write every eligible person's scenario exports and `apple_scenarios.jsonl`; returns the people written."""
    n = 0
    with (out_dir / "apple_scenarios.jsonl").open("w", encoding="utf-8") as fh:
        for person in people:
            made = scenarios(person, seed, langs.get(person.person_id, "zh"))
            if made is None:
                continue
            for name, files, truth in made:
                root = out_dir / "apple_scenarios" / person.person_id / name
                root.mkdir(parents=True, exist_ok=True)
                for file, data in files.items():
                    (root / file).write_bytes(data)
                fh.write(json.dumps({"person_id": person.person_id, "synthetic": True, "scenario": name,
                                     "files": [str((root / f).relative_to(out_dir)) for f in files], **truth},
                                    ensure_ascii=False) + "\n")
            n += 1
    return n
