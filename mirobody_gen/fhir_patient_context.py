"""Narrow BodyStructure/Observation patient-record context audit; no person matching.

Identity consistency is a prerequisite for a same-patient projection, not a
clinical verdict. Focus, modifiers and inactive sites require separate context.
"""
from __future__ import annotations

from collections import Counter
import hashlib

from .fhir_references import BundleIndex

POLICY = "fhir-body-patient-v1"
BODY_SITE = "http://hl7.org/fhir/StructureDefinition/bodySite"


def _at(index: BundleIndex, pointer: str):
    node = index.bundle
    for token in pointer.split("/")[1:]:
        key = token.replace("~1", "/").replace("~0", "~")
        node = node[int(key)] if isinstance(node, list) else node[key]
    return node


def _identity(index: BundleIndex, pointer: str) -> dict:
    n = int(pointer.split("/")[2])
    url = index.sources[n][1]
    if "/contained/" not in pointer and url is not None:
        return {"kind": "full_url", "value": url}
    return {"kind": "source_scoped_pointer", "value": pointer}


def _modifier(node) -> bool:
    if isinstance(node, dict):
        return "modifierExtension" in node or any(_modifier(v) for v in node.values())
    return isinstance(node, list) and any(_modifier(v) for v in node)


def audit_body_patient(payload: bytes) -> dict:
    index = BundleIndex(payload)
    checks, scanned, without = [], 0, 0
    for n, (obs, _, _) in index.sources.items():
        if obs["resourceType"] != "Observation": continue
        scanned += 1
        site = obs.get("bodySite")
        extensions = site.get("extension", []) if isinstance(site, dict) else []
        if not isinstance(extensions, list): extensions = []
        found = False
        for j, extension in enumerate(extensions):
            if not isinstance(extension, dict) or extension.get("url") != BODY_SITE: continue
            found = True
            op = f"/entry/{n}/resource"
            body_link = index.resolve(extension.get("valueReference"), n)
            row = {"observation_pointer": op,
                   "body_reference_pointer": f"{op}/bodySite/extension/{j}/valueReference",
                   "body_reference": body_link, "status": "not_assessable", "reasons": [],
                   "same_patient_record": None, "projection_eligible": False,
                   "clinical_error": "not_adjudicated"}
            checks.append(row)
            if obs.get("status") == "entered-in-error":
                row.update(status="excluded", reasons=["observation_entered_in_error"])
                continue
            if body_link["resolution_status"] != "resolved":
                row["reasons"] = ["body_reference_not_resolved"]
                continue
            bp = body_link["target_pointer"]
            body = _at(index, bp)
            if body["resourceType"] != "BodyStructure":
                row["reasons"] = ["target_is_not_body_structure"]
                continue
            body_n = int(bp.split("/")[2])
            subject = index.resolve(obs.get("subject"), n)
            patient = index.resolve(body.get("patient"), body_n)
            row.update(observation_patient=subject, body_patient=patient)
            blockers = []
            if "focus" in obs: blockers.append("focus_context_not_interpreted")
            if _modifier(obs) or _modifier(body): blockers.append("modifier_context_not_interpreted")
            if "active" in body and body["active"] is not True: blockers.append("body_structure_not_explicitly_active")
            if blockers:
                row["reasons"] = blockers
                continue
            if subject["resolution_status"] != "resolved" or patient["resolution_status"] != "resolved":
                row["reasons"] = ["patient_reference_not_resolved"]
                continue
            sp, pp = subject["target_pointer"], patient["target_pointer"]
            sr, pr = _at(index, sp), _at(index, pp)
            if sr["resourceType"] != "Patient" or pr["resourceType"] != "Patient":
                row["reasons"] = ["both_patient_targets_required"]
                continue
            if _modifier(sr) or _modifier(pr):
                row["reasons"] = ["patient_modifier_context_not_interpreted"]
                continue
            si, pi = _identity(index, sp), _identity(index, pp)
            same = si == pi
            sv, pv = sr.get("meta", {}).get("versionId"), pr.get("meta", {}).get("versionId")
            row.update(status="same_patient_record" if same else "different_patient_records",
                       same_patient_record=same, observation_patient_identity=si, body_patient_identity=pi,
                       patient_version_relation=("not_provided" if sv is None or pv is None else
                                                 "same_explicit_version" if sv == pv else "different_explicit_versions"),
                       reasons=["same_declared_record_identity" if same else "distinct_declared_record_identities"])
        if not found: without += 1
    return {"policy_version": POLICY, "source_sha256": hashlib.sha256(payload).hexdigest(),
            "scope": "top-level Observation root bodySite canonical BodyStructure-reference extension; "
                     "declared patient-record identity only, not biological person matching or clinical validation",
            "observations_scanned": scanned, "observations_without_body_reference": without,
            "checks": checks, "status_counts": dict(Counter(r["status"] for r in checks)),
            "external_requests": 0, "projection_performed": False, "clinical_validity": "not_assessed"}
