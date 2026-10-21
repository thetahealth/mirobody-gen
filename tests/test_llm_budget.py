"""Money limits use peak prices and conservative reservations, without API calls."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from mirobody_gen.llm import budget, client, review_client


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(budget, "now", lambda: datetime(2026, 10, 17, 16, 0, tzinfo=timezone.utc))


def test_peak_cny_reservation_and_usage_accounting(clock):
    flash = budget.quote("deepseek-flash", 2048)
    assert budget.ceiling(flash) == 2_113_536
    assert budget.ceiling(budget.quote("deepseek-v4-pro", 2048)) == 9_492_480
    assert budget.settled_cost({"prompt_tokens": 100, "completion_tokens": 20,
                                "prompt_cache_hit_tokens": 50, "prompt_cache_miss_tokens": 50}, flash) == 262
    assert budget.settled_cost({"prompt_tokens": 100, "completion_tokens": 20}, flash) == 360
    assert budget.settled_cost({"prompt_tokens": 1, "completion_tokens": 0,
                                "prompt_cache_hit_tokens": 1, "prompt_cache_miss_tokens": 0}, flash) == 1
    for usage in ({}, {"completion_tokens": 100}, {"prompt_tokens": True, "completion_tokens": 1},
                  {"prompt_tokens": -1, "completion_tokens": 1}, {"prompt_tokens": 1.0, "completion_tokens": 1},
                  {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 99},
                  {"prompt_tokens": 10, "completion_tokens": 1, "prompt_cache_hit_tokens": 99}):
        assert budget.settled_cost(usage, flash) is None


def test_shanghai_midnight_and_pending_cost_carry(clock, monkeypatch, tmp_path):
    assert budget.day(datetime(2026, 10, 17, 15, 59, tzinfo=timezone.utc)) == "2026-10-17"
    assert budget.day(datetime(2026, 10, 17, 16, 0, tzinfo=timezone.utc)) == "2026-10-18"
    reservation = review_client.reserve(tmp_path, "a", "deepseek-v4-pro", None)
    monkeypatch.setattr(budget, "now", lambda: datetime(2026, 10, 18, 16, 0, tzinfo=timezone.utc))
    with pytest.raises(review_client.ReviewError, match="CNY"):
        review_client.reserve(tmp_path, "b", "deepseek-flash", None)
    review_client._finish(tmp_path, reservation, "failed", {})
    with pytest.raises(review_client.ReviewError, match="CNY"):
        review_client.reserve(tmp_path, "b", "deepseek-flash", None)
    review_client._finish(tmp_path, reservation, "completed", {"prompt_tokens": 100, "completion_tokens": 100})
    assert review_client.reserve(tmp_path, "b", "deepseek-flash", None)


def test_concurrent_cny_reservations_stop_before_ten(clock, tmp_path):
    def attempt(i):
        try:
            return review_client.reserve(tmp_path, str(i), "deepseek-flash", None)
        except review_client.ReviewError:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        reservations = list(pool.map(attempt, range(20)))
    assert sum(r is not None for r in reservations) == 4
    with sqlite3.connect(tmp_path / "usage.sqlite3") as conn:
        assert conn.execute("SELECT sum(accounted_micro_cny) FROM requests").fetchone()[0] <= 10_000_000


def test_changing_response_cache_cannot_bypass_global_money_limit(clock, monkeypatch, tmp_path):
    monkeypatch.setattr(review_client, "LEDGER", tmp_path / "ledger")
    monkeypatch.setattr(review_client, "endpoint", lambda: ("https://api.deepseek.com/v1", "fake"))
    calls = []
    def fail(*args):
        calls.append(1)
        raise review_client.ReviewError("simulated timeout")
    monkeypatch.setattr(review_client, "_request", fail)
    for i in range(2):
        with pytest.raises(review_client.ReviewError):
            review_client.complete_json("deepseek-v4-pro", "JSON", str(i), cache_dir=tmp_path / str(i))
    assert len(calls) == 1


def test_existing_usage_is_migrated_not_reset(clock, tmp_path):
    with sqlite3.connect(tmp_path / "usage.sqlite3") as conn:
        conn.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY,utc_day TEXT,request_hash TEXT,model TEXT,status TEXT,usage_json TEXT)")
        conn.execute("INSERT INTO requests VALUES (1,'2026-10-17','old','deepseek-v4-flash','completed',?)",
                     (json.dumps({"prompt_tokens": 100, "completion_tokens": 100}),))
    conn = review_client._connection(tmp_path)
    try:
        assert conn.execute("SELECT billing_day,accounted_micro_cny,unresolved FROM requests").fetchone() == (
            "2026-10-18", 1000, 0)
    finally:
        conn.close()


def test_old_deepseek_callers_share_bounded_transport(monkeypatch):
    from mirobody_gen.harness import llm_panel
    monkeypatch.setattr(client, "endpoint", lambda: ("https://api.deepseek.com/v1", "fake"))
    monkeypatch.setattr(client, "cached", lambda *a: None)
    calls = []
    def response(*args, **kwargs):
        calls.append(kwargs)
        return {"model": "deepseek-flash", "answer": {"text": "wording"}, "cached": False,
                "time": "fixed", "request_hash": "hash", "usage": {}}
    monkeypatch.setattr(review_client, "complete_json", response)
    assert client.complete("deepseek-flash", "s", "u")["text"] == "wording"
    assert calls[-1]["json_mode"] is False
    assert llm_panel.call_model("deepseek-flash", "prompt") == {"text": "wording"}
    assert len(calls) == 2


def test_unknown_model_and_expired_prices_fail_before_paid_request(clock, monkeypatch, tmp_path):
    monkeypatch.setattr(review_client, "LEDGER", tmp_path)
    monkeypatch.setattr(review_client, "endpoint", lambda: ("https://api.deepseek.com/v1", "fake"))
    monkeypatch.setattr(review_client, "_request", lambda *a: pytest.fail("paid request made"))
    with pytest.raises(review_client.ReviewError, match="tariff"):
        review_client.complete_json("unknown", "JSON", "p", cache_dir=tmp_path)
    monkeypatch.setattr(budget, "now", lambda: datetime(2027, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(review_client.ReviewError, match="expired"):
        review_client.complete_json("deepseek-flash", "JSON", "p", cache_dir=tmp_path)
