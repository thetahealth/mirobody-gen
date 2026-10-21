"""`build --verify` reports every check as passed/failed/skipped/not_run and fails on a gating failure.

Before: `build` ended once files were written; nothing ran the audits, so a build with clinical
findings, a short population or no privacy replay looked the same as a checked one.
"""

import json

import pytest

from mirobody_gen import build, verify
from mirobody_gen.audit import clinical, privacy


def _out(tmp_path, records):
    out = tmp_path / "o"
    out.mkdir()
    (out / "manifest.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records),
                                        encoding="utf-8")
    return out


def _status(summary):
    return {c["check"]: c["status"] for c in summary["checks"]}


@pytest.fixture
def no_replay_index(monkeypatch, tmp_path):
    monkeypatch.setattr(privacy, "CACHE", tmp_path / "absent.npy")


def test_clean_manifest_is_incomplete_not_passed(tmp_path, no_replay_index):
    summary = verify.run(_out(tmp_path, [clinical.CLEAN]), requested_people=1, expected_people=1, people=1,
                         rendered=False, pairs=False, continuous=False)
    status = _status(summary)
    assert status["clinical_manifest"] == "passed" and status["population"] == "passed"
    assert status["privacy_replay"] == "not_run" and status["other_sources_audit"] == "not_run"
    assert status["readability_files"] == "skipped" and status["image_ocr"] == "skipped"
    assert summary["overall"] == "incomplete"
    saved = json.loads((tmp_path / "o" / "verification.json").read_text(encoding="utf-8"))
    assert saved == summary
    assert set(status.values()) <= set(verify.STATUSES)


def test_clinical_findings_fail(tmp_path, no_replay_index):
    summary = verify.run(_out(tmp_path, [clinical.BROKEN]), requested_people=1, expected_people=1, people=1,
                         rendered=False, pairs=False, continuous=False)
    check = next(c for c in summary["checks"] if c["check"] == "clinical_manifest")
    assert check["status"] == "failed" and check["findings"] > 0
    assert summary["overall"] == "failed"
    assert (tmp_path / "o" / check["log"]).read_text(encoding="utf-8").strip()


def test_short_population_fails(tmp_path, no_replay_index):
    summary = verify.run(_out(tmp_path, [clinical.CLEAN]), requested_people=61, expected_people=61, people=60,
                         rendered=False, pairs=False, continuous=False)
    assert _status(summary)["population"] == "failed" and summary["overall"] == "failed"


def test_build_verify_end_to_end(tmp_path, no_replay_index, capsys):
    out = tmp_path / "b"
    build.main(["--seed", "7", "--people", "1", "--verify", "--out", str(out)])
    summary = json.loads((out / "verification.json").read_text(encoding="utf-8"))
    assert summary["overall"] == "incomplete"
    assert "verification: incomplete" in capsys.readouterr().out


def test_build_exits_1_on_gating_failure(tmp_path, no_replay_index, monkeypatch):
    monkeypatch.setattr(clinical, "audit", lambda records: [clinical.Finding("恒等式", "x", "planted")])
    with pytest.raises(SystemExit) as exc:
        build.main(["--seed", "7", "--people", "1", "--verify", "--out", str(tmp_path / "f")])
    assert exc.value.code == 1
    summary = json.loads((tmp_path / "f" / "verification.json").read_text(encoding="utf-8"))
    assert summary["overall"] == "failed"


def test_readability_with_nothing_checked_is_not_passed(tmp_path, no_replay_index):
    out = _out(tmp_path, [clinical.CLEAN])
    (out / "pairs.jsonl").write_text("", encoding="utf-8")
    summary = verify.run(out, requested_people=1, expected_people=1, people=1,
                         rendered=False, pairs=True, continuous=False)
    check = next(c for c in summary["checks"] if c["check"] == "readability_pairs")
    assert check["status"] == "skipped" and check["checked_rows"] == 0
