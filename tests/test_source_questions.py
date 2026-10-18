"""Source lookup contracts; synthetic fixtures and stubbed transport only."""
from __future__ import annotations

import copy
import importlib
import json
import subprocess
import sys

import pytest

from mirobody_gen.harness import scenario_review as review
from mirobody_gen.llm import review_client


def source():
    return importlib.import_module("mirobody_gen.harness.source_questions")


def packet():
    return {"schema_version": "ocular-observations-v1", "synthetic": True,
            "case_id": "source-control", "person_id": "synthetic-person", "language": "en",
            "document_date": "2025-03-05", "source_system": "synthetic-source",
            "observations": [
                {"id": "left", "source_id": "Observation/left", "item_id": "iop",
                 "laterality": "left", "status": "unknown"},
                {"id": "other", "source_id": "Observation/other", "item_id": "va",
                 "laterality": "unspecified", "status": "not_measured", "unit": None},
                {"id": "right", "source_id": "Observation/right", "item_id": "iop",
                 "laterality": "right", "status": "measured", "value": "2.66", "unit": "kPa",
                 "effective_time": "2025-03-02T09:12:34+08:00"}]}


def response(case, candidates):
    return {"criteria": [{"id": c, "status": "not_assessable", "reason": "Insufficient context", "evidence": []}
                         for c in review.criteria("expert")],
            "questions": [], "candidate_questions": candidates}


def test_source_case_and_prompt_omit_unneeded_metadata_and_keep_missingness():
    p = packet()
    p["person_id"] = "PRIVATE_PERSON"
    p["source_system"] = "PRIVATE_SYSTEM"
    p["observations"][0]["source_id"] = "PRIVATE_LOCATION"
    case = source().case_from_packet(p)
    prompt = review.build_prompt(case, "expert")
    assert "PRIVATE" not in prompt and "2025-03-05" not in prompt
    parsed = json.loads(prompt)
    fields = [json.loads(f["text"]) for f in parsed["case"]["facts"]]
    assert fields[0]["status"] == "unknown" and fields[1]["status"] == "not_measured"
    assert fields[1]["laterality"] == "unspecified"
    assert parsed["question_policy"] == source().POLICY
    assert p == case["source_packet"]


@pytest.mark.parametrize("field, expected", [
    ("status", "measured"), ("laterality", "right"), ("value", "2.66"),
    ("unit", "kPa"), ("effective_time", "2025-03-02T09:12:34+08:00"), ("item_id", "iop")])
def test_lookup_preserves_exact_source_fields(field, expected):
    case = source().case_from_packet(packet())
    result = source().materialize(case, {"fact_id": "right", "field": field})
    assert result["source_lookup"]["value"] == expected
    assert result["source_lookup"]["field_present"] is True
    assert result["source_lookup"]["observation_status"] == "measured"
    assert "right" in result["text"]


def test_missing_null_unknown_and_not_measured_do_not_become_values_or_side():
    case = source().case_from_packet(packet())
    missing = source().materialize(case, {"fact_id": "left", "field": "value"})["source_lookup"]
    null = source().materialize(case, {"fact_id": "other", "field": "unit"})["source_lookup"]
    side = source().materialize(case, {"fact_id": "other", "field": "laterality"})["source_lookup"]
    assert missing == {"field_present": False, "value": None, "observation_status": "unknown"}
    assert null == {"field_present": True, "value": None, "observation_status": "not_measured"}
    assert side["value"] == "unspecified"
    assert source().materialize(case, {"fact_id": "left", "field": "effective_time"})["source_lookup"]["value"] is None


@pytest.mark.parametrize("candidate", [
    {"text": "另一眼正常，为什么需要复查？", "fact_ids": ["left"]},
    {"fact_id": "left", "field": "value", "text": "左眼已经测量了吗？"},
    {"fact_id": "left", "field": "diagnosis"},
    {"fact_id": "invented-right", "field": "status"},
    {"fact_id": "left", "field": ["status"]},
])
def test_rejects_free_text_extra_fields_unknown_fields_and_invented_records(candidate):
    case = source().case_from_packet(packet())
    with pytest.raises(ValueError, match="[Ss]ource query"):
        review.validate_answer(response(case, [candidate]), case, "expert")


def test_duplicate_selectors_rejected_and_legacy_freetext_stays_unverified():
    case = source().case_from_packet(packet())
    q = {"fact_id": "left", "field": "status"}
    with pytest.raises(ValueError, match="[Dd]uplicate"):
        review.validate_answer(response(case, [q, q]), case, "expert")
    legacy = {k: v for k, v in case.items() if k not in {"question_policy", "source_packet"}}
    a = response(legacy, [{"text": "另一眼正常，为什么需要复查？", "fact_ids": ["left"]}])
    assert review.validate_answer(a, legacy, "expert") == a


def test_offline_baseline_requires_neither_network_nor_rendering(monkeypatch):
    monkeypatch.setattr(review_client, "complete_json", lambda *a, **kw: pytest.fail("network called"))
    case = source().case_from_packet(packet())
    before = copy.deepcopy(case)
    result = review.review(case)
    assert result["llm"]["status"] == "not_requested"
    assert result["question_policy"] == source().POLICY
    assert len(result["source_question_candidates"]) == 2
    assert result["clinical_judgment"] == "not_assessed"
    assert case == before


def test_stubbed_model_only_selects_fields_and_does_not_author_the_question(monkeypatch):
    case = source().case_from_packet(packet())
    selectors = [{"fact_id": "other", "field": "value"}, {"fact_id": "left", "field": "status"}]

    def complete(*args, validator, **kwargs):
        a = response(case, selectors)
        validator(a)
        return {"answer": a, "cached": False}

    monkeypatch.setattr(review_client, "complete_json", complete)
    result = review.review(case, model="stub")
    assert result["llm"]["status"] == "evidence_validated"
    assert result["llm"]["answer"]["candidate_questions"] == selectors
    assert result["llm"]["source_question_candidates"][0]["source_lookup"]["observation_status"] == "not_measured"
    assert result["llm"]["clinical_correctness"] == "unverified"


def test_unsupported_model_candidate_fails_without_replacing_offline_candidates(monkeypatch):
    case = source().case_from_packet(packet())
    a = response(case, [{"text": "另一眼正常，为什么需要复查？", "fact_ids": ["left"]}])
    monkeypatch.setattr(review_client, "complete_json", lambda *args, **kwargs: {"answer": a})
    result = review.review(case, model="stub")
    assert result["llm"]["status"] == "failed"
    assert result["source_question_candidates"] == source().baseline(case)["source_question_candidates"]
    assert "另一眼正常" not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize("mutation", ["fact", "case_id", "policy", "missing_policy", "invalid_source"])
def test_inconsistent_local_source_metadata_rejected_before_transport(monkeypatch, mutation):
    case = source().case_from_packet(packet())
    if mutation == "fact": case["facts"][0]["text"] = "The left eye is normal."
    if mutation == "case_id": case["case_id"] = "other-case"
    if mutation == "policy": case["question_policy"] = "unknown-policy"
    if mutation == "missing_policy": case.pop("question_policy")
    if mutation == "invalid_source": case["source_packet"]["observations"][0]["value"] = "0"
    monkeypatch.setattr(review_client, "complete_json", lambda *a, **kw: pytest.fail("network called"))
    with pytest.raises(ValueError): review.review(case, model="stub")


def test_invalid_second_source_packet_rejected_before_any_output(tmp_path):
    p = packet()
    bad = copy.deepcopy(p)
    bad["case_id"] = "bad"
    bad["observations"][0]["value"] = "0"
    path = tmp_path / "cases.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in [p, bad]))
    out = tmp_path / "not-created" / "out.jsonl"
    result = subprocess.run([sys.executable, "-m", "mirobody_gen", "review-scenarios", str(path),
                             "--ocular-source", "--out", str(out)], capture_output=True, text=True)
    assert result.returncode == 2
    assert not out.parent.exists()
    assert "cannot carry" in result.stderr
