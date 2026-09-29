"""The definition of the layout fingerprint, shared by the reference set and the synthetic corpus.

版式指纹的**定义**，真实语料与合成语料共用这一份。

"80% 的真实文档版式只出现一次"这句话只有配上定义才有意义，而且两边必须用同一个定义算，
否则 E1 的对账是在比两种尺子。所以定义放在 `audit/`（不依赖生成器），
真实侧（`scripts/build_numbers.py`）与合成侧（`audit/fidelity.py`）各自把文档
整理成同一种**版式摘要**，再交给这里。

版式摘要是一个 dict：

    columns             首张结果表的列头，原样拼写（无表格为空）
    reference_templates 参考值写法的模板（数字换成 {n}），见 distill_layout.templatize_reference
    flag_markers        异常标记的字面（去掉数字），如 ↑、H、偏高
    page_count          页数（表格导出件为 None）
    languages           语言标签集合（zh-Hans / zh-Hant / en / ja）

## 这个定义改过一次（2026-09-23）

旧定义用的是真实侧分析器给参考值与标记写的**自由文本描述**（"range with hyphen"、
"Range with hyphen (e.g., …)"），1,327 条参考值描述里有 1,100 种不同说法——
同一种写法因为措辞不同被算成两种版式。换成模板化的**实例**之后，
指纹种数比 0.818 → 0.791，单次指纹占文档 79.7% → 75.4%。结论没变，但旧数字
有三四个点来自措辞噪声，而且合成侧根本算不出"描述文本"，两边没法用同一把尺子。

指纹对粒度很敏感：只看列头时单次指纹只占文档 40%，加上参考值与标记写法才到 75%。
所以论文里报这个数必须同时报敏感性表（`SENSITIVITY`），不能只挑一个。
"""

from __future__ import annotations

import collections
from collections.abc import Callable, Iterable


def fingerprint(s: dict) -> tuple:
    """主定义：列头 + 前两种参考值模板 + 前两种标记 + 页数 + 语言集合。"""
    return (tuple(s.get("columns") or ()),
            tuple(sorted(set(s.get("reference_templates") or ())))[:2],
            tuple(sorted(set(s.get("flag_markers") or ())))[:2],
            s.get("page_count"),
            tuple(sorted(set(s.get("languages") or ()))))


#: 敏感性分析用的几种粒度。名字即定义。
SENSITIVITY: dict[str, Callable[[dict], tuple]] = {
    "columns": lambda s: (tuple(s.get("columns") or ()),),
    "columns+pages+languages": lambda s: (tuple(s.get("columns") or ()), s.get("page_count"),
                                          tuple(sorted(set(s.get("languages") or ())))),
    "primary": fingerprint,
}


def summarize(summaries: Iterable[dict], fn: Callable[[dict], tuple] = fingerprint) -> dict:
    """种数比、单次指纹（两个分母）、最大的三个家族。"""
    items = list(summaries)
    n = len(items)
    counts = collections.Counter(fn(s) for s in items)
    singles = sum(1 for v in counts.values() if v == 1)
    return {
        "documents": n,
        "fingerprints": len(counts),
        "ratio": len(counts) / n if n else 0.0,
        "singleton_share_of_fingerprints": singles / len(counts) if counts else 0.0,
        "singleton_share_of_documents": singles / n if n else 0.0,
        "largest_families": sorted(counts.values(), reverse=True)[:3],
    }
