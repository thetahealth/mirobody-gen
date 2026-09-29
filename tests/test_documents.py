"""Check-up books, clinic notes and the output schema document.

Runs a tiny text-only build once and checks contracts on the records, not particular content.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


class Documents(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp())
        subprocess.run([sys.executable, "-m", "mirobody_gen.build", "--seed", "5", "--people", "6",
                        "--out", str(cls.tmp), "--render", "--pairs", "1"], cwd=REPO, check=True, capture_output=True)
        cls.files = [json.loads(l) for l in (cls.tmp / "files.jsonl").read_text(encoding="utf-8").splitlines()]
        cls.manifest = [json.loads(l) for l in (cls.tmp / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_every_check_up_book_has_the_documented_block_order(self):
        """Summary first or last, then general examination, departments, auxiliary, laboratory tables."""
        books = [r for r in self.files if r["kind"] == "checkup_book"]
        self.assertTrue(books)
        for b in books:
            sections = [x["section"] for x in b["blocks"]]
            self.assertIn("summary", sections, b["doc_id"])
            self.assertTrue(b["cover"] and b["cover"]["title"], b["doc_id"])
            departments = [s for s in sections if s in ("internal", "surgical", "eye", "ent", "dental", "gyn", "tcm")]
            # 入职体检只有四个科室，其余套餐至少五个
            self.assertGreaterEqual(len(departments), 4 if b["package"] == "entry" else 5, b["doc_id"])
            aux = [s for s in sections if s in ("ecg", "chest_xray", "chest_ct", "abd_us", "thyroid_us", "carotid_us",
                                                  "breast_us", "prostate_us", "gyn_us", "bmd", "hp_breath", "spirometry",
                                                  "arterial", "body_composition", "echo", "fundus_photo", "mammography")]
            self.assertTrue(aux, b["doc_id"])
            # departments come before auxiliary examinations
            self.assertLess(max(sections.index(d) for d in departments), min(sections.index(a) for a in aux))
            conclusions = [s for s in b["summary"] if s["kind"] == "conclusion"]
            self.assertTrue(conclusions)
            for s in b["summary"]:
                self.assertIn(s["kind"], ("conclusion", "advice", "grade", "critical"))
                if s["kind"] == "grade":
                    self.assertIn(s["grade"], "ABCDE")
            for x in b["findings"]:
                self.assertIn(x["expect"], ("coded", "no-match"))
                self.assertEqual(bool(x["icpc3"]), x["expect"] == "coded")

    def test_clinic_records_carry_complaints_with_an_expected_outcome(self):
        notes = [r for r in self.files if r["kind"] == "outpatient_record"]
        self.assertTrue(notes)
        for n in notes:
            for c in n["complaints"] + n["diagnoses"]:
                self.assertIn(c["expect"], ("coded", "no-match", "refused", "needs-input"))
                self.assertEqual(bool(c["icpc3"]), c["expect"] == "coded", c)
            names = {p["item_name"] for p in n["printed_rows"]}
            self.assertTrue({"BP", "T", "P"} & names, names)

    def test_home_logs_have_one_date_per_row(self):
        logs = [r for r in self.files if r["kind"] == "home_log"]
        for log in logs:
            dates = {x["observed"] for x in log["readings"]}
            self.assertGreaterEqual(len(dates), 7, log["doc_id"])
            self.assertTrue(all(x["role"] == "export" for x in log["readings"]))

    def test_no_checkup_prints_respiratory_rate_or_glycated_albumin(self):
        for r in self.manifest:
            if r["exam_type"] == "routine":
                keys = {row["key"] for row in r["rows"]}
                self.assertFalse({"resp", "ga"} & keys, r["file"])

    def test_schema_document_names_every_output_field(self):
        """docs/SCHEMA.md must mention every top-level field of every output file (drift guard)."""
        doc = (REPO / "docs" / "SCHEMA.md").read_text(encoding="utf-8")
        mentioned = set(re.findall(r"`([a-z_0-9]+)(?:\[\]|\{\})?`", doc))
        for name in ("manifest.jsonl", "files.jsonl", "pairs.jsonl", "devices.jsonl", "journal.jsonl", "genomics.jsonl", "people.jsonl"):
            path = self.tmp / name
            if not path.exists():
                continue
            rec = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            missing = sorted(k for k in rec if k not in mentioned)
            self.assertFalse(missing, f"{name}: fields not documented in SCHEMA.md: {missing}")


if __name__ == "__main__":
    unittest.main()
