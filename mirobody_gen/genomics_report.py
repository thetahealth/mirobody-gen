"""Truth for a vendor-style pharmacogenomics report page: one row per PGx site, with a phenotype
only where the site actually supports one.

A real consumer PGx report (WeGene, 23andMe's old Pharmacogenetics reports) shows a **single-variant**
result per row: this gene, this rsID, your genotype, what that variant means. It does not phase several
SNPs of the same gene into one star-allele diplotype -- that needs haplotype information this generator
doesn't model. So this module reports exactly what `genomics.profile_for` actually calls: the genotype,
and a phenotype sentence only for the subset of sites `resources/genomics.json`'s `pgx_function` carries
a CPIC/PharmGKB function class for. Everything else prints the genotype with "no established phenotype"
rather than a guessed-at diplotype metabolizer label -- the generator has no basis for inventing one.
"""

from __future__ import annotations

from . import spec

#: Sentence templates, keyed by how many copies of the alt allele are present. VKORC1's function
#: class text ("increased warfarin sensitivity") reads naturally in the same templates as a
#: function class ("decreased function"), so one template set covers both.
_PHENOTYPE_ZH = {
    0: "未检出此变异（该位点为野生型）",
    1: "杂合携带1个{function}等位基因（{label}）",
    2: "纯合携带2个{function}等位基因（{label}）",
}
_PHENOTYPE_EN = {
    0: "Reference/wild-type at this site (variant allele not detected)",
    1: "Heterozygous carrier of one {function} allele ({label})",
    2: "Homozygous carrier of two {function} alleles ({label})",
}
_NO_PHENOTYPE_ZH = "无已知功能解读"
_NO_PHENOTYPE_EN = "No established phenotype"
_NOT_TESTED_ZH = "未检测"
_NOT_TESTED_EN = "Not tested"


def _alt_copies(call: dict) -> int | None:
    if call["call_status"] == "no_call":
        return None
    return sum(1 for a in (call["a1"], call["a2"]) if a == call["alt"])


def rows_for(profile: dict) -> list[dict]:
    """One row per PGx site in this genomics profile (always all 41: `genomics.profile_for`
    includes every `pgx_sites` row). Each row is both what the rendered page prints and the
    truth it's checked against."""
    function = spec.genomics()["pgx_function"]
    rows: list[dict] = []
    for call in profile["calls"]:
        if not (call["gene"] and call["in_catalog"]):
            # catalog_sites (gene-unlabelled) and off_catalog (gene-labelled but outside
            # mirobody's site table, e.g. ALDH2/MTHFR) are both not PGx-report material.
            continue
        func = function.get(call["rsid"])
        if call["call_status"] == "no_call":
            genotype_display, phenotype_zh, phenotype_en = "--", _NOT_TESTED_ZH, _NOT_TESTED_EN
        else:
            genotype_display = call["a1"] + call["a2"]
            copies = _alt_copies(call)
            if func is None:
                phenotype_zh, phenotype_en = _NO_PHENOTYPE_ZH, _NO_PHENOTYPE_EN
            else:
                phenotype_zh = _PHENOTYPE_ZH[copies].format(function=func["function_zh"], label=call["label"])
                phenotype_en = _PHENOTYPE_EN[copies].format(function=func["function_en"], label=call["label"])
        rows.append({
            "gene": call["gene"], "rsid": call["rsid"], "label": call["label"],
            "genotype": genotype_display, "call_status": call["call_status"],
            "phenotype_zh": phenotype_zh, "phenotype_en": phenotype_en,
        })
    rows.sort(key=lambda r: (r["gene"], r["rsid"]))
    return rows
