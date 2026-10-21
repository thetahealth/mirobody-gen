"""Clinical vocabulary regressions identified during expert scenario review."""

import json
from pathlib import Path
import runpy


def test_hbsag_aliases_preserve_antigen_identity():
    """HK is a glucose assay method, not an alias for hepatitis B surface antigen."""
    root = Path(__file__).resolve().parents[1]
    authored = runpy.run_path(str(root / "scripts" / "build_indicators.py"))
    generated = json.loads((root / "mirobody_gen" / "resources" / "indicators.json").read_text())
    source_hbsag = next(item for item in authored["INDICATORS"] if item["key"] == "hbsag")
    resource_hbsag = next(item for item in generated["indicators"] if item["key"] == "hbsag")

    assert resource_hbsag["name_variants"] == source_hbsag["name_variants"]
    for item in (source_hbsag, resource_hbsag):
        assert item["abbr"] == "HBsAg"
        assert "HBsAg" in item["name_variants"]
        assert all("HK" not in alias.upper() for alias in item["name_variants"])
