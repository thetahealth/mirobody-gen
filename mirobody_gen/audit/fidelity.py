"""Shape fidelity report: synthetic corpus versus reference-set aggregates, item by item.

形态保真度：合成语料 vs 真实语料，逐项对账（docs/zh-CN/paper.md E1，docs/zh-CN/plan.md §5）。

    mirobody-gen audit-fidelity out/p2/files.jsonl
    mirobody-gen audit-fidelity out/p2/files.jsonl --write out/p2/FIDELITY.md

真实侧只读聚合量：`resources/*.json`（过闸后的分布）与 `resources/numbers.json`（版式指纹）。
这里**只报告，不调参**——对不上时要改的是产生分布的机制（机构数、抖动率、版式参数的来源），
不是回头去拧一个让数字好看的系数（docs/zh-CN/plan.md 旧教训第 5 条）。

有几项两边量的**不是同一个东西**，表里照实标出：

* 陷阱：真实侧是分析模型在 627 份文档上**注意到并写下来**的陷阱（自由文本聚类），
  合成侧是我们**逐条认定**的（版式与内容上确实存在的每一类）。后者必然更全，
  每份文档的陷阱数会系统性偏高——这是量法不同，不是生成器太难。
  可比的是**各类的相对频率**（排序相关），不是绝对条数。
* 行数：真实侧是分析器看到的表格行，含受检者字段行等非读数行；合成侧是印刷真值行。
* 通道类陷阱（OCR 认错字等）文本层不造，对账时两边都剔除。
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib

PACKAGE = pathlib.Path(__file__).resolve().parents[1]
RESOURCES = PACKAGE / "resources"
#: 源码检出的根目录。只有需要检出才有的东西（参考集、缓存、产物）才用它；安装后的包里没有这些。
REPO = PACKAGE.parent

from mirobody_gen.audit import fingerprint as fp  # noqa: E402


def _spec(name: str) -> dict:
    return json.loads((RESOURCES / name).read_text(encoding="utf-8"))


def js_divergence(p: dict, q: dict) -> float:
    """Jensen–Shannon 散度（以 2 为底，取值 0–1）。输入是未归一化的计数。"""
    keys = set(p) | set(q)
    sp, sq = sum(p.values()) or 1, sum(q.values()) or 1
    out = 0.0
    for k in keys:
        a, b = p.get(k, 0) / sp, q.get(k, 0) / sq
        m = (a + b) / 2
        if a:
            out += 0.5 * a * math.log2(a / m)
        if b:
            out += 0.5 * b * math.log2(b / m)
    return out


def ks_discrete(sample: list[int], reference: dict[int, int]) -> tuple[float, float]:
    """离散分布的 KS 统计量 D 与渐近 p 值（Kolmogorov 分布，保守）。"""
    n = len(sample)
    m = sum(reference.values())
    support = sorted(set(sample) | set(reference))
    counts = collections.Counter(sample)
    ca = cb = 0.0
    d = 0.0
    for x in support:
        ca += counts.get(x, 0) / n
        cb += reference.get(x, 0) / m
        d = max(d, abs(ca - cb))
    en = math.sqrt(n * m / (n + m))
    lam = (en + 0.12 + 0.11 / en) * d
    p = 2 * sum((-1) ** (k - 1) * math.exp(-2 * k * k * lam * lam) for k in range(1, 101))
    return d, max(0.0, min(1.0, p))


def spearman(xs: list[float], ys: list[float]) -> float:
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else 0.0


def pct(values: list[int], q: float) -> int:
    ordered = sorted(values)
    return ordered[min(int(len(ordered) * q), len(ordered) - 1)] if ordered else 0


def report(records: list[dict]) -> list[str]:
    layout = _spec("layout.json")
    hz = _spec("hazards.json")
    numbers = json.loads((RESOURCES / "numbers.json").read_text(encoding="utf-8"))
    n = len(records)
    out = [f"# 形态保真度（合成 {n} 份 vs 真实 {numbers['fingerprint']['primary']['documents']} 份）", ""]

    # ── 版式指纹：同一个定义 ──
    out += ["## 版式指纹", "", "| 粒度 | 真实 种数比 | 合成 种数比 | 真实 单次占文档 | 合成 单次占文档 | 真实 最大家族 | 合成 最大家族 |",
            "|---|---|---|---|---|---|---|"]
    summaries = [r["layout"] for r in records]
    for name, fn in fp.SENSITIVITY.items():
        real = numbers["fingerprint"]["sensitivity"][name]
        syn = fp.summarize(summaries, fn)
        out.append(f"| {name} | {real['ratio']:.3f} | {syn['ratio']:.3f} | "
                   f"{real['singleton_share_of_documents']:.1%} | {syn['singleton_share_of_documents']:.1%} | "
                   f"{real['largest_families']} | {syn['largest_families']} |")
    # 纵向队列的结构性重复：同一个人每年去同一家体检中心，版式本来就会重复。
    # 真实语料是一款应用的用户上传，以单次上传为主。把"同人同机构"的重复去掉再比一次，
    # 两者的差就是纵向结构贡献的那一部分，而不是版式机制本身的不足。
    first: dict[tuple, dict] = {}
    for r in records:
        first.setdefault((r["person_id"], r["family"]), r["layout"])
    dedup = fp.summarize(list(first.values()))
    real = numbers["fingerprint"]["primary"]
    out.append(f"| primary（去掉同人同机构重复，{dedup['documents']} 份） | {real['ratio']:.3f} | "
               f"{dedup['ratio']:.3f} | {real['singleton_share_of_documents']:.1%} | "
               f"{dedup['singleton_share_of_documents']:.1%} | {real['largest_families']} | "
               f"{dedup['largest_families']} |")

    # ── 陷阱：各类相对频率 ──
    classes = [c for c in hz["classes"] if c["generate"]]
    from mirobody_gen.hazards import STATUS  # 只为知道哪些是通道类
    comparable = [c for c in classes if STATUS.get(c["name"], ("?",))[0] in ("layout", "content", "injected")]
    syn_rate = collections.Counter(h["name"] for r in records for h in r["hazards"])
    xs = [c["document_rate"] for c in comparable]
    ys = [syn_rate.get(c["name"], 0) / n for c in comparable]
    rho = spearman(xs, ys)
    out += ["", "## 陷阱（只比文本层可造的类）", "",
            f"可比类 {len(comparable)} 个；各类文档率的 Spearman ρ = **{rho:.2f}**。",
            "真实侧是分析模型注意到的，合成侧是逐条认定的，绝对值不可直接比（见模块说明）。", "",
            "| 类 | 真实文档率 | 合成文档率 | 来源 |", "|---|---:|---:|---|"]
    for c in sorted(comparable, key=lambda c: -c["document_rate"]):
        out.append(f"| {c['name']} | {c['document_rate']:.1%} | {syn_rate.get(c['name'], 0) / n:.1%} | "
                   f"{STATUS[c['name']][0]} |")
    never = [c["name"] for c in comparable if not syn_rate.get(c["name"])]
    if never:
        out.append(f"\n本批语料里一次都没出现的可造类：{', '.join(never)}")

    counts = [r["hazard_count"] for r in records]
    real_hist = {int(k): v for k, v in hz["per_document_count"]["histogram"].items()}
    d, p = ks_discrete(counts, real_hist)
    out += ["", "## 每份文件的陷阱类数", "",
            f"合成 p50={pct(counts, .5)} p75={pct(counts, .75)} p95={pct(counts, .95)} max={max(counts)} · "
            f"真实 p50={hz['per_document_count']['p50']} p75={hz['per_document_count']['p75']} "
            f"p95={hz['per_document_count']['p95']} max={hz['per_document_count']['max']}",
            f"KS D={d:.3f}（p={p:.3g}）。stress 分层（超过真实 p95）{sum(r['split'] == 'stress' for r in records)} 份。"]

    # ── 行数 ──
    rows = [len(r["printed_rows"]) for r in records]
    real_rows = {int(k): v for k, v in numbers["rows_per_document"]}
    d, p = ks_discrete(rows, real_rows)
    rr = [k for k, v in numbers["rows_per_document"] for _ in range(v)]
    out += ["", "## 每份文件的行数", "",
            f"合成 p25={pct(rows, .25)} p50={pct(rows, .5)} p75={pct(rows, .75)} p95={pct(rows, .95)} max={max(rows)} · "
            f"真实 p25={pct(rr, .25)} p50={pct(rr, .5)} p75={pct(rr, .75)} p95={pct(rr, .95)} max={max(rr)}",
            f"KS D={d:.3f}（p={p:.3g}）。真实侧含 22.5% 零行文档（纯叙述报告，本层不造），"
            "且分析器对 62 份长文档做过截断——体检报告书的行数两边不可比。按类型分："]
    for kind in sorted({r["kind"] for r in records}):
        ks = [len(r["printed_rows"]) for r in records if r["kind"] == kind]
        out.append(f"- {kind}（{len(ks)} 份）：p25={pct(ks, .25)} p50={pct(ks, .5)} p75={pct(ks, .75)} "
                   f"p95={pct(ks, .95)} max={max(ks)}")

    # ── 方言与单位位置 ──
    syn_ref = collections.Counter(t for r in records for t in set(r["layout"]["reference_templates"]))
    real_ref = {x["value"]: x["documents"] for x in layout["reference_dialects"]}
    syn_flag = collections.Counter(t for r in records for t in set(r["layout"]["flag_markers"]))
    real_flag = {x["value"]: x["documents"] for x in layout["flag_markers"]}
    out += ["", "## 写法分布（按文档计，JS 散度，0 = 相同，1 = 不相交）", "",
            f"- 参考范围写法：JS = {js_divergence(syn_ref, real_ref):.3f}（合成 {len(syn_ref)} 种 / 真实 {len(real_ref)} 种）",
            f"- 异常标记写法：JS = {js_divergence(syn_flag, real_flag):.3f}（合成 {len(syn_flag)} 种 / 真实 {len(real_flag)} 种）"]

    # ── 语言与格式 ──
    langs = collections.Counter(r["language"] for r in records)
    fmts = collections.Counter(r["format"] for r in records)
    kinds = collections.Counter(r["kind"] for r in records)
    out += ["", "## 构成", "",
            f"- 主语言：{dict(langs)}（约定：中英各半，按文档主语言计）",
            f"- 格式：{dict(fmts)}（真实文本来源：OCR 377 / xlsx 157 / 文本层 pdf 86）",
            f"- 类型：{dict(kinds)}"]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("--write", default=None)
    args = ap.parse_args()
    records = [json.loads(line) for line in pathlib.Path(args.manifest).read_text(encoding="utf-8").splitlines()]
    text = "\n".join(report(records)) + "\n"
    print(text)
    if args.write:
        pathlib.Path(args.write).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
