# Maintainer scripts

These scripts produce `mirobody_gen/resources/*.json`. Users of the package do not need them.

| Script | Produces | Needs the reference set |
| --- | --- | --- |
| `build_indicators.py` | `indicators.json` — catalogue, public reference intervals, biological variation, LOINC resolvability | no (needs the mirobody resolver for `expect_resolvable`) |
| `build_cohort.py` | `cohort.json` — archetypes, event scripts, orders, package tiers, diagnostic criteria | no |
| `build_profile.py` | `narratives.json`, `complaints.json`, `genomics.json`, `vocab.json`, `delivery.json` | no |
| `build_fiction.py` | `fiction.json` — fictional names, places and institutions | filters candidates against the reference set if present |
| `distill_layout.py` | `layout.json` — format tokens (column sets, dialects, markers, furniture) | yes |
| `distill_hazards.py` | `hazards.json` — hazard taxonomy with document rates | yes |
| `build_numbers.py` | `numbers.json`, `docs/zh-CN/numbers.md` — aggregate statistics | yes |
| `measure_images.py` | `numbers_images.json` — aggregate image statistics | yes |

Run any script without `--write` to see what it would produce; `--write` regenerates the resource.
`tests/test_spec.py::test_spec_is_regenerable_without_drift` runs them all and fails if the tracked
resources differ from what the scripts produce.

The reference set is a private collection of de-identified documents held outside the repository. The
distillation scripts read it and emit only format tokens, class names and aggregate counts; see
[docs/PRIVACY.md](../docs/PRIVACY.md) for the gating rules.
