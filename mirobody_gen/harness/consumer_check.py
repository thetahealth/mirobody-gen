"""Run a build's stress material through mirobody itself (run with mirobody's interpreter).

    ../mirobody/.venv/bin/python -m mirobody_gen consumer-check out/fuzz

Three sections, one per place a field report showed the consumer failing and the generator now writes on
purpose; each runs only when the build holds its material:

* **names**: `resolve-coverage` over the printed names of `files.jsonl` and `pairs.jsonl`, per `name.*`
  class, with the names that resolve to another analyte listed;
* **instructions**: `mirobody.kernel.meds.parse_dose_instruction` over `sig_pairs.jsonl`, each spelling's
  parse against its intent (doses a day, days between doses, as needed). No parse is reported apart from a
  wrong one: mirobody declines a count its schedule has no field for (twice weekly with no weekday named);
* **PDF layers**: whether mirobody's page split (`mirobody.documents.extract._pdf_pages`, private) sends a
  pairs PDF view to OCR, against its `pdf_layers.needs_ocr`.

A section whose mirobody function is missing says so instead of failing; the private one may move.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
from typing import Callable

from mirobody_gen.harness import resolve_coverage


def _jsonl(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def instructions(rows: list[dict], parse: Callable, describe: Callable) -> list[str]:
    """Each spelling's parse against its intent: right, wrong, or declined (no parse)."""
    out, listed = collections.Counter(), []
    for row in rows:
        schedule = parse(row["text"])
        if schedule is None:
            out["declined"] += 1
            listed.append(f"  - {row['text']} (declined)")
            continue
        parts, t = describe(schedule[0]), row["timing"]
        if t["as_needed"]:
            right = parts.as_needed
        else:
            days = t["period"] * (7 if t["period_unit"] == "wk" else 1)
            want = (row["slots_per_day"], 1) if days == 1 else (t["count"], days)
            right = (parts.doses_per_day, parts.period_days or 1) == want
        out["right" if right else "wrong"] += 1
        if not right:
            listed.append(f"  ✗ {row['text']}: {parts.doses_per_day} a day every {parts.period_days or 1} days")
    return [f"{len(rows)} spellings · right {out['right']} · wrong {out['wrong']} · declined {out['declined']}",
            *listed]


def layers(records: list[dict], root: pathlib.Path, sends_to_ocr: Callable[[bytes], bool]) -> list[str]:
    """Per PDF view, how often the consumer's OCR decision matches `needs_ocr`."""
    out = collections.Counter()
    for rec in records:
        want, got = rec["pdf_layers"]["needs_ocr"], sends_to_ocr((root / rec["file"]).read_bytes())
        verdict = "ok" if got == want else "missed OCR" if want else "needless OCR"
        out[rec["pdf_layers"]["container"], verdict] += 1
    return [f"  {container}: {verdict} {n}" for (container, verdict), n in sorted(out.items())]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("build", help="a build directory (files.jsonl, pairs.jsonl, sig_pairs.jsonl)")
    root = pathlib.Path(ap.parse_args().build)
    try:
        version = resolve_coverage.resolver_version()
    except ImportError as e:
        raise SystemExit(f"mirobody is not importable ({e}); run with mirobody's interpreter")
    print(f"mirobody {version['mirobody']} · bundle {version['bundle']}\n")

    files, pairs, sigs = (_jsonl(root / f) if (root / f).exists() else []
                          for f in ("files.jsonl", "pairs.jsonl", "sig_pairs.jsonl"))

    print("## Names\n")
    if files or pairs:
        from mirobody.engine import resolve
        print(*resolve_coverage.report(resolve_coverage.rows_of(files + pairs), resolve,
                                       resolve_coverage.analytes())[0], sep="\n")
    else:
        print("skipped: no files.jsonl or pairs.jsonl (build with --render)")

    print("\n## Instructions\n")
    try:
        from mirobody.kernel.meds import describe_schedule, parse_dose_instruction
    except ImportError as e:
        print(f"skipped: {e}")
    else:
        print(*instructions(sigs, parse_dose_instruction, describe_schedule) if sigs
              else ["skipped: no sig_pairs.jsonl (build with --pairs)"], sep="\n")

    print("\n## PDF layers\n")
    views = [r for r in pairs if r.get("pdf_layers")]
    try:
        from mirobody.documents.extract import MIN_PAGE_TEXT, _pdf_pages
    except ImportError as e:
        print(f"skipped: {e}")
    else:
        def sends_to_ocr(data: bytes) -> bool:
            return bool(_pdf_pages(data, min_page_text=MIN_PAGE_TEXT, dpi=36)[1])

        print(*layers(views, root, sends_to_ocr) if views
              else ["skipped: no PDF layer views (build with --render --pairs)"], sep="\n")

if __name__ == "__main__":
    main()
