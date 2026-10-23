"""The clinical presentation profile is on by default; `--no-clinical` turns it off and `--clinical` is
still accepted, so recipes pinned before the switch keep working."""

from __future__ import annotations

import json

import pytest

from mirobody_gen import build, clinical, clinical_issuers, corpus, layout, person, spec


def _generation(out) -> dict:
    return json.loads((out / "generation.json").read_text())


def test_profile_is_on_by_default():
    assert clinical.ENABLED is True


def test_build_records_the_profile_unless_turned_off(tmp_path, monkeypatch):
    monkeypatch.setattr(clinical, "ENABLED", True)
    build.main(["--seed", "7", "--people", "2", "--out", str(tmp_path / "off"), "--no-clinical"])
    assert "clinical_profile" not in _generation(tmp_path / "off")
    assert clinical.ENABLED is False
    for name, flag in (("default", []), ("pinned", ["--clinical"])):
        build.main(["--seed", "7", "--people", "2", "--out", str(tmp_path / name), *flag])
        assert _generation(tmp_path / name)["clinical_profile"] is True
        assert clinical.ENABLED is True
    assert (tmp_path / "default" / "manifest.jsonl").read_bytes() == (tmp_path / "pinned" / "manifest.jsonl").read_bytes()


@pytest.fixture(scope="module")
def default_build(tmp_path_factory):
    out = tmp_path_factory.mktemp("default")
    build.main(["--seed", "7", "--people", "2", "--render", "--out", str(out)])
    files = [json.loads(line) for line in (out / "files.jsonl").open(encoding="utf-8")]
    sex = {p["person_id"]: p["sex"] for p in map(json.loads, (out / "people.jsonl").open(encoding="utf-8"))}
    return [f for f in files if f["kind"] in ("lab_slip", "checkup_book")], sex


def test_a_default_build_prints_units_ranges_and_flags(default_build):
    files, sex = default_build
    checked = 0
    for f in files:
        abnormal = 0
        for row in f["printed_rows"]:
            reading = f["readings"][row["readings"][0]] if row["readings"] else None
            if not reading or reading["value_kind"] != "quantitative":
                continue
            if spec.reference_bounds(reading["key"], sex[f["person_id"]]) == (None, None):
                continue  # height, weight: no range to print
            assert row["item_range"], (f["doc_id"], row["item_name"])
            assert row["item_unit"] or not reading["unit_ucum"], (f["doc_id"], row["item_name"])
            abnormal += reading["status"] in ("high", "low")
            checked += 1
        # Without the profile a two-column US layout (In Range / Out of Range) prints no flag at all.
        assert len(f["layout"]["flag_markers"]) >= abnormal, f["doc_id"]
    assert checked > 500


def test_a_default_build_prints_the_ecg_with_its_tracing_not_in_a_lab_table(default_build):
    files, _ = default_build
    ecg = set(spec.panels()["ecg"])
    books = [f for f in files if f["kind"] == "checkup_book"]
    assert books and all(any(b["section"] == "ecg" for b in f["blocks"]) for f in books)
    assert not [r["key"] for f in files for r in f["readings"] if r["key"] in ecg]


def test_a_default_build_never_sends_a_man_to_a_maternal_and_child_hospital():
    people = person.build_cohort(7, None)
    encounters = {p.person_id: person.encounters_for(p, 7) for p in people}
    process = corpus.Institutions(layout.build_registry(7), 7)
    homes, chosen, _ = corpus.assign_institutions(7, people, encounters, process)
    men = {p.person_id for p in people if p.sex == "male"}
    names = process.registry.institutions
    issued = [names[i]["name"] for (pid, _), i in chosen.items() if pid in men]
    issued += [names[i]["name"] for pid, (_, home) in homes.items() if pid in men for i in home.values()]
    assert issued and not [n for n in issued if clinical_issuers.MATERNAL_CHILD in n]
