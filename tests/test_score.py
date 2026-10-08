"""Contract tests for the scorer.

    python3 -m pytest tests/test_score.py -q

The test that checks field-for-field agreement with the official MedRepBench script needs that script
itself: `MEDREPBENCH_SCORER=/path/to/scripts/evaluate_objective.py`. It isn't in this repository (the
dataset is CC BY-NC 4.0 and the script ships with the dataset, not redistributed here), and the test is
skipped without it.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mirobody_gen.harness import score  # noqa: E402


def item(name, value="1.0", unit="", rng="", flag=""):
    return {"item_name": name, "item_value": value, "item_unit": unit, "item_range": rng, "is_abnormal": flag}


class Matching(unittest.TestCase):
    def test_hungarian_finds_the_optimum(self):
        cost = [[4, 1, 3], [2, 0, 5], [3, 2, 2]]
        assignment = score.hungarian(cost)
        self.assertEqual(sum(cost[i][j] for i, j in enumerate(assignment)), 5)

    def test_truncation_turns_an_extra_row_into_a_miss(self):
        """The official scoring's second pitfall: one extra row drawn earlier pushes the true last row
        out of the window."""
        refs = [item("白细胞计数"), item("红细胞计数")]
        preds = [item("姓名", "张三"), item("白细胞计数"), item("红细胞计数")]
        self.assertEqual(score.official(refs, preds, truncate=True)["item_name"], 1)
        self.assertEqual(score.official(refs, preds, truncate=False)["item_name"], 2)
        pairs = score.align(refs, preds)
        self.assertEqual(len(pairs), 2)

    def test_name_cascade_and_relaxed_alignment(self):
        """The official scoring's first pitfall: a different spelling of the item name fails the value too,
        however correct it is; the alignment pass keeps the two separate."""
        refs = [item("白细胞计数(WBC)", "6.2")]
        preds = [item("白细胞计数", "6.2")]
        got = score.official(refs, preds)
        self.assertEqual(got["item_name"] + got["item_value"], 0)
        (i, j, s), = score.align(refs, preds)
        self.assertGreaterEqual(s, 0.5)

    def test_trailing_zero_is_not_normalized(self):
        """12.80 and 12.8 are not equal under the official scoring (only a whole-number trailing .0 is
        stripped)."""
        self.assertFalse(score.field_equal("item_value", "12.8", "12.80"))
        self.assertTrue(score.field_equal("item_value", "12", "12.00"))


class Oracle(unittest.TestCase):
    def test_oracle_scores_one(self):
        rec = {"file": "a.pdf", "kind": "lab_slip", "hazards": [], "printed_rows": [
            {**item("白细胞计数", "6.2", "10^9/L", "3.5-9.5", "0"), "readable": True, "unreadable_fields": [],
             "readings": [0], "alternatives": {}, "hazards": [], "table": 0},
            {**item("血红蛋白", "", "g/L", "130-175", "0"), "readable": False, "unreadable_fields": [],
             "readings": [1], "alternatives": {}, "hazards": [], "table": 0}]}
        preds = {"a.pdf": [item("白细胞计数", "6.2", "10^9/L", "3.5-9.5", "0")]}
        report = score.score([rec], preds)
        self.assertEqual(report["v1"]["average"], 1.0)
        self.assertEqual(report["aligned"]["row_recall"], 1.0)
        self.assertEqual(report["aligned"]["row_precision"], 1.0)
        self.assertEqual(report["abstain"]["hallucinated"], 0)

    def test_abstention_counts_a_hallucinated_value(self):
        rec = {"file": "a.pdf", "kind": "lab_slip", "hazards": [], "printed_rows": [
            {**item("血红蛋白", "", "g/L", "130-175", "0"), "readable": False, "unreadable_fields": [],
             "readings": [0], "alternatives": {}, "hazards": [], "table": 0}]}
        report = score.score([rec], {"a.pdf": [item("血红蛋白", "130")]})
        self.assertEqual(report["abstain"]["hallucinated"], 1)


@unittest.skipUnless(os.environ.get("MEDREPBENCH_SCORER"), "MedRepBench's official scoring script was not provided")
class OfficialEquivalence(unittest.TestCase):
    def test_v0_matches_the_official_script(self):
        """The official script and V0 count the same labels and predictions identically, field for field."""
        refs = [item("白细胞计数", "6.20", "10^9/L", "3.5~9.5", "0"), item("PT", "12.80", "", "9-14", ""),
                item("血红蛋白", "88", "g/L", "115-150", "1")]
        preds = [item("白细胞计数", "6.2", "10^9/L", "3.5-9.5", "0"), item("PT", "12.8", "", "9-14", "0"),
                 item("血红蛋白", "88", "g/l", "115-150", "1")]
        with tempfile.TemporaryDirectory() as tmp:
            labels = pathlib.Path(tmp) / "labels.csv"
            pred = pathlib.Path(tmp) / "pred.jsonl"
            import csv
            with labels.open("w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=["image", "meta", "items"])
                w.writeheader()
                w.writerow({"image": "x", "meta": json.dumps({"type": "Laboratory"}),
                            "items": json.dumps(refs, ensure_ascii=False)})
            pred.write_text(json.dumps({"image": "x", "items": preds}, ensure_ascii=False) + "\n", encoding="utf-8")
            out = subprocess.run([sys.executable, os.environ["MEDREPBENCH_SCORER"], "--labels", str(labels),
                                  "--predictions", str(pred)], capture_output=True, text=True, check=True).stdout
        official = {line.split("_recall:")[0]: int(line.split("(")[1].split("/")[0])
                    for line in out.splitlines() if "_recall:" in line and "(" in line}
        ours = score.official(refs, preds)
        self.assertEqual({f"{k}": v for k, v in ours.items()},
                         {("is_abnormal" if k == "is_abnormal" else k): v for k, v in official.items()})


if __name__ == "__main__":
    unittest.main()
