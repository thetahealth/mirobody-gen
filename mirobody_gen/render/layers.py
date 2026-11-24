"""PDF pages that carry both an image and a text layer, and say different things through each.

A text-layer check alone cannot tell these apart, which is how a scanned lab page whose header and footer
are live text never reached OCR (thetahealth/mirobody#304):

* `hybridpdf` - each page is a scanned image of the printed page, and the hospital's PDF system has stamped
  its own header (institution, report title) and footer (page number, print time, report date) as live text in strips
  above and below it, off the image. The text layer is longer than a "this page has text" threshold yet
  holds no result, so the page must still be read from its pixels.
* `ocrpdf` - a searchable scan: the whole page is an image with the page's text laid invisibly on top of it.
  The text layer is exact.
* a born-digital page drawn over a full-page background image (`with_background`): the text layer is the
  page and the image is only paper.
"""

from __future__ import annotations

import io
import random
import unicodedata

from PIL import Image, ImageDraw

#: Height in points of the stamped strips above and below a hybrid page's scan
STRIP = 30.0


def _jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def _new_page(pdf, width: float, height: float):
    """A page with the renderer's own font (MuPDF's CJK font) embedded, so every printed glyph exists."""
    import fitz

    page = pdf.new_page(width=width, height=height)
    page.insert_font(fontname="cjk", fontbuffer=fitz.Font("cjk").buffer)
    return page


def _finish(pdf, title: str, kind: str) -> bytes:
    pdf.set_metadata({"title": title, "subject": "SYNTHETIC", "keywords": f"synthetic; mirobody-gen; {kind}",
                      "creator": "mirobody-gen", "producer": "mirobody-gen"})
    return pdf.tobytes(garbage=4, deflate=True, no_new_id=True)


def hybrid(scans: list[Image.Image], dpi: int, header: str, footer: str, title: str) -> tuple[bytes, list[str]]:
    """Each scan between a stamped header and footer of live text. `footer` may hold `{i}` and `{n}` for
    the page number. Returns the PDF and every live string, page by page."""
    import fitz

    out, live = fitz.open(), []
    for i, scan in enumerate(scans, start=1):
        width, height = scan.width * 72 / dpi, scan.height * 72 / dpi
        page = _new_page(out, width, height + 2 * STRIP)
        page.insert_image(fitz.Rect(0, STRIP, width, STRIP + height), stream=_jpeg(scan))
        font = fitz.Font("cjk")
        head, foot = _typeable(header, font), _typeable(footer.format(i=i, n=len(scans)), font)
        page.insert_text((24, STRIP * 0.6), head, fontname="cjk", fontsize=9)
        page.insert_text((24, height + STRIP * 1.6), foot, fontname="cjk", fontsize=7.5)
        live += [head, foot]
    return _finish(out, title, "hybrid scan"), live


def _typeable(text: str, font) -> str:
    """The text with each character the font lacks replaced by its compatibility form (a ligature by its
    letters); every other character, full-width punctuation included, stays as printed."""
    return "".join(ch if font.has_glyph(ord(ch)) else unicodedata.normalize("NFKC", ch) for ch in text)


def searchable(source: bytes, scans: list[Image.Image], title: str) -> bytes:
    """Each page a whole-page image with every span of the original laid invisibly over it."""
    import fitz

    out, font = fitz.open(), fitz.Font("cjk")
    with fitz.open("pdf", source) as doc:
        for page, scan in zip(doc, scans):
            new = _new_page(out, page.rect.width, page.rect.height)
            new.insert_image(new.rect, stream=_jpeg(scan))
            for block in page.get_text("dict")["blocks"]:
                for span in (s for line in block.get("lines", []) for s in line["spans"] if s["text"].strip()):
                    x0, _, x1, y1 = span["bbox"]
                    text = _typeable(span["text"], font)
                    # Fit the span's own width: the embedded font sets Latin wider than the original
                    size = span["size"] * min(1.0, (x1 - x0) / font.text_length(text, fontsize=span["size"]))
                    new.insert_text((x0, y1 - 0.2 * span["size"]), text, fontname="cjk", fontsize=size,
                                    render_mode=3)
    return _finish(out, title, "searchable scan")


def letterhead(width: int, height: int, seed: str) -> Image.Image:
    """A pale printed template: a tinted band across the top and a faint frame, nothing legible."""
    rng = random.Random(seed)
    tint = tuple(rng.randint(205, 235) for _ in range(3))
    img = Image.new("RGB", (width, height), (252, 251, 248))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, width, int(height * 0.06)), fill=tint)
    margin = int(width * 0.03)
    draw.rectangle((margin, margin, width - margin, height - margin), outline=tint, width=max(1, width // 400))
    return img


def with_background(source: bytes, seed: str) -> bytes:
    """The born-digital page with a full-page template image beneath its text."""
    import fitz

    with fitz.open("pdf", source) as doc:
        for i, page in enumerate(doc):
            bg = letterhead(int(page.rect.width * 2), int(page.rect.height * 2), f"{seed}:{i}")
            page.insert_image(page.rect, stream=_jpeg(bg), overlay=False)
        return doc.tobytes(garbage=4, deflate=True, no_new_id=True)


#: A page is a picture of a page when images cover at least this share of it
IMAGE_COVER = 0.5
#: Text a page needs before its layer counts (mirobody's own threshold, `MIN_PAGE_TEXT`)
MIN_TEXT = 40
#: The top and bottom share of a page where a hybrid scan's stamped header and footer sit
EDGE = 0.15


def page_layers(page) -> str:
    """What one PDF page is, read from its layers alone: `text` (a born-digital page), `image_only` (a scan
    with no text layer), `hybrid` (a scan with live text off the image, at least half of it in the header
    and footer strips), `figure` (text beside a large picture, mid-page: a born-digital page),
    `searchable` (a scan under an invisible OCR layer) or `background` (visible text over a full-page
    image)."""
    import fitz

    # Images and text come in the unrotated frame; the header and footer are where the page is shown
    shown = page.rotation_matrix
    boxes = [r * shown & page.rect for x in page.get_images(full=True) for r in page.get_image_rects(x[0])]
    if _cover(boxes, page.rect) < IMAGE_COVER:
        return "text"
    spans = [(s["type"], fitz.Rect(s["bbox"]) * shown, len(s["chars"])) for s in page.get_texttrace()]
    if sum(n for _, _, n in spans) < MIN_TEXT:
        return "image_only"
    over = [(kind, n) for kind, rect, n in spans if any(rect.intersects(b) for b in boxes)]
    if sum(n for kind, n in over if kind == 3) >= MIN_TEXT:     # render mode 3: invisible
        return "searchable"
    if sum(n for kind, n in over if kind != 3) >= MIN_TEXT:
        return "background"
    height, total = page.rect.height, sum(n for _, _, n in spans)
    edges = sum(n for _, rect, n in spans if rect.y1 < EDGE * height or rect.y0 > (1 - EDGE) * height)
    return "hybrid" if edges >= total / 2 else "figure"


def _cover(boxes: list, frame) -> float:
    """The share of `frame` the boxes cover, overlaps counted once (sampled on a 64 x 64 grid)."""
    import fitz

    n = 64
    points = [fitz.Point(frame.x0 + (i + 0.5) * frame.width / n, frame.y0 + (j + 0.5) * frame.height / n)
              for i in range(n) for j in range(n)]
    return sum(any(p in b for b in boxes) for p in points) / len(points)


def classify(data: bytes) -> str:
    """A PDF's class: `hybrid` when any page is one (a single such page loses its rows to a text-layer
    check), otherwise its most common page class; `empty` without pages."""
    import collections

    import fitz

    with fitz.open("pdf", data) as pdf:
        pages = collections.Counter(page_layers(p) for p in pdf)
    if not pages:
        return "empty"
    return "hybrid" if pages["hybrid"] else pages.most_common(1)[0][0]
