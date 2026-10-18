"""Build a corpus: the truth layer (manifest.jsonl) and, with --render, the files (files.jsonl).

    mirobody-gen build --seed 7 --out out/p3
    mirobody-gen build --seed 7 --people 8 --out out/smoke --stats
    mirobody-gen build --seed 7 --out out/p3 --render --pairs 12
    mirobody-gen build --seed 7 --out out/p3 --continuous      # + CGM sessions, intraday heart rate

The same seed always yields the same people, timeline and values: that is the whole meaning of
"reproducible" here. `out/` can be deleted at any time; the generator plus the seed is the source of
truth, not the bytes on disk.

Run both gates right after a build; they are not optional, they decide whether the corpus is usable:

    mirobody-gen audit-clinical out/p3/manifest.jsonl
    mirobody-gen audit-privacy --targets mirobody_gen/resources out/p3
"""

from __future__ import annotations

import argparse
import collections
import pathlib

from . import spec, manifest, person as person_mod


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a positive integer, got {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"expected a positive integer, got {text!r}")
    return value


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--people", type=_positive_int, default=None,
                    help="generate exactly N people, apportioned across archetypes by prevalence (smoke builds)")
    ap.add_argument("--out", default="out/p1")
    ap.add_argument("--stats", action="store_true", help="print distribution statistics for comparison with the reference aggregates")
    ap.add_argument("--render", action="store_true", help="render files (text-layer PDF, XLSX/CSV, scans, photos, screenshots) and write files.jsonl")
    ap.add_argument("--facility-services", default=None,
                    help="with --render: explicit authored-synthetic service profile JSON; applies to corpus files, not pairs")
    ap.add_argument("--pairs", type=int, default=0, help="also emit N minimal-contrast pair groups (pairs.jsonl)")
    ap.add_argument("--pair-views", choices=("all", "ordinary"), default=None,
                    help="with --pairs: all named stress variants (default), or only the same clean "
                         "controls for ordinary presentation review; does not change --render files")
    ap.add_argument("--pair-scope", choices=("tables", "checkup"), default=None,
                    help="with --pairs: non-routine tables (default), or full routine checkup books with "
                         "frozen clinical content and incident/capture stress variants")
    ap.add_argument("--paraphrase", action="store_true", help="use resources/paraphrases.json for narrative wording (off by default)")
    ap.add_argument("--handwriting", action="store_true",
                    help="with --render: also write handwritten files (notebook logs, doctor's notes, forms filled "
                         "in by hand), after the printed ones; off by default so existing seeds keep their bytes")
    ap.add_argument("--continuous", action="store_true",
                    help="also write continuous device streams: CGM sensor sessions and intraday heart rate in "
                         "each device's export shapes (continuous.jsonl); off by default so existing seeds keep "
                         "their bytes")
    ap.add_argument("--no-banner", action="store_true",
                    help="omit the SYNTHETIC banner (realism stress test; the synthetic metadata mark is always kept)")
    ap.add_argument("--verify", action="store_true",
                    help="after writing, run the deterministic audits (clinical, readability, privacy) and write "
                         "verification.json with passed/failed/skipped/not_run per check; exits 1 if a "
                         "required check fails")
    ap.add_argument("--lang-mix", default=None,
                    help="language-group ratio, e.g. 'zh:0.45,en:0.4,ja:0.15' (default zh:0.5,en:0.5). "
                         "Unknown groups fall back to English document templates; the mix changes "
                         "cohort composition, not dictionary structure.")
    args = ap.parse_args(argv)
    service_profile = None
    if args.facility_services:
        if not args.render:
            ap.error("--facility-services requires --render")
        from .facility_services import ServiceProfile
        try:
            service_profile = ServiceProfile.load(args.facility_services)
        except (OSError, ValueError, TypeError) as e:
            ap.error(f"--facility-services: {e}")
    if args.handwriting and not args.render:
        ap.error("--handwriting renders files: use it with --render")
    if args.pair_views is not None and args.pairs < 1:
        ap.error("--pair-views selects pair output: use it with --pairs N (N >= 1)")
    if args.pair_scope is not None and args.pairs < 1:
        ap.error("--pair-scope selects pair output: use it with --pairs N (N >= 1)")
    spec.set_paraphrases(bool(args.paraphrase))
    if args.lang_mix:
        try:
            person_mod.set_lang_mix(args.lang_mix)
        except ValueError as e:
            ap.error(f"--lang-mix: {e}")

    people = person_mod.build_cohort(args.seed, args.people)
    encounters = {p.person_id: person_mod.encounters_for(p, args.seed) for p in people}
    if service_profile is not None:
        from . import corpus
        try:
            corpus.preflight_services(service_profile, encounters)
        except ValueError as e:
            ap.error(f"--facility-services: {e}")

    out_dir = pathlib.Path(args.out)
    n_enc, n_rows = manifest.write(out_dir, people, encounters)
    print(f"{len(people)} people · {n_enc} encounters · {n_rows} readings → {out_dir}")

    # The same people's other sources: device batches (POST /api/data), diary sentences, genomics
    # exports, medication plans.
    from . import devices, genomics, journal, medications

    langs = {p.person_id: person_mod.person_lang(args.seed, p.person_id) for p in people}
    n_dev = devices.write_all(out_dir, people, args.seed, langs)
    n_journal = journal.write_all(out_dir, people, args.seed, langs)
    n_gen = genomics.write_all(out_dir, people, args.seed, langs)
    n_meds = medications.write_all(out_dir, people, args.seed)
    print(f"devices {n_dev} (devices.jsonl) · journal {n_journal} (journal.jsonl) · genomics {n_gen} (genomics.jsonl) · "
          f"medications {n_meds} (medications.jsonl)")

    # Vendor-cloud payloads: the device series above in Garmin, Oura, WHOOP and HealthKit shapes.
    from . import vendor_signals

    n_vsig = vendor_signals.write_all(out_dir, people, args.seed, langs)
    print(f"vendor pushes {n_vsig} (vendor_signals.jsonl)")

    if args.continuous:
        from . import continuous

        counts = continuous.write_all(out_dir, people, args.seed, langs, banner=not args.no_banner)
        print(f"continuous streams: {counts['cgm']} CGM sessions, {counts['heart_rate']} heart-rate windows, "
              f"{counts['files']} files (continuous.jsonl)")

    if args.stats:
        _stats(people, encounters)

    if args.render or args.pairs:
        from . import corpus, pairs

        if args.render:
            records = corpus.render_corpus(args.seed, people, encounters, out_dir, banner=not args.no_banner,
                                           handwriting=args.handwriting, service_profile=service_profile)
            by_fmt = collections.Counter(r["format"] for r in records)
            print(f"rendered {len(records)} files {dict(by_fmt)} → {out_dir / 'files'}")
            if args.handwriting:
                tiers = collections.Counter((r["handwriting"]["tier"], r["language"]) for r in records
                                            if "handwriting" in r)
                print(f"handwritten {sum(tiers.values())} {dict(sorted(tiers.items()))}")
        if args.pairs:
            records = pairs.render_pairs(args.seed, people, encounters, out_dir, args.pairs,
                                         banner=not args.no_banner, views=args.pair_views or "all",
                                         scope=args.pair_scope or "tables")
            n_groups = len({r["pair"]["pair_id"] for r in records})
            print(f"{n_groups} pair groups ({args.pair_scope or 'tables'}, {args.pair_views or 'all'} views), "
                  f"{len(records)} files → {out_dir / 'pairs'}")

    if args.verify:
        import sys

        from . import verify

        summary = verify.run(out_dir, requested_people=args.people,
                             expected_people=args.people if args.people is not None
                             else sum(a["n"] for a in person_mod.ARCHETYPES),
                             people=len(people), rendered=args.render, pairs=bool(args.pairs),
                             continuous=args.continuous)
        print(verify.render(summary))
        if summary["overall"] == "failed":
            sys.exit(1)


def _stats(people, encounters) -> None:
    from . import spec

    rows_per = [len(e.readings) for items in encounters.values() for e in items]
    rows_per.sort()

    def p(q: float) -> int:
        return rows_per[min(int(len(rows_per) * q), len(rows_per) - 1)]

    print(f"\nreadings per encounter  p25={p(.25)} p50={p(.5)} p75={p(.75)} p95={p(.95)} "
          f"max={rows_per[-1]}")
    print("(reference corpus, per document: p25=1 p50=6 p75=15 p95=36 max=86. "
          "one encounter splits into several files here, so these run higher until compared per file)")

    per_person = collections.Counter(len(v) for v in encounters.values())
    print(f"encounters per person  {dict(sorted(per_person.items()))}")

    # Abnormal rate: the one quantity directly comparable to the reference corpus, and the check
    # that prevalence-weighted cohort sampling is actually working.
    abnormal: dict[str, list[int]] = collections.defaultdict(list)
    for items in encounters.values():
        for encounter in items:
            for reading in encounter.readings:
                abnormal[reading.key].append(reading.status != "normal")
    print("\nabnormal rate (generated vs. reference corpus):")
    observed = {"chol": 0.274, "tg": 0.31, "ldl": 0.205, "ua": 0.143, "hgb": 0.009,
                "glu": 0.026, "alt": 0.021, "crea": 0.037, "hba1c": 0.03, "plt": 0.0}
    for key, truth in observed.items():
        flags = abnormal.get(key) or []
        if flags:
            print(f"  {spec.indicators()[key]['zh']:<12} generated {sum(flags)/len(flags):>6.1%}"
                  f"   reference {truth:>6.1%}   n={len(flags)}")

    print("\nnote: the reference column is a hospital-plus-clinic population, so its abnormal rate runs "
          "naturally high; our cohort is weighted by population prevalence. They should not match, but an "
          "order-of-magnitude gap means the weighting is wrong.")


if __name__ == "__main__":
    main()
