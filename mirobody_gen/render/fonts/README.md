# Handwriting fonts

The handwritten pages (`mirobody-gen build --render --handwriting`) are written with eight open-licensed
handwriting families from the [Google Fonts repository](https://github.com/google/fonts). This folder holds
**subsets** of them, not the fonts themselves: each keeps only the characters a handwritten page can carry
(digits, ASCII, the punctuation of dates and values, and for the Chinese faces the ~200 Han characters of the
wording in `resources/handwriting.json`). A Chinese face drops from 4–6 MB to about 150 KB, under the
repository's 1 MB file limit, and the build needs no network.

| id | Upstream family | Licence | Used for |
| --- | --- | --- | --- |
| `zh-kai` | Ma Shan Zheng | SIL OFL 1.1 | H1, regular script (kaishu) |
| `zh-xing` | Zhi Mang Xing | SIL OFL 1.1 | H2 and H3, running script |
| `zh-pen` | Long Cang | SIL OFL 1.1 | H2, hard-pen running hand |
| `zh-cao` | Liu Jian Mao Cao | SIL OFL 1.1 | H3, cursive script (caoshu) |
| `en-print` | Indie Flower | SIL OFL 1.1 | H1, rounded print |
| `en-pen` | Nanum Pen Script | SIL OFL 1.1 | H1 and H2, narrow felt-pen print |
| `en-casual` | Caveat (instanced at weight 450) | SIL OFL 1.1 | H2, casual joined hand |
| `en-cursive` | Homemade Apple | Apache 2.0 | H3, looped cursive |

`fonts.json` records, per face, the upstream path, its git blob id and sha256, the subset's file and sha256,
the characters it covers, and the licence file next to it (`LICENSE-<id>.txt`, the upstream licence text
verbatim). The google/fonts commit that last changed each upstream file is pinned in
`scripts/build_handwriting.py::FONTS`.

## Licence obligations, and how they are met

* **OFL 1.1.** A subset is a *Modified Version*. Each one is renamed (family `MG Hand …`, PostScript name
  `MGHand-…`), so no Reserved Font Name is used (Nanum Pen Script declares several), keeps the upstream
  copyright and licence name records, records what was changed in its description record, and ships with
  the OFL text. The fonts are bundled with software, not sold on their own.
* **Apache 2.0** (Homemade Apple). The licence text ships alongside; the modification (subset, rename) is
  recorded in the font's description record and in `fonts.json`.

## Rebuilding

The subsets are generated, never edited by hand. To change the characters a hand may write, or a face:

```bash
# the pinned upstream files, laid out as in google/fonts (a sparse checkout works)
git clone --filter=blob:none --sparse https://github.com/google/fonts gf && cd gf
git sparse-checkout set ofl/mashanzheng ofl/zhimangxing ofl/liujianmaocao ofl/longcang \
    ofl/caveat ofl/nanumpenscript ofl/indieflower apache/homemadeapple && cd ..
pip install fonttools          # needed only here, not to build a corpus
python scripts/build_handwriting.py --fonts gf --write
```

The script refuses any upstream file whose size or sha256 differs from the pin in
`scripts/build_handwriting.py::FONTS`, and its output is byte-identical from run to run.
`tests/test_handwriting.py` checks the subsets against `fonts.json` and that every face covers every
character its tier can be asked to write.
