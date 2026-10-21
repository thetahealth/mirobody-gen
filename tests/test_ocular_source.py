import copy
import importlib
import hashlib
import json

import pytest

from mirobody_gen import cli


def module():
    return importlib.import_module("mirobody_gen.ocular_source")


def packet():
    return {"schema_version": "ocular-observations-v1", "synthetic": True, "case_id": "source-eye",
            "person_id": "synthetic-p1", "language": "en", "document_date": "2025-03-05",
            "source_system": "synthetic-fixture", "observations": [
                {"id": "obs-left", "source_id": "Observation/source-left", "item_id": "va",
                 "laterality": "left", "status": "measured", "value": "20/40", "notation": "Snellen",
                 "effective_time": "2025-03-02T09:12:34+08:00"}]}


def test_cli_entry_exists():
    assert "replay-ocular" in cli.COMMANDS


def test_single_side_replay_has_no_completion_and_keeps_raw_time_unit_and_ids(tmp_path, monkeypatch):
    m = module()
    monkeypatch.setattr("mirobody_gen.book._acuity_pair", lambda *a: pytest.fail("source replay must not sample eyes"))
    data = packet()
    data["observations"][0]["unit"] = "source-unit"
    path = tmp_path/"input.jsonl"
    path.write_text(json.dumps(data)+"\n")
    result = m.replay(path, tmp_path/"out")
    record = json.loads((tmp_path/"out/ocular.jsonl").read_text())
    assert record["observations"] == data["observations"] and len(record["observations"]) == 1
    assert record["completion_performed"] is False
    assert record["source_system"] == data["source_system"]
    assert result["audit_ok"] and result["clinical_accuracy_measured"] is False
    assert record["observations"][0]["effective_time"] == "2025-03-02T09:12:34+08:00"


@pytest.mark.parametrize("status,side", [("not_measured", "right"), ("unknown", "left"), ("measured", "unspecified")])
def test_missing_state_or_unknown_side_never_becomes_a_measured_pair(status, side):
    m = module()
    data = packet()
    obs = data["observations"][0]
    obs["status"], obs["laterality"] = status, side
    if status != "measured": obs.pop("value")
    record, pdf = m.render_packet(data)
    assert record["observations"] == data["observations"] and len(record["observations"]) == 1
    assert m.audit_record(data, record, pdf)["ok"]


@pytest.mark.parametrize("fault", ["blank_value", "value_with_missing_state", "naive_time", "bad_date",
                                    "duplicate_key", "unknown_field", "empty", "unsafe_id"])
def test_invalid_or_contradictory_source_is_rejected(fault):
    m = module()
    data = packet()
    obs = data["observations"][0]
    if fault == "blank_value": obs["value"] = " "
    elif fault == "value_with_missing_state": obs["status"] = "not_measured"
    elif fault == "naive_time": obs["effective_time"] = "2025-03-02T09:12:34"
    elif fault == "bad_date": obs["effective_time"] = "2025-02-30"
    elif fault == "duplicate_key": data["observations"].append({**obs, "id": "second-id", "value": "20/20"})
    elif fault == "unknown_field": obs["invented_interpretation"] = "normal"
    elif fault == "empty": data["observations"] = []
    else: data["case_id"] = "../escape"
    with pytest.raises(ValueError): m.validate_packet(data)


@pytest.mark.parametrize("field,value", [("laterality", "right"), ("value", "20/20"),
                                         ("unit", "new-unit"), ("effective_time", "2025-03-03"),
                                         ("source_id", "Observation/other")])
def test_independent_audit_rejects_changed_source_or_wrong_physical_report(field, value):
    m = module()
    data = packet()
    record, pdf = m.render_packet(data)
    changed = copy.deepcopy(record)
    changed["observations"][0][field] = value
    assert not m.audit_record(data, changed, pdf)["ok"]
    wrong_packet = copy.deepcopy(data)
    wrong_packet["observations"][0][field] = value
    _, wrong_pdf = m.render_packet(wrong_packet)
    matching_hash_record = {**record, "pdf_sha256": hashlib.sha256(wrong_pdf).hexdigest()}
    physical_audit = m.audit_record(data, matching_hash_record, wrong_pdf)
    assert not physical_audit["ok"] and physical_audit["checks"]["pdf_hash"]
    assert physical_audit["checks"]["physical_text_exact"] is False


def test_batch_is_validated_before_any_output(tmp_path):
    m = module()
    good = packet()
    bad = copy.deepcopy(good)
    bad["case_id"] = "bad-source"
    bad["observations"][0]["status"] = "unknown"
    path = tmp_path/"input.jsonl"
    path.write_text(json.dumps(good)+"\n"+json.dumps(bad)+"\n")
    with pytest.raises(ValueError): m.replay(path, tmp_path/"absent")
    assert not (tmp_path/"absent").exists()


def test_repeated_source_render_is_byte_deterministic(tmp_path):
    m = module()
    path = tmp_path/"input.jsonl"
    path.write_text(json.dumps(packet())+"\n")
    m.replay(path, tmp_path/"a")
    m.replay(path, tmp_path/"b")
    a = {p.name:p.read_bytes() for p in (tmp_path/"a").iterdir()}
    b = {p.name:p.read_bytes() for p in (tmp_path/"b").iterdir()}
    assert a == b


def test_saved_output_audit_catches_extra_observation_without_rendering(tmp_path, monkeypatch):
    m = module()
    path = tmp_path/"input.jsonl"
    path.write_text(json.dumps(packet())+"\n")
    root = tmp_path/"out"
    m.replay(path, root)
    monkeypatch.setattr(m, "render_packet", lambda *a: pytest.fail("audit-only must not regenerate"))
    assert m.audit_existing(path, root)["audit_ok"]
    record = json.loads((root/"ocular.jsonl").read_text())
    record["observations"].append({**record["observations"][0], "id": "added-right", "laterality": "right"})
    (root/"ocular.jsonl").write_text(json.dumps(record)+"\n")
    assert not m.audit_existing(path, root)["audit_ok"]


def test_duplicate_json_fields_are_not_silently_overwritten(tmp_path):
    m = module()
    path = tmp_path/"duplicate.jsonl"
    path.write_text('{"synthetic":true,"synthetic":false}')
    with pytest.raises(ValueError, match="Duplicate JSON field"): m.load_packets(path)


def test_case_ids_differing_only_in_case_are_rejected(tmp_path):
    first, second = packet(), packet()
    first["case_id"], second["case_id"] = "Eye-A", "eye-a"
    path = tmp_path/"input.jsonl"
    path.write_text(json.dumps(first)+"\n"+json.dumps(second)+"\n")
    with pytest.raises(ValueError, match="case-insensitively"):
        module().replay(path, tmp_path/"out")
    assert not (tmp_path/"out").exists()


@pytest.mark.parametrize("separator", [" ", " ", "\u0085", "\x7f"])
def test_line_separators_and_control_characters_are_rejected(separator):
    data = packet()
    data["source_system"] = f"synthetic{separator}fixture"
    with pytest.raises(ValueError, match="source_system"):
        module().validate_packet(data)


def test_audit_compares_json_types(tmp_path):
    m = module()
    path = tmp_path/"input.jsonl"
    path.write_text(json.dumps(packet())+"\n")
    m.replay(path, tmp_path/"out")
    saved = tmp_path/"out/ocular.jsonl"
    record = json.loads(saved.read_text())
    record["synthetic"] = 1  # equal to True in Python, a different JSON value
    saved.write_text(json.dumps(record)+"\n")
    result = m.audit_existing(path, tmp_path/"out")
    assert not result["audit_ok"] and "source_fields" in result["failures"][0]["checks"]
