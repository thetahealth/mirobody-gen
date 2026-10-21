"""The clinical presentation profile is opt-in: a default build never enters its rules."""

from __future__ import annotations

import json

from mirobody_gen import build, clinical


def test_profile_is_off_by_default():
    assert clinical.ENABLED is False


def test_build_records_the_profile_only_when_asked(tmp_path, monkeypatch):
    monkeypatch.setattr(clinical, "ENABLED", False)
    build.main(["--seed", "7", "--people", "2", "--out", str(tmp_path / "plain")])
    assert "clinical_profile" not in json.loads((tmp_path / "plain" / "generation.json").read_text())
    assert clinical.ENABLED is False
    build.main(["--seed", "7", "--people", "2", "--out", str(tmp_path / "clinical"), "--clinical"])
    assert json.loads((tmp_path / "clinical" / "generation.json").read_text())["clinical_profile"] is True
