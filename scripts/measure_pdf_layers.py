"""Count how the reference set's PDFs are layered -> the observed shares of `pdf_layers` in delivery.json.

    python3 scripts/measure_pdf_layers.py            # print the counts
    python3 scripts/measure_pdf_layers.py --write    # update resources/delivery.json

Each PDF in `corpus/verified` is classified from its layers alone (`mirobody_gen.render.layers.classify`):
a born-digital document (`text`, or `figure` for text beside a large picture), an image-only scan, a
hybrid scan whose only live text is a header and footer off the image, a searchable scan under an
invisible OCR layer, or text over a full-page background image. Only the counts leave this script. They
become the shares of the `observed` choices; a class still at its hand-authored tier is printed, to be
promoted by hand once it is seen.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from mirobody_gen.render import layers  # noqa: E402

CORPUS = REPO / "corpus" / "verified"
DELIVERY = REPO / "mirobody_gen" / "resources" / "delivery.json"
#: Document class -> (group, choice) in delivery.json's pdf_layers
CHOICE = {"image_only": ("scanned", "scanpdf"), "hybrid": ("scanned", "hybridpdf"),
          "searchable": ("scanned", "ocrpdf"), "text": ("born_digital", "pdf"),
          "figure": ("born_digital", "pdf"), "background": ("born_digital", "background")}


def measure() -> collections.Counter:
    return collections.Counter(layers.classify(p.read_bytes()) for p in sorted(CORPUS.iterdir())
                               if p.suffix.lower() == ".pdf")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    counts = measure()
    print(f"{sum(counts.values())} PDFs: {dict(counts)}")
    shares = collections.Counter()
    for cls, n in counts.items():
        shares[CHOICE[cls]] += n
    data = json.loads(DELIVERY.read_text(encoding="utf-8"))
    for group, choices in data["pdf_layers"].items():
        if not isinstance(choices, list):
            continue
        for c in choices:
            n = shares[group, c["word"]]
            if c["tier"] == "observed":
                c["share"] = n
            elif n:
                print(f"{group}/{c['word']} is {c['tier']} but observed {n} times: promote it by hand")
    if args.write:
        DELIVERY.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"written {DELIVERY}")


if __name__ == "__main__":
    main()
