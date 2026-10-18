"""Lossless, offline FHIR Bundle Observation snapshots; no clinical adjudication.

Archive original bytes and exact resource JSON slices. Lifecycle, missingness,
versions, terminology and modifiers are retained, never mapped to local models.
This is a source seam, not a complete FHIR validator or a clinical renderer.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path

POLICY = "fhir-source-v1"
SCHEMA = "fhir-observation-source-v1"
STATUSES = {"registered", "preliminary", "final", "amended", "corrected", "cancelled", "entered-in-error", "unknown"}
VALUE_KEYS = {"value" + suffix for suffix in ("Quantity", "CodeableConcept", "String", "Boolean", "Integer",
                                             "Range", "Ratio", "SampledData", "Time", "DateTime", "Period")}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("Non-JSON numeric constant")


DECODER = json.JSONDecoder(object_pairs_hook=_unique, parse_float=Decimal, parse_constant=_constant)


def _space(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \r\n\t": index += 1
    return index


def _members(text: str, start: int):
    """Walk already-validated object members, retaining value character spans."""
    index = _space(text, start + 1)
    while text[index] != "}":
        key, index = DECODER.raw_decode(text, index)
        index = _space(text, index)
        begin = _space(text, index + 1)  # colon
        _, end = DECODER.raw_decode(text, begin)
        yield key, begin, end
        index = _space(text, end)
        if text[index] == ",": index = _space(text, index + 1)


def _elements(text: str, start: int):
    index = _space(text, start + 1)
    while text[index] != "]":
        _, end = DECODER.raw_decode(text, index)
        yield index, end
        index = _space(text, end)
        if text[index] == ",": index = _space(text, index + 1)


def _choices(part: dict) -> list[str]:
    # A value[x] spelling outside R4 (an R5 valueAttachment, a typo) would otherwise read as "no value".
    if unknown := sorted(k for k in part if k.startswith("value") and k[5:6].isupper() and k not in VALUE_KEYS):
        raise ValueError(f"Unsupported Observation value[x] choice: {', '.join(unknown)}")
    keys = sorted(set(part) & VALUE_KEYS)
    if len(keys) > 1: raise ValueError("Multiple Observation value[x] choices")
    if keys and "dataAbsentReason" in part:
        raise ValueError("obs-6: dataAbsentReason and value[x] cannot coexist")
    return keys


def plan(payload: bytes) -> dict:
    text = payload.decode("utf-8")
    bundle = DECODER.decode(text)
    if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle":
        raise ValueError("A FHIR Bundle JSON object is required")
    if not isinstance(bundle.get("type"), str) or not isinstance(bundle.get("entry"), list):
        raise ValueError("Bundle type and entry list are required")
    entries = bundle["entry"]
    if any(not isinstance(entry, dict) for entry in entries): raise ValueError("Invalid Bundle entry")
    entry_start = next(begin for key, begin, _ in _members(text, _space(text, 0)) if key == "entry")
    digest = hashlib.sha256(payload).hexdigest()
    records, identities = [], defaultdict(list)
    for index, (begin, _) in enumerate(_elements(text, entry_start)):
        resource = entries[index].get("resource")
        if resource is not None and not isinstance(resource, dict): raise ValueError("Invalid entry resource")
        if not resource or resource.get("resourceType") != "Observation": continue
        status = resource.get("status")
        if not isinstance(status, str) or status not in STATUSES: raise ValueError("Unsupported Observation status")
        if not isinstance(resource.get("code"), dict) or not resource["code"]: raise ValueError("Observation code required")
        keys = _choices(resource)
        components = resource.get("component", [])
        if not isinstance(components, list): raise ValueError("Invalid Observation components")
        for part in components:
            if not isinstance(part, dict): raise ValueError("Invalid Observation component")
            _choices(part)
        meta = resource.get("meta", {})
        if not isinstance(meta, dict): raise ValueError("Invalid resource metadata")
        rid, version = resource.get("id"), meta.get("versionId")
        for value in (rid, version):
            if value is not None and (not isinstance(value, str) or not value): raise ValueError("Invalid source identity")
        resource_begin, resource_end = next((a, b) for key, a, b in _members(text, begin) if key == "resource")
        raw = text[resource_begin:resource_end]
        records.append({"schema_version": SCHEMA, "policy_version": POLICY, "synthetic": True,
                        "source_sha256": digest, "source_pointer": f"/entry/{index}/resource",
                        "entry_index": index, "resource_id": rid, "version_id": version,
                        "observation_status": status, "value_keys": keys, "resource_json": raw,
                        "resource_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                        "completion_performed": False,
                        "clinical_use": "excluded_entered_in_error" if status == "entered-in-error" else "not_adjudicated"})
        if rid is not None: identities[rid].append(index)
    if not records: raise ValueError("Bundle has no Observation resources")
    summary = {"schema_version": SCHEMA, "policy_version": POLICY, "synthetic": True,
               "source_sha256": digest, "source_bytes": len(payload), "bundle_type": bundle["type"],
               "bundle_entries": len(entries), "observations": len(records),
               "non_observation_entries": len(entries)-len(records),
               "status_counts": dict(Counter(r["observation_status"] for r in records)),
               "repeated_resource_ids": {k: v for k, v in identities.items() if len(v) > 1},
               "version_selection": "none; preserve input order", "completion_performed": False,
               "full_fhir_validation": "not_performed", "clinical_validity": "not_assessed"}
    return {"records": records, "summary": summary}


def snapshot(input_path: str | Path, out_dir: str | Path, *, synthetic: bool = False) -> dict:
    if synthetic is not True: raise ValueError("Explicit known-synthetic assertion is required")
    payload = Path(input_path).read_bytes()
    prepared = plan(payload)  # All checks happen before output creation,
    # including encoding: a JSON escape such as "\ud800" decodes to a lone surrogate UTF-8 cannot hold.
    observations = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in prepared["records"]).encode("utf-8")
    summary = (json.dumps(prepared["summary"], ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=False)
    (root / "source.fhir.json").write_bytes(payload)
    (root / "observations.jsonl").write_bytes(observations)
    (root / "snapshot.json").write_bytes(summary)
    return prepared["summary"]


def audit_existing(input_path: str | Path, out_dir: str | Path) -> dict:
    original = Path(input_path).read_bytes()
    expected = plan(original)
    root, checks = Path(out_dir), {}
    try:
        checks["archive_bytes"] = (root / "source.fhir.json").read_bytes() == original
    except OSError:
        checks["archive_bytes"] = False
    try:
        # JSONL uses LF, not every Unicode separator recognized by splitlines().
        saved = [json.loads(line, object_pairs_hook=_unique, parse_constant=_constant)
                 for line in (root / "observations.jsonl").read_text(encoding="utf-8").split("\n")
                 if line.strip()]
        # JSON types matter: Python considers False == 0 and 0.0 == 0.
        checks["observation_records"] = (
            json.dumps(saved, sort_keys=True, ensure_ascii=False, allow_nan=False)
            == json.dumps(expected["records"], sort_keys=True, ensure_ascii=False, allow_nan=False))
    except (OSError, ValueError):
        checks["observation_records"] = False
    return {"policy_version": POLICY, "observations": len(expected["records"]), "checks": checks,
            "audit_ok": all(checks.values()), "clinical_validity": "not_assessed"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="known-synthetic FHIR Bundle JSON")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--out", help="new snapshot directory; existing directories are rejected")
    group.add_argument("--audit-existing", help="read-only fidelity audit against the original input")
    group.add_argument("--reference-audit", action="store_true",
                       help="read-only local literal-reference audit; no external fetching or side inference")
    group.add_argument("--body-patient-audit", action="store_true",
                       help="read-only Observation/BodyStructure patient-record context; no clinical projection")
    parser.add_argument("--synthetic", action="store_true", help="assert known-synthetic input; not de-identification")
    args = parser.parse_args()
    try:
        if args.body_patient_audit:
            from .fhir_patient_context import audit_body_patient

            result = audit_body_patient(Path(args.input).read_bytes())
        elif args.reference_audit:
            from .fhir_references import audit_references

            result = audit_references(Path(args.input).read_bytes())
        else:
            result = (audit_existing(args.input, args.audit_existing) if args.audit_existing
                      else snapshot(args.input, args.out, synthetic=args.synthetic))
    except (OSError, ValueError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False))
    if result.get("audit_ok") is False: raise SystemExit(1)


if __name__ == "__main__": main()
