"""Multi-model cross-annotation for the clinical judgements deterministic rules cannot express.

多模型交叉标注：确定性规则表达不了的那部分临床判断。

    mirobody-gen llm-panel out/p3/manifest.jsonl --sample 12        # 干跑，只打包不外发
    mirobody-gen llm-panel out/p3/manifest.jsonl --sample 12 \\
        --models claude-opus-5,gpt-5.4,gemini-3-pro                     # 真调用

## 这不是闸门

`audit/clinical.py` 是闸门：恒等式、生理边界、人口学、标记一致、RCV 超出率、
诊断一致——这些有确定答案，非零退出即拦下。

**这个面板不拦任何东西。** 它回答的是规则写不出来的问题："这条轨迹读起来像不像
一个真实病人"。这类判断没有确定答案，所以它的产物是**给人看的排序**，
不是通过/不通过。把它做成闸门，等于让一个说不清标准的东西决定语料能不能用。

## 为什么要多个模型，以及为什么要报一致性

MITRE 在 Synthea 的 LLM 工作里实测过（arXiv:2507.21123 表 1）：同一个模块、同一套
评分标准，三个模型跑 5 次，GPT 的标准差 18.4、Claude 1.5——**评审模型之间的差异
比被评审对象之间的差异还大**。他们据此选了方差最小的那个当评审。

我们做得更保守一点：不选一个，而是都问，然后**把分歧本身当成信号**。

* 多个模型**一致认为有问题** → 大概率真有问题，排在人工队列最前面。
* 多个模型**打分分歧很大** → 这个案例本身是模糊的，同样要人看，但原因不同。
* 一致认为没问题 → 不代表没问题，只代表这一层没看出来。**不能据此声称质量合格。**

每个模型独立会话、不共享上下文、不知道别的模型说了什么——共享了就不是交叉标注，
是接龙。

## 外发的是什么

**只有合成数据。** 送出去之前逐条断言 `synthetic: true`，有一条不是就整体拒绝，
不做"跳过那一条继续"——那正是隐私事故的典型形状。

真实语料（`corpus/` 及其派生物）在这里一个字节都不参与。这道断言是代码里的，
不是约定。
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
#: 源码检出的根目录。只有需要检出才有的东西（参考集、缓存、产物）才用它；安装后的包里没有这些。
REPO = PACKAGE.parent
CACHE = REPO / ".cache" / "llm_panel"

#: 评分表。编号是刻意的——MITRE 的做法是让评审逐条对编号需求打分并给理由，
#: 这样分数可以被追到具体哪一条，而不是一个说不清来源的总分。
#:
#: 每条都要求"给出证据指向"（哪一次就诊、哪个指标），否则模型会给出漂亮但空的评语。
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
    """一个人的全部就诊 → 一段纯文本，送给模型。"""
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
    """外发前的硬断言。有一条不是合成数据，整体拒绝。"""
    offenders = [r.get("file") for r in records if r.get("synthetic") is not True]
    if offenders:
        raise SystemExit(
            f"拒绝外发：{len(offenders)} 条记录没有 synthetic=true 标记，"
            f"例如 {offenders[:3]}。\n"
            f"这个面板只允许送合成数据。不做『跳过那几条继续』——"
            f"那正是隐私事故的典型形状。")


def call_model(model: str, prompt: str) -> dict:
    """调用一个模型。缓存按 (模型, prompt) 的哈希，重跑不花钱。

    走 OpenAI 兼容接口（`LLM_BASE_URL` + `LLM_API_KEY`），因为我们要问的三家
    都有兼容端点，省得为每家写一个客户端。没有配置就报错退出，不静默降级——
    静默降级会让"面板跑过了"和"面板根本没跑"长得一样。
    """
    key = hashlib.blake2b(f"{model}\n{prompt}".encode(), digest_size=16).hexdigest()
    cached = CACHE / f"{key}.json"
    if cached.is_file():
        return json.loads(cached.read_text(encoding="utf-8"))

    base = os.environ.get("LLM_BASE_URL")
    token = os.environ.get("LLM_API_KEY")
    if not (base and token):
        raise SystemExit("需要 LLM_BASE_URL 与 LLM_API_KEY 环境变量；"
                         "只想看送出去的内容长什么样就不要加 --models（默认干跑）。")

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
    """(均值, 标准差)。标准差就是分歧本身，它和均值一样重要。"""
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
        print(f"干跑：会送 {len(chosen)} 个人给每个模型，每人一次调用。")
        print(f"下面是第一个人的送出内容（{len(packet)} 字符），逐字就是这些：\n")
        print(packet[:2000] + ("\n…（截断）" if len(packet) > 2000 else ""))
        print(f"\n评分表 {len(RUBRIC)} 条。加 --models 才会真的调用。")
        return

    results: dict[str, dict] = {}
    for person_id in chosen:
        prompt = build_prompt(build_packet(by_person[person_id]))
        results[person_id] = {}
        for model in models:
            try:
                results[person_id][model] = call_model(model, prompt)
            except Exception as e:                       # noqa: BLE001
                print(f"  {person_id} / {model} 失败：{type(e).__name__}: {e}", file=sys.stderr)

    # 汇总：一致认为有问题的排最前，其次是分歧最大的。
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

    print(f"{len(chosen)} 个人 × {len(models)} 个模型 × {len(RUBRIC)} 条 = {len(rows)} 个评分点\n")
    print(f"一致认为有问题（均分≤0.5 且分歧小）{len(consensus_bad)} 处：")
    for r in consensus_bad[:10]:
        print(f"  {r['person_id']} {r['item']} 均分 {r['mean']}  "
              f"{list(r['reasons'].values())[0][:90]}")
    print(f"\n分歧大（标准差>0.25，说明案例本身模糊）{len(disputed)} 处：")
    for r in disputed[:10]:
        print(f"  {r['person_id']} {r['item']} 均分 {r['mean']} 标准差 {r['stdev']}")
    if rows:
        overall = statistics.mean(r["mean"] for r in rows)
        spread = statistics.mean(r["stdev"] for r in rows)
        print(f"\n总体均分 {overall:.3f}，模型间平均标准差 {spread:.3f}")
        print("注意：**总体均分高不等于质量合格**——它只说明这一层没看出问题。"
              "能拦住东西的是 audit/clinical.py。")

    out = pathlib.Path(args.out) if args.out else path.parent / "llm_panel.json"
    out.write_text(json.dumps({"models": models, "rubric": dict(RUBRIC),
                               "rows": rows, "raw": results},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写出 {out}（人工复核前端会读它）")


if __name__ == "__main__":
    main()
