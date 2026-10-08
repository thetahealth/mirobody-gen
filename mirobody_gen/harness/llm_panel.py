"""Multi-model cross-annotation for the clinical judgements deterministic rules cannot express.

    mirobody-gen llm-panel out/p3/manifest.jsonl --sample 12               # dry run, packs nothing out
    mirobody-gen llm-panel out/p3/manifest.jsonl --sample 12 \\
        --models claude-opus-5,gpt-5.4,gemini-3-pro                        # calls the models for real

## This is not a gate

`audit/clinical.py` is the gate: identities, physiological bounds, demographics, flag consistency,
RCV excess rate, diagnosis consistency — all have a definite answer, and a non-zero exit blocks the
build. This panel blocks nothing. It answers the question rules cannot: does this trajectory read
like a real patient? That judgement has no definite answer, so its output is a ranking for a human to
read, not a pass/fail. Turning it into a gate would let something with no clear standard decide
whether the corpus can be used.

## Why several models, and why report their agreement

MITRE measured this for Synthea's LLM work (arXiv:2507.21123, table 1): the same module, the same
rubric, three models run 5 times each — GPT's standard deviation was 18.4, Claude's 1.5. The spread
between reviewing models was larger than the spread between the things being reviewed. They picked
the lowest-variance model as the sole judge. We go further: ask all of them, and treat the disagreement
itself as a signal.

* Models **agree there's a problem** → probably a real one; goes first in the human queue.
* Models **disagree sharply** → the case itself is ambiguous, still needs a human, for a different
  reason.
* Models agree there's no problem → that only means this layer found nothing, not that quality is
  acceptable; it cannot be used to claim the corpus passes.

Each model runs in its own session, sharing no context and unaware of the others' answers — sharing
would make this a relay, not cross-annotation.

## What leaves the machine

Synthetic data only. Every record is asserted `synthetic: true` before anything is sent; if even one
record fails that, the whole batch is rejected — never "skip that one and keep going", which is the
shape a privacy incident takes. The real corpus (`corpus/` and anything derived from it) never
touches this path; the assertion is in code, not a convention.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import random
import statistics
import sys

PACKAGE = pathlib.Path(__file__).resolve().parents[1]
RESOURCES = PACKAGE / "resources"
#: Root of the source checkout. Only used for things that exist solely in a checkout (reference
#: sets, caches, build output); an installed package carries none of these.
REPO = PACKAGE.parent
CACHE = REPO / ".cache" / "llm_panel"

#: The rubric. Numbered items are deliberate: MITRE's approach has the reviewer score each numbered
#: requirement with a reason, so a score can be traced to a specific item rather than an opaque total.
#:
#: Each item also demands pointed evidence (which visit, which indicator); without that the model
#: tends to produce polished but empty commentary.
RUBRIC: list[tuple[str, str]] = [
    ("R1", "数值与诊断集合是否互相解释得通？有没有值达到诊断标准却没有诊断，"
           "或有诊断却完全看不出痕迹的情况？"),
    ("R2", "纵向轨迹像不像真实病程？关注两个方向：有没有无法解释的跳变，"
           "以及是不是平滑得不像真人（真实检验值是有噪声的）。"),
    ("R3", "事件与指标反应的**方向**和**时间尺度**是否合理？"
           "例如他汀降 LDL 应在 4–6 周内显现，而不是当天或两年后。"),
    ("R4", "每次就诊开的检验组合像不像真实医嘱？"
           "例如复查血脂不会顺带做全套肿瘤标志物。"),
    ("R5", "有没有人口学上说不通的地方？年龄、性别与项目、参考区间的搭配。"),
    ("R6", "整体判断：这份档案拿给一位临床医生看，他会觉得它像真实病历吗？"),
]

SCALE = ("0.00 完全不成立 / 0.25 明显有问题 / 0.50 勉强说得通 / "
         "0.75 基本合理但有小瑕疵 / 1.00 完全合理")

SYSTEM = (
    "你在审阅一份**合成的**健康档案，目的是判断它像不像真实临床记录。"
    "这些数据全部由程序生成，不涉及任何真人。"
    "请严格按给定的编号条目逐条打分，每条都要给出理由并指向具体证据"
    "（哪一次就诊、哪个指标、哪个数值）。"
    "没有把握时给中间分并说明为什么没把握；**不要为了显得有用而编造问题**，"
    "也不要因为数据看起来整洁就一律给满分。"
)


def build_packet(record_group: list[dict]) -> str:
    """One person's full visit history, rendered as plain text for the model."""
    person = record_group[0]["person"]
    lines = [
        f"# 虚拟人 {record_group[0]['person_id']}",
        f"性别 {person['sex']} · 出生年 {person['birth_year']} · 身高 {person['height_cm']} cm",
        f"诊断集合：{', '.join(c['display'] for c in person.get('conditions') or []) or '（无）'}",
        "",
    ]
    for record in sorted(record_group, key=lambda r: r["collected"]):
        events = record.get("events_since_previous") or []
        lines.append(f"## {record['collected']} · {record['exam_type']} · "
                     f"{'/'.join(record['panels'])}")
        if events:
            lines.append(f"这段时间内开始的事件：{', '.join(events)}")
        for row in record["rows"]:
            flag = {"high": "↑", "low": "↓"}.get(row["status"], "")
            lines.append(f"  {row['original_indicator']}  {row['value']} {row['unit']}"
                         f"  参考 {row['reference_range'] or '—'} {flag}")
        lines.append("")
    return "\n".join(lines)


def build_prompt(packet: str) -> str:
    items = "\n".join(f"{code}. {text}" for code, text in RUBRIC)
    return (
        f"{packet}\n\n---\n\n请逐条评分。评分标准：{SCALE}\n\n{items}\n\n"
        f"只输出 JSON，形如：\n"
        f'{{"R1": {{"score": 0.75, "reason": "……", "evidence": "2024-03-11 的 LDL 3.9"}}, …}}\n'
        f"另外加一个 \"overall_comment\" 字段，一句话。"
    )


def assert_synthetic(records: list[dict]) -> None:
    """Hard assertion before anything is sent: one non-synthetic record rejects the whole batch."""
    offenders = [r.get("file") for r in records if r.get("synthetic") is not True]
    if offenders:
        raise SystemExit(
            f"refusing to send: {len(offenders)} records lack a synthetic=true flag, "
            f"e.g. {offenders[:3]}.\n"
            f"This panel only ever sends synthetic data. There is no 'skip those and continue' — "
            f"that is exactly the shape of a privacy incident.")


def call_model(model: str, prompt: str) -> dict:
    """Call one model. Cached by a hash of (model, prompt), so a rerun costs nothing.

    Uses the OpenAI-compatible chat endpoint (`LLM_BASE_URL` + `LLM_API_KEY`) since all three
    providers we query expose one, sparing a client per vendor. Raises when unconfigured rather
    than degrading silently — silent degradation would make "the panel ran" and "the panel never
    ran" indistinguishable.
    """
    key = hashlib.blake2b(f"{model}\n{prompt}".encode(), digest_size=16).hexdigest()
    cached = CACHE / f"{key}.json"
    if cached.is_file():
        return json.loads(cached.read_text(encoding="utf-8"))

    base = os.environ.get("LLM_BASE_URL")
    token = os.environ.get("LLM_API_KEY")
    if not (base and token):
        raise SystemExit("LLM_BASE_URL and LLM_API_KEY are required; omit --models to see what "
                         "would be sent without calling anything (the default is a dry run).")

    import urllib.request

    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": prompt}],
        "temperature": 0,
    }).encode()
    request = urllib.request.Request(
        f"{base.rstrip('/')}/chat/completions", data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = json.loads(response.read())
    text = payload["choices"][0]["message"]["content"]
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    result = json.loads(text)
    CACHE.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def agreement(scores: list[float]) -> tuple[float, float]:
    """(mean, stdev). The stdev is the disagreement itself, as important as the mean."""
    if len(scores) < 2:
        return (scores[0] if scores else float("nan")), 0.0
    return statistics.mean(scores), statistics.stdev(scores)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("--sample", type=int, default=10, help="how many people to sample")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--models", default="", help="comma-separated model ids; empty means dry run, nothing is sent")
    ap.add_argument("--out", default="", help="output path (default: next to the manifest)")
    args = ap.parse_args()

    path = pathlib.Path(args.manifest)
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert_synthetic(records)

    by_person: dict[str, list[dict]] = {}
    for record in records:
        by_person.setdefault(record["person_id"], []).append(record)
    rng = random.Random(args.seed)
    chosen = rng.sample(sorted(by_person), min(args.sample, len(by_person)))

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models:
        packet = build_packet(by_person[chosen[0]])
        print(f"dry run: would send {len(chosen)} people to each model, one call per person.")
        print(f"below is exactly what would be sent for the first person ({len(packet)} characters):\n")
        print(packet[:2000] + ("\n...(truncated)" if len(packet) > 2000 else ""))
        print(f"\nrubric has {len(RUBRIC)} items. Add --models to actually call them.")
        return

    results: dict[str, dict] = {}
    for person_id in chosen:
        prompt = build_prompt(build_packet(by_person[person_id]))
        results[person_id] = {}
        for model in models:
            try:
                results[person_id][model] = call_model(model, prompt)
            except Exception as e:                       # noqa: BLE001
                print(f"  {person_id} / {model} failed: {type(e).__name__}: {e}", file=sys.stderr)

    # Summarize: consensus problems first, then the most disputed.
    rows = []
    for person_id, per_model in results.items():
        for code, _text in RUBRIC:
            scores = [m[code]["score"] for m in per_model.values()
                      if isinstance(m.get(code), dict) and "score" in m[code]]
            if not scores:
                continue
            mean, sd = agreement([float(s) for s in scores])
            rows.append({"person_id": person_id, "item": code, "mean": round(mean, 3),
                         "stdev": round(sd, 3), "n": len(scores),
                         "reasons": {k: v[code].get("reason", "")[:200]
                                     for k, v in per_model.items() if isinstance(v.get(code), dict)}})

    consensus_bad = sorted([r for r in rows if r["mean"] <= 0.5 and r["stdev"] <= 0.15],
                           key=lambda r: r["mean"])
    disputed = sorted([r for r in rows if r["stdev"] > 0.25], key=lambda r: -r["stdev"])

    print(f"{len(chosen)} people × {len(models)} models × {len(RUBRIC)} items = {len(rows)} scored points\n")
    print(f"consensus problems (mean <= 0.5, low disagreement): {len(consensus_bad)}")
    for r in consensus_bad[:10]:
        print(f"  {r['person_id']} {r['item']} mean {r['mean']}  "
              f"{list(r['reasons'].values())[0][:90]}")
    print(f"\nhigh disagreement (stdev > 0.25, the case itself is ambiguous): {len(disputed)}")
    for r in disputed[:10]:
        print(f"  {r['person_id']} {r['item']} mean {r['mean']} stdev {r['stdev']}")
    if rows:
        overall = statistics.mean(r["mean"] for r in rows)
        spread = statistics.mean(r["stdev"] for r in rows)
        print(f"\noverall mean {overall:.3f}, average inter-model stdev {spread:.3f}")
        print("note: a high overall mean does not mean the corpus passes quality — it only means "
              "this layer found nothing. audit/clinical.py is what actually gates.")

    out = pathlib.Path(args.out) if args.out else path.parent / "llm_panel.json"
    out.write_text(json.dumps({"models": models, "rubric": dict(RUBRIC),
                               "rows": rows, "raw": results},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwritten {out} (the review front end reads it)")


if __name__ == "__main__":
    main()
