"""Offline vocabulary coverage: how many printed names mirobody's resolver can code.

    mirobody-gen resolve-coverage out/p3/manifest.jsonl
    mirobody-gen resolve-coverage out/p3/files.jsonl out/p3/pairs.jsonl
    mirobody-gen resolve-coverage out/p3/manifest.jsonl --fail-on-regression

Costs nothing and runs no extraction: it feeds ground-truth names straight to
`mirobody.engine.resolve`. This isolates the vocabulary stage of the pipeline from "can it be read
out of the file at all" — folding the two into one number would make an extraction regression and a
vocabulary regression look identical.

A visit manifest holds each indicator's catalogue name; `files.jsonl` and `pairs.jsonl` hold the
names as the pages print them (`printed_rows`), with the abbreviations, English names and the
expression layer's spellings, each row tagged with its `name.*` classes. Names are counted once per
(name, truth code), so a common indicator does not dominate and one spelling with two meanings (PCT)
counts twice.

| Number | Question |
|---|---|
| Coverage | How many of these printed names resolve to a LOINC code |
| Miscoded | A name resolved, but not to the code the truth carries (chosen from the LOINC table, never from the resolver). With the LOINC table at hand they split in two: another code for the same analyte (a property, specimen or method the unit or context may still settle) and another analyte altogether — the confidently wrong answers, listed |
| By name class | Coverage and correct codes per spelling class (`plain` for a row with none), printed names only |
| Regressions | The catalogue expected a resolve and got none — the vocabulary got worse (catalogue names only) |
| Surprises | The catalogue expected no resolve but got one — the vocabulary got better, and the catalogue needs rebuilding |

Surprises look like good news and get ignored, but they are harmful: `expect_resolvable` was
measured against one mirobody version, and the evaluation relies on it to tell recall apart from a
correct abstention. Once stale, the questions that test abstention start scoring a correct answer as
wrong.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import subprocess
import sys
from typing import Callable

#: Miscoded names listed in full up to this many; beyond it, the count
SHOWN = 40


def load_rows(path: pathlib.Path) -> list[dict]:
    return rows_of([json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()])


def rows_of(records: list[dict]) -> list[dict]:
    """Every printed name with its truth code: a manifest's rows (with `expect_resolvable`), or the printed
    rows of a file manifest (with their `name.*` classes). A row whose readings carry different codes (血压
    printing systolic and diastolic) names a group, so it has no truth code to miss."""
    rows = []
    for rec in records:
        rows += [{"name": r["original_indicator"], "loinc": r.get("loinc"),
                  "expect": r.get("expect_resolvable"), "classes": None} for r in rec.get("rows", [])]
        for row in rec.get("printed_rows", []):
            if row.get("readings"):
                codes = {rec["readings"][i].get("loinc") for i in row["readings"]}
                rows.append({"name": row["item_name"], "loinc": codes.pop() if len(codes) == 1 else None,
                             "expect": None,
                             "classes": sorted(h for h in row.get("hazards", []) if h.startswith("name."))
                             or ["plain"]})
    return rows


def resolver_version() -> dict:
    """Which mirobody is answering. A source checkout is named by `git describe`, since an editable
    install's metadata can name a release older than the code it runs."""
    import mirobody
    root = pathlib.Path(mirobody.__file__).resolve().parent.parent
    version = mirobody.__version__
    if (root / ".git").exists():
        described = subprocess.run(["git", "-C", str(root), "describe", "--tags", "--always", "--dirty"],
                                   capture_output=True, text=True)
        version = described.stdout.strip() or version
    return {"mirobody": version, "bundle": getattr(mirobody, "BUNDLE_VERSION", None)}


def analytes() -> dict[str, str] | None:
    """Each LOINC code's analyte, from mirobody's bundled LOINC table, or None without it. Plurals are
    folded: LOINC writes one analyte singular in older codes and plural in newer ones (RDW's 30385-9
    `erythrocyte`, 115741-1 `erythrocytes`). Only words of five letters or more fold, so blood-group
    antigens such as `K`/`Ks` and `V`/`Vs` stay apart."""
    try:
        from mirobody import _bundle as bundle
        table, _, _ = bundle.load_axis()
    except Exception:                            # noqa: BLE001 - the split is optional
        return None
    return {table.field(i, bundle.AXIS_CODE): re.sub(r"(?<=\w{4})s\b", "", table.field(i, bundle.AXIS_ANALYTE)
                                                     or table.field(i, bundle.AXIS_COMPONENT))
            for i in range(table._n)}


def report(rows: list[dict], resolve: Callable, analyte: dict[str, str] | None = None) -> tuple[list[str], int]:
    """The report's lines and the number of regressions; `analyte` (code → analyte) splits the miscoded."""
    occurrences = collections.Counter((r["name"], r["loinc"]) for r in rows)
    first = {}
    for r in rows:
        first.setdefault((r["name"], r["loinc"]), r)
    classes: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    resolved, miscoded, same_analyte, regressions, surprises = 0, [], 0, [], []
    for (name, truth), row in sorted(first.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        result = resolve(name)
        ok = bool(result.resolved)
        resolved += ok
        right = ok and result.loinc == truth
        if ok and truth and not right:
            if analyte and analyte.get(truth) and analyte.get(result.loinc) == analyte[truth]:
                same_analyte += 1
            else:
                what = f" ({analyte.get(result.loinc)} for {analyte.get(truth)})" if analyte else ""
                miscoded.append(f"{name} → {result.loinc}, truth {truth}{what}")
        for c in row["classes"] or ():
            classes[c].update(names=1, resolved=ok, right=right)
        if row["expect"] and not ok:
            regressions.append(f"{name} (occurs {occurrences[name, truth]} times)")
        if row["expect"] is False and ok:
            surprises.append(f"{name} → {result.loinc}")

    total = len(first)
    out = [f"{total} printed names ({len(rows)} rows) · resolved {resolved} = {resolved / total:.1%}",
           f"miscoded (resolved to another code) {len(miscoded) + same_analyte}"
           + (f": {same_analyte} for the same analyte, {len(miscoded)} for another" if analyte else "")]
    out += [f"  ≠ {m}" for m in miscoded[:SHOWN]]
    if len(miscoded) > SHOWN:
        out.append(f"  … {len(miscoded) - SHOWN} more")
    if classes:
        out += ["", "| name class | names | resolved | right code |", "|---|---:|---:|---:|"]
        out += [f"| {c} | {n['names']} | {n['resolved'] / n['names']:.0%} | {n['right'] / n['names']:.0%} |"
                for c, n in sorted(classes.items())]
    out.append(f"regressions (expected to resolve, didn't) {len(regressions)}")
    out += [f"  ✗ {r}" for r in regressions]
    out.append(f"surprises (catalogue expected no resolve, got one) {len(surprises)}")
    out += [f"  ! {s}" for s in surprises]
    if surprises:
        out.append("  → mirobody's vocabulary improved. Rerun `build_indicators.py --write` to rebuild "
                   "the catalogue, or abstention questions will mark correct answers wrong.")
    return out, len(regressions)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifests", nargs="+", help="manifest.jsonl, files.jsonl or pairs.jsonl")
    ap.add_argument("--fail-on-regression", action="store_true",
                    help="exit non-zero on regression (for CI where mirobody is installable)")
    args = ap.parse_args()

    try:
        from mirobody.engine import resolve
    except Exception as e:                       # noqa: BLE001
        raise SystemExit(f"failed to import mirobody.engine: {type(e).__name__}: {e}\n"
                         "run it with mirobody's interpreter: ../mirobody/.venv/bin/python -m mirobody_gen "
                         "resolve-coverage ...")

    lines, regressions = report([r for m in args.manifests for r in load_rows(pathlib.Path(m))], resolve,
                                analytes())
    print("\n".join(lines))
    if args.fail_on_regression and regressions:
        sys.exit(1)


if __name__ == "__main__":
    main()
