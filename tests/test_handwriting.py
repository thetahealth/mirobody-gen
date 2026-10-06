"""Handwritten files (`mirobody-gen build --render --handwriting`).

What is held here:

* the option is isolated: without it a build is byte-identical to the generator without this feature, and
  with it every printed file and every printed record is unchanged and the handwritten records follow;
* the same seed gives the same bytes, images included;
* truth is what the pen wrote: every value row was written once, as that exact string, and its ink is
  inside its box; nothing else on the page is a value; the transcript holds every truth field;
* every face covers every character a hand may write, and the faces are the pinned subsets;
* after capture every written value clears the legibility floor (digit height, ink contrast), and the
  geometry used to find a value after capture is the geometry the scene applied.

The builders are exercised directly on cohort people (fast); one class makes three small builds.
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
from datetime import date, timedelta

import numpy as np

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

try:
    import fitz  # noqa: F401
    from PIL import Image

    HAVE_RENDER = True
except ImportError:
    HAVE_RENDER = False

from mirobody_gen import spec  # noqa: E402

SEED = 7
#: A three-person build of this seed carries handwritten files (asserted below, so the isolation test
#: cannot pass by having nothing to isolate).
BUILD_SEED, BUILD_PEOPLE = 7, 3


def _inventory_module():
    import importlib.util

    loader = importlib.util.spec_from_file_location("build_handwriting", REPO / "scripts" / "build_handwriting.py")
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)
    return module


# ── Fonts ─────────────────────────────────────────────────────────────
@unittest.skipUnless(HAVE_RENDER, "needs Pillow and PyMuPDF")
class Fonts(unittest.TestCase):
    def test_subsets_are_the_pinned_files_and_small(self):
        from mirobody_gen.render import hand

        build = _inventory_module()
        for face_id, meta in hand.faces().items():
            data = (hand.FONT_DIR / meta["file"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), meta["sha256"], face_id)
            self.assertLess(len(data), 1_000_000, f"{face_id}: the repository refuses files over 1 MB")
            self.assertEqual(meta["upstream_sha256"], build.FONTS[face_id]["sha256"], face_id)
            self.assertTrue((hand.FONT_DIR / meta["license_file"]).is_file(), face_id)
        self.assertEqual(set(hand.faces()), set(build.FONTS))

    def test_fonts_cover_every_character_a_hand_can_write(self):
        """A character no face has would be drawn as an empty box, or raise mid-build."""
        from mirobody_gen.render import hand

        build = _inventory_module()
        for tier, t in spec.handwriting()["tiers"].items():
            for lang in ("zh", "en"):
                needed = set(build.inventory(lang)) - {" "}
                for face in t["faces"][lang]:
                    fallbacks = list(t["faces"]["en"]) if lang == "zh" else []
                    for latin in fallbacks or [None]:
                        have = hand.coverage(face) | (hand.coverage(latin) if latin else frozenset())
                        missing = sorted(needed - have)
                        self.assertFalse(missing, f"{tier} {lang} {face}+{latin} lacks {missing}")

    def test_every_tier_names_known_faces_and_scenes(self):
        from mirobody_gen.render import degrade, hand

        for tier, t in spec.handwriting()["tiers"].items():
            for lang in ("zh", "en"):
                for face in t["faces"][lang]:
                    self.assertEqual(hand.faces()[face]["script"], lang, (tier, face))
            for cap, scenes in t["scenes"].items():
                for scene in scenes:
                    self.assertEqual(degrade.SCENES[scene]["tier"], cap, (tier, scene))
                    ops = [op for op, _ in degrade.SCENES[scene]["stages"]]
                    self.assertFalse(set(ops) & hand.UNMODELLED, (tier, scene))
                    resizes = [i for i, op in enumerate(ops) if op in ("downscale", "wechat_scale")]
                    moves = [i for i, op in enumerate(ops) if op in ("rotation", "perspective")]
                    self.assertTrue(not resizes or not moves or min(resizes) > max(moves), (tier, scene))


# ── Truth is what the pen wrote ──────────────────────────────────────
_CACHE: dict = {}


def written() -> dict:
    """Every log kind and column set, doctor's notes and filled-in forms, in both languages, built once."""
    if _CACHE:
        return _CACHE
    from mirobody_gen import handwriting, layout
    from mirobody_gen import person as person_mod

    people = person_mod.build_cohort(SEED, 12)
    encounters = {p.person_id: person_mod.encounters_for(p, SEED) for p in people}
    registry = layout.build_registry(SEED)
    families = {lang: next(registry.family(i) for i in range(len(registry.institutions))
                           if registry.family(i).language == lang) for lang in ("zh-Hans", "en")}
    base = families["zh-Hans"]
    out = []
    start = date(2025, 3, 3)
    hands = iter(people * 3)
    for lang in ("zh", "en"):
        v = spec.handwriting()["logs"][lang]
        for k, columns in enumerate(v["bp"]["columns"]):
            days = [{"day": start + timedelta(days=d),
                     "entries": [{"time": f"07:1{d}", "values": {"sbp": 118 + d, "dbp": 74 + d % 5, "pulse": 61 + d}}]
                     + ([{"time": "21:05", "values": {"sbp": 127, "dbp": 81, "pulse": 70}}] if d % 3 == 0 else [])}
                    for d in range(7)]
            out.append(handwriting.build_log(SEED, next(hands), base, lang, "bp", days, f"t_bp_{lang}{k}", True, columns))
        for k, columns in enumerate(v["glucose"]["columns"]):
            meals = [m for m in ("breakfast", "lunch", "dinner", "post") if m in columns]
            p = next(hands)
            days = [{"day": start + timedelta(days=d),
                     "entries": [{"time": "07:00", "values": handwriting.glucose_day(
                         SEED, p, start + timedelta(days=d), meals[:1 + d % 3])}]} for d in range(8)]
            out.append(handwriting.build_log(SEED, p, base, lang, "glucose", days, f"t_glu_{lang}{k}", True, columns))
        days = [{"day": start + timedelta(days=d), "entries": [{"time": "07:00", "values": {"weight": round(70.4 - d / 10, 1)}}]}
                for d in range(9)]
        out.append(handwriting.build_log(SEED, next(hands), base, lang, "weight", days, f"t_wt_{lang}", True))
    clinic = [(q, e) for q in people for e in encounters[q.person_id] if e.exam_type == "clinic"]
    routine = [(q, e) for q in people for e in encounters[q.person_id] if e.exam_type == "routine"]
    for k, lang in enumerate(("zh-Hans", "en")):
        short = "zh" if lang == "zh-Hans" else "en"
        for j, (q, e) in enumerate(clinic[k * 3:k * 3 + 3]):
            w = handwriting.build_note(SEED, q, e, families[lang], short, f"t_note_{q.person_id}_{short}{j}", True)
            if w:
                out.append(w)
        for j, (q, e) in enumerate(routine[k * 3:k * 3 + 3]):
            w = handwriting.build_form(SEED, q, e, families[lang], short, f"t_form_{q.person_id}_{short}{j}", True)
            if w:
                out.append(w)
    _CACHE.update(written=out, people=people, encounters=encounters, families=families, hw=handwriting)
    return _CACHE


@unittest.skipUnless(HAVE_RENDER, "needs Pillow and PyMuPDF")
class Drawn(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = written()
        cls.written = fixture["written"]
        cls.people, cls.encounters = fixture["people"], fixture["encounters"]
        cls.families, cls.hw = fixture["families"], fixture["hw"]

    def test_every_kind_and_language_is_exercised(self):
        kinds = {(w.doc.kind, w.doc.family.language) for w in self.written}
        self.assertTrue({("outpatient_record", "zh-Hans"), ("outpatient_record", "en"), ("lab_slip", "zh-Hans"),
                         ("lab_slip", "en"), ("home_log", "zh-Hans"), ("home_log", "en")} <= kinds, kinds)

    def test_every_value_row_was_written_once_as_that_string(self):
        for w in self.written:
            values = [e for e in w.sheet.log if e["role"] == "value"]
            by_row = {}
            for e in values:
                self.assertNotIn(e["row"], by_row, (w.doc.doc_id, e))
                by_row[e["row"]] = e
            self.assertEqual(set(by_row), set(range(len(w.doc.printed))), w.doc.doc_id)
            for i, row in enumerate(w.doc.printed):
                self.assertEqual(by_row[i]["text"], row.item_value, (w.doc.doc_id, i))

    def test_value_ink_is_inside_its_box_and_nowhere_else(self):
        for w in self.written:
            full = np.asarray(w.sheet.render().convert("L"), dtype=np.float32)
            bare = np.asarray(w.sheet.render(values=False).convert("L"), dtype=np.float32)
            delta = bare - full
            outside = np.ones_like(delta, dtype=bool)
            for e in w.sheet.log:
                if e["role"] != "value":
                    continue
                x0, y0, x1, y1 = (int(e["box"][0]) - 2, int(e["box"][1]) - 2, int(e["box"][2]) + 3, int(e["box"][3]) + 3)
                inside = delta[max(0, y0):y1, max(0, x0):x1]
                self.assertGreater(int((inside > 60).sum()), 20, (w.doc.doc_id, e["text"]))
                outside[max(0, y0):y1, max(0, x0):x1] = False
            self.assertLess(float(delta[outside].max(initial=0)), 8.0, w.doc.doc_id)

    def test_transcript_holds_every_truth_field(self):
        from mirobody_gen.audit.readability import _norm

        for w in self.written:
            blob = _norm("\n".join(w.transcript))
            for row in w.doc.printed:
                for field in ("item_name", "item_value", "item_unit", "item_range"):
                    want = getattr(row, field)
                    if want:
                        self.assertIn(_norm(want), blob, (w.doc.doc_id, field, want))

    def test_corrections_and_dittos_are_recorded_as_written(self):
        seen = {"correction": 0, "ditto": 0}
        for w in self.written:
            struck = [e for e in w.sheet.log if e["role"] == "struck"]
            self.assertEqual(len(struck), len(w.corrections), w.doc.doc_id)
            for c, e in zip(w.corrections, struck):
                self.assertEqual(c["struck"], e["text"])
                self.assertNotEqual(c["struck"], c["written"])
                self.assertEqual(w.doc.printed[c["row"]].item_value, c["written"])
                self.assertIn("hand.correction", w.doc.printed[c["row"]].hazards)
                seen["correction"] += 1
            for d in w.dittos:
                reading = w.doc.readings[w.doc.printed[d["row"]].readings[0]]
                self.assertEqual(reading.observed, d["stands_for"])
                self.assertIn("hand.ditto", w.doc.printed[d["row"]].hazards)
                seen["ditto"] += 1
            self.assertFalse(any("〃" in r.item_value for r in w.doc.printed), w.doc.doc_id)
        self.assertTrue(all(seen.values()), seen)

    def test_every_handwritten_row_carries_the_written_hazard_and_known_hazards_only(self):
        known = {c["name"] for c in spec.hazards()["classes"]} | {c["name"] for c in spec.handwriting()["hazard_classes"]}
        for w in self.written:
            self.assertTrue(set(w.doc.hazards) <= known, set(w.doc.hazards) - known)
            for row in w.doc.printed:
                self.assertIn("hand.written", row.hazards, w.doc.doc_id)

    def test_glucose_is_plausible_and_post_meal_follows_hba1c(self):
        """Post-meal glucose sits above fasting on average and both stay in the meter's range."""
        diabetic = [p for p in self.people if p.archetype == "prediabetes_to_t2dm"] or self.people[:1]
        fasting, post = [], []
        for p in diabetic:
            for k in range(60):
                day = date(2024, 1, 1) + timedelta(days=7 * k)
                g = self.hw.glucose_day(SEED, p, day, ["breakfast", "lunch", "dinner"])
                fasting.append(g["fasting"])
                post += [g["breakfast"], g["lunch"], g["dinner"]]
        self.assertTrue(all(3.0 <= x <= 25.0 for x in fasting + post))
        self.assertGreater(np.mean(post), np.mean(fasting) + 0.5)

    def test_same_seed_same_pixels(self):
        a = self.hw.build_note(SEED, *self._first_clinic(), self.families["en"], "en", "t_det", True)
        b = self.hw.build_note(SEED, *self._first_clinic(), self.families["en"], "en", "t_det", True)
        self.assertEqual(a.sheet.render().tobytes(), b.sheet.render().tobytes())
        self.assertEqual(a.transcript, b.transcript)

    def _first_clinic(self):
        return next((q, e) for q in self.people for e in self.encounters[q.person_id] if e.exam_type == "clinic")


# ── Capture and legibility ───────────────────────────────────────────
@unittest.skipUnless(HAVE_RENDER, "needs Pillow and PyMuPDF")
class Capture(unittest.TestCase):
    def test_forward_follows_the_scene_geometry(self):
        """Dots on a page land where `forward` says after a rotated, oblique, downscaled phone photo."""
        from mirobody_gen.render import degrade, hand

        w, h = 1200, 1700
        points = [(200, 300), (1000, 260), (600, 900), (180, 1500), (1050, 1450)]
        page = Image.new("RGB", (w, h), (250, 250, 250))
        arr = np.asarray(page).copy()
        for x, y in points:
            arr[y - 6:y + 7, x - 6:x + 7] = 0
        page = Image.fromarray(arr)
        for scene in ("phone_oblique", "app_enhanced", "wechat_photo"):
            shot, ops = degrade.apply_scene([page], scene, "moderate", f"geom:{scene}", {0: (100, 200, 1100, 1550)})
            got = np.asarray(shot[0].convert("L"), dtype=np.float32)
            for (x, y), (fx, fy) in zip(points, hand.forward(points, ops, (w, h), shot[0].size)):
                r = 18
                patch = got[int(fy) - r:int(fy) + r + 1, int(fx) - r:int(fx) + r + 1]
                self.assertLess(float(patch.min()), 120, (scene, (x, y), (fx, fy)))

    def test_pages_clear_the_legibility_floor_after_capture(self):
        from mirobody_gen import handwriting

        tmp = pathlib.Path(tempfile.mkdtemp())
        try:
            for k, w in enumerate(written()["written"]):
                shot = handwriting.capture(w, tmp, f"x/{k}", random.Random(k))
                self.assertIsNotNone(shot, f"{w.doc.doc_id}: no capture option keeps every value legible")
                _, delivery, legible = shot
                self.assertGreaterEqual(legible["min_digit_px"], handwriting.MIN_DIGIT_PX)
                self.assertGreaterEqual(legible["min_contrast"], handwriting.MIN_CONTRAST)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ── Whole builds ──────────────────────────────────────────────────────
def _build(out: pathlib.Path, handwriting: bool) -> None:
    args = [sys.executable, "-m", "mirobody_gen.build", "--seed", str(BUILD_SEED), "--people", str(BUILD_PEOPLE),
            "--out", str(out), "--render"] + (["--handwriting"] if handwriting else [])
    subprocess.run(args, cwd=REPO, check=True, capture_output=True)


def _digests(root: pathlib.Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha1(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


@unittest.skipUnless(HAVE_RENDER, "needs Pillow and PyMuPDF")
class Builds(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp())
        cls.off, cls.on, cls.again = cls.tmp / "off", cls.tmp / "on", cls.tmp / "again"
        _build(cls.off, False)
        _build(cls.on, True)
        _build(cls.again, True)
        cls.records = [json.loads(line) for line in (cls.on / "files.jsonl").read_text(encoding="utf-8").splitlines()]
        cls.hand = [r for r in cls.records if "handwriting" in r]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_build_has_handwriting_to_isolate(self):
        self.assertTrue(self.hand)

    def test_printed_output_is_unchanged_and_handwriting_only_appends(self):
        off, on = _digests(self.off), _digests(self.on)
        for name, digest in off.items():
            if name == "files.jsonl":
                continue
            self.assertEqual(on.get(name), digest, name)
        before = (self.off / "files.jsonl").read_bytes()
        after = (self.on / "files.jsonl").read_bytes()
        self.assertTrue(after.startswith(before))
        added = [json.loads(line) for line in after[len(before):].decode("utf-8").splitlines()]
        self.assertTrue(added and all("handwriting" in r for r in added))
        self.assertEqual(set(on) - set(off) - {"files.jsonl"}, {r["file"] for r in added})

    def test_same_seed_is_byte_identical(self):
        self.assertEqual(_digests(self.on), _digests(self.again))

    def test_records_pass_the_readability_audit(self):
        from mirobody_gen.audit import readability

        self.assertEqual(readability.audit(self.on / "files.jsonl"), 0)

    def test_every_capture_passed_the_legibility_floor(self):
        from mirobody_gen import handwriting

        for r in self.hand:
            legible = r["handwriting"]["legibility"]
            self.assertGreaterEqual(legible["min_digit_px"], handwriting.MIN_DIGIT_PX, r["doc_id"])
            self.assertGreaterEqual(legible["min_contrast"], handwriting.MIN_CONTRAST, r["doc_id"])
            self.assertIn(r["tier"], ("T2", "T3"))
            if r["handwriting"]["tier"] == "H3":
                self.assertEqual(r["tier"], "T3")

    def test_images_carry_the_synthetic_mark(self):
        from mirobody_gen.render import degrade

        for r in self.hand:
            img = Image.open(self.on / r["file"])
            self.assertEqual(img.getexif().get(0x010E), degrade.SYNTHETIC_MARK)


if __name__ == "__main__":
    unittest.main()
