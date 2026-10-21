"""Patient-record context is a source statement, not a clinical or person-matching oracle."""
from __future__ import annotations

import copy
import importlib
import json
import subprocess
import sys

import pytest

BODY = "http://hl7.org/fhir/StructureDefinition/bodySite"
BASE = "https://synthetic.test/fhir/"


def module():
    return importlib.import_module("mirobody_gen.fhir_patient_context")


def bundle():
    return {"resourceType": "Bundle", "type": "collection", "entry": [
        {"fullUrl": BASE + "Observation/o", "resource": {"resourceType": "Observation", "id": "o", "status": "final",
            "code": {"text": "synthetic"}, "subject": {"reference": "Patient/p"},
            "bodySite": {"extension": [{"url": BODY, "valueReference": {"reference": "BodyStructure/eye"}}]},
            "valueQuantity": {"value": 0.544, "unit": "logmar"}}},
        {"fullUrl": BASE + "BodyStructure/eye", "resource": {"resourceType": "BodyStructure", "id": "eye",
            "patient": {"reference": "Patient/p"}}},
        {"fullUrl": BASE + "Patient/p", "resource": {"resourceType": "Patient", "id": "p"}},
        {"fullUrl": BASE + "Patient/q", "resource": {"resourceType": "Patient", "id": "q"}}]}


def audit(obj):
    return module().audit_body_patient(json.dumps(obj).encode())


def test_same_patient_record_keeps_every_source_field_and_no_projection():
    obj = bundle()
    original = copy.deepcopy(obj)
    result = audit(obj)
    row = result["checks"][0]
    assert row["status"] == "same_patient_record" and row["same_patient_record"] is True
    assert row["observation_patient"]["target_pointer"] == row["body_patient"]["target_pointer"] == "/entry/2/resource"
    assert row["projection_eligible"] is False and result["clinical_validity"] == "not_assessed"
    assert obj == original


def test_distinct_records_despite_all_references_resolving_are_not_person_or_clinical_error_claims():
    from mirobody_gen.fhir_references import audit_references
    obj = bundle()
    obj["entry"][1]["resource"]["patient"]["reference"] = "Patient/q"
    assert audit_references(json.dumps(obj).encode())["status_counts"] == {"resolved": 3}
    row = audit(obj)["checks"][0]
    assert row["status"] == "different_patient_records" and row["same_patient_record"] is False
    assert row["clinical_error"] == "not_adjudicated"


@pytest.mark.parametrize("field", ["subject", "body_patient", "missing_patient", "identifier_only", "non_patient"])
def test_missing_unsupported_and_non_patient_links_remain_unassessable(field):
    obj = bundle()
    if field == "subject": obj["entry"][0]["resource"].pop("subject")
    if field == "body_patient": obj["entry"][1]["resource"].pop("patient")
    if field == "missing_patient": obj["entry"] = obj["entry"][:2]
    if field == "identifier_only": obj["entry"][1]["resource"]["patient"] = {"identifier": {"value": "p"}}
    if field == "non_patient": obj["entry"][2]["resource"]["resourceType"] = "Group"
    row = audit(obj)["checks"][0]
    assert row["status"] == "not_assessable" and row["same_patient_record"] is None


@pytest.mark.parametrize("context", ["focus", "modifier", "body_modifier", "patient_modifier", "inactive", "withdrawn"])
def test_context_exceptions_block_simple_projection_without_clinical_false_alarm(context):
    obj = bundle()
    obs, body = obj["entry"][0]["resource"], obj["entry"][1]["resource"]
    if context == "focus":
        body["patient"] = {"reference": "Patient/q"}
        obs["focus"] = [{"reference": "Patient/q"}]
    if context == "modifier": obs["modifierExtension"] = [{"url": "urn:synthetic:unknown", "valueBoolean": True}]
    if context == "body_modifier": body["modifierExtension"] = [{"url": "urn:synthetic:unknown", "valueBoolean": True}]
    if context == "patient_modifier": obj["entry"][2]["resource"]["modifierExtension"] = [{"url": "urn:synthetic:unknown"}]
    if context == "inactive": body["active"] = False
    if context == "withdrawn": obs["status"] = "entered-in-error"
    row = audit(obj)["checks"][0]
    assert row["status"] == ("excluded" if context == "withdrawn" else "not_assessable")
    assert row["projection_eligible"] is False and row["clinical_error"] == "not_adjudicated"


def test_explicit_patient_versions_share_logical_record_not_current_head():
    obj = bundle()
    obj["entry"][2]["resource"]["meta"] = {"versionId": "z"}
    second = copy.deepcopy(obj["entry"][2])
    second["resource"]["meta"]["versionId"] = "a"
    obj["entry"].append(second)
    obj["entry"][0]["resource"]["subject"]["reference"] = "Patient/p/_history/z"
    obj["entry"][1]["resource"]["patient"]["reference"] = "Patient/p/_history/a"
    row = audit(obj)["checks"][0]
    assert row["status"] == "same_patient_record"
    assert row["patient_version_relation"] == "different_explicit_versions"
    assert row["observation_patient"]["target_pointer"] != row["body_patient"]["target_pointer"]


def test_unversioned_patient_ambiguity_not_resolved_by_largest_version():
    obj = bundle()
    obj["entry"][2]["resource"]["meta"] = {"versionId": "z"}
    obj["entry"].append(copy.deepcopy(obj["entry"][2]))
    obj["entry"][-1]["resource"]["meta"]["versionId"] = "a"
    assert audit(obj)["checks"][0]["status"] == "not_assessable"


def test_same_resource_id_at_another_server_is_not_same_record():
    obj = bundle()
    obj["entry"][3]["fullUrl"] = "https://other.test/fhir/Patient/p"
    obj["entry"][3]["resource"]["id"] = "p"
    obj["entry"][1]["resource"]["patient"] = {"reference": obj["entry"][3]["fullUrl"]}
    assert audit(obj)["checks"][0]["status"] == "different_patient_records"


def test_contained_body_does_not_inherit_subject_but_explicit_patient_can_match():
    obj = bundle()
    obs = obj["entry"][0]["resource"]
    obs["contained"] = [{"resourceType": "BodyStructure", "id": "eye"}]
    obs["bodySite"]["extension"][0]["valueReference"]["reference"] = "#eye"
    assert audit(obj)["checks"][0]["status"] == "not_assessable"
    obs["contained"][0]["patient"] = {"reference": "Patient/p"}
    assert audit(obj)["checks"][0]["status"] == "same_patient_record"


def test_multiple_body_references_checked_separately_and_unknown_extension_not_guessed():
    obj = bundle()
    obs = obj["entry"][0]["resource"]
    obs["bodySite"]["extension"].append({"url": BODY, "valueReference": {"reference": "BodyStructure/missing"}})
    result = audit(obj)
    assert len(result["checks"]) == 2
    assert result["status_counts"] == {"same_patient_record": 1, "not_assessable": 1}
    obs["bodySite"]["extension"] = [{"url": "urn:unknown:body", "valueReference": {"reference": "BodyStructure/eye"}}]
    result = audit(obj)
    assert result["checks"] == [] and result["observations_without_body_reference"] == 1


def test_standard_library_readonly_cli_and_no_network(tmp_path, monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: pytest.fail("network called"))
    raw = json.dumps(bundle())
    assert audit(bundle())["external_requests"] == 0
    path = tmp_path / "source.json"
    path.write_text(raw)
    result = subprocess.run([sys.executable, "-S", "-m", "mirobody_gen", "snapshot-fhir", str(path), "--body-patient-audit"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status_counts"] == {"same_patient_record": 1}
    assert path.read_text() == raw and len(list(tmp_path.iterdir())) == 1


def test_body_reference_to_patient_does_not_become_a_body_structure():
    obj = bundle()
    obj["entry"][0]["resource"]["bodySite"]["extension"][0]["valueReference"]["reference"] = "Patient/p"
    row = audit(obj)["checks"][0]
    assert row["status"] == "not_assessable" and row["reasons"] == ["target_is_not_body_structure"]


def test_group_subject_is_not_forced_to_patient_identity():
    obj = bundle()
    obj["entry"][3] = {"fullUrl": BASE + "Group/g", "resource": {"resourceType": "Group", "id": "g"}}
    obj["entry"][0]["resource"]["subject"] = {"reference": "Group/g"}
    row = audit(obj)["checks"][0]
    assert row["status"] == "not_assessable" and row["reasons"] == ["both_patient_targets_required"]


def test_same_business_identifier_does_not_automatically_merge_patient_records():
    obj = bundle()
    obj["entry"][1]["resource"]["patient"] = {"reference": "Patient/q"}
    for n in [2, 3]: obj["entry"][n]["resource"]["identifier"] = [{"system": "urn:synthetic:id", "value": "same"}]
    assert audit(obj)["checks"][0]["status"] == "different_patient_records"


def test_same_contained_id_in_separate_containers_is_not_same_record():
    obj = bundle()
    obj["entry"][0]["resource"]["subject"] = {"reference": "#p"}
    obj["entry"][1]["resource"]["patient"] = {"reference": "#p"}
    for n in [0, 1]: obj["entry"][n]["resource"]["contained"] = [{"resourceType": "Patient", "id": "p"}]
    assert audit(obj)["checks"][0]["status"] == "different_patient_records"
