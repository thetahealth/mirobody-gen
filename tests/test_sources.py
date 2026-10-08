"""The three non-document sources: device batches, diary sentences, genotype exports.

They share the cohort with the documents, so the checks here are about contract and consistency,
not about particular values.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mirobody_gen import devices, genomics, journal, person as person_mod, spec, vendor_signals  # noqa: E402

SEED = 7


def _people(n: int = 8):
    people = person_mod.build_cohort(SEED, n)
    langs = {p.person_id: person_mod.person_lang(SEED, p.person_id) for p in people}
    return people, langs


def test_device_records_carry_a_catalogue_metric_and_a_loinc():
    people, langs = _people()
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp)
        total = devices.write_all(out, people, SEED, langs)
        assert total > 0
        for line in (out / "devices.jsonl").read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            assert rec["synthetic"] is True
            assert rec["vendor"] in devices.VENDORS
            for r in rec["records"]:
                assert r["metric"] in devices.LOINC and r["loinc"] == devices.LOINC[r["metric"]]
                assert r["time"][:4].isdigit() and ("+" in r["time"] or "Z" in r["time"])
            for name in rec["files"]:
                batch = json.loads((out / name).read_text(encoding="utf-8"))["records"]
                assert 0 < len(batch) <= devices.BATCH
                assert all(set(b) >= {"indicator", "value", "unit", "time", "source"} for b in batch)
                assert {b["source"] for b in batch} == {rec["source"]}


def test_device_weight_tracks_the_documents_weight():
    """The scale and the check-up book weigh the same person: same model, same timeline."""
    from mirobody_gen import physiology

    people, langs = _people()
    person = people[0]
    series = devices.series_for(person, SEED, langs[person.person_id])
    weights = [(r["time"][:10], r["value"]) for r in series["records"] if r["_metric"] == "weight"]
    assert weights, "the first smoke person should weigh themselves at least once"
    from datetime import date

    for day, value in weights[:20]:
        model = physiology.weight_at(person, date.fromisoformat(day))
        assert abs(value - model) < 1.5, (day, value, model)


def test_journal_entries_split_into_coded_or_frontier_surfaces():
    people, langs = _people()
    comp = spec.complaints()
    coded_surfaces = {s for item in comp["symptoms"].values() for s in item["zh"] + item["en"]}
    coded_surfaces |= {item["en_preferred"] for item in comp["symptoms"].values() if item.get("en_preferred")}
    frontier = {s for item in comp["frontier"].values() for s in item["zh"] + item["en"]}
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp)
        n = journal.write_all(out, people, SEED, langs)
        assert n > 0
        for line in (out / "journal.jsonl").read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            assert rec["expected"], rec
            for e in rec["expected"]:
                if e["kind"] == "symptom":
                    assert e["name"].lower() in rec["text"].lower(), (e["name"], rec["text"])
                    if e["expect"] == "coded":
                        assert e["icpc3"] and (e["name"] in coded_surfaces or e["name"].replace("_", " ") in coded_surfaces)
                    else:
                        assert e["icpc3"] is None and (e["name"] in frontier or e["expect"] == "no-match")
                else:
                    assert e["kind"] == "measurement" and e["loinc"]


def test_genotype_exports_match_their_truth_and_catalogue():
    people, langs = _people(12)
    g = spec.genomics()
    catalogue = {row[0] for row in g["pgx_sites"]} | {row[0] for row in g["catalog_sites"]}
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp)
        n = genomics.write_all(out, people, SEED, langs)
        assert n > 0, "no person got a genotype file in a 12-person cohort"
        for line in (out / "genomics.jsonl").read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            text = (out / rec["file"]).read_text(encoding="utf-8")
            for site in rec["sites"]:
                assert site["in_catalog"] == (site["rsid"] in catalogue)
                if site["call_status"] == "no_call":
                    assert site["array_gt"] is None
                else:
                    assert site["array_gt"] in ("0/0", "0/1", "1/1")
                    assert (site["zygosity"] == "homozygous") == (site["array_gt"] in ("0/0", "1/1"))
                    assert site["call_status"] == ("called" if site["in_catalog"] else "unresolved")
                assert site["rsid"] in text
            assert rec["n_pgx"] == len(g["pgx_sites"])


def test_same_seed_gives_identical_sources():
    people, langs = _people(4)
    a = journal.entries_for(people[1], SEED, langs[people[1].person_id])
    b = journal.entries_for(people[1], SEED, langs[people[1].person_id])
    assert a == b
    assert devices.series_for(people[1], SEED, "zh") == devices.series_for(people[1], SEED, "zh")


def test_default_language_draw_is_the_0_3_0_draw():
    # 0.3.0 drew "en" when the home stream's first number was below 0.5: a seed must keep its cohort.
    for pid in (f"p{i:03d}" for i in range(1, 201)):
        expected = "en" if person_mod.home_stream(SEED, pid).random() < 0.5 else "zh"
        assert person_mod.person_lang(SEED, pid) == expected


def test_lang_mix_ignores_order_and_rejects_bad_weights(monkeypatch):
    monkeypatch.setattr(person_mod, "_LANG_MIX", person_mod._LANG_MIX)
    pids = [f"p{i:03d}" for i in range(1, 101)]
    person_mod.set_lang_mix("zh:0.45,en:0.4,ja:0.15")
    first = [person_mod.person_lang(SEED, pid) for pid in pids]
    person_mod.set_lang_mix("ja:15, en:40, zh:45")
    assert [person_mod.person_lang(SEED, pid) for pid in pids] == first
    assert set(first) == {"en", "ja", "zh"}
    for bad in ("zh:-1,en:1", "zh:0,en:0", "zh:1,zh:2", ",en:1", "zh:nan", "zh:x"):
        with pytest.raises(ValueError):
            person_mod.set_lang_mix(bad)


def test_vendor_payloads_carry_the_phone_store_series():
    people, langs = _people(40)
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp)
        devices.write_all(out, people, SEED, langs)
        vendor_signals.write_all(out, people, SEED, langs)
        stores = {r["person_id"]: r for r in map(json.loads, (out / "devices.jsonl").open(encoding="utf-8"))}
        pushes = [json.loads(line) for line in (out / "vendor_signals.jsonl").open(encoding="utf-8")]
    assert {"apple", "garmin"} <= {p["vendor"] for p in pushes}
    dailies = 0
    for push in pushes:
        store = stores[push["person_id"]]
        if push["vendor"] == "apple":
            assert store["vendor"] == "apple"
            continue
        assert store["habits"]["wearable"] and store["vendor"] in vendor_signals.CLOUD_STORES
        if push["vendor"] == "garmin":
            steps = {r["time"][:10]: r["value"] for r in store["records"] if r["metric"] == "steps"}
            for row in push["records"]:
                if row["data_type"] == "dailies":
                    assert row["input"]["steps"] == steps.get(row["input"]["calendarDate"], 0)
                    dailies += 1
    assert dailies
