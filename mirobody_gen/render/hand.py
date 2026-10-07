"""Handwriting: a pen that writes strings onto paper the way a hand does, and the paper it writes on.

手写：一支笔，把字符串按人手的样子写到纸上；以及它写在什么纸上。

A font gives a hand the shape of its letters. Everything that makes a line look written rather than
typeset is added here, glyph by glyph, from a seeded stream: the baseline wanders and the line drifts
off the rule, each glyph has its own size, rotation and spacing, the whole hand leans by a slant, a
small elastic warp makes two 7s on the same page different bitmaps, pen pressure darkens and fades
strokes, and the ink is a ballpoint colour multiplied into the paper. Glyphs are drawn at twice the
page resolution and reduced once, so a stroke has real anti-aliasing at every angle.

**The page keeps a log of everything the pen wrote** (`Sheet.log`: the string, its role, the printed
row it belongs to and the box it landed in). Truth rows and the transcript are written from what the
document asked the pen to write, and `tests/test_handwriting.py` checks every truth value against this
log and against the ink inside its box, so the record and the pixels cannot drift apart.

The faces are subsets of open-licensed handwriting fonts (`render/fonts/fonts.json`, built by
`scripts/build_handwriting.py`). A character outside a face's subset is never drawn as a blank box:
the pen falls back to its Latin face, and raises if neither has it.

Legibility is measured, not assumed (`ink_contrast`): the page is rendered once with and once without
the handwriting, both go through the same capture scene from the same seed, and the difference is the
ink as the camera or scanner left it.
"""

from __future__ import annotations

import dataclasses
import functools
import io
import json
import math
import pathlib
import random
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

FONT_DIR = pathlib.Path(__file__).resolve().parent / "fonts"
#: Supersampling factor: glyphs are drawn at SS× the page resolution and reduced once.
SS = 2


# ── Faces ─────────────────────────────────────────────────────────────
@functools.lru_cache(maxsize=None)
def faces() -> dict[str, dict]:
    """Face id → its entry in fonts.json (file, upstream identity, licence, coverage)."""
    return json.loads((FONT_DIR / "fonts.json").read_text(encoding="utf-8"))["fonts"]


@functools.lru_cache(maxsize=None)
def coverage(face_id: str) -> frozenset[str]:
    return frozenset(faces()[face_id]["coverage"])


@functools.lru_cache(maxsize=2048)
def _face(face_id: str, px: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / faces()[face_id]["file"]), px)


@functools.lru_cache(maxsize=None)
def digit_ratio(face_id: str) -> float:
    """A digit's ink height as a fraction of the em. Faces differ by almost 2× here (Nanum Pen's digits are
    small, Homemade Apple's tall), so a pen is sized by its digits, the thing a reader must not misread."""
    face = _face(face_id, 400)
    heights = [face.getbbox(d, anchor="ls")[3] - face.getbbox(d, anchor="ls")[1] for d in "0123456789"]
    return float(np.median(heights)) / 400


@functools.lru_cache(maxsize=None)
def cap_ratio(face_id: str) -> float:
    """Capital-letter height as a fraction of the em (Latin faces; the Han block for Chinese faces)."""
    face = _face(face_id, 400)
    probe = "BDEHKLT" if faces()[face_id]["script"] == "en" else "日田国"
    probe = [ch for ch in probe if ch in coverage(face_id)] or ["0"]
    heights = [face.getbbox(ch, anchor="ls")[3] - face.getbbox(ch, anchor="ls")[1] for ch in probe]
    return float(np.median(heights)) / 400


@functools.lru_cache(maxsize=None)
def printed_face_buffer() -> bytes:
    """The generator's printed typeface (PyMuPDF's Droid Sans Fallback), for paper furniture."""
    import fitz

    return fitz.Font("cjk").buffer


@functools.lru_cache(maxsize=64)
def printed_face(px: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(io.BytesIO(printed_face_buffer()), px)


@functools.lru_cache(maxsize=8192)
def _glyph_master(face_id: str, px: int, ch: str) -> tuple[Image.Image, tuple[int, int], float]:
    """(glyph mask at px, its baseline-left origin inside the mask, advance)."""
    face = _face(face_id, px)
    left, top, right, bottom = face.getbbox(ch, anchor="ls")
    pad = max(6, int(px * 0.35))
    img = Image.new("L", (right - left + 2 * pad, bottom - top + 2 * pad), 0)
    ImageDraw.Draw(img).text((pad - left, pad - top), ch, font=face, fill=255, anchor="ls")
    return img, (pad - left, pad - top), face.getlength(ch)


# ── The pen ───────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Pen:
    face: str                      # the hand's own face
    latin: str | None              # face for characters the own face lacks
    digit_px: float                # digit height on the page, pixels
    slant: float                   # shear: x shifts by slant × height above the baseline
    rotate_sd: float               # per-glyph rotation, degrees
    baseline_sd: float             # per-glyph baseline wander, fraction of digit height
    size_sd: float                 # per-glyph size wander (log scale)
    spacing_sd: float              # per-glyph advance wander, fraction
    drift_sd: float                # per-line slope, degrees
    warp: float                    # elastic warp, fraction of the em
    weight: int                    # -1 finer stroke, 0, +1 heavier
    ink: tuple[int, int, int]
    pressure: float                # darkness variation, 0 (even) to ~0.35

    def sized(self, factor: float) -> "Pen":
        return dataclasses.replace(self, digit_px=self.digit_px * factor)

    def face_for(self, ch: str) -> str:
        if ch in coverage(self.face):
            return self.face
        if self.latin and ch in coverage(self.latin):
            return self.latin
        raise ValueError(f"no face of this pen has {ch!r} (U+{ord(ch):04X}); add it to the inventory in "
                         f"scripts/build_handwriting.py and rebuild the subsets")

    def em_px(self, face_id: str) -> float:
        """The em that gives this face digits `digit_px` tall, unless its capitals would then stand more
        than 1.5 digit heights tall (Homemade Apple's loops would swallow the next column): then the
        capitals set the size and the digits come out a little smaller."""
        ratio = digit_ratio(face_id)
        if faces()[face_id]["script"] == "en":
            ratio = max(ratio, cap_ratio(face_id) / 1.5)
        return self.digit_px / ratio


def make_pen(rng: random.Random, tier: dict, face: str, latin: str | None, ink: tuple[int, int, int],
             digit_px: float) -> Pen:
    j = tier["jitter"]
    lo, hi = j["slant_deg"]
    return Pen(face=face, latin=latin, digit_px=digit_px * rng.uniform(0.94, 1.06),
               slant=math.tan(math.radians(rng.uniform(lo, hi))), rotate_sd=j["rotate_deg"],
               baseline_sd=j["baseline"], size_sd=j["size"], spacing_sd=j["spacing"], drift_sd=j["drift_deg"],
               warp=j["warp"], weight=rng.choice(faces()[face]["stroke"]), ink=ink, pressure=rng.uniform(0.08, 0.2))


# ── Glyph geometry ────────────────────────────────────────────────────
def _mesh_warp(img: Image.Image, amp: float, rng: random.Random) -> Image.Image:
    """A small continuous elastic warp: a 3×3 mesh whose four inner vertices move by ~amp pixels."""
    if amp <= 0.2:
        return img
    w, h = img.size
    xs = [0, w // 3, 2 * w // 3, w]
    ys = [0, h // 3, 2 * h // 3, h]
    disp = {(i, j): ((rng.gauss(0, amp), rng.gauss(0, amp)) if 0 < i < 3 and 0 < j < 3 else (0.0, 0.0))
            for i in range(4) for j in range(4)}
    mesh = []
    for i in range(3):
        for j in range(3):
            corners = [(i, j), (i, j + 1), (i + 1, j + 1), (i + 1, j)]     # NW, SW, SE, NE
            quad = []
            for a, b in corners:
                dx, dy = disp[(a, b)]
                quad += [xs[a] + dx, ys[b] + dy]
            mesh.append(((xs[i], ys[j], xs[i + 1], ys[j + 1]), quad))
    return img.transform(img.size, Image.MESH, mesh, resample=Image.BICUBIC)


def _affine(img: Image.Image, origin: tuple[float, float], angle_deg: float, shear: float
            ) -> tuple[Image.Image, tuple[float, float]]:
    """Rotate and shear a glyph about its baseline origin. Returns the new mask and where the origin went."""
    a = math.radians(angle_deg)
    # forward map on coordinates relative to the origin (y grows downwards): shear leans the top right
    m = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]]) @ np.array([[1.0, -shear], [0.0, 1.0]])
    ox, oy = origin
    w, h = img.size
    corners = np.array([[-ox, -oy], [w - ox, -oy], [w - ox, h - oy], [-ox, h - oy]]) @ m.T
    x0, y0 = np.floor(corners.min(axis=0)) - 1
    x1, y1 = np.ceil(corners.max(axis=0)) + 1
    out_w, out_h = int(x1 - x0), int(y1 - y0)
    new_origin = (-x0, -y0)
    inv = np.linalg.inv(m)
    c = np.array([ox, oy]) - inv @ np.array(new_origin)
    data = (inv[0, 0], inv[0, 1], c[0], inv[1, 0], inv[1, 1], c[1])
    return img.transform((out_w, out_h), Image.AFFINE, data, resample=Image.BICUBIC), new_origin


def _pressure(img: Image.Image, amount: float, rng: random.Random, base: float) -> Image.Image:
    """Pen pressure: the whole glyph a little lighter or darker, and a ramp across it (a ballpoint loses
    ink on the upstroke)."""
    arr = np.asarray(img, dtype=np.float32)
    h, w = arr.shape
    angle = rng.uniform(0, 2 * math.pi)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    ramp = ((xx - w / 2) * math.cos(angle) + (yy - h / 2) * math.sin(angle)) / max(w, h)
    field = base * (1.0 - amount * 0.4 * (ramp + 0.5).clip(0, 1))
    return Image.fromarray(np.clip(arr * field, 0, 255).astype(np.uint8))


# ── The page ──────────────────────────────────────────────────────────
class Sheet:
    """Paper plus everything a pen put on it. Coordinates are page pixels (the paper's own resolution)."""

    def __init__(self, paper: Image.Image, seed_text: str):
        self.paper = paper.convert("RGB")
        self.width, self.height = self.paper.size
        self.rng = random.Random(f"sheet:{seed_text}")
        #: (ink, holds truth values) → coverage mask at SS×. Values are kept apart so the page can be
        #: rendered without them, which is how legibility is measured after capture.
        self.layers: dict[tuple[tuple[int, int, int], bool], Image.Image] = {}
        self.log: list[dict] = []

    def _layer(self, ink: tuple[int, int, int], value: bool = False) -> Image.Image:
        if (ink, value) not in self.layers:
            self.layers[(ink, value)] = Image.new("L", (self.width * SS, self.height * SS), 0)
        return self.layers[(ink, value)]

    def _stamp(self, layer: Image.Image, glyph: Image.Image, x: int, y: int) -> None:
        box = (x, y, x + glyph.width, y + glyph.height)
        layer.paste(ImageChops.lighter(layer.crop(box), glyph), box)

    def measure(self, text: str, pen: Pen) -> float:
        """Approximate width of `text` in page pixels (no jitter); for layout before writing."""
        width = 0.0
        for ch in text:
            if ch == " ":
                width += pen.digit_px * 0.55
                continue
            if ch == "〃":                  # drawn as two strokes by `ditto`, not taken from a face
                width += pen.digit_px * 0.9
                continue
            face_id = pen.face_for(ch)
            px = max(8, int(round(pen.em_px(face_id) * SS)))
            advance = _glyph_master(face_id, px, ch)[2] / SS
            width += (max(advance, pen.digit_px * 0.5) if ch.isdigit() else advance) + self._gap(ch, pen)
        return width

    @staticmethod
    def _gap(ch: str, pen: Pen) -> float:
        # Han characters written by hand stand apart; digits need daylight between them (two 1s that touch
        # read as one); the letters of a joined hand touch.
        if ord(ch) >= 0x2E80:
            return pen.digit_px * 0.12
        return pen.digit_px * 0.07 if ch.isdigit() else 0.0

    @staticmethod
    def _advance(ch: str, advance: float, pen: Pen, rng: random.Random) -> float:
        jitter = pen.spacing_sd * (1.0 if ch.isdigit() or ord(ch) >= 0x2E80 else 0.5)
        out = advance * max(0.75, 1 + rng.gauss(0, jitter))
        if ch.isdigit():
            out = max(out, pen.digit_px * 0.5)
        return out

    def write(self, text: str, x: float, baseline: float, pen: Pen, *, role: str = "text",
              row: int | None = None, field: str | None = None) -> tuple[float, float, float, float]:
        """Write `text` starting at (x, baseline). Returns the ink box (x0, y0, x1, y1) in page pixels."""
        rng = self.rng
        layer = self._layer(pen.ink, role == "value")
        slope = math.tan(math.radians(rng.gauss(0, pen.drift_sd)))
        wave_amp = pen.baseline_sd * pen.digit_px * 0.7
        wave_len = rng.uniform(5, 10) * pen.digit_px
        phase = rng.uniform(0, 2 * math.pi)
        line_dark = rng.uniform(0.9, 1.0)
        cx = x
        x0 = y0 = math.inf
        x1 = y1 = -math.inf
        digit_heights: list[float] = []
        for ch in text:
            if ch == " ":
                cx += pen.digit_px * 0.55 * max(0.4, 1 + rng.gauss(0, pen.spacing_sd * 2))
                continue
            face_id = pen.face_for(ch)
            scale = math.exp(rng.gauss(0, pen.size_sd))
            px = max(8, int(round(pen.em_px(face_id) * scale * SS)))
            master, origin, advance = _glyph_master(face_id, px, ch)
            glyph = master
            if pen.weight > 0:
                glyph = glyph.filter(ImageFilter.MaxFilter(3))
            elif pen.weight < 0 and px > 70:
                glyph = glyph.filter(ImageFilter.MinFilter(3))
            glyph = _mesh_warp(glyph, pen.warp * px, rng)
            glyph, origin = _affine(glyph, origin, rng.gauss(0, pen.rotate_sd), pen.slant + rng.gauss(0, 0.03))
            dark = line_dark * (1 - pen.pressure * 0.35 * (1 + math.sin(phase + 2 * math.pi * (cx - x) / wave_len)) / 2)
            glyph = _pressure(glyph, pen.pressure, rng, dark)
            dy = (slope * (cx - x) + wave_amp * math.sin(phase + 2 * math.pi * (cx - x) / wave_len)
                  + rng.gauss(0, pen.baseline_sd * pen.digit_px))
            gx = int(round(cx * SS - origin[0]))
            gy = int(round((baseline + dy) * SS - origin[1]))
            self._stamp(layer, glyph, gx, gy)
            ink = glyph.getbbox()
            if ink:
                bx0, by0, bx1, by1 = (gx + ink[0]) / SS, (gy + ink[1]) / SS, (gx + ink[2]) / SS, (gy + ink[3]) / SS
                x0, y0, x1, y1 = min(x0, bx0), min(y0, by0), max(x1, bx1), max(y1, by1)
                if ch.isdigit():
                    digit_heights.append(by1 - by0)
            cx += self._advance(ch, advance / SS, pen, rng) + self._gap(ch, pen)
        box = (round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)) if x0 < math.inf else \
            (x, baseline, x, baseline)
        self.log.append({"text": text, "role": role, "row": row, "field": field, "box": list(box),
                         "digit_px": round(float(np.median(digit_heights)), 1) if digit_heights else None,
                         "end_x": round(cx, 1)})
        return box

    def stroke(self, points: list[tuple[float, float]], pen: Pen, width: float | None = None,
               role: str = "mark", opacity: float = 0.95) -> None:
        """A pen line through `points` (page pixels): ditto marks, strike-throughs, ruled columns, scribbles."""
        layer = self._layer(pen.ink)
        w = width if width is not None else max(1.6, pen.digit_px * 0.075)
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        x0, y0 = int((min(xs) - w - 2) * SS), int((min(ys) - w - 2) * SS)
        x1, y1 = int((max(xs) + w + 3) * SS), int((max(ys) + w + 3) * SS)
        tmp = Image.new("L", (x1 - x0, y1 - y0), 0)
        ImageDraw.Draw(tmp).line([(px * SS - x0, py * SS - y0) for px, py in points], fill=int(255 * opacity),
                                 width=max(1, int(round(w * SS))), joint="curve")
        self._stamp(layer, tmp, x0, y0)
        self.log.append({"text": "", "role": role, "row": None, "field": None,
                         "box": [round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1)],
                         "digit_px": None, "end_x": None})

    def strike(self, box: tuple[float, float, float, float], pen: Pen) -> None:
        """Cross a written value out: one or two strokes through it, or a tight zigzag."""
        rng = self.rng
        x0, y0, x1, y1 = box
        mid = (y0 + y1) / 2
        style = rng.choice(["line", "double", "zigzag"])
        if style == "zigzag":
            n = max(3, int((x1 - x0) / max(6.0, pen.digit_px * 0.5)))
            pts = [(x0 - 2 + (x1 - x0 + 4) * i / n, mid + (y1 - y0) * 0.32 * (1 if i % 2 else -1)) for i in range(n + 1)]
            self.stroke(pts, pen, role="strike")
            return
        for k in range(2 if style == "double" else 1):
            off = (k - 0.5) * (y1 - y0) * 0.22 if style == "double" else rng.uniform(-0.1, 0.1) * (y1 - y0)
            pts = [(x0 - rng.uniform(2, 6), mid + off + rng.uniform(-2, 2)),
                   ((x0 + x1) / 2, mid + off + rng.uniform(-2, 2)),
                   (x1 + rng.uniform(2, 6), mid + off + rng.uniform(-3, 3))]
            self.stroke(pts, pen, role="strike")

    def ditto(self, x: float, baseline: float, pen: Pen, row: int | None = None, field: str | None = None) -> None:
        """A ditto mark (〃): two short slanted strokes, drawn rather than typeset (few faces have the glyph)."""
        rng = self.rng
        h = pen.digit_px * rng.uniform(0.55, 0.7)
        top = baseline - pen.digit_px * 0.95
        for k in range(2):
            sx = x + k * pen.digit_px * 0.42 + rng.uniform(-1.5, 1.5)
            self.stroke([(sx + h * 0.22, top), (sx, top + h)], pen, role="ditto")
        self.log[-1].update({"text": "〃", "row": row, "field": field})

    def scribble(self, box: tuple[float, float, float, float], pen: Pen) -> None:
        """A signature-like scribble. No letters: a signature would be a name."""
        rng = self.rng
        x0, y0, x1, y1 = box
        loops = rng.randint(3, 6)
        f1, f2 = rng.uniform(0.8, 1.2), rng.uniform(1.7, 2.6)
        a1, a2, p1, p2 = rng.uniform(0.5, 0.8), rng.uniform(0.15, 0.35), rng.uniform(0, 6.3), rng.uniform(0, 6.3)
        pts = []
        n = 90
        for i in range(n):
            t = i / (n - 1)
            phase = 2 * math.pi * loops * t
            # a forward drift with loops: x doubles back on itself where the pen turns
            x = x0 + (x1 - x0) * t + (x1 - x0) / loops * 0.28 * math.cos(phase * f1 + p1)
            y = (y0 + y1) / 2 + (y1 - y0) / 2 * (a1 * math.sin(phase * f1 + p1) + a2 * math.sin(phase * f2 + p2))
            pts.append((x, y))
        self.stroke(pts, pen, role="signature", width=max(1.3, pen.digit_px * 0.055))

    def ruled_column(self, x: float, y_top: float, y_bottom: float, pen: Pen) -> None:
        """A column line ruled by hand along a straight edge: straight, slightly off vertical, lighter."""
        tilt = self.rng.uniform(-0.006, 0.006) * (y_bottom - y_top)
        self.stroke([(x, y_top), (x + tilt, y_bottom)], pen, width=max(1.2, pen.digit_px * 0.05), role="rule",
                    opacity=0.7)

    def render(self, values: bool = True) -> Image.Image:
        """Paper with the ink multiplied in (ballpoint and gel ink absorb light rather than cover it).
        `values=False` leaves the truth values out: everything else on the page is identical."""
        out = np.asarray(self.paper, dtype=np.float32)
        for (ink, is_value), layer in self.layers.items():
            if is_value and not values:
                continue
            alpha = np.asarray(layer.resize((self.width, self.height), Image.LANCZOS), dtype=np.float32) / 255.0
            alpha = alpha.clip(0, 1)[..., None] * 0.96
            absorb = 1.0 - np.array(ink, dtype=np.float32) / 255.0
            out = out * (1.0 - alpha * absorb)
        return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))

    def content_box(self, pad: float = 10) -> tuple[int, int, int, int]:
        """Everything written, for the capture scene's margin rule (a finger may only cover what is outside)."""
        boxes = [e["box"] for e in self.log]
        if not boxes:
            return (0, 0, self.width, self.height)
        return (int(max(0, min(b[0] for b in boxes) - pad)), int(max(0, min(b[1] for b in boxes) - pad)),
                int(min(self.width, max(b[2] for b in boxes) + pad)), int(min(self.height, max(b[3] for b in boxes) + pad)))


# ── Paper ─────────────────────────────────────────────────────────────
@dataclass
class Ruling:
    rules: list[float]          # baselines of the ruled lines, page pixels
    left: float                 # where writing starts
    right: float                # where writing must stop
    spacing: float              # line pitch, pixels


def mm(value: float, dpi: int) -> float:
    return value * dpi / 25.4


def ruled_paper(rng: random.Random, spec: dict, dpi: int, banner: str | None,
                header: list[tuple[str, float | None, float, float]] = ()) -> tuple[Image.Image, Ruling]:
    """A ruled page: tinted stock, blue rules, an optional red margin line, printed furniture.
    `header` is printed text: (string, x_mm or None to centre it, baseline_mm, size_pt)."""
    w_mm, h_mm = rng.choice(spec["sizes_mm"])
    width, height = int(round(mm(w_mm, dpi))), int(round(mm(h_mm, dpi)))
    tint = tuple(int(c + rng.uniform(-2, 2)) for c in rng.choice(spec["tints"]))
    img = Image.new("RGB", (width, height), tint)
    draw = ImageDraw.Draw(img)
    pitch = mm(rng.uniform(*spec["rule_mm"]), dpi)
    top = mm(rng.uniform(*spec["top_mm"]), dpi)
    margin = mm(rng.uniform(*spec["margin_mm"]), dpi)
    rule_rgb = tuple(rng.choice(spec["rule_rgb"]))
    rules = []
    y = top
    while y < height - mm(12, dpi):
        draw.line([(0, y), (width, y)], fill=rule_rgb, width=max(1, int(round(dpi / 110))))
        rules.append(y)
        y += pitch
    if spec["margin_rgb"]:
        draw.line([(margin, 0), (margin, height)], fill=tuple(rng.choice(spec["margin_rgb"])),
                  width=max(1, int(round(dpi / 120))))
    for text, x_mm, base_mm, size_pt in header:
        font = printed_face(max(8, int(round(size_pt * dpi / 72))))
        x = (width - font.getlength(text)) / 2 if x_mm is None else mm(x_mm, dpi)
        draw.text((x, mm(base_mm, dpi)), text, font=font, fill=(70, 74, 84), anchor="ls")
    if banner:
        font = printed_face(max(8, int(round(6 * dpi / 72))))
        draw.text((mm(8, dpi), mm(5.5, dpi)), banner, font=font, fill=(150, 150, 150), anchor="ls")
    return img, Ruling(rules=rules, left=margin + mm(2.5, dpi), right=width - mm(8, dpi), spacing=pitch)


# ── Legibility ────────────────────────────────────────────────────────
#: Capture operators that move pixels in ways `forward` does not replay. Every scene the handwriting
#: tiers use moves pixels only by `rotation` and `perspective` and resizes only at the end
#: (`downscale`, `wechat_scale`); `tests/test_handwriting.py` holds them to that.
UNMODELLED = {"rotate_quarter", "page_curl", "phone_frame", "desktop_frame"}


def forward(points: list[tuple[float, float]], ops: list[dict], page_size: tuple[int, int],
            out_size: tuple[int, int]) -> list[tuple[float, float]] | None:
    """Where page points land in the captured image, replaying the scene's logged geometry.
    None if the scene moved pixels in a way this does not model (a page curl, a quarter turn, a frame)."""
    from .degrade import _perspective_coeffs

    w, h = page_size
    pts = np.array(points, dtype=np.float64)
    for op in ops:
        name = op["op"]
        if name == "rotation":
            # Image.rotate turns counter-clockwise about the centre (y grows downwards)
            a = math.radians(op["angle"])
            x, y = pts[:, 0] - w / 2, pts[:, 1] - h / 2
            pts = np.stack([w / 2 + x * math.cos(a) + y * math.sin(a), h / 2 - x * math.sin(a) + y * math.cos(a)], 1)
        elif name == "perspective":
            c = _perspective_coeffs([tuple(p) for p in op["corners"]], [(0, 0), (w, 0), (w, h), (0, h)])
            den = c[6] * pts[:, 0] + c[7] * pts[:, 1] + 1
            pts = np.stack([(c[0] * pts[:, 0] + c[1] * pts[:, 1] + c[2]) / den,
                            (c[3] * pts[:, 0] + c[4] * pts[:, 1] + c[5]) / den], 1)
        elif name in UNMODELLED:
            return None
        # every other operator is photometric, or a resize at the end of the chain
    sx, sy = out_size[0] / w, out_size[1] / h
    return [(float(x * sx), float(y * sy)) for x, y in pts]


def value_legibility(shot: Image.Image, bare: Image.Image, values: list[dict], ops: list[dict],
                     page_size: tuple[int, int]) -> list[dict] | None:
    """Per written value, after capture: its digit height in delivered pixels and how dark its strokes
    are against the paper right there (90th percentile of the luminance the ink removes, 0–255).

    `shot` and `bare` are the same page with and without the values, through the same scene from the
    same seed, so lighting, blur, noise and geometry cancel and the difference is the values' ink."""
    delta = np.asarray(bare.convert("L"), dtype=np.float32) - np.asarray(shot.convert("L"), dtype=np.float32)
    out = []
    for v in values:
        x0, y0, x1, y1 = v["box"]
        corners = forward([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], ops, page_size, shot.size)
        if corners is None:
            return None
        xs, ys = [c[0] for c in corners], [c[1] for c in corners]
        bx0, by0 = max(0, int(min(xs)) - 2), max(0, int(min(ys)) - 2)
        bx1, by1 = min(shot.width, int(max(xs)) + 3), min(shot.height, int(max(ys)) + 3)
        patch = delta[by0:by1, bx0:bx1]
        ink = patch[patch > 12]
        height = (max(ys) - min(ys)) / max(y1 - y0, 1e-6)
        out.append({"row": v["row"], "digit_px": round((v["digit_px"] or 0) * height, 1),
                    "contrast": round(float(np.percentile(ink, 90)), 1) if ink.size >= 12 else 0.0})
    return out
