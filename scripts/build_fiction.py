"""生成 `resources/fiction.json`：渲染器印在纸上的人名、地名、机构名，全部来自这里。

    python3 scripts/build_fiction.py            # 打印统计
    python3 scripts/build_fiction.py --write    # 写 resources/fiction.json

## 为什么要单独一份虚构池

一张检验单上要印姓名、送检医生、检验者、审核者、医院名。这些字段是隐私闸门的**白名单**
检查对象（`audit/privacy.py` 的 ②）：印出来的名字必须属于这份池子，而不是"看起来不像真名就放行"。
白名单要能被审，所以它必须是一份落盘的、有限的清单，不能是运行时随手拼的。

## 怎么造的

* 姓与名用字是**公共知识**（常见姓氏、常见名字用字），手写在下面；
* 用固定种子从组合空间里**抽样**一个子集，而不是穷举——穷举的话，一个常见名字"不在池里"
  本身就泄露了它在真实语料里出现过（见下一条）；抽样之后，绝大多数组合本来就不在池里，
  缺席不携带任何信息；
* 每个候选都拿去和真实语料的提取文本做子串比对，**出现过的一律丢弃**；机构名还要过一遍
  隐私闸门同款的 12 字 n-gram 回放检测（类型后缀"General Hospital"这类公共词汇除外）——
  2026-09-23 实测，一个生造地名的尾字母加上"Family Clinic"正好撞上真实语料里的某家诊所名。
  这一步只会删、不会加，真实语料里的任何字面都进不了池子；
* 地名是**生造的**两字组合，机构名是"地名 + 机构类型"。英文同理。

生造地名无法保证世界上没有同名的小地方。所以还有第二层：渲染器默认在每页印
`SYNTHETIC SAMPLE` 横幅，PDF/XLSX 元数据里始终写 synthetic（docs/zh-CN/plan.md §9.1）。
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
#: 生造地名的用字：刻意选"像地名、但两两组合很少是真地名"的字。
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
    """与 audit/privacy.py 同一个索引、同一种归一化。命中且不整段落在某个类型后缀里 → True。"""
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

    # 机构 = 地名 × 机构类型。版式多样性的长尾要靠机构数撑（真实语料约四分之三的文档版式只出现一次），
    # 所以池子要比 60 个虚拟人会去的机构多得多；实际用到哪些由 generator/files.py 的中国餐馆过程决定。
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
        # 名字不给回放检测豁免：豁免词汇只能是公开医学词汇，人名不是。
        # 只有机构类型的通用后缀（"General Hospital"、"人民医院"）是公共词汇。
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
        print("警告：没有 corpus/text，无法与真实语料比对。不写文件。")
        if args.write:
            raise SystemExit(1)
    payload, dropped = build(blob)
    print(f"中文姓名 {len(payload['person_names_zh'])} · 英文姓名 {len(payload['person_names_en'])} · "
          f"地名 {len(payload['places_zh'])}+{len(payload['places_en'])} · "
          f"机构 {len(payload['institutions'])} · 因在真实语料中出现而丢弃 {dropped}")
    if args.write:
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"已写出 {OUT}")


if __name__ == "__main__":
    main()
