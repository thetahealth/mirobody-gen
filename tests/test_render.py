"""渲染层的契约测试。

    python3 -m pytest tests/test_render.py -q      # 或
    python3 tests/test_render.py

需要 PyMuPDF 与 openpyxl（渲染依赖）。缺了就跳过渲染相关的几条，不假装通过。
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
        """spec 里每一类可复现的陷阱都登记了状态——没有一类被静默丢掉。"""
        names = {c["name"] for c in spec.hazards()["classes"] if c["generate"]}
        missing = names - set(hazards.STATUS)
        self.assertFalse(missing, f"没有登记状态的陷阱类：{sorted(missing)}")
        extra = set(hazards.STATUS) - {c["name"] for c in spec.hazards()["classes"]}
        self.assertFalse(extra, f"STATUS 里有 spec 不认识的类：{sorted(extra)}")

    def test_injected_classes_have_injectors(self):
        injected = {k for k, (s, _) in hazards.STATUS.items() if s == "injected"}
        self.assertEqual(injected, set(hazards.INCIDENTS))


class SyntheticIds(unittest.TestCase):
    def test_two_independent_implementations_agree(self):
        """生成器与隐私闸门各写了一份校验算法，必须一致。"""
        from mirobody_gen.audit import privacy

        rng = random.Random(3)
        for _ in range(300):
            self.assertTrue(privacy.synthetic_id(synthid.make(rng, rng.choice([9, 10, 12]))))
        # 随机号码碰巧通过的比例应在千分之一量级
        hits = sum(privacy.synthetic_id(str(rng.randrange(10**9, 10**10))) for _ in range(20000))
        self.assertLess(hits, 60)


class PrivacyWhitelist(unittest.TestCase):
    def test_names_and_institutions_outside_the_pool_are_flagged(self):
        from mirobody_gen.audit import privacy

        names, institutions, types = privacy.load_fiction()
        text = "姓名：王小明\nName: John Smith\n北京协和医院\nSt Mary General Hospital"  # privacy-gate:ok 故意构造的池外反例
        flagged = [m.group(1) for m in privacy.NAME_FIELD.finditer(text) if m.group(1) not in names]
        self.assertEqual(flagged, ["王小明", "John Smith"])
        bad = [m.group(1) or m.group(2) for m in privacy.INSTITUTION.finditer(text)
               if not privacy.institution_ok(m.group(1) or m.group(2), institutions, types)]
        self.assertEqual(bad, ["北京协和医院", "St Mary General Hospital"])
        # 虚构池里的机构放行
        inst = institutions[0]
        self.assertTrue(privacy.institution_ok(inst, institutions, types))


@unittest.skipUnless(HAVE_RENDER, "缺 PyMuPDF / openpyxl")
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
        """PDF 的 /ID、XLSX 的 docProps 与 zip 时间戳都不许带"现在"。"""
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
        fingerprint.fingerprint(rec["layout"])     # 不抛异常即可

    def test_pairs_differ_only_by_the_named_hazard(self):
        """对照对：variant 必须带上它名下那一类陷阱，且与 base 的本次读数（语义真值）完全相同。
        （"上次结果"列那一类会多出更早日期的读数——那正是它考的东西，所以只比本次。）"""
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
                # `view:<场景>` 是对齐视图（同一份内容换交付形态），没有具名陷阱，只要求真值一字不变
                if not name.startswith("view:"):
                    self.assertIn(name, {h["name"] for h in rec["hazards"]}, name)
                self.assertEqual(key(rec), key(base), name)


if __name__ == "__main__":
    unittest.main()
