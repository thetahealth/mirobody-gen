"""The definition of the layout fingerprint, shared by the reference set and the synthetic corpus.

"80% of real documents have a layout fingerprint that occurs only once" is only meaningful once a
definition is fixed, and both sides must use the same one or the E1 comparison is measuring with two
different rulers. The definition therefore lives here, in `audit/` (no dependency on the generator);
the real side (`scripts/build_numbers.py`) and the synthetic side (`audit/fidelity.py`) each reduce a
document to the same **layout summary** dict and hand it to this module:

    columns             header row of the first result table, verbatim (empty if there is no table)
    reference_templates reference-range templates with numbers replaced by {n}, see
                         distill_layout.templatize_reference
    flag_markers        abnormal-flag literals with digits stripped (e.g. up-arrow, H, "elevated")
    page_count          page count (None for tabular exports)
    languages           set of language tags (zh-Hans / zh-Hant / en / ja)

## Changed once, on 2026-09-23

The original definition used the reference-side analyzer's free-text descriptions of reference ranges
and flags ("range with hyphen", "Range with hyphen (e.g., ...)"): 1,327 reference-range descriptions
turned out to use 1,100 different wordings, so the same layout got counted as several. Templating on
the worked **example** instead dropped the fingerprint ratio from 0.818 to 0.791 and the singleton
share of documents from 79.7% to 75.4%. The conclusion didn't change, but a few points of the old
numbers were wording noise, and the synthetic side has no "description text" to template in the first
place -- the two sides could not share a ruler under the old definition.

The fingerprint is sensitive to granularity: columns alone put the singleton share at 40%; adding
reference-range and flag wording raises it to 75%. Any reported number must come with the sensitivity
table (`SENSITIVITY`), not a single cherry-picked granularity.
"""

from __future__ import annotations

import collections
from collections.abc import Callable, Iterable


def fingerprint(s: dict) -> tuple:
    """Primary definition: columns + first two reference-range templates + first two flag markers +
    page count + languages."""
    return (tuple(s.get("columns") or ()),
            tuple(sorted(set(s.get("reference_templates") or ())))[:2],
            tuple(sorted(set(s.get("flag_markers") or ())))[:2],
            s.get("page_count"),
            tuple(sorted(set(s.get("languages") or ()))))


#: Granularities used for the sensitivity analysis. The name is the definition.
SENSITIVITY: dict[str, Callable[[dict], tuple]] = {
    "columns": lambda s: (tuple(s.get("columns") or ()),),
    "columns+pages+languages": lambda s: (tuple(s.get("columns") or ()), s.get("page_count"),
                                          tuple(sorted(set(s.get("languages") or ())))),
    "primary": fingerprint,
}


def summarize(summaries: Iterable[dict], fn: Callable[[dict], tuple] = fingerprint) -> dict:
    """Fingerprint ratio, singleton share (both denominators), and the three largest families."""
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
