"""Consumer-genomics raw exports (WeGene, 23andMe, AncestryDNA, MyHeritage, VCF) with per-site truth.

少量基因数据：消费级基因检测的原始导出文件（WeGene / 23andMe / AncestryDNA / MyHeritage / VCF）。

mirobody 1.5.2 起接收这些格式（`collect/files/services/genotype_format.py`：按厂商横幅与列名识别，
无表头的直接拒收），把每个位点对到它自带的 489 个位点表（dbSNP b155 common ∩ CPIC，含 41 个带基因
标注的药物基因组位点），表外的落成 `unresolved`，无调用落成 `no_call`。

这里生成的文件只覆盖：41 个 PGx 位点 + 位点表里其余位点的一个子集 + 8 个表外的常见消费级位点
（ALDH2、MTHFR、APOE……）+ 1–2% 的无调用。基因型按手写的等位基因频率做 Hardy–Weinberg 抽样，
按人的语言组选东亚/欧洲频率列——只求像人，不是任何人群的估计。

真值（`genomics.jsonl`）按 mirobody `testing/genomics/canonical.json` 的字段：
`rsid / chrom / pos37 / pos38 / ref / alt / gene / array_gt / vcf_gt / call_status / zygosity`，
另加 `genotype_raw`（文件里印的字面）与 `in_catalog`（该不该解析得出）。

**结构上不可能泄露任何真人基因组**：没有真实基因型进入生成路径，位点坐标是公开的 dbSNP 记录。
"""

from __future__ import annotations

import json
import pathlib
import random
from datetime import date

from . import spec
from .model import Person

VENDOR_WEIGHTS = {"zh": {"wegene": 55, "23andme": 25, "vcf": 12, "myheritage": 8},
                  "en": {"23andme": 50, "ancestry": 28, "myheritage": 12, "vcf": 10}}
NO_CALL_RATE = 0.015
#: 多少人有基因文件。
COVERAGE = 0.4


def _genotype(rng: random.Random, ref: str, alt: str, freq: float) -> tuple[str, str, str]:
    """(等位基因 1, 等位基因 2, gt)。HWE：alt 纯合 p²，杂合 2pq，ref 纯合 q²。"""
    u = rng.random()
    if u < freq * freq:
        return alt, alt, "1/1"
    if u < freq * freq + 2 * freq * (1 - freq):
        return ref, alt, "0/1"
    return ref, ref, "0/0"


def profile_for(person: Person, seed: int, lang: str) -> dict | None:
    rng = random.Random(f"genome:{seed}:{person.person_id}")
    if rng.random() >= COVERAGE:
        return None
    g = spec.genomics()
    vw = VENDOR_WEIGHTS[lang]
    vendor = rng.choices(list(vw), weights=list(vw.values()))[0]
    freq_col = 7 if lang == "zh" else 8               # alt_freq_eas / alt_freq_eur
    sites = []
    for row in g["pgx_sites"]:
        sites.append((row, True))
    catalog = list(g.get("catalog_sites", []))
    rng.shuffle(catalog)
    for row in catalog[:rng.randint(120, 260)]:
        sites.append((row, True))
    for row in g["off_catalog"]:
        sites.append((row, False))
    calls = []
    for row, in_catalog in sites:
        rsid, chrom, pos37, pos38, ref, alt = row[:6]
        gene = row[6] if len(row) > 6 else None
        freq = float(row[freq_col]) if len(row) > freq_col and row[freq_col] is not None else 0.3
        if rng.random() < NO_CALL_RATE:
            a1 = a2 = None
            gt, status, zyg = None, "no_call", None
        else:
            a1, a2, gt = _genotype(rng, ref, alt, freq)
            status = "called" if in_catalog else "unresolved"
            zyg = "homozygous" if a1 == a2 else "heterozygous"
        calls.append({"rsid": rsid, "chrom": str(chrom), "pos37": int(pos37), "pos38": int(pos38), "ref": ref, "alt": alt,
                      "gene": gene, "a1": a1, "a2": a2, "array_gt": gt, "vcf_gt": gt.replace("/", "|") if gt else None,
                      "call_status": status, "zygosity": zyg, "in_catalog": in_catalog})
    order = {str(c): i for i, c in enumerate(list(range(1, 23)) + ["X", "Y", "MT"])}
    calls.sort(key=lambda c: (order.get(c["chrom"], 99), c["pos37"]))
    return {"vendor": vendor, "calls": calls, "build": g["vendors"][vendor]["build"]}


def render(profile: dict, when: date) -> tuple[str, str]:
    """(文件内容, 扩展名)。各厂商的表头与列格式照 resources/genomics.json 的 vendors。"""
    v = spec.genomics()["vendors"][profile["vendor"]]
    lines = [h.format(date=when.strftime("%a %b %d %H:%M:%S %Y")) for h in v["header"]]
    lines.append(v["columns"])
    pos_key = "pos38" if v["build"] == "GRCh38" else "pos37"
    for c in profile["calls"]:
        pos = c[pos_key]
        if v["genotype"] == "joined":
            gt = (c["a1"] + c["a2"]) if c["a1"] else v["no_call"]
            lines.append(f"{c['rsid']}\t{c['chrom']}\t{pos}\t{gt}")
        elif v["genotype"] == "joined_quoted":
            gt = (c["a1"] + c["a2"]) if c["a1"] else v["no_call"]
            lines.append(f'"{c["rsid"]}","{c["chrom"]}","{pos}","{gt}"')
        elif v["genotype"] == "split":
            a1, a2 = (c["a1"], c["a2"]) if c["a1"] else (v["no_call"], v["no_call"])
            lines.append(f"{c['rsid']}\t{c['chrom']}\t{pos}\t{a1}\t{a2}")
        else:                                              # vcf
            gt = c["vcf_gt"] or "./."
            alt = c["alt"].split(",")[0]
            lines.append(f"{c['chrom']}\t{pos}\t{c['rsid']}\t{c['ref']}\t{alt}\t.\tPASS\t.\tGT\t{gt}")
    return "\n".join(lines) + "\n", v["ext"]


def write_all(out_dir: pathlib.Path, people: list[Person], seed: int, langs: dict[str, str]) -> int:
    root = out_dir / "genomics"
    n = 0
    with (out_dir / "genomics.jsonl").open("w", encoding="utf-8") as fh:
        for person in people:
            prof = profile_for(person, seed, langs.get(person.person_id, "zh"))
            if prof is None:
                continue
            when = person.weight_anchors[0][0]
            text, ext = render(prof, when)
            root.mkdir(parents=True, exist_ok=True)
            path = root / f"{person.person_id}_{prof['vendor']}.{ext}"
            path.write_text(text, encoding="utf-8")
            truth = [{k: v for k, v in c.items() if k not in ("a1", "a2")} | {
                "genotype_raw": (c["a1"] + c["a2"]) if c["a1"] else "--"} for c in prof["calls"]]
            fh.write(json.dumps({"person_id": person.person_id, "synthetic": True, "vendor": prof["vendor"],
                                 "build": prof["build"], "file": str(path.relative_to(out_dir)),
                                 "n_sites": len(truth),
                                 "n_pgx": sum(1 for c in prof["calls"] if c["gene"] and c["in_catalog"]),
                                 "n_off_catalog": sum(1 for c in prof["calls"] if not c["in_catalog"]),
                                 "n_no_call": sum(1 for c in prof["calls"] if c["call_status"] == "no_call"),
                                 "sites": truth}, ensure_ascii=False) + "\n")
            n += 1
    return n
