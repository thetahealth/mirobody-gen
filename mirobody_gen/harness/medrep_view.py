"""Export the synthetic corpus in MedRepBench's objective-track label format.

把合成语料导出成 MedRepBench 客观赛道的标注格式，让它的官方评分脚本原样可用。

    mirobody-gen medrep-view out/p3/files.jsonl --out out/p3/medrep_labels.csv
    mirobody-gen medrep-view out/p3/files.jsonl --kinds lab_slip checkup_book --out ...

格式与 MedRepBench 的 `datasets-meta-zhCN.csv` 相同：三列 `image, meta, items`，
`items` 是 JSON 数组，每项五个字段 `item_name / item_value / item_unit / item_range / is_abnormal`。
`image` 这里放的是文件相对路径（我们的文件不全是图像）。

**为什么要有这个视图**：PAPER E2 要在 MedRepBench（真实）与本语料（合成）上用**同一口径**
给同一批抽取系统打分，比较排名。同一口径最硬的保证就是同一个评分脚本。
MedRepBench 的数据不进本仓库（CC BY-NC 4.0），它的脚本也不进；这里只产出它能读的输入。

**口径上的两处取舍**（官方脚本做不到的，都在 `harness/score.py` 里另算）：

* 必须弃权的行（`readable=False`，如"未做"）不导出——官方脚本没有"答不出才对"这种分支；
* 有字段不可读的行（参考范围被截断）整行不导出，而不是把那个字段留空——
  留空会让官方脚本要求抽取器输出空串，把"没读到"算成"读对了"。导出时会打印丢了多少行。
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
    print(f"{len(rows)} 份 · {n_items} 条 → {args.out}（未导出的必须弃权行或字段不可读行 {dropped} 条）")


if __name__ == "__main__":
    main()
