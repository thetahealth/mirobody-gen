"""Evidence contracts and bounded transport: tests never call a model."""

from __future__ import annotations

import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from mirobody_gen.harness import scenario_review as review
from mirobody_gen.llm import budget, client, review_client


@pytest.fixture(autouse=True)
def isolated_budget(monkeypatch, tmp_path):
    monkeypatch.setattr(review_client, "LEDGER", tmp_path)
    monkeypatch.setattr(budget, "day", lambda *a: "2026-10-18")


def example():
    return {"case_id": "synthetic-case", "synthetic": True, "purpose": "realistic",
            "facts": [{"id": "f1", "text": "Glucose 5.6, unit not provided.", "type": "measurement",
                       "value": 5.6, "unit": "", "unit_required": True}],
            "questions": [{"id": "q1", "text": "What does this result mean?"}],
            "human_feedback": "PRIVATE LABEL MUST NOT LEAVE", "reference_answer": "PRIVATE ANSWER"}


def answer():
    return {"criteria": [{"id": code, "status": "not_assessable", "reason": "Insufficient context", "evidence": []}
                         for code in review.criteria("expert")],
            "questions": [{"id": "q1", "answerability": "needs_more_information", "reason": "Missing unit",
                           "fact_ids": ["f1"]}], "candidate_questions": []}


def test_default_review_never_calls_transport_or_claims_clinical_pass(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("offline review made a model call")

    monkeypatch.setattr(review_client, "complete_json", forbidden)
    result = review.review(example())
    assert result["llm"]["status"] == "not_requested"
    assert result["clinical_judgment"] == "not_assessed"
    assert result["findings"][0]["rule"] == "measurement_unit_missing"
    stress = example() | {"purpose": "stress"}
    assert review.deterministic_review(stress)["findings"][0]["severity"] == "expected_stressor"


def test_request_does_not_contain_feedback_answers_or_local_paths():
    case = example()
    case["facts"][0]["private_path"] = "/private/annotation.xlsx"
    prompt = review.build_prompt(case, "expert")
    assert "PRIVATE" not in prompt and "/private/" not in prompt
    assert case["facts"][0]["text"] in prompt


def test_rejects_omitted_criteria_fabricated_quotes_and_answerability_without_evidence():
    good = answer()
    assert review.validate_answer(good, example(), "expert") == good
    bad = copy.deepcopy(good)
    bad["criteria"].pop()
    with pytest.raises(ValueError, match="every criterion"):
        review.validate_answer(bad, example(), "expert")
    bad = copy.deepcopy(good)
    bad["criteria"][0].update(status="issue", evidence=[{"fact_id": "f1", "quote": "Glucose 9.5"}])
    with pytest.raises(ValueError, match="quotation"):
        review.validate_answer(bad, example(), "expert")
    bad = copy.deepcopy(good)
    bad["questions"][0].update(answerability="answerable", fact_ids=[])
    with pytest.raises(ValueError, match="supporting facts"):
        review.validate_answer(bad, example(), "expert")


def test_bad_case_is_rejected_before_any_network_call(monkeypatch):
    monkeypatch.setattr(review_client, "complete_json", lambda *a, **kw: pytest.fail("network called"))
    with pytest.raises(ValueError, match="synthetic"):
        review.review(example() | {"synthetic": False}, model="test")
    for model in ("", "   "):
        with pytest.raises(ValueError, match="Model id"):
            review.review(example(), model=model)


def test_invalid_model_review_is_failed_not_a_pass(monkeypatch):
    monkeypatch.setattr(review_client, "complete_json", lambda *a, **kw: {"answer": {"criteria": []}})
    assert review.review(example(), model="test")["llm"]["status"] == "failed"


def test_deepseek_key_is_never_sent_to_generic_base_url(monkeypatch):
    for key in ("LLM_BASE_URL", "LLM_API_KEY", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(client, "_dotenv", lambda: {"LLM_BASE_URL": "https://unrelated.example", "DEEPSEEK_API_KEY": "fake"})
    assert client.endpoint() == ("https://api.deepseek.com/v1", "fake")


def test_cache_covers_sampling_parameters_and_budget_counts_only_new_calls(monkeypatch, tmp_path):
    monkeypatch.setattr(review_client, "endpoint", lambda: ("https://api.deepseek.com/v1", "fake"))
    calls = []

    def respond(path, body):
        calls.append(body)
        return {"model": "resolved-model", "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}

    monkeypatch.setattr(review_client, "_request", respond)
    kwargs = {"daily_limit": 2, "cache_dir": tmp_path}
    first = review_client.complete_json("deepseek-flash", "Return JSON", "p", **kwargs)
    second = review_client.complete_json("deepseek-flash", "Return JSON", "p", **kwargs)
    assert not first["cached"] and second["cached"] and len(calls) == 1
    review_client.complete_json("deepseek-flash", "Return JSON", "p", temperature=0.2, **kwargs)
    assert len(calls) == 2 and calls[0]["thinking"] == {"type": "disabled"}
    assert first["usage"]["total_tokens"] == 15
    with pytest.raises(review_client.ReviewError, match="limit"):
        review_client.complete_json("deepseek-flash", "Return JSON", "p", max_tokens=1024, **kwargs)


def test_failed_and_truncated_requests_consume_budget_and_are_not_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(review_client, "endpoint", lambda: ("https://api.deepseek.com/v1", "fake"))
    monkeypatch.setattr(review_client, "_request", lambda *a: {
        "choices": [{"finish_reason": "length", "message": {"content": '{"ok":true}'}}],
        "usage": {"completion_tokens": 2048}})
    with pytest.raises(review_client.ReviewError, match="finish"):
        review_client.complete_json("deepseek-flash", "JSON", "p", cache_dir=tmp_path, daily_limit=1)
    assert not list(tmp_path.glob("*.json"))
    with pytest.raises(review_client.ReviewError, match="limit"):
        review_client.complete_json("deepseek-flash", "JSON", "p", cache_dir=tmp_path, daily_limit=1)
    with sqlite3.connect(tmp_path / "usage.sqlite3") as conn:
        status, usage = conn.execute("SELECT status, usage_json FROM requests").fetchone()
    assert status == "failed" and json.loads(usage)["completion_tokens"] == 2048


def test_concurrent_reservations_cannot_exceed_daily_budget(tmp_path):
    def attempt(i):
        try:
            review_client.reserve(tmp_path, str(i), "deepseek-flash", 3)
            return True
        except review_client.ReviewError:
            return False

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert sum(pool.map(attempt, range(12))) == 3


def test_schema_rejected_response_is_not_cached_or_completed(monkeypatch, tmp_path):
    monkeypatch.setattr(review_client, "endpoint", lambda: ("https://api.deepseek.com/v1", "fake"))
    monkeypatch.setattr(review_client, "_request", lambda *a: {
        "choices": [{"finish_reason": "stop", "message": {"content": '{"criteria":[],"questions":[]}'}}]})
    with pytest.raises(review_client.ReviewError, match="Invalid review"):
        review_client.complete_json("deepseek-flash", "JSON", "p", cache_dir=tmp_path,
                                    validator=lambda a: review.validate_answer(a, example(), "expert"))
    assert len(list(tmp_path.glob("*.rejected-*.json"))) == 1
    assert not [p for p in tmp_path.glob("*.json") if ".rejected-" not in p.name]
    with sqlite3.connect(tmp_path / "usage.sqlite3") as conn:
        assert conn.execute("SELECT status FROM requests").fetchone()[0] == "failed"
