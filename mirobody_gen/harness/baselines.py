"""Two free reference extractors that emit MedRepBench-format predictions.

两个不花钱的参照抽取器，输出 MedRepBench 格式的预测文件。

    mirobody-gen baselines oracle out/p3/files.jsonl --out out/p3/pred_oracle.jsonl
    mirobody-gen baselines rules  out/p3/files.jsonl --out out/p3/pred_rules.jsonl

* `oracle`：直接交出印刷真值。所有口径都应当是 1.0——不是，就是评分器或导出有错。
* `rules`：一个**故意朴素**的确定性抽取器。PDF 按坐标把文字聚成行，表格按单元格读；
  一行里第一个像名字的格当名称、第一个像数的格当值，再按正则认单位、参考范围与标记。
  它的用处不是比谁强，而是：零成本、完全可复现，用来确认陷阱确实会让分数掉、
  掉在哪一类，以及评分链路端到端是通的。它**不读** manifest 里的任何答案，只读文件。
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import pathlib
import re

_NUM = re.compile(r"^[<>≤≥]?\s*-?\d+(?:[.,]\d+)?$")
_NUM_IN = re.compile(r"^([<>≤≥]?\s*-?\d+(?:[.,]\d+)?)(.*)$")
_RANGE = re.compile(r"^\(?\s*[<>≤≥]?\s*\d+(?:\.\d+)?\s*([-~—–一～]+\s*\d+(?:\.\d+)?)?\s*\)?$")
_FLAGS = {"↑", "↓", "↑↑", "↓↓", "H", "L", "High", "Low", "偏高", "偏低", "高", "低", "升高", "降低", "A", "N",
          "Normal", "正常"}
_ABNORMAL = {"↑", "↓", "↑↑", "↓↓", "H", "L", "High", "Low", "偏高", "偏低", "高", "低", "升高", "降低", "A"}
_UNIT = re.compile(r"^(?:[×x]?10[\^*E]?\d+/[Ll]|[GT]/L|%|fL|fl|pg|g/L|g/l|mg/L|mmol/L|mmol/l|μmol/L|umol/L|"
                   r"U/L|U/l|IU/L|ng/mL|ng/ml|pg/mL|mIU/L|uIU/mL|μIU/mL|pmol/L|nmol/L|cm|kg|kg/m²|mmHg|"
                   r"次/分|/min|ms|°|mm/h|mL/min/1\.73m²|s)$")
_QUAL = {"阴性", "阳性", "弱阳性", "+", "++", "+++", "-", "Negative", "Positive"}


def _cells_from_pdf(path: pathlib.Path) -> list[list[str]]:
    import fitz

    rows: list[list[str]] = []
    with fitz.open(path) as doc:
        for page in doc:
            spans = []
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    text = "".join(s["text"] for s in line["spans"]).strip()
                    if text:
                        spans.append((round(line["bbox"][1] / 3), line["bbox"][0], text))
            spans.sort()
            current, key = [], None
            for y, x, text in spans:
                if key is not None and abs(y - key) > 1:
                    rows.append([t for _, t in sorted(current)])
                    current = []
                key = y if not current else key
                current.append((x, text))
            if current:
                rows.append([t for _, t in sorted(current)])
    return rows


def _cells(path: pathlib.Path) -> list[list[str]]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _cells_from_pdf(path)
    if suffix == ".xlsx":
        import openpyxl

        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
        return [["" if c is None else str(c) for c in row] for s in book.worksheets for row in s.iter_rows(values_only=True)]
    raw = path.read_bytes().decode("utf-8-sig")
    return [row for row in csv.reader(io.StringIO(raw))]


def rules_extract(path: pathlib.Path) -> list[dict]:
    items = []
    for row in _cells(path):
        cells = [c.strip() for c in row if c and c.strip()]
        if len(cells) < 2:
            continue
        name = next((c for c in cells if not _NUM.match(c) and not c.isdigit() and len(c) >= 2
                     and c not in _QUAL), None)
        if not name:
            continue
        rest = cells[cells.index(name) + 1:]
        value = unit = rng = flag = ""
        for c in rest:
            if not value:
                if c in _QUAL:
                    value = c
                    continue
                m = _NUM_IN.match(c)
                if m:
                    value, tail = m.group(1).strip(), m.group(2).strip()
                    for token in tail.split():
                        if token in _FLAGS:
                            flag = token
                        elif _UNIT.match(token):
                            unit = token
                    if tail and not unit and _UNIT.match(tail.rstrip("↑↓")):
                        unit = tail.rstrip("↑↓")
                    if tail.endswith(("↑", "↓")):
                        flag = tail[-1]
                    continue
            if c in _FLAGS:
                flag = c
            elif _UNIT.match(c):
                unit = c
            elif not rng and _RANGE.match(c.replace("&", " ").split(" ")[0]):
                parts = c.split("&")
                rng = parts[0].strip()
                if len(parts) > 1 and not unit:
                    unit = parts[1].strip()
        if not value:
            continue
        abnormal = "1" if flag in _ABNORMAL else ("0" if (flag or rng) else "")
        items.append({"item_name": name, "item_value": value, "item_unit": unit,
                      "item_range": rng, "is_abnormal": abnormal})
    return items


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("which", choices=["oracle", "rules"])
    ap.add_argument("manifest")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    manifest = pathlib.Path(args.manifest)
    base = manifest.parent
    with open(args.out, "w", encoding="utf-8") as fh:
        for line in manifest.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if args.which == "oracle":
                items = [{k: r[k] for k in ("item_name", "item_value", "item_unit", "item_range", "is_abnormal")}
                         for r in rec["printed_rows"] if r["readable"]]
            else:
                items = rules_extract(base / rec["file"])
            fh.write(json.dumps({"image": rec["file"], "items": items}, ensure_ascii=False) + "\n")
    print(f"→ {args.out}")


if __name__ == "__main__":
    main()
