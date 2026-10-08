"""Contract tests for the render layer.

    python3 -m pytest tests/test_render.py -q      # or
    python3 tests/test_render.py

Needs PyMuPDF and openpyxl (render dependencies). Without them the render-related tests are skipped,
rather than faked as passing.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mirobody_gen import hazards, spec, synthid  # noqa: E402

try:
    import fitz  # noqa: F401
    import openpyxl  # noqa: F401
    HAVE_RENDER = True
except ImportError:
    HAVE_RENDER = False


def _build(out: pathlib.Path, people: int = 3, pairs: int = 1) -> None:
    subprocess.run([sys.executable, "-m", "mirobody_gen.build", "--seed", "11", "--people", str(people),
                    "--out", str(out), "--render", "--pairs", str(pairs)],
                   cwd=REPO, check=True, capture_output=True)


def _digests(root: pathlib.Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha1(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


class HazardRegistry(unittest.TestCase):
    def test_every_spec_class_has_a_status(self):
        """Every reproducible hazard class in the spec has a registered status -- none is silently dropped."""
        names = {c["name"] for c in spec.hazards()["classes"] if c["generate"]}
        missing = names - set(hazards.STATUS)
        self.assertFalse(missing, f"hazard classes with no registered status: {sorted(missing)}")
        extra = set(hazards.STATUS) - {c["name"] for c in spec.hazards()["classes"]}
        self.assertFalse(extra, f"STATUS has classes the spec doesn't know: {sorted(extra)}")

    def test_injected_classes_have_injectors(self):
        injected = {k for k, (s, _) in hazards.STATUS.items() if s == "injected"}
        self.assertEqual(injected, set(hazards.INCIDENTS))


class SyntheticIds(unittest.TestCase):
    def test_two_independent_implementations_agree(self):
        """The generator and the privacy gate each implement their own check algorithm; they must agree."""
        from mirobody_gen.audit import privacy

        rng = random.Random(3)
        for _ in range(300):
            self.assertTrue(privacy.synthetic_id(synthid.make(rng, rng.choice([9, 10, 12]))))
        # the share of random numbers that pass by chance should be on the order of one in a thousand
        hits = sum(privacy.synthetic_id(str(rng.randrange(10**9, 10**10))) for _ in range(20000))
        self.assertLess(hits, 60)


class PrivacyWhitelist(unittest.TestCase):
    def test_names_and_institutions_outside_the_pool_are_flagged(self):
        from mirobody_gen.audit import privacy

        names, institutions, types = privacy.load_fiction()
        text = "姓名：王小明\nName: John Smith\n北京协和医院\nSt Mary General Hospital"  # privacy-gate:ok deliberately built counter-example outside the pool
        flagged = [m.group(1) for m in privacy.NAME_FIELD.finditer(text) if m.group(1) not in names]
        self.assertEqual(flagged, ["王小明", "John Smith"])
        bad = [m.group(1) or m.group(2) for m in privacy.INSTITUTION.finditer(text)
               if not privacy.institution_ok(m.group(1) or m.group(2), institutions, types)]
        self.assertEqual(bad, ["北京协和医院", "St Mary General Hospital"])
        # an institution from the fiction pool is allowed through
        inst = institutions[0]
        self.assertTrue(privacy.institution_ok(inst, institutions, types))


@unittest.skipUnless(HAVE_RENDER, "missing PyMuPDF / openpyxl")
class Rendering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp())
        cls.a = cls.tmp / "a"
        cls.b = cls.tmp / "b"
        _build(cls.a)
        _build(cls.b)

    @classmethod
    def tearDownClass(cls):
        for p in (cls.tmp, cls.a, cls.b):
            shutil.rmtree(p, ignore_errors=True)

    def test_same_seed_is_byte_identical(self):
        """Neither the PDF's /ID nor the XLSX's docProps and zip timestamps may carry "now"."""
        self.assertEqual(_digests(self.a), _digests(self.b))

    def test_printed_truth_is_on_the_page(self):
        from mirobody_gen.audit import readability

        for name in ("files.jsonl", "pairs.jsonl"):
            self.assertEqual(readability.audit(self.a / name), 0, name)

    def test_every_file_is_marked_synthetic(self):
        import fitz

        for line in (self.a / "files.jsonl").read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            self.assertTrue(rec["synthetic"])
            if rec["format"] == "pdf":
                with fitz.open(self.a / rec["file"]) as doc:
                    self.assertEqual(doc.metadata["subject"], "SYNTHETIC")

    def test_layout_summary_matches_fingerprint_contract(self):
        from mirobody_gen.audit import fingerprint

        rec = json.loads((self.a / "files.jsonl").read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(set(rec["layout"]), {"columns", "reference_templates", "flag_markers",
                                             "page_count", "languages"})
        fingerprint.fingerprint(rec["layout"])     # just needs not to raise

    def test_pairs_differ_only_by_the_named_hazard(self):
        """A matched pair: the variant must carry the hazard class named after it, and its current readings
        (the semantic truth) must be identical to the base's. (The "previous result" column class adds an
        earlier-dated reading -- that's exactly what it's testing, so only the current reading is compared.)"""
        groups: dict[str, dict[str, dict]] = {}
        for line in (self.a / "pairs.jsonl").read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            groups.setdefault(rec["pair"]["pair_id"], {})[rec["pair"]["variant"]] = rec
        self.assertTrue(groups)
        for variants in groups.values():
            base = variants["base"]
            key = lambda r: sorted((x["key"], x["value_text"]) for x in r["readings"] if x["role"] == "current")
            for name, rec in variants.items():
                if name == "base":
                    continue
                # `view:<scene>` is an aligned view (same content, a different delivery form): it has no
                # named hazard, only the requirement that ground truth doesn't change a single character
                if not name.startswith("view:"):
                    self.assertIn(name, {h["name"] for h in rec["hazards"]}, name)
                self.assertEqual(key(rec), key(base), name)


if __name__ == "__main__":
    unittest.main()
