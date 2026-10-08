"""**Aggregate** shape statistics for image files in the real corpus -> `resources/numbers_images.json`.

    python3 scripts/measure_images.py            # print
    python3 scripts/measure_images.py --write    # write resources/numbers_images.json

Calibration targets for the image-degradation layer (`mirobody_gen/render/degrade.py`): how
large, how blurry, how dark, and how compressed a real uploaded photo/screenshot/scan is.
Only quantiles and counts are output, never any file's path, content or identifiable pixels --
the same standard as `resources/numbers.json` (docs/zh-CN/plan.md §4.2: aggregates may pass
the gate, instances may not).

What's measured, and why:

* Size and long edge: WeChat forwarding caps the long edge at 1280 (when the aspect ratio is
  <= 2), a screenshot is a phone's screen width (1080/1170/1242...), and a camera original is
  3000-4000. The long-edge histogram tells us directly how much of each provenance there is.
* JPEG quality estimate: back out an approximate IJG quality factor from the quantization
  table (the Q50 table's scaling ratio). WeChat runs about 70-85, camera originals 90+, and
  repeated forwarding pushes it lower.
* Grayscale/color: scans and photocopies are often grayscale; photos are color.
* Background brightness: the median of the four corners -- near 255 after scanning or app
  enhancement, 150-220 for a phone photo (desk, paper shadow).
* Sharpness: grayscale Laplacian variance (lower means blurrier), normalized to a 1600 long
  edge first, since a larger image is otherwise naturally "sharper."
* Skew: a Hough-line-style estimate of the dominant text-row angle (only the mode within +-10
  degrees is considered).
* EXIF: whether a camera make/model field is present (present in originals, absent from WeChat
  forwards and screenshots).
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib

import numpy as np
from PIL import Image, ImageFilter, ImageOps

REPO = pathlib.Path(__file__).resolve().parent.parent
RESOURCES = REPO / "mirobody_gen" / "resources"
CORPUS = REPO / "corpus" / "verified"          # the manually confirmed batch (the 619 files in library)

#: The IJG standard luminance quantization table (quality 50). Quality-factor estimate: q = 50/scale (scale = the ratio of table means).
_Q50 = np.array([
    16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55, 14, 13, 16, 24, 40, 57, 69, 56,
    14, 17, 22, 29, 51, 87, 80, 62, 18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
    49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99], dtype=np.float64)


def jpeg_quality(img: Image.Image) -> int | None:
    q = getattr(img, "quantization", None)
    if not q or 0 not in q:
        return None
    table = np.array(list(q[0]), dtype=np.float64)
    if table.size != 64:
        return None
    scale = table.sum() / _Q50.sum() * 100        # ratio to the Q50 table, as a percentage
    quality = 5000 / scale if scale > 100 else 100 - scale / 2
    return int(max(1, min(100, round(quality))))


def laplacian_var(gray: Image.Image) -> float:
    w, h = gray.size
    scale = 1600 / max(w, h)
    if scale < 1:
        gray = gray.resize((max(8, int(w * scale)), max(8, int(h * scale))), Image.BILINEAR)
    a = np.asarray(gray, dtype=np.float32)
    lap = (-4 * a + np.roll(a, 1, 0) + np.roll(a, -1, 0) + np.roll(a, 1, 1) + np.roll(a, -1, 1))[1:-1, 1:-1]
    return float(lap.var())


def corner_brightness(gray: Image.Image) -> float:
    a = np.asarray(gray, dtype=np.float32)
    h, w = a.shape
    m = max(4, min(h, w) // 40)
    return float(np.median(np.concatenate([a[:m, :m].ravel(), a[:m, -m:].ravel(), a[-m:, :m].ravel(), a[-m:, -m:].ravel()])))


def is_grayscale(img: Image.Image) -> bool:
    if img.mode in ("L", "1"):
        return True
    small = img.convert("RGB").resize((64, 64))
    a = np.asarray(small, dtype=np.float32)
    return float(np.abs(a[..., 0] - a[..., 1]).mean() + np.abs(a[..., 1] - a[..., 2]).mean()) < 4.0


def skew_estimate(gray: Image.Image) -> float | None:
    """Rough estimate of the text-row skew angle: rotate the edge image in 0.5-degree steps
    over +-10 degrees and take the angle with the greatest row-projection variance."""
    w, h = gray.size
    scale = 800 / max(w, h)
    g = gray.resize((max(8, int(w * scale)), max(8, int(h * scale))), Image.BILINEAR)
    edges = g.filter(ImageFilter.FIND_EDGES)
    best, best_var = None, -1.0
    for tenth in range(-100, 101, 5):
        angle = tenth / 10
        rot = edges.rotate(angle, resample=Image.BILINEAR, fillcolor=0)
        profile = np.asarray(rot, dtype=np.float32).sum(axis=1)
        var = float(profile.var())
        if var > best_var:
            best, best_var = angle, var
    return best


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * q))]


def measure() -> dict:
    files = sorted(p for p in CORPUS.rglob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    long_edges: list[int] = []
    aspects: list[float] = []
    qualities: list[int] = []
    gray_flag = 0
    corners: list[float] = []
    sharp: list[float] = []
    skews: list[float] = []
    exif_camera = 0
    long_edge_hist: collections.Counter = collections.Counter()
    ext = collections.Counter()
    landscape = 0
    n = 0
    for path in files:
        try:
            img = Image.open(path)
            img.load()
        except Exception:
            continue
        n += 1
        ext[path.suffix.lower()] += 1
        w, h = img.size
        le = max(w, h)
        long_edges.append(le)
        aspects.append(round(max(w, h) / max(1, min(w, h)), 2))
        landscape += w > h
        bucket = "≤1000" if le <= 1000 else "1001–1279" if le < 1280 else "1280" if le == 1280 else \
            "1281–1600" if le <= 1600 else "1601–2500" if le <= 2500 else "2501–3500" if le <= 3500 else ">3500"
        long_edge_hist[bucket] += 1
        if path.suffix.lower() in (".jpg", ".jpeg"):
            q = jpeg_quality(img)
            if q:
                qualities.append(q)
        try:
            exif = img.getexif()
            if exif.get(0x010F) or exif.get(0x0110):
                exif_camera += 1
        except Exception:
            pass
        gray_flag += is_grayscale(img)
        gray = ImageOps.exif_transpose(img).convert("L")
        corners.append(corner_brightness(gray))
        sharp.append(laplacian_var(gray))
        s = skew_estimate(gray)
        if s is not None:
            skews.append(abs(s))
    return {
        "_source": "format-token",
        "_note": "参考集图像文件的聚合形态：只含分位数与计数，由 scripts/measure_images.py 生成",
        "_provenance": {"script": "scripts/measure_images.py"},
        "_vocabulary_fields": [],
        "images": n,
        "extensions": dict(ext),
        "landscape_fraction": round(landscape / n, 3) if n else None,
        "long_edge": {"p10": pct(long_edges, .1), "p50": pct(long_edges, .5), "p90": pct(long_edges, .9),
                      "histogram": dict(sorted(long_edge_hist.items()))},
        "long_edge_exactly_1280_fraction": round(long_edge_hist["1280"] / n, 3) if n else None,
        "aspect_ratio": {"p10": pct(aspects, .1), "p50": pct(aspects, .5), "p90": pct(aspects, .9)},
        "jpeg_quality_estimate": {"n": len(qualities), "p10": pct(qualities, .1), "p50": pct(qualities, .5),
                                  "p90": pct(qualities, .9),
                                  "histogram": dict(sorted(collections.Counter(
                                      "<60" if q < 60 else "60–74" if q < 75 else "75–84" if q < 85
                                      else "85–94" if q < 95 else "≥95" for q in qualities).items()))},
        "grayscale_fraction": round(gray_flag / n, 3) if n else None,
        "exif_camera_fraction": round(exif_camera / n, 3) if n else None,
        "corner_brightness": {"p10": pct(corners, .1), "p50": pct(corners, .5), "p90": pct(corners, .9),
                              "white_background_fraction(>235)": round(sum(c > 235 for c in corners) / n, 3) if n else None},
        "sharpness_laplacian_var": {"p10": round(pct(sharp, .1), 1), "p50": round(pct(sharp, .5), 1),
                                    "p90": round(pct(sharp, .9), 1)} if sharp else None,
        "abs_skew_deg": {"p50": pct(skews, .5), "p90": pct(skews, .9),
                         "over_1deg_fraction": round(sum(s > 1.0 for s in skews) / len(skews), 3) if skews else None},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    if not CORPUS.is_dir():
        raise SystemExit("no corpus/ on this machine; this script only runs where the corpus is available")
    report = measure()
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if args.write:
        out = RESOURCES / "numbers_images.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"Wrote {out}")


if __name__ == "__main__":
    main()
