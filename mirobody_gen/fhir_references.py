"""Conservative, Bundle-local literal Reference identity audit; no network or side inference."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import re
from urllib.parse import urlsplit

from .fhir_source import DECODER

POLICY = "fhir-local-references-v1"
_TYPE = r"[A-Z][A-Za-z0-9]*"
_ID = r"[A-Za-z0-9.-]{1,64}"
_REST = re.compile(rf"(?P<base>https?://[^?#]+/)(?P<type>{_TYPE})/(?P<id>{_ID})$")
_RELATIVE = re.compile(rf"{_TYPE}/{_ID}(?:/_history/{_ID})?$")
_VERSION = re.compile(rf"(?P<url>https?://[^?#]+/{_TYPE}/{_ID})/_history/(?P<version>{_ID})$")


def _escape(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


def _target(resource: dict, pointer: str, full_url: str | None = None) -> dict:
    if not isinstance(resource.get("resourceType"), str): raise ValueError("Resource type required")
    meta = resource.get("meta", {})
    if not isinstance(meta, dict): raise ValueError("Resource meta must be an object")
    rid, version = resource.get("id"), meta.get("versionId")
    if any(v is not None and (not isinstance(v, str) or not v) for v in (rid, version)):
        raise ValueError("Resource identities must be nonblank strings")
    rest = _REST.fullmatch(full_url or "")
    conflict = bool(rest and (rest["type"] != resource["resourceType"] or (rid is not None and rest["id"] != rid)))
    return {"pointer": pointer, "resource_type": resource["resourceType"], "version": version,
            "identity_conflict": conflict,
            "clinical_use": "excluded_entered_in_error" if resource["resourceType"] == "Observation"
                            and resource.get("status") == "entered-in-error" else "not_adjudicated"}


class BundleIndex:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.bundle = DECODER.decode(payload.decode("utf-8"))
        if not isinstance(self.bundle, dict) or self.bundle.get("resourceType") != "Bundle":
            raise ValueError("A Bundle is required")
        entries = self.bundle.get("entry")
        if not isinstance(entries, list): raise ValueError("Bundle entry list required")
        self.sources, self.urls, self.contained = {}, defaultdict(list), {}
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict): raise ValueError("Invalid Bundle entry")
            resource = entry.get("resource")
            if resource is None: continue  # e.g. historical DELETE, not a usable target.
            if not isinstance(resource, dict): raise ValueError("Invalid entry resource")
            url = entry.get("fullUrl")
            if url is not None and (not isinstance(url, str) or not url): raise ValueError("Invalid fullUrl")
            pointer = f"/entry/{index}/resource"
            target = _target(resource, pointer, url)
            self.sources[index] = (resource, url, target)
            if url is not None: self.urls[url].append(target)
            contained = resource.get("contained", [])
            if not isinstance(contained, list): raise ValueError("Invalid contained list")
            self.contained[index] = defaultdict(list)
            for n, child in enumerate(contained):
                if not isinstance(child, dict): raise ValueError("Invalid contained resource")
                child_target = _target(child, f"{pointer}/contained/{n}")
                if isinstance(child.get("id"), str): self.contained[index][child["id"]].append(child_target)

    def resolve(self, reference: dict, source_entry_index: int) -> dict:
        if type(source_entry_index) is not int or source_entry_index not in self.sources:
            raise ValueError("Source entry resource required")
        literal = reference.get("reference") if isinstance(reference, dict) else None
        result = {"reference": literal if isinstance(literal, str) else None,
                  "resolution_status": "unsupported", "reason": "no_literal_reference",
                  "candidate_pointers": [], "target_pointer": None, "target_clinical_use": None}
        if not isinstance(literal, str) or not literal: return result
        _, source_url, source_target = self.sources[source_entry_index]
        if literal.startswith("#"):
            matches = [source_target] if literal == "#" else self.contained[source_entry_index].get(literal[1:], [])
            method = "contained_scope"
        else:
            if "?" in literal or "#" in literal:
                return {**result, "reason": "conditional_or_fragment_not_supported"}
            absolute = literal
            if not urlsplit(literal).scheme:
                if not _RELATIVE.fullmatch(literal): return {**result, "reason": "unsupported_relative_form"}
                rest = _REST.fullmatch(source_url or "")
                if not rest: return {**result, "reason": "no_restful_source_base"}
                # An inconsistent source fullUrl is not a trustworthy relative base.
                if source_target["identity_conflict"]: return {**result, "reason": "source_identity_conflict"}
                absolute = rest["base"] + literal
            version = _VERSION.fullmatch(absolute)
            url = version["url"] if version else absolute
            matches = self.urls.get(url, [])
            if version: matches = [m for m in matches if m["version"] == version["version"]]
            method = "explicit_version" if version else "exact_full_url"
        result["candidate_pointers"] = [m["pointer"] for m in matches]
        if not matches: return {**result, "resolution_status": "unresolved", "reason": "not_in_bundle"}
        if len(matches) > 1: return {**result, "resolution_status": "ambiguous", "reason": "multiple_local_matches"}
        target = matches[0]
        if target["identity_conflict"]:
            return {**result, "resolution_status": "identity_conflict", "reason": "full_url_resource_mismatch"}
        declared_type = reference.get("type")
        if declared_type is not None:
            if not isinstance(declared_type, str): return {**result, "reason": "unsupported_reference_type"}
            declared_type = declared_type.removeprefix("http://hl7.org/fhir/StructureDefinition/")
            if not re.fullmatch(_TYPE, declared_type): return {**result, "reason": "unsupported_reference_type"}
            if declared_type != target["resource_type"]:
                return {**result, "resolution_status": "type_mismatch", "reason": "declared_target_type_differs"}
        return {**result, "resolution_status": "resolved", "reason": method,
                "target_pointer": target["pointer"], "target_clinical_use": target["clinical_use"]}


def audit_references(payload: bytes) -> dict:
    index = BundleIndex(payload)
    links, unscanned = [], []

    def walk(node, pointer, source_index):
        if isinstance(node, dict):
            # Nested Bundle/Parameters resources establish other resolution scopes.
            if node.get("resourceType") in {"Bundle", "Parameters"}:
                unscanned.append(pointer)
                return
            if "reference" in node:
                links.append({"source_pointer": f"/entry/{source_index}/resource",
                              "reference_pointer": pointer + "/reference", **index.resolve(node, source_index)})
            for key, child in node.items(): walk(child, pointer + "/" + _escape(key), source_index)
        elif isinstance(node, list):
            for n, child in enumerate(node): walk(child, pointer + f"/{n}", source_index)

    for n, (resource, _, _) in index.sources.items(): walk(resource, f"/entry/{n}/resource", n)
    return {"policy_version": POLICY, "source_sha256": hashlib.sha256(payload).hexdigest(),
            "scope": "literal reference fields in top-level entry resources and their contained resources; "
                     "no identifier/canonical/conditional resolution",
            "links": links, "status_counts": dict(Counter(r["resolution_status"] for r in links)),
            "unscanned_resource_boundaries": unscanned, "external_requests": 0,
            "version_selection": "explicit reference version only; no current-head inference",
            "clinical_validity": "not_assessed"}
