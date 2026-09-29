"""生成 `docs/zh-CN/numbers.md`：全项目**唯一**的实测数字表。

    python3 scripts/build_numbers.py              # 打印
    python3 scripts/build_numbers.py --write      # 写 docs/zh-CN/numbers.md 与 resources/numbers.json

## 为什么要有这个脚本

2026-09-22 的评审在 `docs/zh-CN/plan.md` 里找出同一个量有两到三个值：陷阱密度 p50 既写 4 又写 6，
陷阱条数既写 2725 又写 3722，无表格文档数写 46 而 spec 里是 142，
WS/T 405 的血红蛋白区间在 §2 写的是旧教材值而 spec 里是标准值——那一格还标着"已验证"。

根因不是粗心，是**数字被手抄进了散文**。44KB 的文档里同一个量出现四次，改一处忘三处是必然。
文档纪律解决不了必然。所以这些数字改成**算出来**的：正文只引用这张表，不复述数值。

每一行都带定义与来源脚本。**同名不同义的量必须分成两行**，这正是 2725 与 3722、
readings 与 rows 之所以打架的原因：它们本来就是不同的量，只是没人写下区别。

## 这个文件曾经叫 `numbers.py`

改名是因为它**遮蔽了标准库的 `numbers`**：`python3 scripts/xxx.py` 会把 `scripts/` 放在
`sys.path[0]`，于是 numpy 导入 `numbers` 时拿到的是这个文件，`numbers.Integral` 不存在，
numpy 炸，mirobody 的解析器随之 import 失败——而 `build_indicators.resolve_loinc` 用
`except Exception` 把它接住了，结果是 89 个 LOINC 静默变成 0，spec 照样写出去。

`scripts/` 里的文件名不能与标准库同名。`tests/test_spec.py` 里有一条测试守着这件事。
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

Row = tuple[str, str, str, str]   # 量 / 值 / 定义 / 来源

#: 机器可读的那几个分布，写进 resources/numbers.json 给 `audit/fidelity.py` 对账用。
#: 只放聚合量（计数、比例、直方图），不放任何一份文档的内容。
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
    """真实侧：把一份分析整理成 `audit.fingerprint` 要的版式摘要。

    参考值与标记用**实例**（`example`）模板化，不用分析器写的描述文本——
    描述文本的措辞噪声会把同一种写法算成两种版式，见 `audit/fingerprint.py` 的说明。
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

    # ── 版式多样性 ──
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

    # ── 表格与行 ──
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

    # ── pipeline 的 readings 是另一个量 ──
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

    # ── 语言与页数 ──
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

    # ── 陷阱 ──
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

    # ── 指标目录 ──
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

    # ── 单位位置 ──
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
        print(f"已写出 {out} 与 {RESOURCES / 'numbers.json'}")


if __name__ == "__main__":
    main()
