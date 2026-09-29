"""Readability audit: whatever the manifest says is printed must be found on the page. Non-zero exit on problems.

可读性审计：manifest 说纸上印了什么，就去纸上找。非零退出即有问题。

    mirobody-gen audit-readability out/p2/files.jsonl
    mirobody-gen audit-readability out/p2/pairs.jsonl

印刷真值是评分的根。它由渲染器写，渲染器又是这个项目里最复杂的一段代码——
如果渲染器把 `5.9↑` 印成了 `5.9 ↑`，manifest 却还说值格里是 `5.9↑`，
那么所有抽取器都会在这一行上"犯错"，而错的其实是我们。

所以这里**不 import 生成器**，只读产物：把文件里的文字重新抽出来（PDF 文本层 / XLSX / CSV），
逐行检查 manifest 里每个可读行的名称、值、单位、参考范围确实出现在纸上。
比较前去掉所有空白——折行、表格单元格边界在抽取文本里会变成换行或空格，那不算印错。

另查一件与印刷无关、但同样是真值自洽性的事：**标记与参考范围一致**。
数值型、参考范围是一个可解析的区间时，`is_abnormal` 必须等于"值在不在区间里"。

图像层的产物读不出文字，这里跳过它们并计数；`--ocr` 时用本机 tesseract（chi_sim+eng）
把图像认一遍，按场景报告"印刷真值的值有多大比例被认出来"——**只报告不判定**：
OCR 认不出不等于人读不出，PureDocBench 也是同一份真值贯穿三个视图。

表格之外还有键值对、叙述与总检（`blocks`）、主诉与诊断（门诊病历）：它们的每一段印出来的文字
也必须在纸上找得到，与印刷行同一口径。
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import pathlib
import re
import sys
import unicodedata

PACKAGE = pathlib.Path(__file__).resolve().parents[1]
RESOURCES = PACKAGE / "resources"
#: 源码检出的根目录。只有需要检出才有的东西（参考集、缓存、产物）才用它；安装后的包里没有这些。
REPO = PACKAGE.parent


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))


def _furniture_patterns() -> list[re.Pattern]:
    """页脚构件（横幅、页码、打印时间）的正则：一段文字跨页时它们会插在中间，核对前先剔掉。
    模板从 resources/templates.json 读（只读 JSON，不 import 生成器）。"""
    path = RESOURCES / "templates.json"
    out = [re.compile(r"^SYNTHETIC SAMPLE.*$")]
    if not path.is_file():
        return out
    t = json.loads(path.read_text(encoding="utf-8"))
    out.append(re.compile("^" + re.escape(t.get("banner", "SYNTHETIC")) + "$"))
    for lang in ("zh", "en"):
        for key in ("page_number", "printed_at"):
            tpl = (t.get(key) or {}).get(lang)
            if tpl:
                pat = re.escape(tpl).replace(r"\{i\}", r"\d+").replace(r"\{n\}", r"\d+").replace(r"\{t\}", r".+")
                out.append(re.compile("^" + pat + "$"))
    return out


_FURNITURE = _furniture_patterns()


def _strip_furniture(text: str, extra: tuple[str, ...] = ()) -> str:
    """Drop page furniture (banner, page number, print stamp) and the repeated institution header
    (`extra`), which sit between the two halves of a paragraph that crosses a page break."""
    return "\n".join(line for line in text.splitlines()
                     if not any(p.match(line.strip()) for p in _FURNITURE) and line.strip() not in extra)


def file_text(path: pathlib.Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        import fitz

        with fitz.open(path) as doc:
            text = "\n".join(page.get_text() for page in doc)
            return text if text.strip() else None          # 扫描件 PDF 没有文本层，按图像处理
    if suffix == ".xlsx":
        import openpyxl

        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
        return "\n".join("\t".join("" if c is None else str(c) for c in row)
                         for sheet in book.worksheets for row in sheet.iter_rows(values_only=True))
    if suffix == ".csv":
        raw = path.read_bytes().decode("utf-8-sig")
        return "\n".join("\t".join(row) for row in csv.reader(io.StringIO(raw)))
    return None


def _present(want: str, blob: str) -> bool:
    """一段文字在不在纸上。逐行找；一行太长、跨了页（页脚会插在中间）时退而按 20 字块找。"""
    for line in want.split("\n"):
        n = _norm(line)
        if not n or n in blob:
            continue
        if len(n) < 24:
            return False
        chunks = [n[i:i + 20] for i in range(0, len(n) - 19, 20)]
        if not all(c in blob for c in chunks):
            return False
    return True


_RANGE = re.compile(r"^\(?\s*(-?\d+(?:\.\d+)?)\s*[-~—–一～]+\s*(-?\d+(?:\.\d+)?)\s*\)?$")
_UPPER = re.compile(r"^\(?\s*[<≤]\s*(-?\d+(?:\.\d+)?)\s*\)?$")
_LOWER = re.compile(r"^\(?\s*[>≥]\s*(-?\d+(?:\.\d+)?)\s*\)?$")


def flag_consistent(row: dict) -> bool | None:
    """None = 不适用（定性、分层范围、非数值）。"""
    value = row["item_value"].replace(",", ".")
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", value) or not row["item_range"] or row["is_abnormal"] == "":
        return None
    v = float(value)
    rng = row["item_range"]
    if m := _RANGE.match(rng):
        lo, hi = float(m.group(1)), float(m.group(2))
        abnormal = v < lo or v > hi
    elif m := _UPPER.match(rng):
        abnormal = v > float(m.group(1)) if "<" in rng else v > float(m.group(1))
    elif m := _LOWER.match(rng):
        abnormal = v < float(m.group(1))
    else:
        return None
    return (row["is_abnormal"] == "1") == abnormal


def _ocr_text(path: pathlib.Path) -> str | None:
    """本机 tesseract 认图像（或扫描件 PDF 的各页）。没有 tesseract 时返回 None。"""
    import shutil
    import subprocess
    import tempfile

    if not shutil.which("tesseract"):
        return None
    images: list[pathlib.Path] = []
    tmp = tempfile.mkdtemp()
    if path.suffix.lower() == ".pdf":
        import fitz

        with fitz.open(path) as doc:
            for i, page in enumerate(doc):
                out = pathlib.Path(tmp) / f"p{i}.png"
                page.get_pixmap(dpi=200).save(out)
                images.append(out)
    else:
        images.append(path)
    text = []
    for img in images:
        run = subprocess.run(["tesseract", str(img), "stdout", "-l", "chi_sim+eng", "--psm", "6"],
                             capture_output=True, text=True)
        text.append(run.stdout)
    return "\n".join(text)


def audit(manifest: pathlib.Path, ocr: bool = False) -> int:
    base = manifest.parent
    problems = checked = skipped = 0
    ocr_stats: dict[str, list[int]] = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if not rec.get("synthetic"):
            print(f"缺 synthetic 标记  {rec['file']}")
            problems += 1
        text = file_text(base / rec["file"])
        if text is None:
            skipped += 1
            if ocr and rec.get("scene"):
                got = _ocr_text(base / rec["file"])
                if got is not None:
                    blob = _norm(got)
                    values = [r["item_value"] for r in rec["printed_rows"] if r["readable"] and r["item_value"]]
                    hit = sum(_norm(v) in blob for v in values)
                    st = ocr_stats.setdefault(f"{rec['tier']}/{rec['scene']}/{rec['severity']}", [0, 0])
                    st[0] += hit
                    st[1] += len(values)
            continue
        blob = _norm(_strip_furniture(text, tuple(x for x in (rec.get("institution"),) if x)))
        # 块、总检、主诉、诊断——印出来的每段文字都得在纸上
        for block in rec.get("blocks") or []:
            for want in block.get("printed") or []:
                checked += 1
                if not _present(want, blob):
                    problems += 1
                    print(f"纸上找不到  {rec['file']} 块[{block.get('section')}] {want[:40]!r}")
        for item in (rec.get("complaints") or []) + (rec.get("diagnoses") or []):
            checked += 1
            if _norm(item["text"]) not in blob:
                problems += 1
                print(f"纸上找不到  {rec['file']} 主诉/诊断 {item['text']!r}")
        for i, row in enumerate(rec["printed_rows"]):
            if not row["readable"]:
                continue
            checked += 1
            for field in ("item_name", "item_value", "item_unit", "item_range"):
                want = row[field]
                if not want or field in row["unreadable_fields"]:
                    continue
                if _norm(want) not in blob:
                    problems += 1
                    print(f"纸上找不到  {rec['file']} 行{i} {field}={want!r}")
            ok = flag_consistent(row)
            if ok is False:
                problems += 1
                print(f"标记与范围不符  {rec['file']} 行{i} 值={row['item_value']} "
                      f"范围={row['item_range']} 标记={row['is_abnormal']}")
    print(f"\n核对 {checked} 个可读行 · 问题 {problems} · 读不出文字的文件（图像层，跳过）{skipped}")
    if ocr_stats:
        print("图像层 OCR 复核（tesseract chi_sim+eng，只报告不判定）：印刷真值的值被认出的比例")
        for key, (hit, total) in sorted(ocr_stats.items()):
            print(f"  {key:<40} {hit}/{total} = {hit / max(total, 1):.0%}")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifests", nargs="+")
    ap.add_argument("--ocr", action="store_true", help="re-check image tiers with local tesseract (report only)")
    args = ap.parse_args()
    problems = sum(audit(pathlib.Path(m), ocr=args.ocr) for m in args.manifests)
    if problems:
        print("可读性审计未通过。")
        sys.exit(1)
    print("可读性审计通过。")


if __name__ == "__main__":
    main()
