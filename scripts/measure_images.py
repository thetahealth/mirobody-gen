"""真实语料里图像文件的**聚合**形态统计 → `resources/numbers_images.json`。

    python3 scripts/measure_images.py            # 打印
    python3 scripts/measure_images.py --write    # 写 resources/numbers_images.json

给图像劣化层（`mirobody_gen/render/degrade.py`）当校准目标：真实上传的照片/截图/扫描件
是多大、多糊、多暗、经过了什么压缩。只输出分位数与计数，不输出任何一份文件的路径、
内容或可辨识的像素——与 `resources/numbers.json` 同一口径（docs/zh-CN/plan.md §4.2：聚合量可以过闸，实例不行）。

量了什么，为什么：

* 尺寸与长边：微信转发会把长边压到 1280（宽高比 ≤ 2 时），截图是手机屏幕宽度（1080/1170/1242…），
  相机原图是 3000–4000。长边直方图直接告诉我们三条来路各占多少；
* JPEG 质量估计：从量化表反推 IJG 质量因子的近似值（Q50 表的缩放比例）。微信约 70–85，
  相机原图 90+，多次转发会更低；
* 灰度/彩色：扫描件与复印件常是灰度，照片是彩色；
* 背景亮度：取四角中位数——扫描/App 增强后接近 255，手机照片在 150–220（桌面、纸的阴影）；
* 清晰度：灰度拉普拉斯方差（越低越糊），按长边归一到 1600 后算，否则大图天然更"清晰"；
* 倾斜：用霍夫直线粗估文本行的主方向偏角（只看 ±10° 内的众数）；
* EXIF：有没有相机厂商/机型字段（原图有，微信转发和截图没有）。
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
CORPUS = REPO / "corpus" / "verified"          # 人工确认过的那一批（对应 library 的 619 份）

#: IJG 标准亮度量化表（质量 50）。质量因子估计：q = 50/scale（scale = 表均值比）。
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
    scale = table.sum() / _Q50.sum() * 100        # 与 Q50 表的比例（百分数）
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
    """文本行倾斜角的粗估：对边缘图按 ±10° 逐 0.5° 旋转，取行投影方差最大的角度。"""
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
        raise SystemExit("本机没有 corpus/，这个脚本只能在有语料的机器上跑")
    report = measure()
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if args.write:
        out = RESOURCES / "numbers_images.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"已写出 {out}")


if __name__ == "__main__":
    main()
