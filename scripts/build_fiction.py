"""Build `resources/fiction.json`: the sole source of person names, place names and
institution names the renderer prints on a document.

    python3 scripts/build_fiction.py            # print the statistics
    python3 scripts/build_fiction.py --write    # write resources/fiction.json

## Why a separate fictional pool

A lab slip prints a patient name, ordering physician, technician, reviewer and hospital
name. These fields are checked against the privacy gate's **allowlist** (`audit/privacy.py`,
check 2): a printed name must belong to this pool, rather than being waved through just
because it "doesn't look like a real name." An allowlist has to be auditable, so it must be a
finite list on disk, not something assembled on the fly at runtime.

## How it's built

* Surname and given-name characters are **public knowledge** (common surnames, common
  name characters), hand-written below.
* A fixed seed **samples** a subset of the combination space rather than enumerating it --
  enumeration would mean a common name's absence from the pool reveals that it appeared in
  the real corpus (see below). After sampling, most combinations are simply not in the pool
  to begin with, so an absence carries no information.
* Every candidate is substring-matched against the real corpus's extracted text, and **any
  match is dropped**; institution names also go through the same 12-character n-gram replay
  check used by the privacy gate (exempting public type suffixes like "General Hospital").
  A 2026-09-23 run found a fabricated place name's trailing characters plus "Family Clinic"
  collided with a real clinic's name in the corpus. This step only removes candidates, never
  adds any -- no real-corpus string can enter the pool.
* Place names are **fabricated** two-character combinations; institution names are "place +
  institution type." Same approach for English.

A fabricated place name can't guarantee no real, small place shares it, so there's a second
layer: the renderer prints a `SYNTHETIC SAMPLE` banner on every page by default, and PDF/XLSX
metadata always marks the file synthetic (docs/zh-CN/plan.md §9.1).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random
import unicodedata

REPO = pathlib.Path(__file__).resolve().parent.parent
RESOURCES = REPO / "mirobody_gen" / "resources"
CORPUS_TEXT = REPO / "corpus" / "text"
OUT = RESOURCES / "fiction.json"
SEED = 20260923

SURNAMES = list("王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘"
                "于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江"
                "尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤")
GIVEN = list("伟芳娜敏静丽强磊军洋勇艳杰娟涛明超秀霞平刚桂英华玉萍红建文辉力宇浩鑫欣怡婷雪"
             "琳晨阳帆博然瑶佳悦睿泽轩航凯宁萱涵彤蕾薇楠岚松柏峰川海波清源远翔腾飞健康安乐")
FIRST_EN = ["James", "Mary", "John", "Linda", "David", "Susan", "Daniel", "Karen", "Paul", "Nancy",
            "Mark", "Laura", "Steven", "Emily", "Kevin", "Rachel", "Brian", "Megan", "Eric", "Hannah",
            "Adam", "Grace", "Ryan", "Chloe", "Owen", "Alice", "Lucas", "Ella", "Henry", "Nora",
            "Wei", "Min", "Jun", "Ling", "Hao", "Yan", "Chen", "Xin", "Ming", "Hui"]
LAST_EN = ["Carter", "Hughes", "Porter", "Bennett", "Fletcher", "Holland", "Sutton", "Barker",
           "Warren", "Palmer", "Lowe", "Chambers", "Fox", "Webb", "Lambert", "Hayes", "Doyle",
           "Chan", "Wong", "Lau", "Tan", "Lim", "Ng", "Ho", "Leung", "Tsang", "Yip", "Kwok"]
#: Characters for fabricated place names: deliberately chosen to "look place-like, but rarely
#: form a real place name when paired."
PLACE_CHARS = list("岚澄霁穗岫汀澜溪桐棠杉蘅苓芷沅湄涟霖樾渚峪麓禾砚翎珩璟晗曦")
PLACE_EN_PREFIX = ["Wren", "Quill", "Yar", "Brack", "Fern", "Kest", "Lorn", "Mistle", "Ossen",
                   "Pell", "Rook", "Sable", "Tarn", "Vell", "Whin"]
PLACE_EN_SUFFIX = ["moor", "holt", "combe", "mere", "wick", "hollow", "stead", "vale"]

INSTITUTION_ZH = [("hospital", "{p}市第{n}人民医院"), ("hospital", "{p}市中心医院"),
                  ("hospital", "{p}中医医院"), ("hospital", "{p}妇幼保健院"),
                  ("checkup_center", "{p}健康体检中心"), ("checkup_center", "{p}区健康管理中心"),
                  ("lab", "{p}医学检验所"), ("lab", "{p}临床检验中心"),
                  ("clinic", "{p}社区卫生服务中心"), ("clinic", "{p}门诊部")]
INSTITUTION_EN = [("hospital", "{p} General Hospital"), ("hospital", "{p} Medical Centre"),
                  ("lab", "{p} Clinical Laboratories"), ("lab", "{p} Pathology Services"),
                  ("checkup_center", "{p} Health Screening Centre"), ("clinic", "{p} Family Clinic")]
NUMERALS = "一二三四五六七八九"


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()


def _corpus_blob() -> str | None:
    if not CORPUS_TEXT.is_dir():
        return None
    return "\n".join(_norm(p.read_text(encoding="utf-8", errors="ignore"))
                     for p in sorted(CORPUS_TEXT.glob("*.txt")))


def _replay_checker(types: list[str]):
    """Same index, same normalization as audit/privacy.py. True if it hits and the hit doesn't
    fall entirely inside a type-suffix window."""
    import sys

    import numpy as np

    sys.path.insert(0, str(REPO))
    from mirobody_gen.audit import privacy

    index = privacy.build_index(False)
    vocab = [privacy.normalize(t) for t in types]

    def hit(name: str) -> bool:
        norm = privacy.normalize(name)
        hashes = privacy.ngram_hashes(norm)
        if not hashes.size:
            return False
        pos = np.clip(np.searchsorted(index, hashes), 0, index.size - 1)
        for i in np.nonzero(index[pos] == hashes)[0]:
            if not privacy.is_public_term_window(norm[int(i):int(i) + privacy.NGRAM], vocab):
                return True
        return False

    return hit


def build(blob: str | None) -> tuple[dict, int]:
    rng = random.Random(SEED)
    dropped = 0

    def keep(candidates: list[str], want: int) -> list[str]:
        nonlocal dropped
        out: list[str] = []
        for c in candidates:
            if c in out:
                continue
            if blob is not None and _norm(c) in blob:
                dropped += 1
                continue
            out.append(c)
            if len(out) == want:
                break
        return out

    zh_names = keep([rng.choice(SURNAMES) + "".join(rng.sample(GIVEN, rng.choice((1, 2, 2))))
                     for _ in range(900)], 320)
    en_names = keep([f"{rng.choice(FIRST_EN)} {rng.choice(LAST_EN)}" for _ in range(600)], 200)
    places_zh = keep(["".join(rng.sample(PLACE_CHARS, 2)) for _ in range(3000)], 420)
    places_en = keep([rng.choice(PLACE_EN_PREFIX) + rng.choice(PLACE_EN_SUFFIX) for _ in range(3000)], 110)

    # Institution = place x institution type. Layout diversity's long tail has to be carried by
    # the number of institutions (about three-quarters of real-corpus document layouts occur
    # only once), so the pool needs to be far larger than the institutions 60 synthetic people
    # would actually visit; which ones get used is decided by the Chinese-restaurant process in
    # generator/files.py.
    institutions: list[dict] = []
    seen: set[str] = set()

    types = sorted({p.replace("{p}", "").replace("{n}", "").strip()
                    for _, p in INSTITUTION_ZH + INSTITUTION_EN})
    replay = _replay_checker(types) if blob is not None else None

    def add(name: str, language: str, kind: str) -> None:
        nonlocal dropped
        if name in seen:
            return
        seen.add(name)
        if blob is not None and (_norm(name) in blob or (replay and replay(name))):
            dropped += 1
            return
        institutions.append({"name": name, "language": language, "kind": kind})

    for place in places_zh:
        for kind, pattern in rng.sample(INSTITUTION_ZH, 2):
            add(pattern.format(p=place, n=NUMERALS[rng.randrange(len(NUMERALS))]), "zh", kind)
    for place in places_en:
        for kind, pattern in rng.sample(INSTITUTION_EN, 3):
            add(pattern.format(p=place), "en", kind)

    payload = {
        "_source": "hand-authored",
        "_note": "渲染器印在纸上的人名、地名、机构名的唯一来源。姓名用字与常见英文名是公共知识，"
                 "地名为生造组合；用固定种子抽样，并剔除在真实语料提取文本中出现过的任何候选。"
                 "隐私闸门把这里当白名单：输出文件里的姓名与机构名必须属于本文件。",
        "_provenance": {"script": "scripts/build_fiction.py", "seed": SEED,
                        "filtered_against_corpus": blob is not None},
        # Names get no exemption from the replay check: exempt vocabulary must be public
        # medical terminology, and a person's name is not that. Only the generic institution-type
        # suffixes (the English "General Hospital" and its Chinese equivalent) count as public vocabulary.
        "_vocabulary_fields": ["institution_types"],
        "institution_types": sorted({p.replace("{p}", "").replace("{n}", "").strip()
                                     for _, p in INSTITUTION_ZH + INSTITUTION_EN}),
        "person_names_zh": zh_names,
        "person_names_en": en_names,
        "places_zh": places_zh,
        "places_en": places_en,
        "institutions": institutions,
    }
    return payload, dropped


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    blob = _corpus_blob()
    if blob is None:
        print("Warning: no corpus/text, cannot compare against the real corpus. Not writing the file.")
        if args.write:
            raise SystemExit(1)
    payload, dropped = build(blob)
    print(f"Chinese names {len(payload['person_names_zh'])} · English names {len(payload['person_names_en'])} · "
          f"places {len(payload['places_zh'])}+{len(payload['places_en'])} · "
          f"institutions {len(payload['institutions'])} · dropped for appearing in the real corpus {dropped}")
    if args.write:
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
