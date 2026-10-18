"""Local identity resolution; never guess side, server or current version."""
from __future__ import annotations

import copy
import importlib
import json
import subprocess
import sys

import pytest


def module():
    return importlib.import_module("mirobody_gen.fhir_references")


def entry(url, kind="Patient", rid="p", version=None, **extra):
    resource = {"resourceType": kind, "id": rid, **extra}
    if version is not None: resource["meta"] = {"versionId": version}
    return {"fullUrl": url, "resource": resource}


def index(entries):
    return module().BundleIndex(json.dumps({"resourceType": "Bundle", "type": "collection", "entry": entries}).encode())


def test_rest_relative_uses_source_base_and_never_cross_server_basename():
    i = index([entry("https://a.test/fhir/Observation/o", "Observation", "o"),
               entry("https://a.test/fhir/Patient/p"), entry("https://b.test/fhir/Patient/p")])
    result = i.resolve({"reference": "Patient/p"}, 0)
    assert result["resolution_status"] == "resolved" and result["target_pointer"] == "/entry/1/resource"
    assert i.resolve({"reference": "https://b.test/fhir/Patient/p"}, 0)["target_pointer"] == "/entry/2/resource"
    assert i.resolve({"reference": "https://unknown.test/fhir/Patient/p"}, 0)["resolution_status"] == "unresolved"


def test_urn_exact_match_and_relative_without_rest_base_remains_unsupported():
    i = index([entry("urn:uuid:o", "Observation", "o"), entry("urn:uuid:p")])
    assert i.resolve({"reference": "urn:uuid:p"}, 0)["target_pointer"] == "/entry/1/resource"
    result = i.resolve({"reference": "Patient/p"}, 0)
    assert result["resolution_status"] == "unsupported" and result["reason"] == "no_restful_source_base"


def test_explicit_version_selects_exact_version_without_ordering():
    i = index([entry("https://a.test/fhir/Observation/o", "Observation", "o"),
               entry("https://a.test/fhir/Patient/p", version="z"),
               entry("https://a.test/fhir/Patient/p", version="a")])
    assert i.resolve({"reference": "Patient/p/_history/z"}, 0)["target_pointer"] == "/entry/1/resource"
    assert i.resolve({"reference": "https://a.test/fhir/Patient/p/_history/a"}, 0)["target_pointer"] == "/entry/2/resource"
    assert i.resolve({"reference": "Patient/p/_history/missing"}, 0)["resolution_status"] == "unresolved"
    result = i.resolve({"reference": "Patient/p"}, 0)
    assert result["resolution_status"] == "ambiguous" and result["target_pointer"] is None
    assert len(result["candidate_pointers"]) == 2


def test_duplicate_same_version_remains_ambiguous_not_first_wins():
    i = index([entry("https://a.test/Observation/o", "Observation", "o"),
               entry("https://a.test/Patient/p", version="a"), entry("https://a.test/Patient/p", version="a")])
    assert i.resolve({"reference": "Patient/p/_history/a"}, 0)["resolution_status"] == "ambiguous"


def test_contained_scoped_to_parent_and_no_global_fallback():
    i = index([entry("urn:uuid:o", "Observation", "o", contained=[{"resourceType": "BodyStructure", "id": "eye"}]),
               entry("urn:uuid:q", "Observation", "q", contained=[{"resourceType": "BodyStructure", "id": "eye"}])])
    assert i.resolve({"reference": "#eye"}, 0)["target_pointer"] == "/entry/0/resource/contained/0"
    assert i.resolve({"reference": "#eye"}, 1)["target_pointer"] == "/entry/1/resource/contained/0"
    assert i.resolve({"reference": "#missing"}, 0)["resolution_status"] == "unresolved"
    assert i.resolve({"reference": "#"}, 0)["target_pointer"] == "/entry/0/resource"


@pytest.mark.parametrize("reference", [{"identifier": {"system": "urn:local", "value": "p"}},
                                       {"reference": "Patient?identifier=p"}, {"reference": "Patient/p#fragment"}])
def test_out_of_scope_reference_modes_are_not_guessed(reference):
    i = index([entry("https://a.test/fhir/Observation/o", "Observation", "o"), entry("https://a.test/fhir/Patient/p")])
    assert i.resolve(reference, 0)["resolution_status"] == "unsupported"


def test_type_mismatch_and_entered_error_are_never_clinical_pass():
    i = index([entry("urn:uuid:o", "Observation", "o"), entry("urn:uuid:p"),
               entry("urn:uuid:bad", "Observation", "bad", status="entered-in-error")])
    assert i.resolve({"reference": "urn:uuid:p", "type": "BodyStructure"}, 0)["resolution_status"] == "type_mismatch"
    result = i.resolve({"reference": "urn:uuid:bad"}, 0)
    assert result["resolution_status"] == "resolved" and result["target_clinical_use"] == "excluded_entered_in_error"


def test_inconsistent_rest_fullurl_identity_not_silently_accepted():
    i = index([entry("https://a.test/fhir/Observation/o", "Observation", "o"),
               entry("https://a.test/fhir/Patient/p", "BodyStructure", "p")])
    assert i.resolve({"reference": "Patient/p"}, 0)["resolution_status"] == "identity_conflict"


def test_audit_walks_extension_and_contained_links_without_following_them_or_mutating_input(monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: pytest.fail("network used"))
    obj = {"resourceType": "Bundle", "type": "collection", "entry": [
        entry("urn:uuid:o", "Observation", "o", code={"text": "synthetic"}, status="final",
              bodySite={"extension": [{"url": "urn:body-site", "valueReference": {"reference": "urn:uuid:eye"}}]},
              contained=[{"resourceType": "Provenance", "id": "prov", "target": [{"reference": "#"}]}]),
        entry("urn:uuid:eye", "BodyStructure", "eye")]}
    original = copy.deepcopy(obj)
    result = module().audit_references(json.dumps(obj).encode())
    assert result["status_counts"] == {"resolved": 2}
    assert result["links"][0]["reference_pointer"] == "/entry/0/resource/bodySite/extension/0/valueReference/reference"
    assert result["links"][1]["target_pointer"] == "/entry/0/resource"
    assert result["clinical_validity"] == "not_assessed" and result["external_requests"] == 0
    assert obj == original


def test_stdlib_readonly_cli(tmp_path):
    p = tmp_path / "input.json"
    raw = json.dumps({"resourceType": "Bundle", "type": "collection", "entry": [
        entry("urn:uuid:o", "Observation", "o", code={"text": "synthetic"}, status="final", subject={"reference": "urn:uuid:p"}),
        entry("urn:uuid:p")]})
    p.write_text(raw)
    proc = subprocess.run([sys.executable, "-S", "-m", "mirobody_gen", "snapshot-fhir", str(p), "--reference-audit"],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["status_counts"] == {"resolved": 1}
    assert p.read_text() == raw and len(list(tmp_path.iterdir())) == 1


def test_nested_bundle_scopes_are_explicitly_unscanned():
    nested = {"resourceType": "Bundle", "id": "nested", "type": "collection", "entry": [
        entry("https://other.test/Observation/o", "Observation", "o", subject={"reference": "Patient/p"})]}
    obj = {"resourceType": "Bundle", "type": "collection", "entry": [{"resource": nested},
           entry("https://a.test/Patient/p")]}
    result = module().audit_references(json.dumps(obj).encode())
    assert result["links"] == []
    assert result["unscanned_resource_boundaries"] == ["/entry/0/resource"]


def test_boolean_source_index_rejected_and_literal_lexemes_not_normalized():
    i = index([entry("urn:uuid:o", "Observation", "o"), entry("urn:uuid:p")])
    with pytest.raises(ValueError): i.resolve({"reference": "urn:uuid:p"}, False)
    assert i.resolve({"reference": "urn:uuid:P"}, 0)["resolution_status"] == "unresolved"


def test_reference_pointer_escapes_json_pointer_keys():
    obj = {"resourceType": "Bundle", "type": "collection", "entry": [
        entry("urn:uuid:o", "Observation", "o", **{"synthetic/key~": {"reference": "urn:uuid:p"}}),
        entry("urn:uuid:p")]}
    result = module().audit_references(json.dumps(obj).encode())
    assert result["links"][0]["reference_pointer"] == "/entry/0/resource/synthetic~1key~0/reference"


def test_null_and_numeric_reference_fields_are_explicitly_unsupported():
    obj = {"resourceType": "Bundle", "type": "collection", "entry": [
        entry("urn:uuid:o", "Observation", "o", subject={"reference": None}, focus=[{"reference": 0.5}])]}
    result = module().audit_references(json.dumps(obj).encode())
    assert result["status_counts"] == {"unsupported": 2}
    assert all(x["target_pointer"] is None for x in result["links"])
    assert json.loads(json.dumps(result))["clinical_validity"] == "not_assessed"
