"""Offline vocabulary coverage: how many printed names mirobody's resolver can code.

    mirobody-gen resolve-coverage out/p3/manifest.jsonl
    mirobody-gen resolve-coverage out/p3/manifest.jsonl --fail-on-regression

Costs nothing and runs no extraction: it feeds ground-truth rows straight to
`mirobody.engine.resolve`. This isolates the vocabulary stage of the pipeline from "can it be read
out of the file at all" — folding the two into one number would make an extraction regression and a
vocabulary regression look identical.

Three numbers, each answering a different question:

| Number | Question |
|---|---|
| Coverage | How many of this corpus's printed names resolve to a LOINC code |
| Regressions | The manifest expected a resolve and got none — the vocabulary got worse |
| Surprises | The manifest expected no resolve but got one — the vocabulary got better, and the spec needs rebuilding |

The third number looks like good news and gets ignored, but it is harmful: `expect_resolvable` was
measured on some past day, mirobody's vocabulary changing makes it stale, and the evaluation relies on
it to tell recall apart from a correct abstention. Once stale, the questions that test abstention start
scoring a correct answer as wrong.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

PACKAGE = pathlib.Path(__file__).resolve().parents[1]
RESOURCES = PACKAGE / "resources"
#: Root of the source checkout. Only used for things that exist solely in a checkout (reference
#: sets, caches, build output); an installed package carries none of these.
REPO = PACKAGE.parent


def load_rows(path: pathlib.Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows += json.loads(line)["rows"]
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("--fail-on-regression", action="store_true",
                    help="exit non-zero on regression (for CI where mirobody is installable)")
    args = ap.parse_args()

    try:
        from mirobody.engine import resolve
    except Exception as e:                       # noqa: BLE001
        raise SystemExit(
            f"failed to import mirobody.engine: {type(e).__name__}: {e}\n"
            f"run it with mirobody's interpreter: ../mirobody/.venv/bin/python harness/resolve_coverage.py ...")

    rows = load_rows(pathlib.Path(args.manifest))
    # Dedupe by printed name: the same name repeats across thousands of rows, so counting per row
    # would let common indicators dominate this number.
    by_name: dict[str, dict] = {}
    occurrences: collections.Counter[str] = collections.Counter()
    for row in rows:
        occurrences[row["original_indicator"]] += 1
        by_name.setdefault(row["original_indicator"], row)

    resolved = regressions = surprises = 0
    regression_names: list[str] = []
    surprise_names: list[str] = []
    for name, row in sorted(by_name.items()):
        result = resolve(name)
        ok = bool(result.resolved)
        resolved += ok
        if row.get("expect_resolvable") and not ok:
            regressions += 1
            regression_names.append(name)
        if not row.get("expect_resolvable") and ok:
            surprises += 1
            surprise_names.append(f"{name} → {result.loinc}")

    total = len(by_name)
    print(f"{total} printed names ({len(rows)} rows) · resolved {resolved} = {resolved / total:.1%}")
    print(f"regressions (expected to resolve, didn't) {regressions}")
    for name in regression_names:
        print(f"  ✗ {name} (occurs {occurrences[name]} times)")
    print(f"surprises (spec expected no resolve, got one) {surprises}")
    for name in surprise_names:
        print(f"  ! {name}")
    if surprises:
        print("  → mirobody's vocabulary improved. Rerun `build_indicators.py --write` to "
              "rebuild the spec, or abstention questions will mark correct answers wrong.")

    if args.fail_on_regression and regressions:
        sys.exit(1)


if __name__ == "__main__":
    main()
