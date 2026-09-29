"""Extraction scoring: MedRepBench official and corrected metrics, hazard attribution, pair deltas.

抽取评分：MedRepBench 官方口径 + 修正口径 + 按陷阱归因 + 对照对差值。

    mirobody-gen score out/p3/files.jsonl predictions.jsonl
    mirobody-gen score out/p3/pairs.jsonl predictions.jsonl --pairs
    mirobody-gen score out/p3/files.jsonl predictions.jsonl --json report.json

预测文件与 MedRepBench 同格式：每行 `{"image": <文件相对路径>, "items": [五字段…]}`，
所以同一份预测既能交给它的官方脚本（配 `mirobody-gen medrep-view` 导出的标注），也能交给这里。
每项可以多带三个字段 `loinc / value_num / unit_ucum`（抽取之后术语层的输出），带了就另算语义层。

## 为什么官方口径之外还要别的口径

读 MedRepBench 的 `scripts/evaluate_objective.py` 得到的四处（见 docs/handoff 评测计划 §5）：

1. **名字不中，整行作废**：按名字精确匹配，名字没对上，这一行的值、单位、范围、标记一并算错；
2. **按真值条数按序截断**：预测只留前 K 条（K = 真值条数）。前面多抽一行受检者字段，
   最后一行真读数就被挤掉——**多抽**被记成了**漏抽**，而且记在哪一行取决于顺序；
3. **只有召回，没有精确率**：多抽的行只通过截断间接受罚；
4. **"无法判定"与"判错"同分**。

所以这里同时给出：

* `V0`：官方口径的逐行复现（`tests/test_score.py` 在有官方脚本时断言两者逐字段一致）；
* `aligned`：**最优一对一行对齐**（匈牙利算法，名字相似度 + 值是否相同）代替按序截断，
  报告行召回、行精确率，以及**只在对齐行上**算的字段准确率——把"读没读到这一行"
  与"这一行读得对不对"分开。表格识别领域的做法同理：GriTS [smock2023grits] 先求两张表的
  最相似子结构再比单元格，TEDS [zhong2020image] 比树编辑距离，都不按顺序截断；
* **必须弃权**：印了行名却没印值的行（"未做"），抽出一个值就记一次幻觉；
* **干扰行**：受检者字段、"异常项目数"、混进表格的日期——抽出来就是误报；
* **按陷阱归因**：每一行带着它身上的陷阱名，逐类统计召回与值准确率；
* **对照对**（`--pairs`）：同一内容只差一类陷阱的两份文件，分数之差就是这类陷阱的效应。
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re

FIELDS = ["item_name", "item_value", "item_unit", "item_range", "is_abnormal"]


# ── 官方口径（按 MedRepBench 公开脚本的语义重写，不含其代码）───────────
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
    """一份文档的官方口径计数：每个字段答对几条（分母是真值条数）。"""
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


# ── 行对齐 ───────────────────────────────────────────────────────
_PAREN = re.compile(r"[\(（][^\)）]*[\)）]")


def relaxed(name: str) -> str:
    return norm_text(_PAREN.sub("", name or "")).replace("\n", "")


def _bigrams(s: str) -> set[str]:
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}


def name_similarity(pred: str, ref: str) -> float:
    a, b = norm_text(pred), norm_text(ref)
    if a == b:
        return 1.0          # 含两边都是空串：MedRepBench 有 10 条名称为空的标注，官方口径视为相等
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
    """最小费用完美匹配（方阵）。返回每行分到的列。O(n³)，n 在一百以内足够快。"""
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
    """(真值下标, 预测下标, 相似度)。相似度 = 名字相似度 + 0.5×值是否相同；低于阈值不算对齐。"""
    if not refs or not preds:
        return []
    n = max(len(refs), len(preds))
    sim = [[0.0] * n for _ in range(n)]
    for i, r in enumerate(refs):
        for j, p in enumerate(preds):
            s = name_similarity(p.get("item_name", ""), r["item_name"])
            if s > 0:            # 值相同只能加分，不能单独促成对齐
                s += 0.5 * field_equal("item_value", p.get("item_value"), r["item_value"])
            sim[i][j] = s
    assignment = hungarian([[-x for x in row] for row in sim])
    return [(i, j, sim[i][j]) for i, j in enumerate(assignment)
            if i < len(refs) and 0 <= j < len(preds) and sim[i][j] >= threshold]


# ── 一份文档 ─────────────────────────────────────────────────────
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

    # V0：官方口径只看可读且无不可读字段的行（与 medrep_view 导出的一致）
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
    # 必须弃权：名字对得上某个不可读行、却给了一个值
    for i in abstain:
        name = rows[i]["item_name"]
        doc.add(abstain_rows=1)
        for j, p in enumerate(preds):
            if j not in matched_pred and name_similarity(p.get("item_name", ""), name) >= 0.8:
                if norm_text(p.get("item_value")) not in ("", "--", "/", "未做", "n/a", "notdone"):
                    doc.add(hallucinated=1)
                break
    # 干扰行：受检者字段、统计行被当成指标
    for j, p in enumerate(preds):
        if j not in matched_pred and relaxed(p.get("item_name", "")) in {relaxed(x) for x in _DISTRACTOR_LABELS}:
            doc.add(distractor_extracted=1)
    tally.c.update(doc.c)

    # 按陷阱：行级陷阱落在行上，文档级陷阱（rows 为空）落在整份文档的所有行上
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
    """对照对：每组内 variant − base，逐个口径求平均差。

    比率类口径只在两份文件上都有定义时才计入（例如 `reference.absent` 之后范围准确率没有定义，
    这一组就不进"Δ范围准确率"的平均）；计数类（幻觉、干扰行）直接相减。
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
    lines = [f"文档 {report['documents']} 份", "",
             "| 口径 | 名称 | 值 | 单位 | 范围 | 标记 | 平均 |", "|---|---|---|---|---|---|---|"]
    for tag, label in (("v0", "V0 官方（按序截断）"), ("v1", "V1 不截断")):
        r = report[tag]
        lines.append(f"| {label} | " + " | ".join(f(r[k]) for k in FIELDS) + f" | {f(r['average'])} |")
    a = report["aligned"]
    lines += ["", "对齐口径：行召回 " + f(a["row_recall"]) + " · 行精确率 " + f(a["row_precision"])
              + " · 对齐行上：值 " + f(a["value_acc"]) + " 单位 " + f(a["unit_acc"]) + " 范围 " + f(a["range_acc"])
              + " 标记（可判定）" + f(a["flag_acc_determinable"]) + " 标记（应弃权）" + f(a["flag_abstain_acc"]),
              f"必须弃权行 {report['abstain']['rows']}，其中给出了值（幻觉）{report['abstain']['hallucinated']}；"
              f"干扰行被抽成指标 {report['distractors_extracted']} 条", "",
              "| 陷阱 | 行数 | 行召回 | 对齐行值准确率 |", "|---|---:|---:|---:|"]
    for name, h in sorted(report["by_hazard"].items(), key=lambda kv: (kv[1]["row_recall"] or 0)):
        lines.append(f"| {name} | {h['rows']} | {f(h['row_recall'])} | {f(h['value_acc_given_aligned'])} |")
    if effects:
        def d(x):
            return "—" if x is None else ("0" if abs(x) < 5e-4 else f"{x:+.3f}")
        lines += ["", "对照对（variant − base，组内配对；计数类为每组平均多出的条数）", "",
                  "| 陷阱 | 组数 | Δ行召回 | Δ行精确率 | Δ值 | Δ单位 | Δ范围 | Δ标记 | Δ幻觉 | Δ干扰行 |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        order = sorted(effects.items(), key=lambda kv: min((kv[1][f"delta_{m}"] or 0) for m in PAIR_METRICS[:6]))
        for name, e in order:
            lines.append(f"| {name} | {e['pairs']} | " + " | ".join(d(e[f"delta_{m}"]) for m in PAIR_METRICS) + " |")
    return "\n".join(lines)


def records_from_medrep(path: pathlib.Path, include_types: set[str] = frozenset({"Laboratory"})) -> list[dict]:
    """MedRepBench 的标注 CSV → 本评分器的记录。没有陷阱、没有弃权行：它的标注不带这些。
    这样 mirobody 在 MedRepBench 上跑出的预测，也能用同一把尺子算对齐口径（评测计划 §S6）。"""
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
