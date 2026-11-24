"""The proposer's contract, collisions, judge and resource; the models are faked, nothing is sent."""
from __future__ import annotations

import dataclasses
import json
import random

import pytest

from mirobody_gen import layout, spec, surface
from mirobody_gen.document import print_name
from mirobody_gen.llm import client, propose


@pytest.fixture()
def generated(tmp_path, monkeypatch):
    """Point the generated tier at a temporary file; restore the caches and the mode afterwards."""
    monkeypatch.setattr(spec, "GENERATED", tmp_path / "surface_generated.json")
    spec.surface.cache_clear()
    propose._owners.cache_clear()
    yield spec.GENERATED
    spec.surface.cache_clear()
    propose._owners.cache_clear()
    surface.set_mode("realistic")


@pytest.mark.parametrize("word,why", [
    ("血清谷丙转氨酶", None),
    ("谷丙转氨酶", "already a spelling of this indicator"),
    ("丙氨酸氨基转移酶测定", "adds 测定"),
    ("尿丙氨酸氨基转移酶", "adds the specimen 尿"),
    ("ALT活性", "adds Latin letters"),
    ("丙氨酸氨基转移酶2", "adds a digit"),
    ("A", "not a Chinese name of 2-20 characters"),
])
def test_the_synonym_contract(word, why):
    got = propose.reasons("synonym", spec.indicators()["alt"], word)
    assert got[:1] == ([why] if why else [])


def test_the_abbreviation_contract_and_collisions_with_another_indicator():
    crea = spec.indicators()["crea"]
    assert propose.reasons("abbr", crea, "CREAT") == []
    assert propose.reasons("abbr", crea, "SCR") == ["already a spelling of this indicator"]     # Scr, by case
    assert propose.reasons("abbr", crea, "Cr") == ["already a spelling of this indicator"]
    assert propose.reasons("abbr", crea, "肌酐值") == ["not a Latin-letter abbreviation of 2-12 characters"]
    assert propose.collisions("abbr", spec.indicators()["urea"], "crea") == ["crea"]          # case ignored
    assert propose.collisions("synonym", spec.indicators()["tbil"], "血清直接胆红素") == ["dbil"]
    assert propose.collisions("synonym", spec.indicators()["tbil"], "胆红素总量") == []


def test_screen_keeps_collisions_apart_and_counts_rejects():
    answers = {"m1": {"alt": ["血清谷丙转氨酶", "谷丙转氨酶"], "tbil": ["直接胆红素"], "nope": ["x"]},
               "m2": {"alt": ["血清谷丙转氨酶"]}}
    passing, collided, rejects = propose.screen("synonym", answers)
    assert passing == {"alt": {"血清谷丙转氨酶": {"m1", "m2"}}}
    assert collided == {"tbil": {"直接胆红素": ["dbil"]}}
    assert rejects == {"already a spelling of this indicator": 1, "unknown indicator": 1}


def test_the_judge_keeps_only_what_it_reads_as_the_same_test(monkeypatch):
    def fake(model, system, user, temperature=0.7):
        assert model == "judge" and "血清谷丙转氨酶" in user
        return {"text": '```json\n{"alt": {"血清谷丙转氨酶": true, "谷草转氨酶": false}}\n```'}

    monkeypatch.setattr(client, "complete", fake)
    kept, dropped = propose.judged("synonym", {"alt": {"血清谷丙转氨酶": {"m1"}, "谷草转氨酶": {"m1"}}}, "judge")
    assert kept == {"alt": {"血清谷丙转氨酶": {"m1"}}} and dropped == 1


def test_written_forms_are_drawn_by_fuzz_builds_only(generated):
    run = {"axis": "synonym", "judge": "judge", "date": "2026-11-24"}
    propose.write("synonym", {"alt": {"血清谷丙转氨酶": {"m1"}}}, {"tbil": {"直接胆红素": ["dbil"]}}, run)
    propose.write("synonym", {"alt": {"血清谷丙转氨酶": {"m2"}, "谷丙转氨酶活性": {"m2"}}}, {}, run)
    data = json.loads(generated.read_text(encoding="utf-8"))
    assert data["_vocabulary_fields"] == [] and len(data["_provenance"]["runs"]) == 2
    assert [c["word"] for c in data["axes"]["name_synonyms"]["alt"]] == ["血清谷丙转氨酶", "谷丙转氨酶活性"]
    assert data["collisions"]["name_synonyms"] == {"tbil": {"直接胆红素": ["dbil"]}}

    spec.surface.cache_clear()
    listed = spec.surface()["name_synonyms"]["indicators"]["alt"]
    assert listed[0] == {"word": "丙氨酸氨基转移酶", "tier": "reference", "share": 0,
                         "sources": ["the catalogue (scripts/build_indicators.py)"]}
    assert {c["tier"] for c in listed[1:]} == {"generated"}

    printed = {}
    for mode in ("off", "realistic", "fuzz"):
        surface.set_mode(mode)
        registry = layout.build_registry(7)
        for i in registry.pools[("zh", "hospital")]:
            f = dataclasses.replace(registry.family(i), name_style="native", columns=("name", "result"))
            if f.language == "zh-Hans":
                name, _, applied = print_name(spec.indicators()["alt"], f, random.Random(0))
                printed.setdefault(mode, set()).add((name, "name.synonym" in applied))
    assert printed["off"] == printed["realistic"] == {("丙氨酸氨基转移酶", False)}
    assert ("血清谷丙转氨酶", True) in printed["fuzz"] and ("丙氨酸氨基转移酶", False) in printed["fuzz"]


def test_write_needs_a_judge(monkeypatch):
    monkeypatch.setattr("sys.argv", ["propose", "--axis", "abbr", "--apply", "x.jsonl", "--write"])
    with pytest.raises(SystemExit, match="--judge"):
        propose.main()
