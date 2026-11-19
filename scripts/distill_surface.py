"""Distill how real reports write indicator names into the `observed` numbers of `resources/surface.json`.

    python3 scripts/distill_surface.py --analysis ../analysis           # print the measurements
    python3 scripts/distill_surface.py --analysis ../analysis --write   # update resources/surface.json

The input is each reference document's `indicator_rows[].name` in `analysis/*.json`. Only counts
leave this script: how many Simplified Chinese documents join a native name and its abbreviation in each way,
how many append an assay word or a trailing marker, and how many rows those documents mark. No name,
value or fragment is written anywhere.

Medication-instruction spellings (the `sig` axes) are counted only inside complete instructions found
in a reference document's analysis text, a frequency next to a dose with its unit (每日2次，每次1片, in
either order), so that "每日监测血糖3次" counts for nothing: in how many documents each word occurs
in one, and in how many a count stands unspaced.

A join is counted only when both halves are catalogue vocabulary (a native name or Chinese variant,
and an abbreviation or Latin variant). This undercounts - a report's "白细胞计数" is not the
catalogue's "白细胞" - but it never mistakes a name that contains Latin letters (IgM, 维生素B12) for
a join. Hand-authored parts of the resource (tiers, panels, notes, non-observed choices) are kept.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
RESOURCES = REPO / "mirobody_gen" / "resources"
sys.path.insert(0, str(REPO))
from mirobody_gen.layout import script_of  # noqa: E402

CJK = re.compile(r"[一-鿿]")
LATIN = r"[A-Za-z][A-Za-z0-9\-]+"
JOINS = {
    "paren": re.compile(rf"^(?P<native>[一-鿿]+)[(（](?P<abbr>{LATIN})[)）]$"),
    "glued": re.compile(rf"^(?P<native>[一-鿿]+)(?P<abbr>{LATIN})$"),
    "spaced": re.compile(rf"^(?P<native>[一-鿿]+)\s+(?P<abbr>{LATIN})$"),
    "abbr_first": re.compile(rf"^(?P<abbr>{LATIN})\s*(?P<native>[一-鿿]+)$"),
}


def vocabulary() -> tuple[set[str], set[str]]:
    natives, abbrs = set(), set()
    for item in json.loads((RESOURCES / "indicators.json").read_text(encoding="utf-8"))["indicators"]:
        variants = item.get("name_variants") or []
        natives |= {item["zh"], *(v for v in variants if CJK.search(v))}
        abbrs |= {a.upper() for a in [item.get("abbr") or "", *variants] if re.fullmatch(LATIN, a)}
    return natives, abbrs


def documents(analysis: pathlib.Path) -> list[list[str]]:
    """Each Simplified Chinese reference document's printed row names (the layer varies only those)."""
    out = []
    for path in sorted(analysis.glob("*.json")):
        a = json.loads(path.read_text(encoding="utf-8")).get("analysis")
        if not isinstance(a, dict):
            continue
        names = [r["name"].strip() for r in a.get("indicator_rows") or [] if isinstance(r, dict) and r.get("name")]
        scripts = {script_of(n) for n in names}
        if "zh-Hans" in scripts and "zh-Hant" not in scripts:
            out.append(names)
    return out


def measure_affix(docs: list[list[str]], words: list[str]) -> dict:
    """Document rate, in-document row rate and per-word row counts of a trailing word or mark."""
    pattern = re.compile("(" + "|".join(map(re.escape, words)) + ")$")
    rows, hit_docs, rows_in_hit_docs = collections.Counter(), 0, 0
    for names in docs:
        hits = [m.group(1) for n in names if (m := pattern.search(n))]
        if hits:
            hit_docs += 1
            rows_in_hit_docs += len(names)
            rows.update(hits)
    total = sum(rows.values())
    return {"family_rate": round(hit_docs / len(docs), 4),
            "row_rate": round(total / rows_in_hit_docs, 4) if rows_in_hit_docs else 0.0,
            "documents": hit_docs, "rows": dict(sorted(rows.items()))}


COUNT = r"[0-9一二三四两半]+"
UNIT = r"(?:mg|g|毫克|克|微克|μg|ug|片|粒|袋|丸|支|ml|mL|毫升|单位|IU)"
FREQUENCY = rf"(?P<per>每日|每天|一日|一天|1日|每周|一周)(?P<space1>\s*){COUNT}(?P<space2>\s*)次"
DOSE = rf"(?P<dose>每次|一次)\s*{COUNT}\s*{UNIT}"
INSTRUCTIONS = [re.compile(rf"{FREQUENCY}[，,；;\s]*{DOSE}"), re.compile(rf"{DOSE}[，,；;\s]*{FREQUENCY}")]


def measure_sig(analysis: pathlib.Path) -> dict[str, int]:
    """Documents whose complete instructions use each sig word; '' counts unspaced counts."""
    docs = collections.Counter()
    for path in sorted(analysis.glob("*.json")):
        text = json.dumps(json.loads(path.read_text(encoding="utf-8")).get("analysis"), ensure_ascii=False)
        words = set()
        for m in (m for p in INSTRUCTIONS for m in p.finditer(text)):
            words |= {m["per"], m["dose"], " " if m["space1"] or m["space2"] else ""}
        docs.update(words)
    return dict(sorted(docs.items()))


def measure(analysis: pathlib.Path) -> dict:
    docs = documents(analysis)
    natives, abbrs = vocabulary()
    joins = collections.Counter()
    for names in docs:
        forms = set()
        for n in names:
            for form, pattern in JOINS.items():
                m = pattern.match(n)
                if m and m["native"] in natives and m["abbr"].upper() in abbrs:
                    forms.add(form)
        joins.update(forms)
    return {"documents": len(docs), "name_join": dict(sorted(joins.items())),
            "name_suffix": measure_affix(docs, ["测定", "检测"]),
            "name_marker": measure_affix(docs, ["#", "＃", "*", "＊", "†"]),
            "sig": measure_sig(analysis)}


def apply(payload: dict, m: dict) -> dict:
    """Write the measurements into the observed choices; leave everything hand-authored alone."""
    for choice in payload["name_join"]["choices"]:
        if choice["tier"] == "observed":
            choice["share"] = m["name_join"].get(choice["form"], 0)
    for axis in ("name_suffix", "name_marker"):
        spec, got = payload[axis], m[axis]
        spec["family_rate"], spec["row_rate"] = got["family_rate"], got["row_rate"]
        for choice in spec["choices"]:
            if choice["tier"] == "observed":
                choice["share"] = got["rows"].get(choice["word"], 0)
    for axis in payload["sig"]["axes"].values():
        for choice in axis["choices"]:
            if choice["tier"] == "observed":
                choice["share"] = m["sig"].get(choice["word"], 0)
    payload["_provenance"]["measured"] = {"reference_documents": m["documents"],
                                          "name_join_documents": m["name_join"],
                                          "name_suffix_documents": m["name_suffix"]["documents"],
                                          "name_marker_documents": m["name_marker"]["documents"],
                                          "sig_word_documents": m["sig"]}
    return payload


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--analysis", type=pathlib.Path, default=REPO / "analysis",
                    help="directory of per-document analysis JSON (local reference set, never committed)")
    ap.add_argument("--write", action="store_true", help="update resources/surface.json")
    args = ap.parse_args()
    if not args.analysis.is_dir():
        raise SystemExit(f"no reference analysis at {args.analysis}; the numbers in surface.json stand as committed")
    m = measure(args.analysis)
    print(json.dumps(m, ensure_ascii=False, indent=2))
    if args.write:
        path = RESOURCES / "surface.json"
        payload = apply(json.loads(path.read_text(encoding="utf-8")), m)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {path.relative_to(REPO)}")


if __name__ == "__main__":
    main()
