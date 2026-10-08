"""Generate `docs/zh-CN/numbers.md`: the project's **single** table of measured numbers.

    python3 scripts/build_numbers.py              # print
    python3 scripts/build_numbers.py --write      # write docs/zh-CN/numbers.md and resources/numbers.json

## Why this script exists

The 2026-09-22 review found the same quantity carrying two or three different values across
`docs/zh-CN/plan.md`: hazard-density p50 was written as both 4 and 6, the hazard-description
count as both 2725 and 3722, the table-less document count as 46 in prose but 142 in the
spec, and the WS/T 405 hemoglobin range in §2 quoting an old textbook value while the spec
had the standard one -- that cell was even marked "verified."

The root cause wasn't carelessness; it's that **numbers get hand-copied into prose**. A
44KB document repeating the same quantity four times makes "fix one, forget three" a
near-certainty. Documentation discipline can't fix a near-certainty, so these numbers are
made **computed** instead: prose cites this table and never restates a value.

Every row carries its definition and source script. **Quantities with the same name but a
different meaning must get separate rows** -- that's exactly why 2725 and 3722, and
`readings` and `rows`, collided: they were always different quantities, nobody had written
down the distinction.

## This file used to be called `numbers.py`

It was renamed because it **shadowed the standard-library `numbers`**: `python3
scripts/xxx.py` puts `scripts/` at `sys.path[0]`, so when numpy imported `numbers` it got
this file instead, `numbers.Integral` didn't exist, numpy crashed, and mirobody's resolver
failed to import as a result -- which `build_indicators.resolve_loinc` caught with `except
Exception`, silently zeroing 89 LOINC fields while the spec was written out anyway.

No file under `scripts/` may share a name with a standard-library module. A test in
`tests/test_spec.py` guards this.
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
RESOURCES = REPO / "mirobody_gen" / "resources"
sys.path.insert(0, str(REPO / "scripts"))

Row = tuple[str, str, str, str]   # quantity / value / definition / source

#: The machine-readable distributions, written into resources/numbers.json for
#: `audit/fidelity.py` to reconcile against. Only aggregate quantities (counts, proportions,
#: histograms) go here, never the content of any one document.
MACHINE: dict = {}


def _analysis() -> list[dict]:
    out = []
    for f in sorted(glob.glob(str(REPO / "analysis" / "*.json"))):
        a = (json.load(open(f, encoding="utf-8")) or {}).get("analysis") or {}
        if a:
            out.append(a)
    return out


def _norm(x):
    if isinstance(x, dict):
        return json.dumps({k: _norm(v) for k, v in sorted(x.items())}, ensure_ascii=False)
    if isinstance(x, list):
        return tuple(_norm(v) for v in x)
    return x


def layout_summary(a: dict) -> dict:
    """Real-document side: reduce one analysis to the layout summary `audit.fingerprint` needs.

    Reference values and flag markers are templatized from an **instance** (`example`), not
    from the analyzer's free-text description -- description wording noise would count the
    same layout as two different ones; see the note in `audit/fingerprint.py`.
    """
    import re

    from distill_layout import LANGUAGE_CANON, templatize_reference

    tables = a.get("result_tables") or []
    refs = []
    for r in a.get("reference_forms") or []:
        example = r.get("example") if isinstance(r, dict) else None
        if example:
            refs.append(templatize_reference(str(example)) or "?")
    flags = []
    for r in a.get("flag_forms") or []:
        example = (r.get("example") if isinstance(r, dict) else None) or ""
        flags.append(re.sub(r"[\d.]+", "", str(example)).strip()[:6])
    return {
        "columns": [str(_norm(c)) for c in (tables[0].get("columns") or [])] if tables else [],
        "reference_templates": refs,
        "flag_markers": flags,
        "page_count": a.get("page_count"),
        "languages": [LANGUAGE_CANON.get(str(x).strip(), str(x).strip())
                      for x in (a.get("languages") or [])],
    }


def pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(int(len(ordered) * q), len(ordered) - 1)]


def collect() -> tuple[list[Row], list[str]]:
    rows: list[Row] = []
    notes: list[str] = []
    analyses = _analysis()
    n = len(analyses)
    corpus_text = len(list((REPO / "corpus" / "text").glob("*.txt"))) if \
        (REPO / "corpus" / "text").is_dir() else 0

    rows.append(("真实文档（带版式分析的）", str(n),
                 "`analysis/*.json` 中 `analysis` 非空的文件数", "scripts/build_numbers.py"))
    rows.append(("真实文档提取文本", str(corpus_text),
                 "`corpus/text/*.txt` 的文件数，回放检测的索引就建在它上面", "scripts/build_numbers.py"))

    # ── Layout diversity ──
    sys.path.insert(0, str(REPO))
    from mirobody_gen.audit import fingerprint as fp

    summaries = [layout_summary(a) for a in analyses]
    main_fp = fp.summarize(summaries)
    rows.append(("版式指纹种数", f"{main_fp['fingerprints']} / {n} = **{main_fp['ratio']:.3f}**",
                 "定义见 `audit/fingerprint.py`：首表列头 + 前两种参考值模板 + 前两种标记 + 页数 + 语言。"
                 "2026-09-23 前用的是分析器的描述文本，得 0.818，其中三四个点是措辞噪声",
                 "audit/fingerprint.py"))
    rows.append(("只出现一次的指纹",
                 f"占指纹 {main_fp['singleton_share_of_fingerprints']:.1%}，"
                 f"占文档 **{main_fp['singleton_share_of_documents']:.1%}**",
                 "**两个分母都要写**：用错分母会把验收阈值定歪", "audit/fingerprint.py"))
    rows.append(("最大的三个版式家族", "、".join(f"{x} 份" for x in main_fp["largest_families"]),
                 "同一指纹重复出现的文档数，对应真实世界里「同一家机构的模板」",
                 "audit/fingerprint.py"))
    sens = []
    MACHINE["fingerprint"] = {"primary": main_fp, "sensitivity": {}}
    for name, fn in fp.SENSITIVITY.items():
        r = fp.summarize(summaries, fn)
        MACHINE["fingerprint"]["sensitivity"][name] = r
        sens.append(f"{name}: {r['ratio']:.3f} / 单次占文档 {r['singleton_share_of_documents']:.1%}")
    rows.append(("指纹粒度敏感性", " · ".join(sens),
                 "同一批文档、三种粒度。**这个数对定义很敏感**，引用时必须连同定义一起给",
                 "audit/fingerprint.py"))

    # ── Tables and rows ──
    tables_per = collections.Counter(len(a.get("result_tables") or []) for a in analyses)
    colsets = collections.Counter(_norm(t.get("columns") or [])
                                  for a in analyses for t in (a.get("result_tables") or []))
    rows_per = [len(a.get("indicator_rows") or []) for a in analyses]
    rows.append(("完全没有表格的文档", f"{tables_per[0]}（{tables_per[0]/n:.1%}）",
                 "`result_tables` 为空的文档数——纯叙述型报告。"
                 "**不是**「列头为空的表格数」（那是另一个量，曾被我写成 46）",
                 "scripts/build_numbers.py"))
    rows.append(("不同列组（未过闸）", str(len(colsets)),
                 "所有表格的列头元组去重，含 OCR 坏字与指标名列", "scripts/build_numbers.py"))
    MACHINE["rows_per_document"] = sorted(collections.Counter(rows_per).items())
    rows.append(("每文档表格行数", f"p50={pct(rows_per,.5)} p75={pct(rows_per,.75)} "
                 f"p95={pct(rows_per,.95)} max={max(rows_per)} "
                 f"零行={sum(1 for x in rows_per if x==0)/n:.1%}",
                 "`analysis[].indicator_rows` 的条数：**分析器看到的表格行**",
                 "scripts/build_numbers.py"))

    # ── the pipeline's readings is a different quantity ──
    synth = REPO / "library" / "synth_spec.json"
    if synth.is_file():
        docs = json.loads(synth.read_text(encoding="utf-8"))["documents"]["readings_per_document"]
        rows.append(("每文档 readings", f"p50={docs['p50']} p75={docs['p75']} "
                     f"p95={docs['p95']} max={docs['max']}（n={docs['n']}）",
                     "**与上一行不是同一个量**：这是产线提取管线的输出条数，"
                     "一份文档的多次检查会各出一批 readings，所以比表格行数大得多",
                     "library/synth_spec.json（旧管线产物）"))
        notes.append("`readings` 与 `rows` 是两个量。验收标准（docs/zh-CN/plan.md §5）里的分布检验要写明用哪个；"
                     "生成器目前按 `rows` 对齐，因为它对应「一份文件里印了多少行」。")

    # ── Language and page count ──
    layout = json.loads((RESOURCES / "layout.json").read_text(encoding="utf-8"))
    langs = layout["distributions"]["languages"]
    rows.append(("语言（多标签，已归一）",
                 " / ".join(f"{k} {v}" for k, v in langs.items()),
                 "一份文档可带多个语言标签，所以合计大于文档数。"
                 "归一前有 9 种写法，其中 4 种都是繁体", "scripts/distill_layout.py"))
    pages = layout["distributions"]["page_count"]
    one = int(pages.get("1", 0))
    total_pages = sum(int(v) for v in pages.values())
    rows.append(("单页文档占比", f"{one}/{total_pages} = {one/total_pages:.1%}",
                 "已剔除分析器的 0 页伪值（xlsx 没有页的概念）", "scripts/distill_layout.py"))

    # ── Hazards ──
    hazards = json.loads((RESOURCES / "hazards.json").read_text(encoding="utf-8"))
    prov, per_doc = hazards["_provenance"], hazards["per_document_count"]
    rows.append(("陷阱描述条数", f"{prov['descriptions']}（命中 {prov['matched']}，"
                 f"{prov['match_rate']:.1%}）",
                 "`analysis[].extraction_hazards` 的条数。"
                 "**不是** `library/grammar.json` 里那个 2725——那是旧管线按另一种口径聚合的，"
                 "两者没有换算关系", "scripts/distill_hazards.py"))
    rows.append(("陷阱分类", f"{len(hazards['classes'])} 类"
                 f"（可复现 {sum(1 for c in hazards['classes'] if c['generate'])} 类）",
                 "确定性关键词组合聚类；不可复现的那类是本语料的脱敏痕迹",
                 "scripts/distill_hazards.py"))
    rows.append(("每文档陷阱密度（生成器用这个）",
                 f"p50={per_doc['p50']} p75={per_doc['p75']} p95={per_doc['p95']} "
                 f"max={per_doc['max']} 零={per_doc['zero_share']:.1%}",
                 "**剔除 `artifact.redaction_placeholder` 之后**的条数。"
                 "含脱敏痕迹的原始密度是 p50=6/p95=10，用它会让注入密度系统性偏高",
                 "scripts/distill_hazards.py"))

    # ── Indicator catalogue ──
    indicators = json.loads((RESOURCES / "indicators.json").read_text(encoding="utf-8"))
    items = indicators["indicators"]
    resolved = sum(1 for i in items if i.get("expect_resolvable"))
    src = collections.Counter(i["reference_source"] for i in items)
    rows.append(("指标目录", f"{len(items)} 项 · 恒等式派生 "
                 f"{sum(1 for i in items if i['derived_from'])} 项 · 带 CVI "
                 f"{sum(1 for i in items if i['cvi'])} 项",
                 "手写，参考区间来自公开标准与指南", "scripts/build_indicators.py"))
    rows.append(("LOINC 可解析率", f"{resolved}/{len(items)} = {resolved/len(items):.0%}",
                 f"构建时向 `mirobody.engine.resolve` 查询。解析不出的 {len(items)-resolved} 项"
                 "是**刻意保留**的弃权素材。**注意：这是我们自己目录的覆盖率，有循环性**"
                 "——目录是对着这个解析器建的，不代表 mirobody 在真实世界名字上的覆盖率。"
                 "无偏估计见 docs/PAPER.md §9 第 8 条（MedRepBench 真实名字，约 50%）",
                 "scripts/build_indicators.py"))
    rows.append(("参考区间出处构成",
                 " / ".join(f"{k.split('（')[0]} {v}" for k, v in src.most_common()),
                 "每一项都必须是可引用的出处，`audit/privacy.py` 会检查",
                 "scripts/build_indicators.py"))

    # ── Unit location ──
    unit_loc = layout.get("unit_location") or []
    rows.append(("单位印在哪里", " / ".join(f"{x['value']} {x['occurrences']}" for x in unit_loc),
                 "已从分析器的 28 种自由文本收敛成枚举", "scripts/distill_layout.py"))

    notes.append("LOINC 可解析率是**对某一天的 mirobody 解析器**测出来的。"
                 "mirobody 的词表变了，`expect_resolvable` 就会悄悄过时——"
                 "重跑 `build_indicators.py --write` 是发版前的固定动作。")
    return rows, notes


def render(rows: list[Row], notes: list[str]) -> str:
    out = ["# 实测数字（自动生成，不要手改）", "",
           "本文件由 `python3 scripts/build_numbers.py --write` 生成。",
           "**所有文档引用数字时引用这里，不要在正文里复述数值**——",
           "同一个量在四处出现，改一处忘三处是必然，不是粗心。", "",
           "| 量 | 值 | 定义 | 来源 |", "| --- | --- | --- | --- |"]
    for name, value, definition, source in rows:
        out.append(f"| {name} | {value} | {definition} | `{source}` |")
    if notes:
        out += ["", "## 脚注", ""]
        out += [f"- {note}" for note in notes]
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    rows, notes = collect()
    text = render(rows, notes)
    print(text)
    if args.write:
        out = REPO / "docs" / "zh-CN" / "numbers.md"
        out.parent.mkdir(exist_ok=True)
        out.write_text(text, encoding="utf-8")
        payload = {"_source": "format-token",
                   "_note": "参考集（不随仓库分发）的版式与规模聚合统计：只含计数与分位数，由 scripts/build_numbers.py 生成",
                   "_provenance": {"script": "scripts/build_numbers.py"}, "_vocabulary_fields": [], **MACHINE}
        (RESOURCES / "numbers.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"Wrote {out} and {RESOURCES / 'numbers.json'}")


if __name__ == "__main__":
    main()
