"""Tests for the vendor-style pharmacogenomics report page: the rows genomics_report.rows_for
derives, the PDF mirobody_gen.render.pgx_report renders, and files.jsonl's genetics_report records.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mirobody_gen import corpus, genomics, genomics_report, person as person_mod, spec  # noqa: E402
from mirobody_gen.render import pgx_report  # noqa: E402

SEED = 20261014
PEOPLE = 60
DISALLOWED_KEYS = {"polygenic_risk_score", "prs", "trait", "risk_score", "probability"}


def _cohort():
    return person_mod.build_cohort(SEED, PEOPLE)


def _profiles():
    people = _cohort()
    langs = {p.person_id: person_mod.person_lang(SEED, p.person_id) for p in people}
    out = []
    for p in people:
        prof = genomics.profile_for(p, SEED, langs[p.person_id])
        if prof is not None:
            out.append((p, langs[p.person_id], prof))
    return out


def test_rows_cover_every_pgx_site_once():
    n_pgx = len(spec.genomics()["pgx_sites"])
    profiles = _profiles()
    assert profiles, "no genomics profile in a 60-person cohort"
    for _, _, prof in profiles:
        rows = genomics_report.rows_for(prof)
        assert len(rows) == n_pgx
        assert len({r["rsid"] for r in rows}) == n_pgx


def test_phenotype_only_where_the_function_table_supports_it():
    function_table = spec.genomics()["pgx_function"]
    for _, _, prof in _profiles():
        for r in genomics_report.rows_for(prof):
            if r["call_status"] == "no_call":
                assert r["phenotype_zh"] == "未检测" and r["phenotype_en"] == "Not tested"
                assert r["genotype"] == "--"
            elif r["rsid"] not in function_table:
                assert r["phenotype_zh"] == "无已知功能解读"
                assert r["phenotype_en"] == "No established phenotype"
            else:
                assert r["phenotype_zh"] and r["phenotype_en"]
                assert r["phenotype_zh"] not in ("未检测", "无已知功能解读")


def test_no_invented_polygenic_or_trait_fields():
    for _, _, prof in _profiles():
        for r in genomics_report.rows_for(prof):
            assert not (set(r) & DISALLOWED_KEYS)


def test_slco1b1_variant_uses_cpic_allele_function_not_diplotype_phenotype():
    """CPIC 2022 statin guideline, Genetic test interpretation: *5 is a no-function allele;
    decreased function is the *1/*5 individual's phenotype, not the allele function class.
    """
    for a1, a2, copies in (("T", "C", "one"), ("C", "C", "two")):
        call = {"rsid": "rs4149056", "gene": "SLCO1B1", "in_catalog": True,
                "label": "*5 (V174A)", "alt": "C", "a1": a1, "a2": a2, "call_status": "called"}
        row, = genomics_report.rows_for({"calls": [call]})
        assert f"{copies} no function allele" in row["phenotype_en"]
        assert "无功能等位基因" in row["phenotype_zh"]


def test_render_is_a_valid_one_or_two_page_pdf():
    import fitz

    _, _, prof = _profiles()[0]
    rows = genomics_report.rows_for(prof)
    data, pages = pgx_report.render("p_test", prof["vendor"], "2024-01-01", rows)
    assert 1 <= pages <= 2
    with fitz.open("pdf", data) as doc:
        assert doc.page_count == pages
        assert doc.metadata["subject"] == "SYNTHETIC"
        assert doc.metadata["author"] == pgx_report.VENDOR_BRAND[prof["vendor"]]
        text = doc[0].get_text()
        for r in rows[:3]:
            assert r["gene"] in text and r["rsid"] in text


def test_report_brand_and_language_follow_the_vendor_not_the_person():
    """Regression: the report's institution/brand and language must come from the vendor that
    produced the person's raw genotype file (`genomics.jsonl`'s own `vendor`), not from the
    person's spoken language -- a zh-speaking person whose raw file is a 23andMe export must get
    an English 23andMe-branded report, not a WeGene one."""
    people = person_mod.build_cohort(SEED, 20)
    encounters = {p.person_id: person_mod.encounters_for(p, SEED) for p in people}
    langs = {p.person_id: person_mod.person_lang(SEED, p.person_id) for p in people}
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp)
        from mirobody_gen import manifest

        manifest.write(out, people, encounters)
        genomics.write_all(out, people, SEED, langs)
        records = corpus.render_corpus(SEED, people, encounters, out, banner=True)
        reports = [r for r in records if r["kind"] == "genetics_report"]
        assert reports, "no genetics_report record produced"
        mismatches = 0
        for r in reports:
            assert r["institution"] == pgx_report.VENDOR_BRAND[r["vendor"]], r
            assert r["language"] == pgx_report.VENDOR_LANG[r["vendor"]], r
            if langs[r["person_id"]] != r["language"]:
                mismatches += 1
        # confirms this cohort actually exercises the bug scenario (a vendor whose report
        # language differs from the person's own spoken language), not just the easy cases
        assert mismatches > 0, "no person in this cohort had a vendor/spoken-language mismatch"


def test_genetics_report_appears_in_files_jsonl_consistent_with_genomics_jsonl():
    # A small cohort: full document rendering (hazards, layout, handwriting-free) is the
    # expensive part of this test, not genomics -- 60 people here would be slow for no extra
    # coverage (COVERAGE = 0.4 already gives several genomics people at this size).
    people = person_mod.build_cohort(SEED, 15)
    encounters = {p.person_id: person_mod.encounters_for(p, SEED) for p in people}
    langs = {p.person_id: person_mod.person_lang(SEED, p.person_id) for p in people}
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp)
        from mirobody_gen import manifest

        manifest.write(out, people, encounters)
        genomics.write_all(out, people, SEED, langs)
        records = corpus.render_corpus(SEED, people, encounters, out, banner=True)

        genomics_people = {p.person_id for p in people
                           if genomics.profile_for(p, SEED, langs[p.person_id]) is not None}
        reports = [r for r in records if r["kind"] == "genetics_report"]
        assert reports, "no genetics_report record produced"
        assert {r["person_id"] for r in reports} == genomics_people

        gen_lines = [json.loads(line) for line in (out / "genomics.jsonl").read_text(encoding="utf-8").splitlines()]
        gen_by_person = {g["person_id"]: g for g in gen_lines}
        for r in reports:
            assert r["format"] in ("pdf", "jpg", "png")
            assert r["page_count"] in (1, 2)
            assert r["language"] in ("zh", "en")
            assert r["synthetic"] is True
            assert (out / r["file"]).is_file()
            assert len(r["pgx_rows"]) == len(spec.genomics()["pgx_sites"])
            # cross-check against the raw genotype export's own truth for the same person: same
            # vendor, same genotype per site -- the report and the raw file must never disagree.
            truth = gen_by_person[r["person_id"]]
            assert r["vendor"] == truth["vendor"]
            called = {s["rsid"]: s["genotype_raw"] for s in truth["sites"] if s["gene"]}
            for row in r["pgx_rows"]:
                assert row["genotype"] == called[row["rsid"]]
