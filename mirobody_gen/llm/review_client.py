"""Optional, bounded JSON review calls. This module is never imported by the generator.

The cache identifies the entire request (including provider and sampling settings).
SQLite reserves a request and its worst-case CNY cost before network I/O. Daily
money accounting uses Asia/Shanghai; unknown charges remain reserved across days.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Callable

from .client import REPO, _ssl_context, endpoint
from . import budget

CACHE = REPO / ".cache" / "scenario_review"
# Accounting stays here whatever cache_dir a caller passes for responses. It is per checkout, not
# per account: another clone keeps its own ledger, and deleting it loses the accounting.
LEDGER = CACHE


class ReviewError(RuntimeError):
    """An unsuccessful review is an error, never a passing quality assessment."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ReviewError("Authenticated model requests must not redirect")


def _request(path: str, body: dict | None = None, timeout: int = 90) -> dict:
    base, token = endpoint()
    if not base or not token:
        raise ReviewError("Configure DEEPSEEK_API_KEY or LLM_BASE_URL and LLM_API_KEY")
    if not base.startswith("https://"):
        raise ReviewError("Review credentials require an HTTPS endpoint")
    request = urllib.request.Request(
        f"{base.rstrip('/')}/{path}",
        data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=_ssl_context()), _NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise ReviewError(f"Model endpoint returned HTTP {exc.code}; response body omitted") from None
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise ReviewError("Model endpoint connection or JSON response failed") from None


def list_models() -> list[str]:
    """Explicit read-only discovery; model names are not assumed from old aliases."""
    payload = _request("models")
    return sorted(x["id"] for x in payload.get("data", [])
                  if isinstance(x, dict) and isinstance(x.get("id"), str))


def request_key(base: str, body: dict) -> str:
    content = json.dumps({"endpoint": base.rstrip("/"), "body": body},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode()).hexdigest()


def _connection(root: pathlib.Path) -> sqlite3.Connection:
    root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(root / "usage.sqlite3", timeout=30)
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("CREATE TABLE IF NOT EXISTS requests "
                 "(id INTEGER PRIMARY KEY, utc_day TEXT, request_hash TEXT, model TEXT, "
                 "status TEXT, usage_json TEXT)")
    columns = {r[1] for r in conn.execute("PRAGMA table_info(requests)")}
    for name, kind in {"billing_day": "TEXT", "settlement_day": "TEXT", "price_json": "TEXT",
                       "reserved_micro_cny": "INTEGER", "accounted_micro_cny": "INTEGER",
                       "unresolved": "INTEGER"}.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE requests ADD COLUMN {name} {kind}")
    # Old UTC-only rows have no trustworthy local start time. Charge them to the
    # migration day conservatively; never reset already-used money to zero.
    for row_id, model, usage_json in conn.execute(
            "SELECT id,model,usage_json FROM requests WHERE billing_day IS NULL").fetchall():
        try:
            price = budget.quote(model, 2048)
            reserve_cost = budget.ceiling(price)
            cost = budget.settled_cost(json.loads(usage_json or "{}"), price)
        except (ValueError, TypeError):
            price, reserve_cost, cost = {}, budget.DAILY_LIMIT_MICRO_CNY, None
        conn.execute("UPDATE requests SET billing_day=?,price_json=?,reserved_micro_cny=?,"
                     "accounted_micro_cny=?,unresolved=? WHERE id=?",
                     (budget.day(), json.dumps(price), reserve_cost, reserve_cost if cost is None else cost,
                      int(cost is None), row_id))
    conn.commit()
    return conn


def reserve(root: pathlib.Path, key: str, model: str, daily_limit: int | None,
            price: dict | None = None) -> int:
    if daily_limit is not None and daily_limit < 1:
        raise ReviewError("Daily request limit reached (configured limit is zero)")
    day = datetime.now(timezone.utc).date().isoformat()
    conn = _connection(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        local_day = budget.day()
        count = conn.execute("SELECT count(*) FROM requests WHERE billing_day=?", (local_day,)).fetchone()[0]
        if daily_limit is not None and count >= daily_limit:
            raise ReviewError(f"Daily request limit reached: {count}/{daily_limit}; use cached/offline reviews")
        price = price or budget.quote(model, 2048)
        cost = budget.ceiling(price)
        used = conn.execute("SELECT coalesce(sum(accounted_micro_cny),0) FROM requests "
                            "WHERE billing_day=? OR settlement_day=? OR unresolved=1",
                            (local_day, local_day)).fetchone()[0]
        if used + cost > budget.DAILY_LIMIT_MICRO_CNY:
            raise ReviewError("Daily CNY budget has insufficient unreserved funds; continue offline")
        cur = conn.execute("INSERT INTO requests (utc_day,request_hash,model,status,billing_day,"
                           "price_json,reserved_micro_cny,accounted_micro_cny,unresolved) VALUES (?,?,?,?,?,?,?,?,1)",
                           (day, key, model, "reserved", local_day, json.dumps(price), cost, cost))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _finish(root: pathlib.Path, reservation: int, status: str, usage: dict | None = None) -> None:
    conn = _connection(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        price, reserved = conn.execute("SELECT price_json,reserved_micro_cny FROM requests WHERE id=?",
                                       (reservation,)).fetchone()
        cost = budget.settled_cost(usage or {}, json.loads(price))
        conn.execute("UPDATE requests SET status=?,usage_json=?,accounted_micro_cny=?,unresolved=?,"
                     "settlement_day=? WHERE id=?",
                     (status, json.dumps(usage or {}), reserved if cost is None else cost, int(cost is None),
                      budget.day(), reservation))
        conn.commit()
    finally:
        conn.close()


def complete_json(model: str, system: str, prompt: str, *, max_tokens: int = 2048,
                  temperature: float = 0, daily_limit: int | None = None,
                  cache_dir: pathlib.Path | None = None,
                  validator: Callable[[dict], dict] | None = None, json_mode: bool = True) -> dict:
    """Call only when requested by the caller. Invalid/truncated JSON never becomes a result."""
    if not 1 <= max_tokens <= 2048:
        raise ReviewError("Review max_tokens must be between 1 and 2048")
    base, token = endpoint()
    body = {"model": model, "temperature": temperature, "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
    if not json_mode:
        body.pop("response_format")
    if urllib.parse.urlsplit(base or "").hostname == "api.deepseek.com":
        body["thinking"] = {"type": "disabled"}
    key = request_key(base or "unconfigured", body)
    root = cache_dir or CACHE
    path = root / f"{key}.json"
    if path.exists():
        hit = json.loads(path.read_text(encoding="utf-8"))
        if validator is not None:
            try:
                validator(hit["answer"])
            except (KeyError, ValueError, TypeError):
                # Keep an audit copy but permit a budgeted retry on a later invocation.
                path.replace(path.with_suffix(".rejected.json"))
                raise ReviewError("Cached review violates the current evidence contract; quarantined") from None
        return {**hit, "cached": True}
    if not base or not token:
        raise ReviewError("No model credentials configured; offline review remains available")
    if urllib.parse.urlsplit(base).hostname != "api.deepseek.com":
        raise ReviewError("No verified CNY tariff for this provider; paid review is disabled")
    try:
        price = budget.quote(model, max_tokens)
    except ValueError as exc:
        raise ReviewError(str(exc)) from None
    reservation = reserve(LEDGER, key, model, daily_limit, price)
    root.mkdir(parents=True, exist_ok=True)
    usage = {}
    answer = None
    try:
        payload = _request("chat/completions", body)
        usage = payload.get("usage") or {}
        choice = payload["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise ReviewError("Model output did not finish normally; review is not assessable")
        answer = json.loads(choice["message"]["content"]) if json_mode else {"text": choice["message"]["content"]}
        if not isinstance(answer, dict):
            raise ReviewError("Model output must be a JSON object")
        if validator is not None:
            answer = validator(answer)
        result = {"requested_model": model, "model": payload.get("model", model),
                  "request_hash": key, "cached": False,
                  "time": datetime.now(timezone.utc).isoformat(), "usage": usage, "answer": answer,
                  "contract_validated": validator is not None}
        temp = path.with_suffix(f".{reservation}.tmp")
        temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
        _finish(LEDGER, reservation, "completed", usage)
        return result
    except Exception as exc:
        _finish(LEDGER, reservation, "failed", usage)
        if answer is not None:
            # An unaccepted answer is evidence for debugging, not a successful cache entry.
            rejected = {"request_hash": key, "model": model, "usage": usage,
                        "error_type": type(exc).__name__, "error": str(exc), "unaccepted_answer": answer}
            (root / f"{key}.rejected-{reservation}.json").write_text(
                json.dumps(rejected, ensure_ascii=False, indent=2), encoding="utf-8")
        if isinstance(exc, ReviewError):
            raise
        detail = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else type(exc).__name__
        raise ReviewError(f"Invalid review response: {detail}; no assessment accepted") from None
