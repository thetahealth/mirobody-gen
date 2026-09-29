"""Model calls with a content-addressed cache; dry runs never leave the machine.

The transport is the OpenAI-compatible chat endpoint (`LLM_BASE_URL`, `LLM_API_KEY`) that the panel
in `harness/llm_panel.py` also uses. Every call is cached under `.cache/llm/<sha256>.json` keyed by
(model, system, user) so that a rerun is free and every response can be traced to its prompt.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
from datetime import datetime, timezone

PACKAGE = pathlib.Path(__file__).resolve().parents[1]
REPO = PACKAGE.parent
CACHE = REPO / ".cache" / "llm"


def cache_key(model: str, system: str, user: str) -> str:
    return hashlib.sha256(f"{model}\n\x00{system}\n\x00{user}".encode()).hexdigest()


def cached(model: str, system: str, user: str) -> dict | None:
    path = CACHE / f"{cache_key(model, system, user)}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def complete(model: str, system: str, user: str, temperature: float = 0.7, timeout: int = 180) -> dict:
    """Return {"model", "text", "cached", "time"}; raises SystemExit when no endpoint is configured."""
    hit = cached(model, system, user)
    if hit is not None:
        return {**hit, "cached": True}
    base = os.environ.get("LLM_BASE_URL")
    token = os.environ.get("LLM_API_KEY")
    if not (base and token):
        raise SystemExit("LLM_BASE_URL and LLM_API_KEY are required to call a model; "
                         "use --dry-run to write the request bundle without sending anything.")
    import urllib.request

    body = json.dumps({"model": model, "temperature": temperature,
                       "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}).encode()
    request = urllib.request.Request(f"{base.rstrip('/')}/chat/completions", data=body,
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read())
    text = payload["choices"][0]["message"]["content"]
    result = {"model": model, "text": text, "cached": False,
              "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "prompt_sha256": cache_key(model, system, user)}
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / f"{result['prompt_sha256']}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def parse_list(text: str) -> list[str]:
    """A model answer as a list of strings: a JSON array, or one candidate per line."""
    body = text.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[1] if "\n" in body else ""
        body = body.rsplit("```", 1)[0]
    try:
        data = json.loads(body)
        if isinstance(data, list):
            return [str(x) for x in data if str(x).strip()]
        if isinstance(data, dict):
            for key in ("candidates", "paraphrases", "items"):
                if isinstance(data.get(key), list):
                    return [str(x) for x in data[key] if str(x).strip()]
    except ValueError:
        pass
    lines = []
    for line in body.splitlines():
        line = line.strip().lstrip("-*•").strip()
        line = line[2:].strip() if len(line) > 2 and line[0].isdigit() and line[1] in ".)、" else line
        if line:
            lines.append(line)
    return lines
