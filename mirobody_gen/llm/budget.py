"""Conservative CNY accounting for the project's optional DeepSeek experiments.

Prices are official peak CNY prices per million tokens, verified 2026-10-18.
Reserve the model's entire input context plus bounded output, not an estimated
prompt token count. Discounts and credits never increase the authorized budget.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from zoneinfo import ZoneInfo

SOURCE = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"
VERIFIED = "2026-10-18"
VALID_THROUGH = "2026-10-19"
DAILY_LIMIT_MICRO_CNY = 10_000_000
MAX_CONTEXT = 1_048_576
RATES = {
    "deepseek-flash": {"hit": "0.04", "miss": "2", "output": "8"},
    "deepseek-v4-pro": {"hit": "0.30", "miss": "9", "output": "27"},
}
ALIASES = {"deepseek-v4-flash": "deepseek-flash", "deepseek-v4-flash-vision-exp": "deepseek-flash"}


def now() -> datetime:
    return datetime.now(timezone.utc)


def day(at: datetime | None = None) -> str:
    return (at or now()).astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()


def quote(model: str, max_tokens: int) -> dict:
    if day() > VALID_THROUGH:
        raise ValueError("DeepSeek price snapshot expired; verify official CNY prices before new calls")
    canonical_model = ALIASES.get(model, model)
    if canonical_model not in RATES:
        raise ValueError("Model has no verified CNY tariff; no paid request authorized")
    if type(max_tokens) is not int or not 1 <= max_tokens <= 2048:
        raise ValueError("Budgeted output must be between 1 and 2048 tokens")
    return {"model": model, "rates": RATES[canonical_model], "max_input_tokens": MAX_CONTEXT,
            "max_output_tokens": max_tokens, "verified": VERIFIED, "source": SOURCE,
            "currency": "CNY", "tariff": "peak_conservative"}


def _cost(hit: int, miss: int, output: int, rates: dict) -> int:
    # CNY / million tokens is numerically micro-CNY / token. Round upwards.
    value = hit * Decimal(rates["hit"]) + miss * Decimal(rates["miss"]) + output * Decimal(rates["output"])
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def ceiling(price: dict) -> int:
    return _cost(0, price["max_input_tokens"], price["max_output_tokens"], price["rates"])


def settled_cost(usage: dict, price: dict) -> int | None:
    """None means keep the full reservation, including failed calls with incomplete usage."""
    if not isinstance(usage, dict):
        return None
    prompt, output = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if any(type(n) is not int or n < 0 for n in (prompt, output)):
        return None
    if prompt > price["max_input_tokens"] or output > price["max_output_tokens"]:
        return None
    if "total_tokens" in usage and (type(usage["total_tokens"]) is not int or usage["total_tokens"] != prompt + output):
        return None
    hit, miss = usage.get("prompt_cache_hit_tokens"), usage.get("prompt_cache_miss_tokens")
    if hit is None and miss is None:
        hit, miss = 0, prompt
    elif any(type(n) is not int or n < 0 for n in (hit, miss)) or hit + miss != prompt:
        return None
    # reasoning_tokens is part of completion_tokens, not an additional charge.
    return _cost(hit, miss, output, price["rates"])
