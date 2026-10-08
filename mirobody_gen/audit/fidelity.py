"""Shape fidelity report: synthetic corpus versus reference-set aggregates, item by item
(docs/zh-CN/paper.md E1, docs/zh-CN/plan.md section 5).

    mirobody-gen audit-fidelity out/p2/files.jsonl
    mirobody-gen audit-fidelity out/p2/files.jsonl --write out/p2/FIDELITY.md

The reference side is read only as aggregates: `resources/*.json` (post-gate distributions) and
`resources/numbers.json` (layout fingerprint). This module **only reports, it never tunes** -- a
mismatch means fixing the mechanism that produces the distribution (institution count, jitter rate,
where layout parameters come from), not dialing a coefficient until the number looks right
(docs/zh-CN/plan.md, old lesson #5).

A few rows compare quantities that **aren't quite the same thing**, flagged as such in the table:

* Hazards: the reference side is what the analysis model **noticed and wrote down** across 627
  documents (free-text clustering); the synthetic side is what we **determined exhaustively** (every
  class that is actually present in the layout or content). The latter is necessarily more complete, so
  the per-document hazard count is systematically higher on the synthetic side -- that's a difference in
  measurement method, not the generator overdoing it. What's comparable is the **relative frequency of
  each class** (rank correlation), not the absolute counts.
* Row counts: the reference side counts table rows as seen by the analyzer, including non-reading rows
  such as subject-info fields; the synthetic side counts printed truth rows.
* Channel-layer hazards (e.g. OCR misreads) aren't produced at the text layer, and are excluded from
  both sides of the comparison.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib

PACKAGE = pathlib.Path(__file__).resolve().parents[1]
RESOURCES = PACKAGE / "resources"
#: Root of the source checkout. Used only for things that exist only in a checkout (reference set,
#: cache, build output) -- an installed package has none of these.
REPO = PACKAGE.parent

from mirobody_gen.audit import fingerprint as fp  # noqa: E402


def _spec(name: str) -> dict:
    return json.loads((RESOURCES / name).read_text(encoding="utf-8"))


def js_divergence(p: dict, q: dict) -> float:
    """Jensen-Shannon divergence (base 2, range 0-1). Input is unnormalized counts."""
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
    """KS statistic D and asymptotic p-value for a discrete distribution (Kolmogorov distribution,
    conservative)."""
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
    out = [f"# Shape fidelity (synthetic {n} documents vs reference "
           f"{numbers['fingerprint']['primary']['documents']} documents)", ""]

    # ── Layout fingerprint: same definition on both sides ──
    out += ["## Layout fingerprint", "",
            "| granularity | real ratio | synthetic ratio | real singleton share | "
            "synthetic singleton share | real largest family | synthetic largest family |",
            "|---|---|---|---|---|---|---|"]
    summaries = [r["layout"] for r in records]
    for name, fn in fp.SENSITIVITY.items():
        real = numbers["fingerprint"]["sensitivity"][name]
        syn = fp.summarize(summaries, fn)
        out.append(f"| {name} | {real['ratio']:.3f} | {syn['ratio']:.3f} | "
                   f"{real['singleton_share_of_documents']:.1%} | {syn['singleton_share_of_documents']:.1%} | "
                   f"{real['largest_families']} | {syn['largest_families']} |")
    # A longitudinal cohort has structural repetition: the same person visits the same screening center
    # every year, so layouts repeat by construction. The reference corpus is mostly single uploads from
    # an app's users. Comparing again after deduplicating "same person, same institution" isolates how
    # much of the gap is contributed by longitudinal structure rather than a shortfall in the layout
    # mechanism itself.
    first: dict[tuple, dict] = {}
    for r in records:
        first.setdefault((r["person_id"], r["family"]), r["layout"])
    dedup = fp.summarize(list(first.values()))
    real = numbers["fingerprint"]["primary"]
    out.append(f"| primary (same person/institution dedup'd, {dedup['documents']} docs) | "
               f"{real['ratio']:.3f} | {dedup['ratio']:.3f} | {real['singleton_share_of_documents']:.1%} | "
               f"{dedup['singleton_share_of_documents']:.1%} | {real['largest_families']} | "
               f"{dedup['largest_families']} |")

    # ── Hazards: relative frequency by class ──
    classes = [c for c in hz["classes"] if c["generate"]]
    from mirobody_gen.hazards import STATUS  # only to tell which classes are channel-layer

    comparable = [c for c in classes if STATUS.get(c["name"], ("?",))[0] in ("layout", "content", "injected")]
    syn_rate = collections.Counter(h["name"] for r in records for h in r["hazards"])
    xs = [c["document_rate"] for c in comparable]
    ys = [syn_rate.get(c["name"], 0) / n for c in comparable]
    rho = spearman(xs, ys)
    out += ["", "## Hazards (classes the text layer can produce only)", "",
            f"{len(comparable)} comparable classes; Spearman rho of per-class document rate = **{rho:.2f}**.",
            "The reference side is what the analysis model noticed; the synthetic side is determined "
            "exhaustively. Absolute values aren't directly comparable (see the module docstring).", "",
            "| class | real document rate | synthetic document rate | layer |", "|---|---:|---:|---|"]
    for c in sorted(comparable, key=lambda c: -c["document_rate"]):
        out.append(f"| {c['name']} | {c['document_rate']:.1%} | {syn_rate.get(c['name'], 0) / n:.1%} | "
                   f"{STATUS[c['name']][0]} |")
    never = [c["name"] for c in comparable if not syn_rate.get(c["name"])]
    if never:
        out.append(f"\nComparable classes that never occurred in this batch: {', '.join(never)}")

    counts = [r["hazard_count"] for r in records]
    real_hist = {int(k): v for k, v in hz["per_document_count"]["histogram"].items()}
    d, p = ks_discrete(counts, real_hist)
    out += ["", "## Hazard classes per document", "",
            f"synthetic p50={pct(counts, .5)} p75={pct(counts, .75)} p95={pct(counts, .95)} max={max(counts)} · "
            f"real p50={hz['per_document_count']['p50']} p75={hz['per_document_count']['p75']} "
            f"p95={hz['per_document_count']['p95']} max={hz['per_document_count']['max']}",
            f"KS D={d:.3f} (p={p:.3g}). stress tier (above the real p95): "
            f"{sum(r['split'] == 'stress' for r in records)} docs."]

    # ── Row counts ──
    rows = [len(r["printed_rows"]) for r in records]
    real_rows = {int(k): v for k, v in numbers["rows_per_document"]}
    d, p = ks_discrete(rows, real_rows)
    rr = [k for k, v in numbers["rows_per_document"] for _ in range(v)]
    out += ["", "## Rows per document", "",
            f"synthetic p25={pct(rows, .25)} p50={pct(rows, .5)} p75={pct(rows, .75)} p95={pct(rows, .95)} "
            f"max={max(rows)} · real p25={pct(rr, .25)} p50={pct(rr, .5)} p75={pct(rr, .75)} "
            f"p95={pct(rr, .95)} max={max(rr)}",
            f"KS D={d:.3f} (p={p:.3g}). The reference side has 22.5% zero-row documents (narrative-only "
            "reports, not produced at this layer), and the analyzer truncated 62 long documents -- "
            "comprehensive health-exam report row counts aren't comparable between the two sides. By kind:"]
    for kind in sorted({r["kind"] for r in records}):
        ks = [len(r["printed_rows"]) for r in records if r["kind"] == kind]
        out.append(f"- {kind} ({len(ks)} docs): p25={pct(ks, .25)} p50={pct(ks, .5)} p75={pct(ks, .75)} "
                   f"p95={pct(ks, .95)} max={max(ks)}")

    # ── Wording and unit placement ──
    syn_ref = collections.Counter(t for r in records for t in set(r["layout"]["reference_templates"]))
    real_ref = {x["value"]: x["documents"] for x in layout["reference_dialects"]}
    syn_flag = collections.Counter(t for r in records for t in set(r["layout"]["flag_markers"]))
    real_flag = {x["value"]: x["documents"] for x in layout["flag_markers"]}
    out += ["", "## Wording distribution (per document, JS divergence: 0 = identical, 1 = disjoint)", "",
            f"- reference-range wording: JS = {js_divergence(syn_ref, real_ref):.3f} "
            f"(synthetic {len(syn_ref)} forms / real {len(real_ref)} forms)",
            f"- flag wording: JS = {js_divergence(syn_flag, real_flag):.3f} "
            f"(synthetic {len(syn_flag)} forms / real {len(real_flag)} forms)"]

    # ── Composition ──
    langs = collections.Counter(r["language"] for r in records)
    fmts = collections.Counter(r["format"] for r in records)
    kinds = collections.Counter(r["kind"] for r in records)
    out += ["", "## Composition", "",
            f"- primary language: {dict(langs)} (target: half Chinese, half English, by document)",
            f"- format: {dict(fmts)} (real-corpus text source: OCR 377 / xlsx 157 / text-layer PDF 86)",
            f"- kind: {dict(kinds)}"]
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
