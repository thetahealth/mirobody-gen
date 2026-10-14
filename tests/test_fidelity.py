"""Laboratory fidelity metrics must accept the mixed files.jsonl manifest."""

from __future__ import annotations

from mirobody_gen.audit import fidelity
from mirobody_gen.harness import medrep_view


def test_genetics_reports_do_not_change_laboratory_fidelity():
    lab = {"file": "lab.pdf", "person_id": "p001", "family": "f0001", "kind": "lab_slip",
           "layout": {"columns": ["Item", "Result"], "reference_templates": [], "flag_markers": [],
                      "page_count": 1, "languages": ["en"]},
           "hazards": [], "hazard_count": 0, "printed_rows": [], "split": "main",
           "language": "en", "format": "pdf"}
    genetics = {"file": "pgx.pdf", "kind": "genetics_report", "pgx_rows": []}
    expected = fidelity.report([lab])
    got = fidelity.report([lab, genetics])
    assert any("Excluded 1 genetics_report" in line for line in got)
    assert [line for line in got if not line.startswith("Excluded ")] == expected


def test_genetics_only_manifest_has_no_laboratory_fidelity_metrics():
    lines = fidelity.report([{"file": "pgx.pdf", "kind": "genetics_report", "pgx_rows": []}])
    assert any("Excluded 1 genetics_report" in line for line in lines)
    assert any("No compatible documents" in line for line in lines)


def test_medrep_export_does_not_treat_pgx_rows_as_laboratory_rows():
    rows, dropped = medrep_view.export(
        [{"file": "pgx.pdf", "kind": "genetics_report", "pgx_rows": []}], {"genetics_report"})
    assert rows == []
    assert dropped == 0
