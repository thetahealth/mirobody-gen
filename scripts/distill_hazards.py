"""Distill the real corpus's extraction-hazard descriptions into a taxonomy: `resources/hazards.json`.

    python3 scripts/distill_hazards.py                 # coverage report, doesn't write a file
    python3 scripts/distill_hazards.py --unmatched 30  # see what's still missed
    python3 scripts/distill_hazards.py --write         # write resources/hazards.json

The input is each real document's `extraction_hazards` in `analysis/*.json`: 627 documents,
3722 free-text descriptions (originally written one by one by a model, so almost no two are
alike -- 3694 are distinct). Using these strings as classes directly won't work: they're
free text, and some contain real fragments.

**This script outputs only class names and counts.** The `snippet` (a real-document
fragment) and `wrong_result` (a reference to a real value) in each raw description are never
read or emitted. This is the gate rule from docs/zh-CN/plan.md §4.2: a hazard carries only
its class and frequency; fragments are synthesized by the generator itself.

Clustering uses deterministic rules, not a model. Not to save cost: first, so the same input
produces the same taxonomy on every run, which is what lets the taxonomy live in version
control; second, so "this class is 12%" can be traced back to the rule that matched it.
Unmatched descriptions are reported as-is (`--unmatched`) -- **coverage is never pretended to
be 100%**.

A rule's shape is a **combination of keyword groups**, not one long regex: a rule is several
groups, any alternative within a group counts as a hit, and all groups must hit for the
class to match. The first version was a long regex with word order baked in, and only
covered 40% -- real descriptions have both "garbled reference range" and "reference range is
garbled," and an order constraint between them makes no sense.
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent
RESOURCES = REPO / "mirobody_gen" / "resources"

#: A rule = (class name, description, [keyword group, ...], whether the generator should reproduce it)
#:
#: Order **matters**: scanning top to bottom, the first rule that matches wins, so specific
#: rules come before general ones (`unit.glued_to_reference` must precede
#: `unit.glued_to_value`, or the latter swallows it; the catch-all `ocr.other` must come
#: after all other ocr.* rules).
#:
#: The last field being False means **this class should not be reproduced by the
#: generator** -- it isn't a real-world phenomenon but an artifact of how this corpus was
#: de-identified. Currently there's only one: the redaction placeholder `«NAME»`. It stays in
#: the taxonomy rather than being deleted, so the "match rate" number stays honest.
Rule = tuple[str, str, list[tuple[str, ...]], bool]

TAXONOMY: list[Rule] = [
    # ── De-identification artifacts: counted, but not reproduced ──────
    ("artifact.redaction_placeholder", "脱敏占位符（书名号包起来的 NAME/ID/ORG 等）出现在正文里。这是本语料的加工痕迹，不是真实现象",
     [("placeholder", "«", "redact", "占位符")], False),

    # ── Units ─────────────────────────────────────────────────────────
    ("unit.inconsistent_across_sets", "同一指标在不同检查组/不同行用了不同单位（uIU/ml 与 mU/L）",
     [("unit", "单位"),
      ("different", "differs", "differing", "inconsist", "vary", "varies", "varying", "mismatch", "不同", "不一致"),
      ("across", "between", "test set", "panel", "same analyte", "same test", "同一", "各组")], True),
    ("unit.glued_to_reference", "参考范围与单位粘连，常见 '&' 作分隔",
     [("reference", "range", "参考"),
      ("unit", "单位", "&"),
      ("concat", "combin", "glue", "attach", "embed", "no separator", "without separator",
       "no space", "without space", "粘连", "&")], True),
    ("unit.glued_to_value", "数值与单位之间没有空格",
     [("unit", "单位"),
      ("glue", "attach", "concat", "adjacent", "append", "embed", "directly",
       "no space", "without space", "无空格", "紧密", "粘")], True),
    ("unit.in_header_or_reference_only", "结果格里没有单位，单位只在表头或参考范围里",
     [("unit", "单位"),
      ("header", "reference", "表头", "参考", "column"),
      ("only", "not in", "missing", "absent", "instead of", "rather than", "仅", "只", "缺")], True),
    ("unit.superscript", "上标与科学计数法（10^9、×10⁹、/μL）",
     [("superscript", "scientific notation", "10^", "×10", "*10^", "exponent", "上标")], True),
    ("unit.ocr_corrupted", "单位被 OCR 改形（μ→u、l→1、随机字形）",
     [("unit", "单位"),
      ("ocr", "misread", "misrecogn", "garbl", "corrupt", "typo", "misspell", "错误", "识别")], True),
    ("unit.slash_ambiguous", "单位里的斜杠可能被当成分数或分隔符（次/分）",
     [("unit", "单位"), ("slash", "fraction", "分数", "斜杠")], True),
    ("unit.case_variant", "单位大小写方言（fL/fl、/L 与 /l、Kg/kg）",
     [("unit", "单位"), ("case", "capital", "uppercase", "lowercase", "大小写")], True),
    ("unit.missing", "整行或整份文档没有单位",
     [("unit", "单位"), ("no ", "missing", "absent", "lack", "without", "empty", "缺失", "没有", "未标")], True),

    # ── Reference ranges ─────────────────────────────────────────────
    ("reference.multiple_rows", "多行参考范围（每个检查组一行），只读第一行就会错配",
     [("reference", "参考"),
      ("multiple", "several", "two ", "three", "one per", "each", "多个", "多行", "每组")], True),
    ("reference.split_across_lines", "参考范围跨行断开或被截断",
     [("reference", "range", "参考"),
      ("split", "across line", "two lines", "wrapped", "line break", "cut off", "truncat",
       "断开", "跨行", "换行", "截断")], True),
    ("reference.inequality", "参考范围是不等式（≤ ≥ < >）或单个数，而不是区间",
     [("reference", "range", "参考"),
      ("inequal", "less than", "greater than", "≤", "≥", "<", ">", "不等式",
       "single value", "single number", "one number", "单个")], True),
    ("reference.sex_partitioned", "参考范围按性别、年龄或检测系统分层",
     [("reference", "range", "参考"),
      ("male", "female", "sex", "gender", "男", "女", "性别", "age-specific", "age group",
       "按年龄", "检测系统", "system")], True),
    ("reference.garbled", "参考范围被 OCR 弄坏",
     [("reference", "range", "参考"),
      ("garbl", "corrupt", "ocr", "misread", "unreadable", "nonsens", "stray",
       "乱码", "错误", "识别错")], True),
    ("reference.absent", "没有参考范围",
     [("reference", "range", "参考"),
      ("no ", "missing", "absent", "empty", "without", "not provided", "not given",
       "缺失", "为空", "没有", "未提供")], True),
    ("reference.separator_dialect", "区间分隔符方言（-- ~ – 一 全角）",
     [("reference", "range", "参考"),
      ("separator", "dash", "hyphen", "tilde", "em-dash", "en-dash", "delimiter",
       "双连字", "波浪", "分隔", "全角")], True),
    ("reference.space_inside_number", "参考范围的数字中间有空格（0. 28）",
     [("reference", "range", "参考"),
      ("space", "空格"),
      ("inside", "within", "between", "digit", "decimal", "number", "数字", "小数", "中间")], True),
    ("reference.ambiguous_column", "哪一列是参考范围说不清（与结果列、单位列混淆）",
     [("reference", "参考"),
      ("ambiguous", "unclear", "which column", "confus", "mistaken", "misinterpret",
       "歧义", "不清", "混淆")], True),

    # ── Values ────────────────────────────────────────────────────────
    ("value.pair_in_one_cell", "一格里印了一对值（血压 120/80）",
     [("blood pressure", "血压", "pair", "two values", "systolic"),
      ("single", "one cell", "one field", "combined", "same cell", "一格", "同一格", "/")], True),
    ("value.non_numeric", "结果是文字而非数字（阴性/未见异常/正常）",
     [("non-numeric", "nonnumeric", "text value", "textual", "qualitative", "descriptive result",
       "阴性", "阳性", "未见异常", "文字结果", "文本结果")], True),
    ("value.comparator", "值带比较符（<0.5、>1000）",
     [("comparator", "less than", "greater than", "prefix", "<", ">"),
      ("value", "result", "数值", "结果")], True),
    ("value.parenthetical", "值后跟括号内容（3.5 (H)、5.2（偏高））",
     [("value", "result", "数值", "结果"), ("parenthes", "bracket", "括号")], True),
    ("value.decimal_comma", "逗号当小数点用",
     [("comma", "逗号"), ("decimal", "separator", "小数")], True),
    ("value.missing_decimal_point", "小数点丢失（OCR 或印刷）",
     [("decimal", "小数点"), ("missing", "lost", "absent", "dropped", "丢失", "缺失", "没有")], True),
    ("value.space_in_number", "数值中间有空格（9. 9、1 13）",
     [("value", "number", "数值", "数字"),
      ("space", "空格"),
      ("digit", "decimal", "inside", "within", "between", "split", "数字", "小数", "中间")], True),
    ("value.duplicated_in_summary", "小结/结论段重复了表里的数值，容易被计两次",
     [("summary", "conclusion", "小结", "总结", "结论"),
      ("repeat", "same value", "duplicate", "again", "also appear", "重复")], True),
    ("value.multiple_per_row", "一行里有多个测量值",
     [("multiple", "two", "several", "多个"),
      ("measurement", "value", "result", "测量", "数值"),
      ("row", "cell", "line", "行", "格")], True),
    ("value.blank_column", "整列指标是空的",
     [("blank", "empty", "no value", "空白", "为空"),
      ("column", "analyte", "列", "指标")], True),

    # ── Table structure ──────────────────────────────────────────────
    ("table.flag_row_as_data", "'是否异常' 之类的标记行被当成结果行",
     [("是否异常", "flag row", "status row", "abnormal row"),
      ("row", "data", "result", "value", "misinterpret", "mistaken", "行", "结果")], True),
    ("table.bilingual_header", "中英双表头，英文行可能被当数据",
     [("header", "表头", "目录"),
      ("chinese", "english", "bilingual", "two", "double", "second", "中英", "中文", "英文", "双")], True),
    ("table.transposed", "转置表：指标在列上，标签在行首",
     [("transpos", "转置", "in column", "as column", "columns are", "指标.*列")], True),
    ("table.row_label_as_column", "一级/二级目录这类行标签被当成数据列",
     [("一级目录", "二级目录", "指标简称", "row label", "first column", "row header"),
      ("data", "value", "column", "not a", "rather than", "行标签", "数据", "列")], True),
    ("table.misalignment", "缺值、合并或换行导致整列错位",
     [("misalign", "shift", "offset", "串行", "错位", "对不齐"),
      ], True),
    ("table.multiple_tables", "一页多张表 / 一份文档多次体检",
     [("multiple", "two", "three", "several", "多个", "多次", "多张"),
      ("table", "panel", "test set", "sheet", "检查", "体检", "表")], True),
    ("table.two_result_columns", "两列都装结果（影像表现 与 检查结果）",
     [("two", "both", "两"), ("result", "finding", "结果", "表现"), ("column", "列")], True),
    ("table.wrapped_cell", "单元格内换行或跨单元格合并",
     [("wrap", "merged", "line break", "multi-line", "multiline", "换行", "合并"),
      ("cell", "row", "column", "单元格", "行", "列")], True),
    ("table.header_ambiguous", "表头本身有歧义：缺列名、合并列、表头不是第一行",
     [("header", "column name", "表头", "列名"),
      ("ambiguous", "unclear", "missing", "merged", "mistaken", "misread", "not a", "no ",
       "歧义", "不清", "缺", "合并")], True),
    ("table.truncated", "内容被截断或不完整",
     [("cut off", "truncat", "incomplete", "partially", "clipped", "截断", "不完整", "残缺")], True),

    # ── OCR and imaging ──────────────────────────────────────────────
    ("ocr.name_misspelled", "指标名被 OCR 认错",
     [("ocr", "misspell", "misread", "typo", "garbl", "misrecogn", "识别", "错字"),
      ("test name", "indicator", "analyte", "item name", "label", "项目", "指标", "名称", "abbreviation")], True),
    ("ocr.noise_text", "线条、印章、水印、杂字产生的假文本",
     [("noise", "artifact", "line art", "stamp", "watermark", "seal", "stray", "spurious",
       "false text", "印章", "水印", "杂")], True),
    ("ocr.punctuation_swap", "中文标点被识别成英文标点（或反之）",
     [("punctuation", "全角", "半角", "标点"),
      ], True),
    ("ocr.chart_annotation", "图表上的标注（均值线、坐标轴、图例）被当成数据",
     [("chart", "graph", "plot", "curve", "axis", "legend", "图表", "曲线", "坐标"),
      ("annotation", "label", "data point", "misread", "mistaken", "标注", "当作")], True),
    ("ocr.mixed_script", "同一份文档里繁简混排",
     [("traditional", "simplified", "繁體", "繁体", "简体"),
      ("mix", "both", "and ", "while", "混")], True),
    ("ocr.other", "其他 OCR 错误（兜底）",
     [("ocr", "misread", "misrecogn", "garbl", "识别错", "乱码")], True),

    # ── Metadata bleed-in ────────────────────────────────────────────
    ("meta.mixed_into_rows", "日期/机构/文件名混进了结果行",
     [("metadata", "date", "facility", "institution", "file name", "filename",
       "机构", "文件名", "日期"),
      ("mixed", "same row", "alongside", "in the row", "within the row", "contains", "混", "同一行")], True),
    ("meta.subject_field_as_indicator", "受检者字段（姓名/年龄/性别）被当成指标行",
     [("patient", "subject", "受检者", "姓名", "年龄", "性别", "name", "age", "gender", "sex"),
      ("indicator", "test", "value", "row", "field", "extract", "指标", "行", "字段")], True),
    ("meta.multiple_dates", "多个日期（采样/报告/审核/打印）易混",
     [("date", "日期", "时间"),
      ("multiple", "two", "three", "several", "different", "separate", "多个", "不同", "分开")], True),
    ("meta.date_format_dialect", "日期格式方言（YYYY.MM.DD、无分隔、中文年月日）",
     [("date", "日期"),
      ("format", "格式", "separator", "dot", "slash", "yyyy", "年月日")], True),
    ("meta.narrative_block", "叙述段落，没有数字表格",
     [("narrative", "prose", "free text", "free-text", "paragraph", "descriptive",
       "叙述", "段落", "描述性")], True),
    ("meta.page_furniture", "页码、页眉页脚、打印信息被当成数据",
     [("page number", "footer", "page header", "printed", "页码", "页脚", "页眉"),
      ("mistaken", "misread", "data", "value", "extract", "当作", "误")], True),

    # ── Flags ─────────────────────────────────────────────────────────
    ("flag.arrow_glued", "箭头标记粘在数值上（5.9↑）",
     [("arrow", "↑", "↓", "箭头"), ("attach", "glue", "value", "adjacent", "数值", "粘")], True),
    ("flag.contradicts_reference", "标记与参考范围矛盾",
     [("flag", "status", "标记", "提示"),
      ("inconsist", "contradict", "mismatch", "not match", "wrong", "矛盾", "不符")], True),
    ("flag.column_confusion", "标记列与结果列混淆，或标记列为空",
     [("flag", "status", "标志", "提示", "标记"),
      ("column", "列"),
      ("empty", "missing", "confus", "mistaken", "separate", "misinterpret",
       "为空", "缺", "混淆")], True),
    ("value.flag_combined_in_cell", "值与标记挤在同一格（5.9+、3.2↑、8.1 H）",
     [("value", "result", "数值", "结果"),
      ("flag", "sign", "symbol", "标记", "标志", "箭头", "plus", "arrow"),
      ("combined", "attach", "same", "one field", "one cell", "without separator",
       "together", "同一", "一格", "粘")], True),
    ("value.everything_in_one_cell", "结果、标记、参考范围全挤在一格",
     [("one cell", "same cell", "single cell", "one field", "一格", "同一格"),
      ("reference", "range", "参考")], True),
    ("value.missing", "某个项目没有结果值",
     [("missing", "no value", "absent", "empty", "缺失", "为空", "没有"),
      ("value", "result", "数值", "结果")], True),
    ("unit.on_separate_line", "单位单独占一行（在参考范围之后或之前）",
     [("unit", "单位"), ("separate line", "own line", "next line", "单独一行", "另起一行")], True),
    ("reference.ratio_not_unit", "参考范围里写的是比值/判读口径（s/co、index），不是单位",
     [("s/co", "ratio", "index", "cut-off", "cutoff", "比值", "判读"),
      ("unit", "reference", "range", "单位", "参考")], True),
    ("table.page_break_loses_header", "表格跨页，续页没有列标题",
     [("page break", "next page", "continued", "分页", "跨页", "下一页", "续页"),
      ("header", "column", "标题", "列", "表头")], True),
    ("value.contradicts_other_section", "小结段与表格里的同一指标数值对不上",
     [("contradict", "inconsist", "differ", "mismatch", "不一致", "对不上", "矛盾"),
      ("section", "summary", "key findings", "table", "小结", "结论", "表格")], True),
    ("flag.text_dialect", "文字标记（偏高/偏低/正常/H/L/阴性）",
     [("flag", "status", "标记", "提示", "标志"),
      ("text", "chinese", "word", "偏高", "偏低", "正常", "中文")], True),
]


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).lower()


def classify(text: str) -> str | None:
    """The first class name that matches; every keyword group must hit (any alternative within a group counts)."""
    t = normalize(text)
    for name, _desc, groups, _gen in TAXONOMY:
        if all(any(alt in t for alt in group) for group in groups):
            return name
    return None


GENERATABLE = {name for name, _d, _g, gen in TAXONOMY if gen}


def walk() -> tuple[list[tuple[int, str]], list[int], int]:
    """[(document index, description)], hazard count per document, document count. Only the
    `hazard` field is read."""
    items: list[tuple[int, str]] = []
    per_doc: list[int] = []
    doc_index = -1
    for f in sorted(glob.glob(str(REPO / "analysis" / "*.json"))):
        analysis = (json.load(open(f, encoding="utf-8")) or {}).get("analysis") or {}
        if not analysis:
            continue
        doc_index += 1
        hazards = analysis.get("extraction_hazards") or []
        per_doc.append(len(hazards))
        for h in hazards:
            # `snippet` and `wrong_result` are real-document fragments; they're never even read here.
            name = h.get("hazard") if isinstance(h, dict) else h
            if name:
                items.append((doc_index, str(name)))
    return items, per_doc, doc_index + 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="write resources/hazards.json")
    ap.add_argument("--unmatched", type=int, default=0, help="print the first N unmatched descriptions")
    args = ap.parse_args()

    items, per_doc_raw, docs = walk()
    hits: collections.Counter[str] = collections.Counter()
    docs_with: collections.defaultdict[str, set[int]] = collections.defaultdict(set)
    unmatched: list[str] = []

    for doc_index, text in items:
        cls = classify(text)
        if cls is None:
            unmatched.append(text)
            continue
        hits[cls] += 1
        docs_with[cls].add(doc_index)

    # Per-document hazard counts must be recomputed **excluding the non-reproduced classes**:
    # `artifact.redaction_placeholder` is an artifact of how this corpus was de-identified, and
    # counting it in would make the generator's injection density systematically too high.
    gen_per_doc = collections.Counter()
    for doc_index, text in items:
        cls = classify(text)
        if cls in GENERATABLE:
            gen_per_doc[doc_index] += 1
    per_doc = [gen_per_doc.get(i, 0) for i in range(docs)]

    total = len(items)
    matched = total - len(unmatched)
    print(f"{docs} documents · {total} hazard descriptions · {matched} matched "
          f"({matched / total:.1%}) · {len(unmatched)} unmatched")
    raw_sorted, gen_sorted = sorted(per_doc_raw), sorted(per_doc)
    print(f"{len(TAXONOMY)} classes in the taxonomy, {len(hits)} matched")
    print(f"Hazards per document  raw p50={raw_sorted[len(raw_sorted)//2]} "
          f"p95={raw_sorted[int(len(raw_sorted)*.95)]}  ·  "
          f"excluding non-reproduced classes p50={gen_sorted[len(gen_sorted)//2]} "
          f"p75={gen_sorted[int(len(gen_sorted)*.75)]} "
          f"p95={gen_sorted[int(len(gen_sorted)*.95)]} "
          f"max={max(per_doc)} zero_share={sum(1 for x in per_doc if x==0)/len(per_doc):.1%}"
          "   <- the generator uses the latter\n")
    print(f"{'class':<38}{'count':>6}{'docs':>7}{'doc share':>9}  generate")
    for name, _desc, _groups, gen in TAXONOMY:
        n, d = hits.get(name, 0), len(docs_with.get(name, ()))
        print(f"{name:<38}{n:>6}{d:>7}{d / docs:>8.1%}  {'yes' if gen else 'no'}")

    if args.unmatched:
        print(f"\nUnmatched descriptions (first {args.unmatched}):")
        for t in unmatched[: args.unmatched]:
            print("  ·", t[:110])

    if args.write:
        hist = collections.Counter(per_doc)
        ordered = sorted(per_doc)
        payload = {
            "_vocabulary_fields": ["classes", "name"],
            "_source": "format-token",
            "_note": (
                "从 627 份真实文档的提取陷阱描述蒸馏。只含类名、说明与频次；"
                "原始描述里的文档片段与真实取值一律未取用。生成器按这里的频率注入陷阱，"
                "片段自己合成。generate=false 的类是本语料的加工痕迹，不复现。"
            ),
            "_provenance": {
                "documents": docs,
                "descriptions": total,
                "matched": matched,
                "match_rate": round(matched / total, 4),
                "script": "scripts/distill_hazards.py",
            },
            "per_document_count": {
                "histogram": {str(k): v for k, v in sorted(hist.items())},
                "zero_share": round(hist[0] / len(per_doc), 4),
                "p50": ordered[len(ordered) // 2],
                "p75": ordered[int(len(ordered) * 0.75)],
                "p95": ordered[int(len(ordered) * 0.95)],
                "max": max(per_doc),
            },
            "classes": [
                {
                    "name": name,
                    "description": desc,
                    "generate": gen,
                    "occurrences": hits.get(name, 0),
                    "documents": len(docs_with.get(name, ())),
                    "document_rate": round(len(docs_with.get(name, ())) / docs, 4),
                }
                for name, desc, _groups, gen in TAXONOMY
            ],
        }
        out = RESOURCES / "hazards.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
