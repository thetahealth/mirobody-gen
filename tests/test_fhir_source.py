"""FHIR source snapshot fidelity; no clinical normalization or network."""
from __future__ import annotations

import importlib
import json
import subprocess
import sys

import pytest


def module():
    return importlib.import_module("mirobody_gen.fhir_source")


def bundle_bytes():
    return b'''{"resourceType":"Bundle","type":"history","entry":[
      {"fullUrl":"urn:uuid:synthetic-obs","resource":{"resourceType":"Observation","id":"same",
       "meta":{"versionId":"z","lastUpdated":"2025-03-01T10:00:00Z"},"status":"final",
       "code":{"coding":[{"system":"urn:synthetic","code":"iop"},{"system":"urn:alternate","code":"A"}]},
       "bodySite":{"text":"Side not supplied"},"method":{"text":"Synthetic method"},
       "effectiveDateTime":"2025-03-01T18:00:00+08:00","issued":"2025-03-02T00:00:00Z",
       "valueQuantity":{"value":20.00,"unit":"mmHg","system":"http://unitsofmeasure.org","code":"mm[Hg]"},
       "referenceRange":[{"low":{"value":10.00},"high":{"value":21.00},"appliesTo":[{"text":"group1"}]},{"text":"method specific"}],
       "extension":[{"url":"urn:synthetic:keep","valueDecimal":1.234567890123456789e-3}]}},
      {"fullUrl":"urn:uuid:synthetic-obs","resource":{"resourceType":"Observation","id":"same",
       "meta":{"versionId":"a"},"status":"corrected","code":{"text":"IOP"},
       "valueQuantity":{"value":19.00,"unit":"kPa"}}},
      {"request":{"method":"DELETE","url":"Observation/old"}},
      {"resource":{"resourceType":"Patient","id":"synthetic-person"}}
    ]}'''


def test_raw_slices_preserve_all_fields_decimal_lexemes_and_source_order():
    plan = module().plan(bundle_bytes())
    records = plan["records"]
    assert len(records) == 2
    assert [r["version_id"] for r in records] == ["z", "a"]
    assert [r["source_pointer"] for r in records] == ["/entry/0/resource", "/entry/1/resource"]
    raw = records[0]["resource_json"]
    assert '"value":20.00' in raw and "1.234567890123456789e-3" in raw
    obj = json.loads(raw)
    assert len(obj["code"]["coding"]) == 2 and len(obj["referenceRange"]) == 2
    assert obj["method"]["text"] == "Synthetic method" and "bodySite" in obj
    assert obj["effectiveDateTime"] == "2025-03-01T18:00:00+08:00"
    assert all("laterality" not in r and "measurement_status" not in r for r in records)
    assert plan["summary"]["non_observation_entries"] == 2


@pytest.mark.parametrize("status", ["registered", "preliminary", "final", "amended", "corrected",
                                    "cancelled", "entered-in-error", "unknown"])
def test_lifecycle_status_is_preserved_without_measurement_state_mapping(status):
    raw = json.dumps({"resourceType": "Bundle", "type": "collection", "entry": [{"resource": {
        "resourceType": "Observation", "status": status, "code": {"text": "synthetic"},
        "dataAbsentReason": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/data-absent-reason",
                                         "code": "not-performed"}]}}}]}).encode()
    r = module().plan(raw)["records"][0]
    assert r["observation_status"] == status and r["value_keys"] == []
    assert "not-performed" in r["resource_json"]
    assert r["clinical_use"] == ("excluded_entered_in_error" if status == "entered-in-error" else "not_adjudicated")


def test_components_extensions_and_partial_times_stay_raw():
    obj = {"resourceType": "Bundle", "type": "collection", "entry": [{"resource": {
        "resourceType": "Observation", "status": "final", "code": {"text": "panel"},
        "effectiveDateTime": "2025", "modifierExtension": [{"url": "urn:synthetic:modifier", "valueBoolean": True}],
        "component": [{"code": {"text": "part"}, "valueString": "20/40"}]}}]}
    r = module().plan(json.dumps(obj).encode())["records"][0]
    assert json.loads(r["resource_json"]) == obj["entry"][0]["resource"]
    assert r["clinical_use"] == "not_adjudicated" and r["value_keys"] == []


@pytest.mark.parametrize("malformation", ["duplicate", "nan", "two_values", "obs6", "component_obs6", "status",
                                          "unknown_value_choice", "lone_surrogate"])
def test_malformed_or_explicitly_conflicting_source_rejected_before_output(tmp_path, malformation):
    raw = b'{"resourceType":"Bundle","type":"collection","entry":[{"resource":{"resourceType":"Observation","status":"final","code":{"text":"synthetic"},"valueQuantity":{"value":17}}}]}'
    if malformation == "duplicate": raw = raw.replace(b'"status":"final"', b'"status":"final","status":"unknown"')
    if malformation == "nan": raw = raw.replace(b'"value":17', b'"value":NaN')
    if malformation == "two_values": raw = raw.replace(b'"valueQuantity":', b'"valueString":"17","valueQuantity":')
    if malformation == "obs6": raw = raw.replace(b'"valueQuantity":', b'"dataAbsentReason":{"text":"unknown"},"valueQuantity":')
    if malformation == "component_obs6":
        obj = json.loads(raw)
        obj["entry"][0]["resource"]["component"] = [{"code": {"text": "part"}, "valueString": "17",
                                                  "dataAbsentReason": {"text": "unknown"}}]
        raw = json.dumps(obj).encode()
    if malformation == "status": raw = raw.replace(b'"status":"final"', b'"status":"measured"')
    if malformation == "unknown_value_choice": raw = raw.replace(b'"valueQuantity":', b'"valueAttachment":')
    if malformation == "lone_surrogate":
        raw = raw.replace(b'"resourceType":"Observation",', b'"resourceType":"Observation","id":"\\ud800",')
    path = tmp_path / "source.json"
    path.write_bytes(raw)
    out = tmp_path / "never-created"
    with pytest.raises(ValueError): module().snapshot(path, out, synthetic=True)
    assert not out.exists()


def test_snapshot_requires_synthetic_assertion_and_never_overwrites_existing_output(tmp_path):
    p = tmp_path / "input.json"
    p.write_bytes(bundle_bytes())
    out = tmp_path / "output"
    with pytest.raises(ValueError, match="synthetic"): module().snapshot(p, out, synthetic=False)
    assert not out.exists()
    out.mkdir()
    sentinel = out / "user-file"
    sentinel.write_text("keep")
    with pytest.raises(FileExistsError): module().snapshot(p, out, synthetic=True)
    assert sentinel.read_text() == "keep" and len(list(out.iterdir())) == 1


def test_readonly_audit_ignores_saved_summary_and_detects_raw_metadata_and_archive_tampering(tmp_path):
    p = tmp_path / "input.json"
    p.write_bytes(bundle_bytes())
    out = tmp_path / "out"
    module().snapshot(p, out, synthetic=True)
    original = {f.name: f.read_bytes() for f in out.iterdir()}
    assert module().audit_existing(p, out)["audit_ok"] is True
    assert original == {f.name: f.read_bytes() for f in out.iterdir()}
    (out / "snapshot.json").write_text('{"audit_ok":true}')
    records = [json.loads(x) for x in (out / "observations.jsonl").read_text().splitlines()]
    records[0]["version_id"] = "fake"
    (out / "observations.jsonl").write_text("\n".join(json.dumps(x) for x in records))
    assert module().audit_existing(p, out)["audit_ok"] is False
    (out / "observations.jsonl").write_bytes(original["observations.jsonl"])
    (out / "source.fhir.json").write_bytes(bundle_bytes().replace(b'20.00', b'20.01'))
    assert module().audit_existing(p, out)["audit_ok"] is False


def test_cli_stdlib_only_and_no_assertion_no_output(tmp_path):
    p = tmp_path / "input.json"
    p.write_bytes(bundle_bytes())
    out = tmp_path / "out"
    cmd = [sys.executable, "-S", "-m", "mirobody_gen", "snapshot-fhir", str(p), "--out", str(out)]
    bad = subprocess.run(cmd, capture_output=True, text=True)
    assert bad.returncode == 2 and not out.exists()
    good = subprocess.run([*cmd, "--synthetic"], capture_output=True, text=True)
    assert good.returncode == 0, good.stderr
    assert (out / "source.fhir.json").read_bytes() == p.read_bytes()


def test_unicode_line_separator_and_crlf_source_roundtrip(tmp_path):
    p = tmp_path / "unicode.json"
    raw = bundle_bytes().replace(b'Synthetic method', 'Synthetic\u2028method'.encode()).replace(b'\n', b'\r\n')
    p.write_bytes(raw)
    out = tmp_path / "out"
    module().snapshot(p, out, synthetic=True)
    assert out.joinpath("source.fhir.json").read_bytes() == raw
    assert module().audit_existing(p, out)["audit_ok"] is True


def test_audit_rejects_boolean_in_place_of_numeric_entry_index(tmp_path):
    p = tmp_path / "input.json"
    p.write_bytes(bundle_bytes())
    out = tmp_path / "out"
    module().snapshot(p, out, synthetic=True)
    path = out / "observations.jsonl"
    records = [json.loads(x) for x in path.read_text().splitlines()]
    records[0]["entry_index"] = False  # Python False == 0 is not JSON type fidelity.
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    assert module().audit_existing(p, out)["audit_ok"] is False
