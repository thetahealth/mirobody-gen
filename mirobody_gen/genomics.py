"""Consumer-genomics raw exports (WeGene, 23andMe, AncestryDNA, MyHeritage, VCF) with per-site truth.

mirobody has accepted these formats since 1.5.2 (`collect/files/services/genotype_format.py`:
identified by vendor banner and column names; a file with no header is rejected outright). Each
site is matched against its own 489-site table (dbSNP b155 common intersect CPIC, including 41
gene-annotated pharmacogenomic sites); sites outside that table resolve to `unresolved`, and
no-calls resolve to `no_call`.

Generated files cover only: the 41 PGx sites, a subset of the rest of the site table, 8 common
consumer sites outside that table (ALDH2, MTHFR, APOE, ...), and a 1-2% no-call rate. Genotypes are
drawn by Hardy-Weinberg sampling against hand-curated allele frequencies, picking the East Asian or
European frequency column by the person's language group — this aims for plausibility, not an
estimate of any real population.

Truth (`genomics.jsonl`) follows the fields of mirobody's `testing/genomics/canonical.json`:
`rsid / chrom / pos37 / pos38 / ref / alt / gene / array_gt / vcf_gt / call_status / zygosity`, plus
`genotype_raw` (the literal text printed in the file) and `in_catalog` (whether it should resolve).

**No real genome can leak through this by construction**: no real genotype enters the generation
path, and the site coordinates are public dbSNP records.
"""

from __future__ import annotations

import json
import pathlib
import random
from datetime import date

from . import spec
from .model import Person

VENDOR_WEIGHTS = {"zh": {"wegene": 55, "23andme": 25, "vcf": 12, "myheritage": 8},
                  "en": {"23andme": 50, "ancestry": 28, "myheritage": 12, "vcf": 10},
                  "ja": {"23andme": 45, "vcf": 25, "ancestry": 15, "myheritage": 15}}
NO_CALL_RATE = 0.015
#: Ancestry -> allele-frequency column. Each site in `resources/genomics.json` carries an eas and an
#: eur column; picking the wrong one is a silent error with real consequences: CYP2C19*2 frequency
#: is 0.3 in East Asians vs. 0.15 in Europeans, so Japanese people sampled against the European
#: column would skew the whole PGx phenotype distribution.
ANCESTRY_FREQ_COL = {"zh": 7, "ja": 7, "en": 8}   # eas / eas / eur
_DEFAULT_FREQ_COL = 8
#: Fraction of people who have a genomics file.
COVERAGE = 0.4


def _genotype(rng: random.Random, ref: str, alt: str, freq: float) -> tuple[str, str, str]:
    """(allele 1, allele 2, gt). HWE: alt homozygous p^2, heterozygous 2pq, ref homozygous q^2."""
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
    vw = VENDOR_WEIGHTS.get(lang, VENDOR_WEIGHTS["en"])
    vendor = rng.choices(list(vw), weights=list(vw.values()))[0]
    freq_col = ANCESTRY_FREQ_COL.get(lang, _DEFAULT_FREQ_COL)
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
    """(file contents, extension). Header and column format per vendor, from resources/genomics.json."""
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
