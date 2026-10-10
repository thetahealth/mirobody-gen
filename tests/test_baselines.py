"""Contract tests for the rules baseline: what it reads, what it abstains on, and how it fails.

    python3 -m pytest tests/test_baselines.py -q
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mirobody_gen.harness import baselines  # noqa: E402

try:
    import fitz  # noqa: F401
    HAVE_PYMUPDF = True
except ImportError:
    HAVE_PYMUPDF = False


def _write_scan_and_text_pdf(root: pathlib.Path) -> None:
    doc = fitz.open()
    page = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 100), False)
    pix.clear_with(200)
    page.insert_image(fitz.Rect(0, 0, 200, 100), pixmap=pix)
    doc.save(root / "scan.pdf")
    doc.close()

    doc = fitz.open()
    page = doc.new_page()
    for x, word in ((72, "WBC"), (200, "6.2"), (280, "10^9/L"), (400, "3.5-9.5")):
        page.insert_text((x, 72), word, fontname="helv")
    doc.save(root / "text.pdf")
    doc.close()


def _run_rules(root: pathlib.Path, names: list[str]) -> tuple[subprocess.CompletedProcess, dict]:
    manifest = root / "files.jsonl"
    manifest.write_text("".join(json.dumps({"file": name, "printed_rows": []}) + "\n" for name in names),
                        encoding="utf-8")
    out = root / "pred.jsonl"
    run = subprocess.run([sys.executable, "-m", "mirobody_gen", "baselines", "rules", str(manifest),
                          "--out", str(out)], cwd=REPO, capture_output=True, text=True)
    preds = {}
    if run.returncode == 0:
        preds = {p["image"]: p["items"] for p in map(json.loads, out.read_text(encoding="utf-8").splitlines())}
    return run, preds


class RulesBaseline(unittest.TestCase):
    def test_image_tiers_get_an_empty_prediction(self):
        """A `--render` manifest holds jpg/png image tiers next to the text formats. The rules baseline
        used to decode the first JPEG as CSV and crash (UnicodeDecodeError on 0xff); it now abstains on
        images and still reads the text formats."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "slip.csv").write_text("项目,结果,单位,参考范围\n白细胞计数,6.2,10^9/L,3.5-9.5\n", encoding="utf-8")
            (root / "scan.jpg").write_bytes(b"\xff\xd8\xff\xe0" + bytes(64))
            (root / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(64))
            run, preds = _run_rules(root, ["scan.jpg", "slip.csv", "shot.png"])
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("2 files with no text to read", run.stdout)
        self.assertEqual(preds["scan.jpg"], [])
        self.assertEqual(preds["shot.png"], [])
        self.assertEqual([i["item_value"] for i in preds["slip.csv"]], ["6.2"])

    @unittest.skipUnless(HAVE_PYMUPDF, "needs PyMuPDF")
    def test_scanned_pdf_abstains_like_an_image_and_a_text_pdf_is_read(self):
        """A PDF without a text layer is an image in all but name; `audit-readability` skips it as the image
        layer, so the rules baseline must count it the same way."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            _write_scan_and_text_pdf(root)
            run, preds = _run_rules(root, ["scan.pdf", "text.pdf"])
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("1 files with no text to read", run.stdout)
        self.assertEqual(preds["scan.pdf"], [])
        self.assertEqual([i["item_value"] for i in preds["text.pdf"]], ["6.2"])

    def test_non_utf8_csv_names_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "gbk.csv"
            path.write_bytes("项目,结果\n白细胞计数,6.2\n".encode("gbk"))
            with self.assertRaisesRegex(ValueError, "gbk.csv: CSV is not UTF-8"):
                baselines.rules_extract(path)

    def test_an_unknown_format_is_an_error_not_an_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "rows.tsv"
            path.write_text("a\t1\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                baselines.rules_extract(path)


if __name__ == "__main__":
    unittest.main()
