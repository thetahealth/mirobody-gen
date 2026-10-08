"""Tests for the generator. `python3 tests/test_generator.py` (or pytest).

Tests **contracts and invariants** only, never specific values: whether a person's glucose is 5.3 or 5.4
doesn't matter; what matters is that the same seed gives the same 5.3 twice, the manifest's fields are all
present, and the clinical audit is clean.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

SMOKE_PEOPLE = 8


def _build(seed: int, out: pathlib.Path, people: int = SMOKE_PEOPLE):
    from mirobody_gen import manifest, person as person_mod

    cohort = person_mod.build_cohort(seed, people)
    encounters = {p.person_id: person_mod.encounters_for(p, seed) for p in cohort}
    manifest.write(out, cohort, encounters)
    return cohort, encounters


def test_same_seed_is_byte_identical():
    """The same seed, built twice, must be byte-identical.

    This is the whole meaning of "replayable", and the reason `out/` can be deleted without a second
    thought: the source of truth is the generator plus a seed, not the pile of bytes. One stray unordered
    set or system random call anywhere and this fails immediately.
    """
    with tempfile.TemporaryDirectory() as tmp:
        a, b = pathlib.Path(tmp) / "a", pathlib.Path(tmp) / "b"
        _build(7, a)
        _build(7, b)
        for name in ("manifest.jsonl", "people.jsonl"):
            assert (a / name).read_bytes() == (b / name).read_bytes(), name


def test_different_seed_gives_a_different_corpus():
    """A different seed must give a different corpus.

    This is the precondition for "the benchmark is the generator, not a fixed file": scoring uses a sealed
    seed and development uses an open one, and only because they differ can anyone claim no one tuned
    against the leaderboard.
    """
    with tempfile.TemporaryDirectory() as tmp:
        a, b = pathlib.Path(tmp) / "a", pathlib.Path(tmp) / "b"
        _build(7, a)
        _build(8, b)
        assert (a / "manifest.jsonl").read_bytes() != (b / "manifest.jsonl").read_bytes()


def test_manifest_rows_carry_the_audit_contract():
    """Every field the audit depends on must be present. Missing one means the audit silently skips that
    class of check."""
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp)
        _build(7, out)
        records = [json.loads(line) for line in
                   (out / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
        assert records
        for record in records:
            for field in ("file", "person_id", "person", "collected", "rows",
                          "hazards", "events_since_previous", "synthetic"):
                assert field in record, field
            assert record["person"]["sex"] in ("male", "female")
            for row in record["rows"]:
                for field in ("key", "original_indicator", "value", "unit",
                              "reference_range", "status", "detection_method",
                              "canonical_value", "value_kind", "readable"):
                    assert field in row, (row.get("key"), field)
                assert row["status"] in ("normal", "high", "low")


def test_clinical_audit_is_clean_on_generated_output():
    """The generated ground-truth layer must raise **zero findings**.

    This is the acceptance criterion for the ground-truth layer itself. It exercises identities,
    physiological bounds, demographics, marker consistency and longitudinal out-of-range rates together --
    a model error anywhere surfaces here.
    """
    from mirobody_gen.audit import clinical

    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp)
        _build(7, out)
        records = [json.loads(line) for line in
                   (out / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
        findings = clinical.audit(records)
        assert not findings, "\n".join(str(f) for f in findings[:10])


def test_cohort_matches_the_spec():
    from mirobody_gen import person as person_mod, spec

    cohort_spec = spec.cohort()
    names = [a["name"] for a in cohort_spec["archetypes"]]
    assert len(names) == len(set(names)), "duplicate archetype name"
    assert sum(a["n"] for a in cohort_spec["archetypes"]) == 60
    # a reduced sample must cut **across** archetypes, not just take the first N -- otherwise the smoke
    # test only ever sees healthy people.
    small = person_mod.build_cohort(7, 8)
    assert len({p.archetype for p in small}) >= 4, \
        f"an 8-person reduced cohort covers only {len({p.archetype for p in small})} archetypes"


def test_events_are_visible_in_the_timeline():
    """An intervention event must fall inside the observation window, with encounters both before and
    after it -- otherwise there is no way to attribute an effect to it."""
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp)
        cohort, encounters = _build(7, out)
        with_script = [p for p in cohort if p.archetype != "healthy"]
        assert with_script, "the reduced cohort has no chronic-disease archetype at all"
        for person in with_script:
            dates = [e.exam_date for e in encounters[person.person_id]]
            scripted = [e for e in person.events if e.note == "原型剧本"]
            for event in scripted:
                assert any(d < event.start for d in dates), \
                    f"{person.person_id}'s '{event.name}' has no encounter before it, so there's no baseline"
                assert any(d > event.start for d in dates), \
                    f"{person.person_id}'s '{event.name}' has no encounter after it, so no effect is visible"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok  {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL  {name}: {e}")
    print(f"\n{failures} failed" if failures else "\nall passed")
    sys.exit(1 if failures else 0)
