"""Export the synthetic corpus in MedRepBench's objective-track label format.

    mirobody-gen medrep-view out/p3/files.jsonl --out out/p3/medrep_labels.csv
    mirobody-gen medrep-view out/p3/files.jsonl --kinds lab_slip checkup_book --out ...

Matches MedRepBench's `datasets-meta-zhCN.csv` layout exactly, so its official scorer runs on this
output unmodified: three columns `image, meta, items`, where `items` is a JSON array of five-field
objects (`item_name / item_value / item_unit / item_range / is_abnormal`). `image` here holds a file's
relative path, since not every file in this corpus is an image.

This view exists because PAPER E2 scores the same extraction systems on MedRepBench (real) and this
corpus (synthetic) under one ruler to compare rankings, and the only hard guarantee of one ruler is
one scoring script. MedRepBench's data is CC BY-NC 4.0 and does not ship in this repository, nor does
its script; this module only produces input the official script can read.

Two deliberate departures from what the official script can express (both scored separately in
`harness/score.py`): rows that must abstain (`readable=False`, e.g. "not performed") are dropped
rather than exported, since the official script has no "correctly unanswerable" branch; and a row
with any unreadable field (e.g. a truncated reference range) is dropped whole rather than exported
with that field blank, since a blank field would make the official script credit an extractor that
also returned nothing — turning "didn't read it" into "read it correctly". The export prints how many
rows were dropped either way.
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib


def items_of(record: dict) -> tuple[list[dict], int]:
    items, dropped = [], 0
    for row in record["printed_rows"]:
        if not row["readable"] or row["unreadable_fields"]:
            dropped += 1
            continue
        items.append({k: row[k] for k in ("item_name", "item_value", "item_unit", "item_range", "is_abnormal")})
    return items, dropped


def export(records: list[dict], kinds: set[str]) -> tuple[list[dict], int]:
    rows, dropped = [], 0
    for r in records:
        if r["kind"] not in kinds:
            continue
        items, d = items_of(r)
        dropped += d
        if not items:
            continue
        subject = r.get("subject") or {}
        sex = next((v for k, v in subject.items() if k in ("性别", "Sex")), "")
        age = next((v for k, v in subject.items() if k in ("年龄", "Age")), "")
        meta = {"type": "Laboratory", "department": "", "gender": sex, "age": age,
                "synthetic": True, "kind": r["kind"], "language": r["language"], "tier": r["tier"]}
        rows.append({"image": r["file"], "meta": json.dumps(meta, ensure_ascii=False),
                     "items": json.dumps(items, ensure_ascii=False)})
    return rows, dropped


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("--out", required=True)
    ap.add_argument("--kinds", nargs="+", default=["lab_slip"],
                    help="document kinds to export (default: lab slips only, matching MedRepBench's objective track)")
    args = ap.parse_args()
    records = [json.loads(line) for line in pathlib.Path(args.manifest).read_text(encoding="utf-8").splitlines()]
    rows, dropped = export(records, set(args.kinds))
    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["image", "meta", "items"])
        writer.writeheader()
        writer.writerows(rows)
    n_items = sum(len(json.loads(r["items"])) for r in rows)
    print(f"{len(rows)} documents · {n_items} items → {args.out} "
          f"(dropped {dropped} must-abstain or unreadable-field rows)")


if __name__ == "__main__":
    main()
