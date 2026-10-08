"""Readability audit: whatever the manifest says is printed must be found on the page. Non-zero exit on problems.

    mirobody-gen audit-readability out/p2/files.jsonl
    mirobody-gen audit-readability out/p2/pairs.jsonl

Printed truth is the ground truth every score is built on. It's written by the renderer, the most
complex piece of code in this project -- if the renderer prints `5.9↑` as `5.9 ↑` while the manifest
still says the value cell holds `5.9↑`, every extractor "gets it wrong" on that row, and the fault is
actually ours.

So this module **never imports the generator**; it only reads the rendered output: text is re-extracted
from the file (PDF text layer / XLSX / CSV), and each readable row's name, value, unit and reference
range from the manifest is checked line by line against what's actually on the page. All whitespace is
stripped before comparing -- line wraps and table-cell boundaries become newlines or spaces in extracted
text, and that doesn't count as a printing error.

A second, print-independent check of truth self-consistency: **flags must agree with the reference
range**. When a value is numeric and the reference range parses as an interval, `is_abnormal` must equal
whether the value falls outside that interval.

Image-layer output has no extractable text; it's skipped and counted. With `--ocr`, local tesseract
(chi_sim+eng) reads the image and reports, per scene, what share of the printed truth values it
recognized -- **reported, not judged**: tesseract failing to read something doesn't mean a human
couldn't, and PureDocBench is likewise one ground truth carried through three views.

Handwritten pages are checked against the transcript their record carries (`handwriting.transcript`):
every truth row must be in what the hand wrote. Whether the transcript is what the ink says is the
generator's own test (`tests/test_handwriting.py`), not this audit's.

Beyond tables there are key-value blocks, narrative and summary sections (`blocks`), and complaints and
diagnoses (outpatient charts): every piece of text they print must likewise be found on the page, to the
same standard as a printed row.
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
#: Root of the source checkout. Used only for things that exist only in a checkout (reference set,
#: cache, build output) -- an installed package has none of these.
REPO = PACKAGE.parent


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))


def _furniture_patterns() -> list[re.Pattern]:
    """Regexes for page furniture (banner, page number, print timestamp): when a paragraph crosses a
    page break these sit in the middle of it and must be stripped before comparing. Templates are read
    from resources/templates.json (JSON only, no import from the generator)."""
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
            return text if text.strip() else None          # a scanned PDF has no text layer; treat as an image
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
    """Whether a piece of text is on the page. Checked line by line; a line that's too long or crosses
    a page break (furniture gets inserted mid-line) falls back to checking it in 20-character chunks."""
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
    """None = not applicable (qualitative result, tiered range, or non-numeric)."""
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
    """Recognize an image (or each page of a scanned PDF) with local tesseract. Returns None without
    tesseract installed."""
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
            print(f"missing synthetic flag  {rec['file']}")
            problems += 1
        text = file_text(base / rec["file"])
        if text is None and rec.get("handwriting"):
            # A handwritten page cannot be re-read without a handwriting recogniser. What can be checked
            # here is that the record is consistent with its own transcript (every truth row was written);
            # the generator's tests check the transcript against the ink on the page.
            text = "\n".join(rec["handwriting"].get("transcript") or [])
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
        # blocks, summary, complaints, diagnoses -- every printed piece of text must be on the page
        for block in rec.get("blocks") or []:
            for want in block.get("printed") or []:
                checked += 1
                if not _present(want, blob):
                    problems += 1
                    print(f"not found on page  {rec['file']} block[{block.get('section')}] {want[:40]!r}")
        for item in (rec.get("complaints") or []) + (rec.get("diagnoses") or []):
            checked += 1
            if _norm(item["text"]) not in blob:
                problems += 1
                print(f"not found on page  {rec['file']} complaint/diagnosis {item['text']!r}")
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
                    print(f"not found on page  {rec['file']} row{i} {field}={want!r}")
            ok = flag_consistent(row)
            if ok is False:
                problems += 1
                print(f"flag disagrees with range  {rec['file']} row{i} value={row['item_value']} "
                      f"range={row['item_range']} flag={row['is_abnormal']}")
    print(f"\nchecked {checked} readable rows · {problems} problems · "
          f"{skipped} files with unreadable text (image layer, skipped)")
    if ocr_stats:
        print("image-layer OCR recheck (tesseract chi_sim+eng, reported not judged): "
              "share of printed-truth values recognized")
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
        print("readability audit failed.")
        sys.exit(1)
    print("readability audit passed.")


if __name__ == "__main__":
    main()
