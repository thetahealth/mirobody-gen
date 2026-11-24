"""The shipped `generated` tier: every form still passes the proposer's contract and names no other indicator."""
from __future__ import annotations

import json

import pytest

from mirobody_gen import spec
from mirobody_gen.llm import propose

pytestmark = pytest.mark.shipped_generated_tier
AXIS = {name: axis for axis, name in propose.AXES.items()}


@pytest.fixture(scope="module")
def shipped():
    if not spec.GENERATED.is_file():
        pytest.skip("no generated tier shipped")
    return json.loads(spec.GENERATED.read_text(encoding="utf-8"))


def test_the_file_is_untrusted_text_with_its_runs(shipped):
    assert shipped["_vocabulary_fields"] == [] and shipped["_provenance"]["runs"]
    assert set(shipped["axes"]) <= set(spec.GENERATED_AXES)


def test_every_form_is_generated_and_passes_the_contract(shipped, monkeypatch):
    catalogue = spec.indicators()
    monkeypatch.setattr(spec, "GENERATED", spec.GENERATED.with_name("absent.json"))   # judge against the rest
    spec.surface.cache_clear()
    propose._owners.cache_clear()
    for name, by_key in shipped["axes"].items():
        for key, choices in by_key.items():
            for c in choices:
                assert c["tier"] == "generated" and c["share"] == 0 and c["sources"], (key, c)
                assert propose.reasons(AXIS[name], catalogue[key], c["word"]) == [], (key, c["word"])
                assert propose.collisions(AXIS[name], catalogue[key], c["word"]) == [], (key, c["word"])
    spec.surface.cache_clear()
    propose._owners.cache_clear()


def test_the_merged_tier_starts_each_list_with_the_catalogue(shipped):
    s, catalogue = spec.surface(), spec.indicators()
    for name, by_key in shipped["axes"].items():
        for key in by_key:
            first = s[name]["indicators"][key][0]
            assert first["word"] == catalogue[key][spec.GENERATED_AXES[name]] and first["tier"] != "generated"
