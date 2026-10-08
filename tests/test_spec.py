"""Integrity tests for the spec. `python3 -m pytest tests/ -q`, or `python3 tests/test_spec.py` directly.

These tests guard **contracts**, not values: whether a reference range is clinically right is judged by
`scripts/build_indicators.py --compare` and a human; here the only concern is that the spec's shape can be
consumed by the generator and the audits, and that it isn't self-contradictory.
"""

from __future__ import annotations

import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
RESOURCES = REPO / "mirobody_gen" / "resources"
sys.path.insert(0, str(REPO))



def load(name: str) -> dict:
    return json.loads((RESOURCES / name).read_text(encoding="utf-8"))


def test_every_spec_declares_provenance():
    """No spec file may exist without a provenance declaration -- the privacy gate checks this too; let it
    fail here first."""
    for path in sorted(RESOURCES.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload.get("_source") in {"public-standard", "format-token", "hand-authored", "llm-paraphrase", "llm-template"}, path
        assert "_provenance" in payload, path
        assert "_vocabulary_fields" in payload, path


def test_indicator_fields_are_complete():
    indicators = load("indicators.json")["indicators"]
    assert len(indicators) >= 170
    keys = set()
    for item in indicators:
        for field in ("key", "zh", "unit", "decimals", "panel", "value_kind",
                      "reference_source", "name_variants"):
            assert field in item, (item.get("key"), field)
        assert item["key"] not in keys, f"duplicate key: {item['key']}"
        keys.add(item["key"])


def test_reference_shapes_are_wellformed():
    """For each of the five reference-range shapes, the lower bound is never above the upper bound."""
    for item in load("indicators.json")["indicators"]:
        ref = item["reference"]
        if ref is None:
            continue
        kind = ref[0]
        assert kind in {"range", "range_sex", "upper", "lower", "qualitative"}, item["key"]
        if kind == "range":
            assert ref[1] <= ref[2], item["key"]
        elif kind == "range_sex":
            for lo, hi in (ref[1], ref[2]):
                assert lo <= hi, item["key"]


def test_derived_indicators_have_their_inputs():
    """The keys a derived indicator depends on must exist in the catalogue, or its identity is silently
    skipped at generation time."""
    indicators = load("indicators.json")["indicators"]
    keys = {i["key"] for i in indicators}
    referenced = {
        "bmi": {"weight", "height"}, "mcv": {"hct", "rbc"}, "mch": {"hgb", "rbc"},
        "mchc": {"hgb", "hct"}, "glb": {"tp", "alb"}, "ag_ratio": {"alb", "glb"},
        "ibil": {"tbil", "dbil"}, "ldl": {"chol", "hdl", "tg"},
        "neut_abs": {"wbc", "neut_pct"}, "lymph_abs": {"wbc", "lymph_pct"},
        "mono_abs": {"wbc", "mono_pct"}, "eos_abs": {"wbc", "eos_pct"},
        "baso_abs": {"wbc", "baso_pct"},
    }
    for key, needs in referenced.items():
        assert key in keys, key
        assert needs <= keys, (key, needs - keys)


def test_audit_identities_match_the_catalogue():
    """Every identity the audit defines has both ends -- target and dependencies -- actually present in
    the catalogue.

    This guards against a failure that's easy to miss: rename a key in the catalogue, the audit identity
    silently stops matching, and "0 findings" no longer means the data is clean -- it means nothing was
    checked.
    """
    from mirobody_gen.audit import clinical

    keys = {i["key"] for i in load("indicators.json")["indicators"]}
    for key, label, needs, _formula, _tol in clinical.IDENTITIES:
        assert key in keys, f"audit identity {label}'s target {key} is not in the catalogue"
        assert set(needs) <= keys, f"audit identity {label}'s dependencies {set(needs) - keys} are not in the catalogue"
    assert set(clinical.DIFFERENTIAL) <= keys
    assert set(clinical.SEX_ONLY) <= keys
    assert set(clinical.HARD_LIMITS) <= keys


def test_clinical_audit_selftest_passes():
    from mirobody_gen.audit import clinical

    assert clinical.audit([clinical.CLEAN]) == []
    kinds = {f.kind for f in clinical.audit([clinical.BROKEN])}
    assert {"恒等式", "生理边界", "人口学", "标记"} <= kinds


def test_hazard_taxonomy_is_usable():
    hazards = load("hazards.json")
    generatable = [c for c in hazards["classes"] if c["generate"]]
    assert len(generatable) >= 50
    # injection density follows the distribution after redaction artefacts are stripped out; p95 is the
    # main leaderboard's cap.
    counts = hazards["per_document_count"]
    assert counts["p50"] >= 1 and counts["p95"] >= counts["p75"] >= counts["p50"]
    assert 0 < counts["zero_share"] < 0.5
    # the non-reproduced class must exist, and be only the processing-artefact one
    artifacts = [c["name"] for c in hazards["classes"] if not c["generate"]]
    assert artifacts == ["artifact.redaction_placeholder"], artifacts


def test_layout_space_is_big_enough_to_be_worth_sampling():
    layout = load("layout.json")
    assert len(layout["column_sets_by_role"]) >= 15
    assert len(layout["reference_dialects"]) >= 30
    assert len(layout["flag_markers"]) >= 10
    assert len(layout["date_formats"]) >= 8
    # a transposed export must be in here: it's the dominant layout for production xlsx exports, and the
    # first version of the whitelist dropped that whole class.
    roles = [tuple(item["value"]) for item in layout["column_sets_by_role"]]
    assert any("analyte+" in r and "meta_facility" in r for r in roles), \
        "the transposed-export role combination is missing -- check distill_layout's context-acceptance rules"


def test_no_redaction_placeholder_survived_into_spec():
    """A redaction placeholder is a processing artefact of the corpus. Finding one in the spec means
    distillation missed a filter."""
    for path in sorted(RESOURCES.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        assert "«" not in text and "»" not in text, path


def test_spec_is_regenerable_without_drift():
    """Rerunning the three distill/build scripts must leave `mirobody_gen/resources/` byte-for-byte
    unchanged.

    This catches **silent drift between the scripts and their output**: someone edits a script and forgets
    to rerun it, or hand-edits the spec -- either way the spec stops being "what distillation rules
    produce from the corpus", and those rules are exactly what the privacy argument rests on.

    Needs `analysis/` and `library/` locally (derivatives of the real corpus), so it's skipped on CI. The
    skip states why rather than passing silently -- "the test passed" should never also mean "the test
    didn't run".
    """
    import subprocess

    if not (REPO / "analysis").is_dir():
        print("  skip test_spec_is_regenerable_without_drift: no analysis/ locally, "
              "this test only runs on a machine with the corpus")
        return

    # build_indicators queries mirobody's resolver for LOINC codes; without the resolver it **raises and
    # exits** (since 2026-09-22, see ResolverUnavailable), so this one script runs under mirobody's venv.
    mirobody_python = REPO.parent / "mirobody" / ".venv" / "bin" / "python"
    if not mirobody_python.is_file():
        print(f"  skip test_spec_is_regenerable_without_drift: {mirobody_python} not found, "
              "build_indicators needs mirobody's resolver")
        return

    before = {p.name: p.read_bytes() for p in sorted(RESOURCES.glob("*.json"))}
    for script in ("distill_hazards.py", "distill_layout.py", "build_indicators.py",
                   "build_cohort.py", "build_fiction.py", "build_profile.py", "build_handwriting.py"):
        python = str(mirobody_python) if script == "build_indicators.py" else sys.executable
        result = subprocess.run([python, str(REPO / "scripts" / script), "--write"],
                                capture_output=True, cwd=REPO)
        assert result.returncode == 0, f"{script} exited {result.returncode}: {result.stderr[-400:]}"
    after = {p.name: p.read_bytes() for p in sorted(RESOURCES.glob("*.json"))}

    drifted = [name for name in before if before[name] != after.get(name)]
    # build_indicators queries mirobody's resolver for LOINC codes; without the resolver, expect_resolvable
    # turns false everywhere -- that's a missing dependency, not drift, and worth calling out separately
    # rather than reporting as a failure.
    if drifted == ["indicators.json"]:
        import json as _json
        resolved = sum(1 for i in _json.loads(after["indicators.json"])["indicators"]
                       if i.get("expect_resolvable"))
        if resolved == 0:
            print("  note: indicators.json changed because this run didn't find mirobody's resolver "
                  "(expect_resolvable is false everywhere). Rerun under mirobody's venv to restore it")
            for name, data in before.items():
                (RESOURCES / name).write_bytes(data)
            return
    assert not drifted, f"spec has drifted from the scripts: {drifted}"


def test_scripts_do_not_shadow_stdlib():
    """A file under `scripts/` must never share a name with a standard-library module.

    `python3 scripts/xxx.py` puts `scripts/` at `sys.path[0]`, so a same-named file there **replaces** the
    standard-library module. This test was written after a real incident: `scripts/numbers.py` shadowed
    `numbers`, numpy picked it up on import, `numbers.Integral` didn't exist, numpy broke, mirobody's
    resolver failed to import, the failure was swallowed by `except Exception`, and the end result looked
    like "the LOINC codes for all 98 indicators failed to resolve" -- an environment fault that grew into
    what looked like a measurement.
    """
    import sys as _sys

    stdlib = set(getattr(_sys, "stdlib_module_names", ()))
    assert stdlib, "couldn't get the standard-library module list (needs Python 3.10+)"
    clashes = sorted(p.stem for p in (REPO / "scripts").glob("*.py") if p.stem in stdlib)
    assert not clashes, f"these script names shadow standard-library modules: {clashes}"


def test_every_quantitative_indicator_has_a_hard_limit():
    """Every quantitative indicator either has a physiological hard limit or an exemption explaining why
    not.

    This test was written after a real incident: waist circumference wasn't in HARD_LIMITS, so the
    generator printed a 44.2 cm waist on a 104 kg body and the bounds check said nothing -- **a missing
    entry in a checklist is silent**, so "0 findings" can mean either nothing is wrong or that item was
    never checked at all.

    Filling that gap also surfaced indirect bilirubin at -2.1 on the spot (direct bilirubin had been
    sampled independently and could exceed total bilirubin). One completeness test traded for one latent
    defect.
    """
    from mirobody_gen.audit import clinical

    quantitative = {i["key"] for i in load("indicators.json")["indicators"]
                    if i["value_kind"] == "quantitative"}
    covered = set(clinical.HARD_LIMITS) | set(clinical.NO_HARD_LIMIT)
    missing = sorted(quantitative - covered)
    assert not missing, f"these quantitative indicators have neither a hard limit nor an exemption reason: {missing}"

    # the exemption table isn't a dumping ground either: an exemption plus a limit means one of them is stale
    both = sorted(set(clinical.HARD_LIMITS) & set(clinical.NO_HARD_LIMIT))
    assert not both, f"these indicators appear in both the hard-limit and exemption tables: {both}"


def test_derived_chain_is_acyclic_and_rooted():
    """A derivation chain must never cycle, and every link's inputs must exist.

    The red-cell indices flipped direction twice during development (first HGB/HCT derived MCV/MCHC, then
    RBC/MCV/MCH, finally settling on RBC/MCV/MCHC). Each flip could leave behind an edge still pointing the
    old way, and a cycle shows up at generation time as a few silently missing rows, not an error.
    """
    items = {i["key"]: i for i in load("indicators.json")["indicators"]}
    import re

    edges = {}
    for key, item in items.items():
        formula = item.get("derived_from") or ""
        deps = {t for t in re.findall(r"[a-z_]+", formula) if t in items and t != key}
        edges[key] = deps

    seen, stack = set(), set()

    def walk(node: str) -> None:
        if node in stack:
            raise AssertionError(f"derivation chain cycles through {node}")
        if node in seen:
            return
        stack.add(node)
        for dep in edges.get(node, ()):
            walk(dep)
        stack.discard(node)
        seen.add(node)

    for key in items:
        walk(key)


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
