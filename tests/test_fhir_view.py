"""The source view consumes facts; it never creates semantic clinical facts."""
from __future__ import annotations

import importlib
import json
from html.parser import HTMLParser
import subprocess
import sys

import pytest


def module():
    return importlib.import_module("mirobody_gen.fhir_view")


def source_bytes():
    return b'''{"resourceType":"Bundle","type":"collection","entry":[
    {"fullUrl":"https://synthetic.test/fhir/Observation/o","resource":{"resourceType":"Observation","id":"o","status":"final",
      "code":{"coding":[{"system":"urn:synthetic","version":"v1","code":"eye"},{"system":"urn:alternate","code":"A"}]},
      "subject":{"reference":"Patient/p"},"effectiveDateTime":"2025-03-01T18:00:00+08:00","issued":null,
      "valueQuantity":{"value":20.00,"unit":"mm[Hg]","comparator":"<"},"method":{"text":"source method"},
      "referenceRange":[{"low":{"value":10.00},"high":{"value":21.00}},{"text":"source range2"}],
      "bodySite":{"extension":[{"url":"http://hl7.org/fhir/StructureDefinition/bodySite","valueReference":{"reference":"BodyStructure/eye"}}]},
      "extension":[{"url":"urn:synthetic:unknown","valueDecimal":1.234567890123456789e-3}]}},
    {"fullUrl":"https://synthetic.test/fhir/BodyStructure/eye","resource":{"resourceType":"BodyStructure","id":"eye",
      "patient":{"reference":"Patient/p"},"locationQualifier":[{"text":"source right"}]}},
    {"fullUrl":"https://synthetic.test/fhir/Patient/p","resource":{"resourceType":"Patient","id":"p"}}
    ]}'''


class TextAndTags(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text, self.tags = [], []

    def handle_data(self, data): self.text.append(data)

    def handle_starttag(self, tag, attrs): self.tags.append((tag, dict(attrs)))


def test_source_fields_and_linked_resource_are_visible_without_lossy_packet_conversion():
    result = module().prepare(source_bytes(), [0])
    html = result["html"]
    parsed = TextAndTags()
    parsed.feed(html)
    text = "".join(parsed.text)
    for value in ['20.00', '1.234567890123456789e-3', '2025-03-01T18:00:00+08:00', 'source range2', 'source right']:
        assert value in text
    assert '"issued":null' in text and "same_patient_record" in text
    assert result["manifest"]["semantic_projection"] == "not_performed"
    assert result["manifest"]["clinical_validity"] == "not_assessed"
    assert result["manifest"]["selection"] == ["/entry/0/resource"]


def test_untrusted_narrative_is_literal_text_and_never_active_markup():
    obj = json.loads(source_bytes())
    evil = '</pre><img src="https://untrusted.example/pixel" onerror="alert(1)"><script>alert(1)</script>'
    obj["entry"][0]["resource"]["text"] = {"status": "generated", "div": evil}
    html = module().prepare(json.dumps(obj).encode(), [0])["html"]
    parsed = TextAndTags()
    parsed.feed(html)
    assert evil in "".join(parsed.text)
    assert not {"img", "script", "iframe", "object", "embed"} & {tag for tag, _ in parsed.tags}
    assert any(tag == "meta" and attrs.get("http-equiv") == "Content-Security-Policy" for tag, attrs in parsed.tags)
    assert "default-src 'none'" in html


@pytest.mark.parametrize("selection", [[], [0, 0], [True], [1], [-1], [99]])
def test_invalid_or_non_observation_selection_rejected_before_output(tmp_path, selection):
    p = tmp_path / "input.json"
    p.write_bytes(source_bytes())
    out = tmp_path / "never-created"
    with pytest.raises(ValueError): module().render(p, out, selection, synthetic=True)
    assert not out.exists()


def test_selection_cannot_omit_an_invalid_later_observation_in_the_batch(tmp_path):
    obj = json.loads(source_bytes())
    obj["entry"].append({"resource": {"resourceType": "Observation", "status": "final", "code": {"text": "bad"},
                                     "valueString": "17", "dataAbsentReason": {"text": "unknown"}}})
    p = tmp_path / "input.json"
    p.write_text(json.dumps(obj))
    out = tmp_path / "never-created"
    with pytest.raises(ValueError, match="obs-6"): module().render(p, out, [0], synthetic=True)
    assert not out.exists()


def test_not_measured_unknown_side_time_and_null_are_not_filled():
    obj = json.loads(source_bytes())
    o = obj["entry"][0]["resource"]
    o.pop("valueQuantity")
    o.pop("effectiveDateTime")
    o["status"] = "cancelled"
    o["dataAbsentReason"] = {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/data-absent-reason", "code": "not-performed"}]}
    result = module().prepare(json.dumps(obj).encode(), [0])
    record = result["manifest"]["records"][0]
    raw = json.loads(record["source_record"]["resource_json"])
    assert raw == o and "effectiveDateTime" not in raw
    assert record["source_record"]["observation_status"] == "cancelled"
    assert "laterality" not in record and result["manifest"]["semantic_projection"] == "not_performed"


def test_unresolved_patient_and_withdrawn_status_remain_explicit():
    obj = json.loads(source_bytes())
    obj["entry"].pop()
    result = module().prepare(json.dumps(obj).encode(), [0])
    assert result["manifest"]["records"][0]["patient_checks"][0]["status"] == "not_assessable"
    obj["entry"][0]["resource"]["status"] = "entered-in-error"
    result = module().prepare(json.dumps(obj).encode(), [0])
    assert result["manifest"]["records"][0]["source_record"]["clinical_use"] == "excluded_entered_in_error"


def test_no_overwrite_and_explicit_synthetic_assertion(tmp_path):
    p = tmp_path / "input.json"
    p.write_bytes(source_bytes())
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="synthetic"): module().render(p, out, [0])
    assert not out.exists()
    out.mkdir()
    sentinel = out / "user-file"
    sentinel.write_text("keep")
    with pytest.raises(FileExistsError): module().render(p, out, [0], synthetic=True)
    assert sentinel.read_text() == "keep" and len(list(out.iterdir())) == 1


def test_saved_audit_is_readonly_and_does_not_trust_updated_html_hash(tmp_path):
    import hashlib
    p = tmp_path / "input.json"
    p.write_bytes(source_bytes())
    out = tmp_path / "out"
    module().render(p, out, [0], synthetic=True)
    before = {x.name: x.read_bytes() for x in out.iterdir()}
    assert module().audit_existing(p, out, [0])["audit_ok"] is True
    assert before == {x.name: x.read_bytes() for x in out.iterdir()}
    bad = out.joinpath("view.html").read_text().replace("20.00", "20.01")
    out.joinpath("view.html").write_text(bad)
    manifest = json.loads(out.joinpath("view.json").read_text())
    manifest["html_sha256"] = hashlib.sha256(bad.encode()).hexdigest()
    out.joinpath("view.json").write_text(json.dumps(manifest))
    assert module().audit_existing(p, out, [0])["audit_ok"] is False


def test_standard_library_cli(tmp_path):
    p = tmp_path / "input.json"
    p.write_bytes(source_bytes())
    out = tmp_path / "out"
    proc = subprocess.run([sys.executable, "-S", "-m", "mirobody_gen", "render-fhir-view", str(p),
                           "--synthetic", "--entry", "0", "--out", str(out)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert out.joinpath("source.fhir.json").read_bytes() == p.read_bytes()


def test_two_versions_remain_two_explicitly_selected_records_without_head_selection():
    import copy
    obj = json.loads(source_bytes())
    obj["entry"][0]["resource"]["meta"] = {"versionId": "z"}
    second = copy.deepcopy(obj["entry"][0])
    second["resource"]["meta"]["versionId"] = "a"
    obj["entry"].append(second)
    result = module().prepare(json.dumps(obj).encode(), [3, 0])
    assert result["manifest"]["selection"] == ["/entry/3/resource", "/entry/0/resource"]
    assert [r["source_record"]["version_id"] for r in result["manifest"]["records"]] == ["a", "z"]
    assert 'data-source-pointer="/entry/3/resource"' in result["html"]


def test_saved_metadata_boolean_does_not_equal_numeric_entry_index(tmp_path):
    p = tmp_path / "input.json"
    p.write_bytes(source_bytes())
    out = tmp_path / "out"
    module().render(p, out, [0], synthetic=True)
    path = out / "view.json"
    manifest = json.loads(path.read_text())
    manifest["records"][0]["source_record"]["entry_index"] = False
    path.write_text(json.dumps(manifest))
    assert module().audit_existing(p, out, [0])["audit_ok"] is False
