"""The image tiers: every scene runs, is deterministic, keeps the page in frame, and is declared."""

from __future__ import annotations

import pathlib
import sys

import numpy as np
from PIL import Image

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mirobody_gen import spec  # noqa: E402
from mirobody_gen.render import degrade  # noqa: E402


def _page(w: int = 300, h: int = 420) -> Image.Image:
    img = Image.new("RGB", (w, h), (250, 250, 250))
    arr = np.asarray(img).copy()
    arr[40:44, 30:270] = 20            # a "rule"
    for y in range(80, 380, 24):
        arr[y:y + 6, 30:200] = 30      # "text lines"
    return Image.fromarray(arr, "RGB")


def test_every_scene_runs_and_is_deterministic():
    pages = [_page(), _page()]
    for scene in degrade.SCENES:
        a, log = degrade.apply_scene(pages, scene, "moderate", "doc", {0: (30, 40, 270, 380), 1: (30, 40, 270, 380)})
        b, _ = degrade.apply_scene(pages, scene, "moderate", "doc", {0: (30, 40, 270, 380), 1: (30, 40, 270, 380)})
        assert len(a) == 2 and [o["op"] for o in log] == [op for op, _ in degrade.SCENES[scene]["stages"]]
        assert degrade.encode(a, "jpg", "t") == degrade.encode(b, "jpg", "t"), scene


def test_scenes_and_ops_are_declared_vocabulary():
    vocab = spec.vocab()
    assert set(degrade.SCENES) <= set(vocab["scenes"])
    assert set(degrade.OPS) <= set(vocab["ops"])
    tiers = {s["tier"] for s in degrade.SCENES.values()}
    assert tiers <= set(vocab["tiers"])
    weights = spec.delivery()["scene_weights"]
    assert {n for t in weights.values() for n in t} == set(degrade.SCENES), "every scene needs a weight and vice versa"


def test_finger_stays_in_the_margin():
    """The occluder may only paint outside the content box, so no truth can be covered."""
    arr = degrade._to_np(_page())
    rng = np.random.RandomState(3)
    for _ in range(20):
        out = degrade.finger_occluder(arr.copy(), rng, strength=0.9, margin=(30, 40, 270, 380))
        out = out[0] if isinstance(out, tuple) else out
        assert np.array_equal(out[40:380, 30:270], arr[40:380, 30:270])


def test_wechat_scale_rule():
    small = degrade._to_np(_page(800, 1100))
    assert degrade.wechat_scale(small, None, long_edge=1280).shape == small.shape
    big = degrade._to_np(_page(2000, 2800))
    out = degrade.wechat_scale(big, None, long_edge=1280)
    assert max(out.shape[:2]) == 1280


def test_encoded_images_carry_the_synthetic_mark():
    pages, _ = degrade.apply_scene([_page()], "clean_photo", "mild", "doc")
    jpg = degrade.encode(pages, "jpg", "t")
    import io

    img = Image.open(io.BytesIO(jpg))
    assert degrade.SYNTHETIC_MARK == img.getexif().get(0x010E)
    png = degrade.encode(pages, "png", "t")
    assert degrade.SYNTHETIC_MARK.encode() in png
