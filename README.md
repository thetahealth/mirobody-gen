# mirobody-gen

Synthetic health records, rendered as the documents people actually upload: lab slips, multi-page
check-up books, clinic notes, ECG and ultrasound reports, home blood-pressure logs — as text-layer PDFs,
spreadsheets, scans, phone photos, photocopies and app screenshots — plus the same people's wearable
batches, symptom diaries and consumer-genomics exports. Every file ships with row-level ground truth.

[中文说明](README.zh-CN.md) · [Architecture](docs/ARCHITECTURE.md) · [Privacy model](docs/PRIVACY.md) ·
[Output schema](docs/SCHEMA.md) · [Changelog](CHANGELOG.md)

## What it is for

Health-data engines such as [mirobody](https://github.com/thetahealth/mirobody) have to read a lab report
off a creased phone photo, decide that `血紅素` is haemoglobin and not HbA1c, notice that a printed
`120/80` is two readings, and merge seven mornings of weight from one spreadsheet into seven days.
Testing that pipeline needs a corpus with three properties that real patient files cannot give you:

- **ground truth for every printed cell** (name, value, unit, reference range, flag) and for every
  semantic reading behind it (indicator key, LOINC, UCUM unit, observation date), so extraction and
  standardisation can be scored rather than eyeballed;
- **named difficulty**: each file records which layout and content hazards it carries (units glued to
  values, sex-split ranges in one cell, bilingual headers, a page break that loses the table header, …)
  and which capture path it went through (flatbed scan, WeChat-forwarded photo, fax), so a drop in
  recall can be attributed;
- **no privacy exposure**: nothing in the corpus is derived from a real person.

## How the values are made

The generator is knowledge-driven, not fitted to data:

- reference intervals come from published standards (China's WS/T 404 and WS/T 405 series,
  clinical guidelines, the national laboratory procedures manual);
- within- and between-subject biological variation comes from the public Westgard / EFLM database;
- each of the 60 virtual people has a disease archetype, an event timeline (a statin started, an
  upper-respiratory infection, a month of overtime) with onset delays and magnitudes, and values that
  follow from a mechanistic model; derived quantities (BMI, LDL, MCH/MCHC, eGFR, globulin, differential
  absolute counts) are computed by their defining identities, never sampled;
- document *shape* — column sets, reference-range dialects, unit spellings, flag markers, hazard
  classes, page furniture — was distilled from a private reference set of de-identified documents that
  is not distributed. Only aggregate statistics and format tokens entered the repository, each tagged
  with its `source`. See [docs/PRIVACY.md](docs/PRIVACY.md) for the threat model and the gates that
  enforce it.

## Install

```bash
pip install -e ".[render]"          # numpy + PyMuPDF, openpyxl, Pillow
pip install -e ".[dev]"             # + pytest, ruff
```

Python 3.11+. Rendering uses the font bundled with PyMuPDF, so output is byte-identical across
machines for the same seed. Image tiers use Pillow and numpy only; OCR-based checks use a local
`tesseract` binary if present and are report-only.

## Quickstart

```bash
# 60 people, ~700 files, 12 minimal-contrast pairs. About 10 minutes with image tiers.
mirobody-gen build --seed 7 --out out/p3 --render --pairs 12

# The four audits are the acceptance criteria, not optional post-processing.
mirobody-gen audit-clinical    out/p3/manifest.jsonl                 # identities, bounds, flags, RCV, diagnoses
mirobody-gen audit-readability out/p3/files.jsonl out/p3/pairs.jsonl   # every printed truth is on the page
mirobody-gen audit-readability out/p3/files.jsonl --ocr              # image tiers: OCR recovery per scene (report)
mirobody-gen audit-privacy     --targets mirobody_gen/resources out/p3 # PII predicates, allow-lists, replay index
mirobody-gen audit-fidelity    out/p3/files.jsonl                     # shape statistics vs. the reference aggregates

# Scoring an extractor (predictions in the MedRepBench format)
mirobody-gen baselines rules out/p3/files.jsonl --out out/p3/pred_rules.jsonl
mirobody-gen score out/p3/files.jsonl out/p3/pred_rules.jsonl
mirobody-gen score out/p3/pairs.jsonl out/p3/pred_rules_pairs.jsonl --pairs
```

`python -m mirobody_gen <command>` is equivalent to `mirobody-gen <command>`; every command also runs
as its module (`python -m mirobody_gen.audit.clinical …`). A smaller build for smoke tests:
`--people 8`.

## What a build produces

| Path | Content | Truth |
| --- | --- | --- |
| `manifest.jsonl` | one record per visit: readings with the mirobody extraction field names, diagnoses, events since the previous visit, package, complaints, named findings | the truth root |
| `people.jsonl` | one record per person: archetype, conditions, event timeline with magnitude / onset / half-life | attribution answers |
| `files/` + `files.jsonl` | one record per file: printed rows (MedRepBench fields), semantic readings, layout summary, hazards with row attribution, delivery tier / scene / operator parameters, blocks (department key-values, narratives, summary), findings, complaints, diagnoses | extraction + standardisation |
| `pairs/` + `pairs.jsonl` | minimal-contrast pairs: one clean base per visit, one variant per hazard class, and aligned scan / photo / copy / screenshot views sharing the base truth | causal effect of one hazard |
| `devices/` + `devices.jsonl` | phone health-store batches (Apple / Huawei / Xiaomi / Health Connect field names, ≤500 records each, ready to POST) | LOINC per record |
| `journal.jsonl` | one-sentence diary entries in the person's words | the entries a sentence should split into, with ICPC-3 codes |
| `genomics/` + `genomics.jsonl` | consumer-genomics exports (WeGene, 23andMe, AncestryDNA, MyHeritage, VCF) | per-site genotype, call status, catalogue membership |

Field-by-field definitions: [docs/SCHEMA.md](docs/SCHEMA.md).

## Document kinds and delivery tiers

Kinds: lab slip, check-up book (cover, summary and advice, general examination, department key-value
sections, ECG strip, ultrasound / radiography narratives, laboratory tables), outpatient record (chief
complaint, history, examination line, diagnosis, plan), ECG report, ultrasound report, imaging report,
home BP / weight log, app export table.

Tiers: T0 text-layer PDF · T1 XLSX / CSV · T2 scans and app-enhanced captures · T3 phone photos ·
T4 photocopies, faxes, aged archives, re-forwarded compressions · T6 screenshots and screen photos.
The 24 scenes are operator chains modelled on PureDocBench's degradation profiles and real-capture
pipelines; all views of a document share one truth. Details and calibration numbers:
[docs/zh-CN/degradation.md](docs/zh-CN/degradation.md).

## Determinism and audits

The corpus is a function of the generator and a seed: two builds with the same seed are byte-identical
(files, images, spreadsheets, metadata), so `out/` is disposable and the generator plus the seed is the
artefact. Three audits gate a build and one reports (the test suite runs the clinical and readability audits on a small build; CI adds the privacy gate):

- **clinical** — algebraic identities within a panel, physiological hard limits, sex-specific items,
  flag/range agreement, reference-change-value screening across visits, diagnosis/value coherence;
- **readability** — every printed truth string is present on the page (text tiers), with page furniture
  stripped; image tiers are reported through OCR;
- **privacy** — PII predicates, name and institution allow-lists, spec provenance, and an n-gram replay
  index over the reference set on machines that hold it;
- **fidelity** — layout fingerprint diversity, hazard density, row-count and dialect distributions
  against the reference aggregates (report only).

## Status

- Integration with mirobody's test suites (an environment variable pointing at a build directory) is
  designed but not yet wired; see the roadmap in [docs/zh-CN/plan.md](docs/zh-CN/plan.md) §6.
- The catalogue covers 172 indicators (153 quantitative, 17 qualitative, 2 categorical), 52 order
  groups, 5 package tiers, 7 departments, 17 auxiliary examinations and 55 named findings. Every
  reference interval cites a public standard, guideline or expert consensus; see
  [docs/zh-CN/research-2026-09-29.md §8](docs/zh-CN/research-2026-09-29.md) for the sources behind
  the enlarged schema.
  Extending it is a spec change (`scripts/build_*.py`), not a code change.

## Contributing, security, citation

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md) (including what to do if you
believe a file in this repository or a build is not synthetic) and [CITATION.cff](CITATION.cff).
Licensed under the Apache License 2.0.
