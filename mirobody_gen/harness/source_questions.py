"""Opt-in source field questions: model selects a lookup, code owns its wording.

This grammar cannot express a diagnosis, normality, inferred side or measurement.
It does not verify the separate critic's free-text reasons or clinical judgments.
"""
from __future__ import annotations

import hashlib
import json

from ..ocular_source import validate_packet

POLICY = "ocular-source-questions-v1"
FIELDS = {
    "item_id": "项目标识", "status": "测量状态", "laterality": "侧别",
    "value": "原始数值", "unit": "原始单位", "notation": "记法", "effective_time": "观测时间",
}


def _facts(packet: dict) -> list[dict]:
    # IDs identify observations; person, source location and document date are
    # unnecessary for these field queries and never enter the outbound packet.
    return [{"id": obs["id"], "text": json.dumps({k: obs[k] for k in FIELDS if k in obs},
                                                ensure_ascii=False, sort_keys=True)}
            for obs in packet["observations"]]


def case_from_packet(packet: dict) -> dict:
    packet = validate_packet(packet)
    return {"case_id": packet["case_id"], "synthetic": True, "purpose": "realistic",
            "facts": _facts(packet), "questions": [], "question_policy": POLICY, "source_packet": packet}


def validate_source_case(case: dict) -> None:
    if case.get("question_policy") != POLICY:
        raise ValueError("Unsupported or missing source question policy")
    packet = validate_packet(case.get("source_packet"))
    if case["case_id"] != packet["case_id"] or case["facts"] != _facts(packet):
        raise ValueError("Source question facts/case identity differ from the original source packet")


def validate_queries(candidates: list, case: dict) -> None:
    validate_source_case(case)
    ids = {o["id"] for o in case["source_packet"]["observations"]}
    seen = set()
    for q in candidates:
        if not isinstance(q, dict) or set(q) != {"fact_id", "field"}:
            raise ValueError("Source query must contain only fact_id and field; no model-authored prose")
        if (not isinstance(q["fact_id"], str) or q["fact_id"] not in ids
                or not isinstance(q["field"], str) or q["field"] not in FIELDS):
            raise ValueError("Source query references an unknown observation or field")
        key = (q["fact_id"], q["field"])
        if key in seen:
            raise ValueError("Duplicate source query")
        seen.add(key)


def materialize(case: dict, query: dict) -> dict:
    validate_queries([query], case)
    obs = next(o for o in case["source_packet"]["observations"] if o["id"] == query["fact_id"])
    field = query["field"]
    return {"text": f"来源观测“{obs['id']}”记录的{FIELDS[field]}是什么？未提供时请明确说明。",
            "fact_ids": [obs["id"]], "source_query": dict(query), "question_policy": POLICY,
            "scope": "source_field_lookup_only; not a clinical interpretation",
            "source_lookup": {"field_present": field in obs, "value": obs.get(field),
                              "observation_status": obs["status"]}}


def baseline(case: dict) -> dict:
    validate_source_case(case)
    first = case["source_packet"]["observations"][0]["id"]
    return {"question_policy": POLICY,
            "source_packet_sha256": hashlib.sha256(json.dumps(case["source_packet"], sort_keys=True,
                                                               ensure_ascii=False).encode()).hexdigest(),
            "source_question_candidates": [materialize(case, {"fact_id": first, "field": field})
                                           for field in ("status", "laterality")]}
