"""Tests see the expression layer without its `generated` tier unless they put one in place: the tier
grows with every `mirobody-gen propose` run, and a test of exact draws must not move with it.
`test_generated_forms.py` checks the shipped file itself."""
from __future__ import annotations

import pytest

from mirobody_gen import spec


@pytest.fixture(autouse=True)
def _no_generated_tier(tmp_path_factory, monkeypatch, request):
    if request.node.get_closest_marker("shipped_generated_tier"):
        yield
        return
    monkeypatch.setattr(spec, "GENERATED", tmp_path_factory.getbasetemp() / "no_surface_generated.json")
    spec.surface.cache_clear()
    yield
    spec.surface.cache_clear()
