"""Extraction scoring: MedRepBench official and corrected metrics, hazard attribution, pair deltas.

    mirobody-gen score out/p3/files.jsonl predictions.jsonl
    mirobody-gen score out/p3/pairs.jsonl predictions.jsonl --pairs
    mirobody-gen score out/p3/files.jsonl predictions.jsonl --json report.json

Predictions use MedRepBench's own format: each line is `{"image": <relative path>, "items": [five
fields...]}`, so one prediction file works both with MedRepBench's official script (paired with labels
exported by `mirobody-gen medrep-view`) and with this module. Each item may carry three extra fields,
`loinc / value_num / unit_ucum` (the terminology layer's output after extraction); when present, the
semantic layer is scored too.

## Why score anything beyond the official metric

Reading MedRepBench's `scripts/evaluate_objective.py` turns up four gaps (see docs/handoff evaluation
plan §5):

1. A name mismatch voids the whole row: matching is by exact name, and a miss fails the value, unit,
   range and flag together even when those are right;
2. Predictions are truncated to the ground-truth row count, in order: one extra row ahead of a true
   reading (e.g. a subject field) pushes the last real reading out, so an over-extraction gets scored
   as a miss, and which row eats the penalty depends on order;
3. Recall only, no precision: over-extracted rows are penalized only indirectly, through truncation;
4. "undeterminable" and "wrong" score the same.

So this module reports, alongside the official metric:

* `V0`: a row-for-row reproduction of the official metric (`tests/test_score.py` asserts field-for-
  field agreement with the official script when it is available);
* `aligned`: optimal one-to-one row alignment (the Hungarian algorithm, on name similarity plus
  whether the value matches) in place of truncation by order, reporting row recall, row precision,
  and field accuracy computed only over aligned rows — separating "was this row found" from "was it
  read correctly". Table-recognition work does the same: GriTS [smock2023grits] finds the most
  similar substructure between two tables before comparing cells, TEDS [zhong2020image] compares tree
  edit distance; neither truncates by order;
* mandatory abstention: a row whose name is printed but whose value is not (e.g. "not performed")
  counts a hallucination if the prediction supplies a value anyway;
* distractor rows: subject fields, an "abnormal result count", a stray date inside a table — extracting
  any of these is a false positive;
* hazard attribution: each row carries the name of the hazard planted on it, and recall and value
  accuracy are tallied per hazard;
* paired contrasts (`--pairs`): two files with identical content but one hazard's difference, whose
  score delta is that hazard's measured effect.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re

FIELDS = ["item_name", "item_value", "item_unit", "item_range", "is_abnormal"]


# ── Official metric (reimplemented from the public MedRepBench script's semantics; no code copied) ──
def norm_text(value) -> str:
    value = "" if value is None else str(value)
    value = value.strip().lower().replace(" ", "")
    for a, b in (("：", ":"), ("－", "-"), ("—", "-"), ("–", "-"), ("~", "-"), ("～", "-")):
        value = value.replace(a, b)
    return value


def norm_number(value) -> str:
    return re.sub(r"(?<=\d)\.0+(?=\D|$)", "", norm_text(value))


def field_equal(field: str, pred, ref) -> bool:
    if field in ("item_value", "item_range"):
        return norm_number(pred) == norm_number(ref)
    return norm_text(pred) == norm_text(ref)


def official(ref_items: list[dict], pred_items: list[dict], truncate: bool = True) -> dict[str, int]:
    """Per-document official-metric counts: rows correct per field (denominator is the truth row count)."""
    preds = pred_items[:len(ref_items)] if truncate else list(pred_items)
    used: set[int] = set()
    correct = dict.fromkeys(FIELDS, 0)
    for ref in ref_items:
        name = norm_text(ref.get("item_name"))
        for j, pred in enumerate(preds):
            if j not in used and norm_text(pred.get("item_name")) == name:
                used.add(j)
                correct["item_name"] += 1
                for field in FIELDS[1:]:
                    correct[field] += field_equal(field, pred.get(field), ref.get(field))
                break
    return correct


# ── Row alignment ──────────────────────────────────────────────────
_PAREN = re.compile(r"[\(（][^\)）]*[\)）]")


def relaxed(name: str) -> str:
    return norm_text(_PAREN.sub("", name or "")).replace("\n", "")


def _bigrams(s: str) -> set[str]:
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}


def name_similarity(pred: str, ref: str) -> float:
    a, b = norm_text(pred), norm_text(ref)
    if a == b:
        return 1.0          # includes both empty: MedRepBench has 10 labels with an empty name,
                             # which the official metric treats as equal
    if not a or not b:
        return 0.0
    ra, rb = relaxed(pred), relaxed(ref)
    if ra and ra == rb:
        return 0.8
    if ra and rb and (ra in rb or rb in ra):
        return 0.6
    x, y = _bigrams(ra or a), _bigrams(rb or b)
    dice = 2 * len(x & y) / (len(x) + len(y))
    return 0.5 * dice if dice >= 0.5 else 0.0


def hungarian(cost: list[list[float]]) -> list[int]:
    """Minimum-cost perfect matching on a square matrix. Returns each row's assigned column.
    O(n^3), fast enough for n under a hundred."""
    n = len(cost)
    INF = float("inf")
    u, v, p, way = [0.0] * (n + 1), [0.0] * (n + 1), [0] * (n + 1), [0] * (n + 1)
    for i in range(1, n + 1):
        p[0], j0 = i, 0
        minv, used = [INF] * (n + 1), [False] * (n + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], INF, 0
            for j in range(1, n + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    out = [-1] * n
    for j in range(1, n + 1):
        if p[j]:
            out[p[j] - 1] = j - 1
    return out


def align(refs: list[dict], preds: list[dict], threshold: float = 0.5) -> list[tuple[int, int, float]]:
    """(truth index, prediction index, similarity). similarity = name similarity + 0.5 x value match;
    below the threshold it is not an alignment."""
    if not refs or not preds:
        return []
    n = max(len(refs), len(preds))
    sim = [[0.0] * n for _ in range(n)]
    for i, r in enumerate(refs):
        for j, p in enumerate(preds):
            s = name_similarity(p.get("item_name", ""), r["item_name"])
            if s > 0:            # a value match can only add to the score, never create an alignment alone
                s += 0.5 * field_equal("item_value", p.get("item_value"), r["item_value"])
            sim[i][j] = s
    assignment = hungarian([[-x for x in row] for row in sim])
    return [(i, j, sim[i][j]) for i, j in enumerate(assignment)
            if i < len(refs) and 0 <= j < len(preds) and sim[i][j] >= threshold]


# ── One document ────────────────────────────────────────────────────
_DISTRACTOR_LABELS = {"姓名", "性别", "年龄", "门诊号", "病历号", "ID号", "标本号", "科室", "标本类型", "送检医生",
                      "Name", "Sex", "Age", "MRN", "Lab No.", "Specimen", "Requested by",
                      "异常项目数", "是否异常", "Abnormal results", "Flagged"}


class Tally:
    def __init__(self):
        self.c = collections.Counter()

    def add(self, **kw):
        self.c.update(kw)

    def rate(self, num: str, den: str) -> float | None:
        return self.c[num] / self.c[den] if self.c[den] else None


def score_document(record: dict, preds: list[dict], tally: Tally, by_hazard: dict[str, Tally]) -> dict:
    rows = record["printed_rows"]
    readable = [i for i, r in enumerate(rows) if r["readable"]]
    abstain = [i for i, r in enumerate(rows) if not r["readable"]]
    refs = [rows[i] for i in readable]

    # V0: the official metric only sees readable rows with no unreadable field (matches medrep_view's export)
    v0_refs = [{k: rows[i][k] for k in FIELDS} for i in readable if not rows[i]["unreadable_fields"]]
    for truncate, tag in ((True, "v0"), (False, "v1")):
        got = official(v0_refs, preds, truncate)
        tally.add(**{f"{tag}_{f}": got[f] for f in FIELDS}, **{f"{tag}_rows": len(v0_refs)})

    pairs = align(refs, preds)
    matched_pred = {j for _, j, _ in pairs}
    doc = Tally()
    doc.add(truth_rows=len(refs), pred_rows=len(preds), matched=len(pairs))
    for i, j, _ in pairs:
        ref, pred = refs[i], preds[j]
        doc.add(name_exact=field_equal("item_name", pred.get("item_name"), ref["item_name"]))
        for f in ("item_value", "item_unit", "item_range"):
            if f in ref["unreadable_fields"]:
                continue
            doc.add(**{f"{f}_n": 1, f"{f}_ok": field_equal(f, pred.get(f), ref[f])})
        if "is_abnormal" not in ref["unreadable_fields"]:
            key = "flag_det" if ref["is_abnormal"] != "" else "flag_undet"
            doc.add(**{f"{key}_n": 1, f"{key}_ok": norm_text(pred.get("is_abnormal")) == ref["is_abnormal"]})
    # Mandatory abstention: the name matches an unreadable row, but a value was given anyway
    for i in abstain:
        name = rows[i]["item_name"]
        doc.add(abstain_rows=1)
        for j, p in enumerate(preds):
            if j not in matched_pred and name_similarity(p.get("item_name", ""), name) >= 0.8:
                if norm_text(p.get("item_value")) not in ("", "--", "/", "未做", "n/a", "notdone"):
                    doc.add(hallucinated=1)
                break
    # Distractor rows: a subject field or a summary row extracted as if it were an indicator
    for j, p in enumerate(preds):
        if j not in matched_pred and relaxed(p.get("item_name", "")) in {relaxed(x) for x in _DISTRACTOR_LABELS}:
            doc.add(distractor_extracted=1)
    tally.c.update(doc.c)

    # By hazard: a row-level hazard applies to its own rows; a document-level hazard (empty `rows`)
    # applies to every row in the document
    matched_ref = {readable[i]: preds[j] for i, j, _ in pairs}
    for hz in record["hazards"]:
        idxs = hz["rows"] or readable
        t = by_hazard.setdefault(hz["name"], Tally())
        for idx in idxs:
            if idx not in readable:
                continue
            t.add(rows=1)
            pred = matched_ref.get(idx)
            if pred is not None:
                t.add(matched=1, value_ok=field_equal("item_value", pred.get("item_value"), rows[idx]["item_value"]),
                      unit_ok=field_equal("item_unit", pred.get("item_unit"), rows[idx]["item_unit"]))
    return {"recall": doc.rate("matched", "truth_rows"), "precision": doc.rate("matched", "pred_rows"),
            "value_acc": doc.rate("item_value_ok", "item_value_n"),
            "unit_acc": doc.rate("item_unit_ok", "item_unit_n"),
            "range_acc": doc.rate("item_range_ok", "item_range_n"),
            "flag_acc": doc.rate("flag_det_ok", "flag_det_n"),
            "hallucinated": doc.c["hallucinated"], "distractors": doc.c["distractor_extracted"]}


def summarize(tally: Tally, n_docs: int) -> dict:
    c = tally.c
    out = {"documents": n_docs}
    for tag in ("v0", "v1"):
        recalls = {f: (c[f"{tag}_{f}"] / c[f"{tag}_rows"] if c[f"{tag}_rows"] else 0.0) for f in FIELDS}
        recalls["average"] = sum(recalls.values()) / len(FIELDS)
        out[tag] = recalls
    out["aligned"] = {
        "row_recall": tally.rate("matched", "truth_rows"),
        "row_precision": tally.rate("matched", "pred_rows"),
        "name_exact_given_aligned": tally.rate("name_exact", "matched"),
        "value_acc": tally.rate("item_value_ok", "item_value_n"),
        "unit_acc": tally.rate("item_unit_ok", "item_unit_n"),
        "range_acc": tally.rate("item_range_ok", "item_range_n"),
        "flag_acc_determinable": tally.rate("flag_det_ok", "flag_det_n"),
        "flag_abstain_acc": tally.rate("flag_undet_ok", "flag_undet_n"),
    }
    out["abstain"] = {"rows": c["abstain_rows"], "hallucinated": c["hallucinated"]}
    out["distractors_extracted"] = c["distractor_extracted"]
    return out


def load_predictions(path: pathlib.Path) -> dict[str, list[dict]]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            obj = json.loads(line)
            items = obj.get("items") or []
            out[obj["image"]] = json.loads(items) if isinstance(items, str) else items
    return out


def score(records: list[dict], predictions: dict[str, list[dict]]) -> dict:
    tally, by_hazard, per_doc = Tally(), {}, {}
    for r in records:
        per_doc[r["file"]] = score_document(r, predictions.get(r["file"], []), tally, by_hazard)
    out = summarize(tally, len(records))
    out["by_hazard"] = {
        name: {"rows": t.c["rows"], "row_recall": t.rate("matched", "rows"),
               "value_acc_given_aligned": t.rate("value_ok", "matched"),
               "unit_acc_given_aligned": t.rate("unit_ok", "matched")}
        for name, t in sorted(by_hazard.items())}
    out["per_document"] = per_doc
    return out


PAIR_METRICS = ("recall", "precision", "value_acc", "unit_acc", "range_acc", "flag_acc",
                "hallucinated", "distractors")


def paired_effects(records: list[dict], predictions: dict[str, list[dict]]) -> dict:
    """Paired contrasts: variant minus base within each group, averaged per metric.

    A ratio metric counts only when it is defined on both files (e.g. range accuracy is undefined
    after `reference.absent`, so that group is excluded from the average delta range accuracy);
    count metrics (hallucinations, distractors) are subtracted directly.
    """
    groups: dict[str, dict[str, dict]] = collections.defaultdict(dict)
    for r in records:
        pair = r.get("pair")
        if pair:
            groups[pair["pair_id"]][pair["variant"]] = r
    deltas: dict[str, dict[str, list[float]]] = collections.defaultdict(lambda: collections.defaultdict(list))
    counts: dict[str, int] = collections.Counter()
    for variants in groups.values():
        base = variants.get("base")
        if not base:
            continue
        b = score_document(base, predictions.get(base["file"], []), Tally(), {})
        for name, rec in variants.items():
            if name == "base":
                continue
            counts[name] += 1
            v = score_document(rec, predictions.get(rec["file"], []), Tally(), {})
            for m in PAIR_METRICS:
                if v[m] is not None and b[m] is not None:
                    deltas[name][m].append(v[m] - b[m])
    out = {}
    for name in sorted(counts):
        row = {"pairs": counts[name]}
        for m in PAIR_METRICS:
            ds = deltas[name][m]
            row[f"delta_{m}"] = sum(ds) / len(ds) if ds else None
        out[name] = row
    return out


def render(report: dict, effects: dict | None) -> str:
    def f(x):
        return "—" if x is None else f"{x:.3f}"
    lines = [f"{report['documents']} documents", "",
             "| Metric | Name | Value | Unit | Range | Flag | Average |", "|---|---|---|---|---|---|---|"]
    for tag, label in (("v0", "V0 official (truncated by order)"), ("v1", "V1 untruncated")):
        r = report[tag]
        lines.append(f"| {label} | " + " | ".join(f(r[k]) for k in FIELDS) + f" | {f(r['average'])} |")
    a = report["aligned"]
    lines += ["", "Aligned metric: row recall " + f(a["row_recall"]) + " · row precision " + f(a["row_precision"])
              + " · on aligned rows: value " + f(a["value_acc"]) + " unit " + f(a["unit_acc"])
              + " range " + f(a["range_acc"]) + " flag (determinable) " + f(a["flag_acc_determinable"])
              + " flag (should-abstain) " + f(a["flag_abstain_acc"]),
              f"mandatory-abstain rows {report['abstain']['rows']}, of which {report['abstain']['hallucinated']} "
              f"were given a value (hallucinated); distractor rows extracted as indicators "
              f"{report['distractors_extracted']}", "",
              "| Hazard | Rows | Row recall | Value accuracy (aligned) |", "|---|---:|---:|---:|"]
    for name, h in sorted(report["by_hazard"].items(), key=lambda kv: (kv[1]["row_recall"] or 0)):
        lines.append(f"| {name} | {h['rows']} | {f(h['row_recall'])} | {f(h['value_acc_given_aligned'])} |")
    if effects:
        def d(x):
            return "—" if x is None else ("0" if abs(x) < 5e-4 else f"{x:+.3f}")
        lines += ["", "Paired contrasts (variant minus base, paired within group; count metrics are the "
                  "average extra count per group)", "",
                  "| Hazard | Groups | Δ row recall | Δ row precision | Δ value | Δ unit | Δ range | "
                  "Δ flag | Δ hallucinated | Δ distractors |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        order = sorted(effects.items(), key=lambda kv: min((kv[1][f"delta_{m}"] or 0) for m in PAIR_METRICS[:6]))
        for name, e in order:
            lines.append(f"| {name} | {e['pairs']} | " + " | ".join(d(e[f"delta_{m}"]) for m in PAIR_METRICS) + " |")
    return "\n".join(lines)


def records_from_medrep(path: pathlib.Path, include_types: set[str] = frozenset({"Laboratory"})) -> list[dict]:
    """MedRepBench's label CSV, converted to this scorer's record shape. No hazards, no abstention
    rows: its labels don't carry those. This lets predictions mirobody produces on MedRepBench be
    scored with the same aligned metric (evaluation plan §S6)."""
    import csv

    csv.field_size_limit(10**9)
    out = []
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            meta = json.loads(row.get("meta") or "{}")
            items = json.loads(row.get("items") or "[]")
            if include_types and meta.get("type") not in include_types or not items:
                continue
            out.append({"file": row["image"], "kind": "medrep", "hazards": [], "printed_rows": [
                {**{k: it.get(k, "") for k in FIELDS}, "readable": True, "unreadable_fields": [],
                 "readings": [], "alternatives": {}, "hazards": [], "table": 0} for it in items]})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest", help="files.jsonl / pairs.jsonl, or a MedRepBench label CSV with --medrep-labels")
    ap.add_argument("predictions")
    ap.add_argument("--medrep-labels", action="store_true", help="the manifest is MedRepBench's datasets-meta-zhCN.csv")
    ap.add_argument("--pairs", action="store_true", help="the manifest is pairs.jsonl: also report within-pair deltas")
    ap.add_argument("--kinds", nargs="*", default=None, help="score only these document kinds (default: all)")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    if args.medrep_labels:
        records = records_from_medrep(pathlib.Path(args.manifest))
    else:
        records = [json.loads(line) for line in pathlib.Path(args.manifest).read_text(encoding="utf-8").splitlines()]
    if args.kinds:
        records = [r for r in records if r["kind"] in args.kinds]
    predictions = load_predictions(pathlib.Path(args.predictions))
    report = score(records, predictions)
    effects = paired_effects(records, predictions) if args.pairs else None
    print(render(report, effects))
    if args.json:
        report["paired_effects"] = effects
        pathlib.Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
