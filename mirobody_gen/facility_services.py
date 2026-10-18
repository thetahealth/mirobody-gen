"""Explicit authored-synthetic service contracts; never inferred from provider names."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import MappingProxyType

from . import spec

POLICY = "facility-services-v1"
SCHEMA = "fictional-facility-services-v1"


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


class ServiceProfile:
    def __init__(self, data: dict):
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA:
            raise ValueError("Invalid facility service schema_version")
        if data.get("authority") != "authored_synthetic" or not _text(data.get("profile_id")):
            raise ValueError("Facility profile requires profile_id and authored_synthetic authority")
        required = data.get("required_by_exam_type")
        if not isinstance(required, dict) or not required or any(
                k not in {"routine", "follow-up", "clinic", "specialty"} or not _text(v)
                for k, v in required.items()):
            raise ValueError("Invalid required_by_exam_type service requests")
        catalogue = spec.fiction()["institutions"]
        self._catalogue = tuple((i["name"], i["language"], i["kind"]) for i in catalogue)
        self.catalog_sha256 = _digest(self._catalogue)
        names = {}
        for index, (name, _, _) in enumerate(self._catalogue):
            names.setdefault(name, []).append(index)
        entries = data.get("institutions")
        if not isinstance(entries, list) or not entries:
            raise ValueError("Facility profile requires explicit institution services")
        providers = {}
        for entry in entries:
            if not isinstance(entry, dict) or not _text(entry.get("name")):
                raise ValueError("Invalid institution service declaration")
            matches = names.get(entry["name"], [])
            if len(matches) != 1 or matches[0] in providers:
                raise ValueError("Unknown, ambiguous or duplicate institution in service profile")
            services = entry.get("services")
            if not isinstance(services, list) or any(not _text(s) for s in services) or len(set(services)) != len(services):
                raise ValueError("Invalid or duplicate institution service codes")
            providers[matches[0]] = frozenset(services)
        self.profile_id = data["profile_id"]
        self.profile_sha256 = _digest(data)
        self.required = MappingProxyType(dict(required))
        self.providers = MappingProxyType(providers)

    @classmethod
    def load(cls, path: str | Path) -> ServiceProfile:
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def assert_catalog(self, catalogue: list[dict]) -> None:
        if self.catalog_sha256 != _digest(tuple((i["name"], i["language"], i["kind"]) for i in catalogue)):
            raise ValueError("Facility service profile catalogue changed; reload and validate")

    def supports(self, index: int, service: str) -> bool:
        return service in self.providers.get(index, ())

    def eligible(self, kind: str, group: str, service: str) -> list[int]:
        group = spec.doc_lang(group)
        return [i for i, (_, lang, k) in enumerate(self._catalogue)
                if k == kind and ("en" if lang == "en" else "zh") == group and self.supports(i, service)]

    def evidence(self, index: int, service: str) -> dict:
        if not self.supports(index, service):
            raise ValueError("Selected institution does not declare the required service")
        return {"policy_version": POLICY, "profile_id": self.profile_id,
                "profile_sha256": self.profile_sha256, "catalog_sha256": self.catalog_sha256,
                "authority": "authored_synthetic", "required_service": service,
                "provider": self._catalogue[index][0]}
