"""Compare two builds of the same seed (`mirobody-gen compare A B`): what changed and what must not.

    mirobody-gen compare out/p3 out/p3_llm            # e.g. without and with the paraphrase resource

Reports, per build: wording diversity of the narrative layer (distinct sentences, type-token ratio,
distinct-2 over characters or words) for findings, advice, department notes, chief complaints and
diary entries; and, across the pair: whether the truth layer (manifest rows, semantic readings,
printed rows) is byte-for-byte the same, which is the invariant a surface-only change must keep.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

CJK = re.compile(r"[一-鿿]")


def _load(path: pathlib.Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.is_file() else []


def _tokens(text: str) -> list[str]:
    return list(re.sub(r"\s+", "", text)) if CJK.search(text) else re.findall(r"[A-Za-z0-9%°/.-]+", text.lower())


def _diversity(texts: list[str]) -> dict:
    if not texts:
        return {"n": 0}
    toks = [t for x in texts for t in _tokens(x)]
    bigrams = [tuple(toks[i:i + 2]) for i in range(len(toks) - 1)]
    return {"n": len(texts), "distinct": len(set(texts)), "distinct_ratio": round(len(set(texts)) / len(texts), 3),
            "ttr": round(len(set(toks)) / max(1, len(toks)), 3),
            "distinct2": round(len(set(bigrams)) / max(1, len(bigrams)), 3)}


def narrative_layers(out: pathlib.Path) -> dict[str, list[str]]:
    files = _load(out / "files.jsonl")
    layers: dict[str, list[str]] = collections.defaultdict(list)
    for f in files:
        for b in f.get("blocks") or []:
            if b["kind"] == "narrative":
                # items are impressions (a coded surface, never paraphrased); the descriptions are printed strings
                layers["impression"] += [str(i.get("value", "")) for i in b.get("items") or []]
                layers["narrative_text"] += [t for t in b.get("printed") or [] if len(t) > 20]
            elif b["kind"] == "kv":
                layers["department_note"] += [str(i.get("value", "")) for i in b.get("items") or [] if i.get("finding")]
        for s in f.get("summary") or []:
            if s["kind"] == "advice":
                layers["advice"].append(s["text"])
        for c in f.get("complaints") or []:
            layers["chief_complaint"].append(c["text"])
    for j in _load(out / "journal.jsonl"):
        layers["diary"].append(j["text"])
    return layers


def truth_signature(out: pathlib.Path) -> dict[str, str]:
    import hashlib

    sig: dict[str, str] = {}
    m = out / "manifest.jsonl"
    if m.is_file():
        sig["manifest"] = hashlib.sha256(m.read_bytes()).hexdigest()[:16]
    files = _load(out / "files.jsonl")
    h_read, h_rows = hashlib.sha256(), hashlib.sha256()
    for f in sorted(files, key=lambda x: x["doc_id"]):
        h_read.update(json.dumps([(r["key"], r["canonical_value"], r["value_text"], r["status"], r["observed"]) for r in f.get("readings", [])],
                                 ensure_ascii=False).encode())
        h_rows.update(json.dumps([(r["item_name"], r["item_value"], r["item_unit"], r["item_range"], r["is_abnormal"]) for r in f.get("printed_rows", [])],
                                 ensure_ascii=False).encode())
    sig["readings"] = h_read.hexdigest()[:16]
    sig["printed_rows"] = h_rows.hexdigest()[:16]
    sig["files"] = str(len(files))
    return sig


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare two builds of the same seed")
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--json", default="", help="write the report as JSON to this path")
    args = ap.parse_args()
    a, b = pathlib.Path(args.a), pathlib.Path(args.b)
    report = {"a": str(a), "b": str(b), "truth": {"a": truth_signature(a), "b": truth_signature(b)}, "diversity": {}}
    la, lb = narrative_layers(a), narrative_layers(b)
    for layer in sorted(set(la) | set(lb)):
        da, db = _diversity(la.get(layer, [])), _diversity(lb.get(layer, []))
        if db.get("n"):
            db["novel_vs_a"] = round(len(set(lb[layer]) - set(la.get(layer, []))) / max(1, len(set(lb[layer]))), 3)
        report["diversity"][layer] = {"a": da, "b": db}
    same = all(report["truth"]["a"].get(k) == report["truth"]["b"].get(k) for k in ("manifest", "readings", "printed_rows"))
    report["truth"]["identical"] = same

    print(f"truth layer identical: {same}  {report['truth']['a']}  vs  {report['truth']['b']}")
    print(f"{'layer':<20} {'n':>6} {'distinct A':>11} {'distinct B':>11} {'ratio A':>8} {'ratio B':>8} {'ttr A':>7} {'ttr B':>7} {'d2 A':>7} {'d2 B':>7} {'novel B':>8}")
    for layer, d in report["diversity"].items():
        x, y = d["a"], d["b"]
        if not x.get("n") and not y.get("n"):
            continue
        print(f"{layer:<20} {x.get('n', 0):>6} {x.get('distinct', 0):>11} {y.get('distinct', 0):>11} "
              f"{x.get('distinct_ratio', 0):>8} {y.get('distinct_ratio', 0):>8} {x.get('ttr', 0):>7} {y.get('ttr', 0):>7} "
              f"{x.get('distinct2', 0):>7} {y.get('distinct2', 0):>7} {y.get('novel_vs_a', 0):>8}")
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    sys.exit(0 if same else 1)


if __name__ == "__main__":
    main()
