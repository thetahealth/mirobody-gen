"""Continuous streams: CGM sessions and intraday heart rate (`build --continuous`).

The physiology checks hold the curves to the person's own model (eAG, resting heart rate, the shared day
plan); the format checks hold every export to its truth record and to the byte-level shape documented in
`docs/DEVICE_FORMATS.md`.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import pathlib
import sys
import tempfile
from datetime import date, datetime, timedelta
from xml.etree import ElementTree as ET

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mirobody_gen import continuous as C  # noqa: E402
from mirobody_gen import continuous_formats as F  # noqa: E402
from mirobody_gen import devices, person as person_mod, physiology, spec, vendor_signals  # noqa: E402

SEED = 7


@pytest.fixture(scope="module")
def cohort():
    people = person_mod.build_cohort(SEED, None)
    langs = {p.person_id: person_mod.person_lang(SEED, p.person_id) for p in people}
    return people, langs


def _life(person, langs):
    return C.Life(SEED, person, devices.series_for(person, SEED, langs[person.person_id]))


#: Every device, every unit and both settings, whatever the adoption weights give this seed.
ROTATION = [("dexcom_g7", "mg/dL"), ("libre_2", "mmol/L"), ("sibionics_gs1", "mmol/L"), ("dexcom_g6", "mmol/L"),
            ("libre_3", "mg/dL"), ("ican_i3", "mmol/L"), ("dexcom_g7", "mmol/L"), ("sibionics_gs1", "mg/dL")]


@pytest.fixture(scope="module")
def built(cohort):
    """A build of everyone with a sensor session (devices rotated so each format appears) plus a few
    watch and ring wearers."""
    people, langs = cohort
    chosen = [p for p in people if C.cgm_plan(SEED, p, langs[p.person_id])]
    rotation = {p.person_id: ROTATION[i % len(ROTATION)] for i, p in enumerate(chosen)}
    original = C.cgm_plan

    def rotated(seed, person, lang):
        out = original(seed, person, lang)
        if person.person_id in rotation:
            dev, unit = rotation[person.person_id]
            for k, sess in enumerate(out):
                wear = timedelta(days=spec.streams()["devices"][dev]["wear_days"])
                sess.update(device=dev, unit=unit, end=min(sess["end"], sess["start"] + wear),
                            setting="clinic" if dev == "sibionics_gs1" and k == 0 else "home")
        return out

    for p in people:
        s = devices.series_for(p, SEED, langs[p.person_id])
        if s["habits"]["wearable"] and p not in chosen and len(chosen) < 22:
            chosen.append(p)
    tmp = tempfile.TemporaryDirectory()
    out = pathlib.Path(tmp.name)
    C.cgm_plan = rotated
    try:
        counts = C.write_all(out, chosen, SEED, langs)
    finally:
        C.cgm_plan = original
    truths = [json.loads(line) for line in (out / "continuous.jsonl").open(encoding="utf-8")]
    yield out, truths, counts, chosen, langs
    tmp.cleanup()


# ── Physiology ───────────────────────────────────────────────────────────────
def test_glucose_day_means_are_the_eag_of_the_expected_hba1c(cohort):
    people, langs = cohort
    for person in [p for p in people if p.archetype == "prediabetes_to_t2dm"][:4]:
        life = _life(person, langs)
        start = datetime(2024, 5, 1)
        bg = C.glucose_minutes(life, start, 10 * 1440)
        for k in range(1, 9):                                    # whole local days
            day = start.date() + timedelta(days=k)
            m0 = int((datetime.combine(day, datetime.min.time()) - start).total_seconds() // 60)
            mean = sum(bg[m0:m0 + 1440]) / 1440
            eag = 1.59 * physiology.expected(person, "hba1c", day) - 2.59
            assert abs(mean - eag) < 0.6, (person.person_id, day, mean, eag)


def test_healthy_glucose_stays_physiological(cohort):
    people, langs = cohort
    for person in [p for p in people if p.archetype == "healthy"][:6]:
        bg = C.glucose_minutes(_life(person, langs), datetime(2025, 3, 1, 9), 14 * 1440)
        tir = sum(3.9 <= v <= 10.0 for v in bg) / len(bg)
        assert min(bg) > 3.0 and max(bg) < 14.0 and tir > 0.9, (person.person_id, min(bg), max(bg), tir)


def test_a_run_is_a_heart_rate_peak_and_a_glucose_dip_at_the_same_minutes(cohort):
    """The same day with and without its run: the run is what lifts heart rate and lowers glucose."""
    import dataclasses

    people, langs = cohort
    found = 0
    for person in people:
        running = next((e for e in person.events if e.name == C.RUN), None)
        if running is None:
            continue
        life = _life(person, langs)
        for k in range(120, 160):
            day = running.start + timedelta(days=k)
            plan = life.plan(day)
            if not plan.run:
                continue
            start = datetime.combine(day, datetime.min.time())
            m0 = int((plan.run[0] - start).total_seconds() // 60)
            m1 = m0 + plan.run[1]
            hr = C.heart_minutes(life, start, 1440)
            bg = C.glucose_minutes(life, start, 1440)
            rest = C.Life(SEED, person, life.series)
            rest._plans[day] = dataclasses.replace(plan, run=None)
            hr0 = C.heart_minutes(rest, start, 1440)
            bg0 = C.glucose_minutes(rest, start, 1440)
            during = [v for v, s in hr[m0 + 5:m1] if s == "run"]
            assert during and sum(during) / len(during) > sum(v for v, _ in hr0[m0 + 5:m1]) / (m1 - m0 - 5) + 40
            assert bg[m1] < bg0[m1] - 0.3, (bg[m1], bg0[m1])
            found += 1
            break
    assert found >= 3


def test_the_day_plan_reads_the_stores_own_resting_heart_rate_and_sleep(cohort):
    people, langs = cohort
    for person in people[:30]:
        series = devices.series_for(person, SEED, langs[person.person_id])
        life = C.Life(SEED, person, series)
        for r in [x for x in series["records"] if x["_metric"] == "rhr"][:20]:
            assert life.plan(date.fromisoformat(r["time"][:10])).rhr == r["value"]
        for r in [x for x in series["records"] if x["_metric"] == "sleep"][:20]:
            wake = date.fromisoformat(r["end_time"][:10])
            assert life.plan(wake).wake == datetime.fromisoformat(r["end_time"][:19])


# ── Builds ───────────────────────────────────────────────────────────────────
def test_every_file_exists_and_every_session_is_summarised(built):
    out, truths, counts, _, _ = built
    assert counts["cgm"] >= 5 and counts["heart_rate"] >= 5
    for t in truths:
        files = t.get("files", []) + [f for d in t.get("devices", []) for f in d["files"]]
        no_export = any(h["name"] == "stream.no_export" for h in t.get("hazards", []))
        assert files or no_export, (t["person_id"], t["stream"])
        for f in files:
            assert (out / f["file"]).is_file(), f
        if t["stream"] == "cgm":
            s = t["summary"]
            assert 0 < s["coverage_pct"] <= 100.5 and 3 < s["mean_mmol"] < 15
            assert abs(s["gmi_pct"] - (3.31 + 0.02392 * s["mean_mgdl"])) < 0.01
            assert abs(s["tbr_lt_3_0_pct"] + s["tbr_3_0_3_8_pct"] + s["tir_3_9_10_0_pct"] + s["tar_10_1_13_9_pct"]
                       + s["tar_gt_13_9_pct"] - 100) < 0.5
            times = [x["time"] for x in t["readings"]]
            assert times == sorted(times) and t["start"] <= times[0] and times[-1] <= t["end"]


def _hist(t):
    return [x for x in t["readings"] if x["kind"] == "historic"]


def test_clarity_csv_matches_its_session(built):
    out, truths, *_ = built
    seen = 0
    for t in [t for t in truths if t["stream"] == "cgm"]:
        for f in [f for f in t["files"] if f["format"] == "dexcom_clarity_csv"]:
            raw = (out / f["file"]).read_bytes()
            assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw and not raw.replace(b"\r\n", b"").count(b"\n")
            rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
            assert rows[0][7] == f"Glucose Value ({t['unit']})" and len(rows[0]) == 14
            egv = [r for r in rows[1:] if r[2] == "EGV"]
            assert all(len(r) == 14 for r in egv) and all(len(r) == 13 for r in rows[1:] if r[2] not in ("EGV", "Calibration"))
            assert [int(r[0]) for r in rows[1:]] == list(range(1, len(rows)))
            hist = _hist(t)
            assert len(egv) == len(hist)
            for r, x in zip(egv, hist):
                assert r[1] == x["time"][:19]
                want = ("Low" if x["flag"] == "low" else "High") if x["flag"] else \
                    (str(x["mgdl"]) if t["unit"] == "mg/dL" else f"{x['mmol']:.1f}")
                assert r[7] == want
            seen += 1
    assert seen


def test_dexcom_api_codes_out_of_range_as_39_and_401(built):
    out, truths, *_ = built
    seen = 0
    for t in [t for t in truths if t["stream"] == "cgm"]:
        for f in [f for f in t["files"] if f["format"] == "dexcom_api_v3_egvs"]:
            body = json.loads((out / f["file"]).read_text(encoding="utf-8"))
            recs = list(reversed(body["records"]))
            hist = _hist(t)
            assert len(recs) == len(hist)
            for rec, x in zip(recs, hist):
                assert rec["displayTime"] == x["time"] and rec["systemTime"].endswith("Z")
                assert rec["value"] == (39 if x["flag"] == "low" else 401 if x["flag"] == "high" else x["mgdl"])
                assert rec["status"] == x["flag"]
            seen += 1
    assert seen


def test_libreview_csv_groups_by_record_type_and_clamps_out_of_range(built):
    out, truths, *_ = built
    libre = [t for t in truths if t["stream"] == "cgm" and t["device"].startswith("libre")]
    if not libre:
        pytest.skip("no Libre wearer in this cohort slice")
    for f in {f["file"] for t in libre for f in t["files"] if f["format"] == "libreview_csv"}:
        raw = (out / f).read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf") and raw.count(b"\r\n") == raw.count(b"\n")
        rows = list(csv.reader(io.StringIO(raw.decode("utf-8"))))
        assert len(rows[0]) == 5 and rows[0][0] == "Glucose Data"
        assert all(len(r) == 19 for r in rows[1:])
        types = [int(r[3]) for r in rows[2:]]
        blocks = [(r[1], int(r[3])) for r in rows[2:]]
        assert blocks == sorted(blocks, key=lambda b: ([s for s, _ in blocks].index(b[0]), b[1])), \
            "rows are grouped by serial, then record type"
        assert 0 in types and 6 in types


def test_api_data_batches_hold_at_most_500_records_and_one_source(built):
    out, truths, *_ = built
    for t in truths:
        files = t.get("files", []) + [f for d in t.get("devices", []) for f in d["files"]]
        for f in [f for f in files if f["format"] == "api_data_batch"]:
            recs = json.loads((out / f["file"]).read_text(encoding="utf-8"))["records"]
            assert 0 < len(recs) <= 500
            assert len({r["source"] for r in recs}) == 1 and {r["indicator"] for r in recs} == {f["indicator"]}


def test_heart_rate_samples_stay_inside_the_window_and_off_the_charger(built):
    _, truths, *_ = built
    for t in [t for t in truths if t["stream"] == "heart_rate"]:
        for d in t["devices"]:
            off = [(o["start"], o["end"]) for o in d["off_body"]]
            for x in d["samples"]:
                assert t["start"] <= x["time"][:10] <= t["end"]
                assert 30 <= x["bpm"] <= 220 and abs(x["bpm"] - x["true_bpm"]) < 12
                assert not any(a <= x["time"] < b for a, b in off), (t["person_id"], x["time"])


def test_apple_export_steps_add_up_to_the_stores_daily_total(built):
    out, truths, _, chosen, langs = built
    exports = sorted({e for t in truths for e in t.get("exports", [])})
    assert exports
    path = out / exports[0]
    pid = path.parts[-3]
    person = next(p for p in chosen if p.person_id == pid)
    series = devices.series_for(person, SEED, langs[pid])
    want = {r["time"][:10]: r["value"] for r in series["records"] if r["_metric"] == "steps"}
    got: dict[str, int] = {}
    for _, elem in ET.iterparse(path):
        if elem.tag == "Record" and elem.get("type") == "HKQuantityTypeIdentifierStepCount":
            got[elem.get("startDate")[:10]] = got.get(elem.get("startDate")[:10], 0) + int(elem.get("value"))
        elem.clear()
    days = [d for d in want if d in got][:200]
    assert days and all(got[d] == want[d] for d in days)


def test_a_scan_based_sensor_loses_history_it_was_not_scanned_for(cohort):
    people, langs = cohort
    person = next(p for p in people if p.archetype == "prediabetes_to_t2dm")
    life = _life(person, langs)
    start = datetime(2024, 4, 2, 10, 0)
    session = {"n": 9, "device": "libre_2", "unit": "mmol/L", "region": "default", "start": start,
               "end": start + timedelta(days=14), "ended": "wear_period"}
    sess = C.sensor_readings(life, session)
    lost = [g for g in sess["gaps"] if g["cause"] == "not_scanned"]
    assert lost, "a wearer who sleeps more than 8 h between scans now and then loses history"
    for g in lost:
        assert not any(g["start"] <= x["time"] < g["end"] for x in sess["readings"] if x["kind"] == "historic")
    assert any(x["kind"] == "scan" for x in sess["readings"])


def test_identifiers_never_contain_long_digit_runs(built):
    out, *_ = built
    for path in out.rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        for key in ("recordId", "transmitterId", "userId"):
            for value in [line.split(":", 1)[1] for line in text.splitlines() if f'"{key}"' in line]:
                assert not F._LONG_DIGITS.search(value.replace("-", "")), (path.name, value)


def test_same_seed_gives_identical_bytes(cohort):
    people, langs = cohort
    subset = [p for p in people if C.cgm_plan(SEED, p, langs[p.person_id])][:3]

    def digest() -> str:
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            C.write_all(out, subset, SEED, langs)
            h = hashlib.sha256()
            for p in sorted(out.rglob("*")):
                if p.is_file():
                    h.update(str(p.relative_to(out)).encode() + p.read_bytes())
            return h.hexdigest()

    assert digest() == digest()


def test_streams_leave_every_other_source_untouched(cohort):
    people, langs = cohort
    subset = people[15:25]
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp)
        devices.write_all(out, subset, SEED, langs)
        vendor_signals.write_all(out, subset, SEED, langs)
        before = {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}
        C.write_all(out, subset, SEED, langs)
        after = {p: p.read_bytes() for p in before}
        assert before == after
        assert "baseline" not in (out / "devices.jsonl").read_text(encoding="utf-8")


def test_every_hazard_is_a_declared_class(built):
    _, truths, *_ = built
    declared = set(spec.streams()["hazard_classes"])
    for t in truths:
        names = [h["name"] for h in t.get("hazards", [])] + [h["name"] for d in t.get("devices", []) for h in d["hazards"]]
        assert set(names) <= declared, set(names) - declared
