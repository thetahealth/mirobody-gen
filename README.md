# mirobody-gen

**The adversarial input source for [mirobody](https://github.com/thetahealth/mirobody): regenerable synthetic people whose files, wearable pushes, journal entries and genotype exports carry row-level ground truth.**

[中文说明](README.zh-CN.md) · [Architecture](docs/ARCHITECTURE.md) · [Privacy model](docs/PRIVACY.md) · [Output schema](docs/SCHEMA.md) · [Changelog](CHANGELOG.md) · [Companion article (working draft, 中文)](docs/zh-CN/paper.md)

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3775A9)](pyproject.toml)

---

A person hands over a creased phone photo of a lab slip, an XLSX their employer's check-up chain
exports, a Garmin account, a WeGene file and a paper notebook of morning blood pressures. Mirobody
is built to turn exactly that into one coded record. Testing it needs inputs with three properties
no real patient file can give you at scale:

- **ground truth behind every printed cell** — what the page says (name, value, unit, reference
  range, flag, MedRepBench's five fields) *and* what it means (indicator key, LOINC code, UCUM
  unit, observation date), so extraction and standardisation are scored, not eyeballed;
- **named difficulty** — every file declares which hazard classes it carries, from a taxonomy of
  62 classes distilled from a real-corpus study (61 generatable in the text layer; `unit.glued_to_value`
  appears on 22% of real documents, `unit.in_header_or_reference_only` on 9%); minimal pairs isolate
  each hazard's causal cost;
- **no privacy exposure** — nothing in the corpus is derived from a real person. Values are
  computed from mechanistic physiology and public standards, never sampled from a distribution
  fitted to patient data.

This repository is mirobody's **official source of such inputs**, and an organic part of the
project family: the people it generates present to **every** ingestion path mirobody has —
documents to the file pipeline, HealthKit/Garmin/Oura/WHOOP payloads to the provider decoders,
phone health-store batches to `/api/data`, and genotype exports to the genetics handler — so one
synthetic person exercises the whole engine end to end. Benchmark-facing uses compose it with
[ESL-Bench](https://arxiv.org/abs/2604.02834), whose records arrive already structured: the
difference between the two arrival modes on the same person is what the working article calls the
*document-boundary cost*.

## The four delivery channels

| Channel | artefact | mirobody entry point |
| --- | --- | --- |
| **Documents** | lab slips, check-up books, clinic notes, ECG / ultrasound / imaging reports, home logs, app exports — as text-layer PDF, XLSX, CSV, and 24 scan / photo / copy / screenshot scenes (tiers T0–T6) | file upload pipeline |
| **Phone health store** | `devices/` batches of ≤500 records in Apple / Huawei / Xiaomi / Health Connect field names, ready to POST | `POST /api/data` |
| **Vendor cloud** | `vendor_signals/` byte-level HealthKit JSON, Garmin Health API (dailies / sleeps / bodyComps / activities / pulseOx), Oura v2 (activity / sleep / heartrate / spo2 / stress), WHOOP v2 (cycle / workouts / recovery) | `kernel/decoders/{apple,garmin,oura,whoop}.py` — output shape pinned against the acceptance records in [`mirobody/kernel/decoders/samples/`](https://github.com/thetahealth/mirobody/tree/feat/1.5.4/mirobody/kernel/decoders/samples) |
| **Genomics** | WeGene, 23andMe, AncestryDNA, MyHeritage and VCF exports; 41 PGx sites + catalog subset + off-catalog sites at ancestry-correct allele frequencies, 1.5% no-call | genetics handler |

A person exists **longitudinally across all four channels**: the haemoglobin on the 2024 check-up
book, the resting heart rate in that week's Garmin dailies and the CYP2C19 diplotype in the
consumer-genomics file belong to one person with one event timeline. That identity is what makes
"*did you merge this person's lab slip with this person's wearable stream?*" a checkable question.

## How the values are made

Knowledge-driven, not fitted to data:

- reference intervals from China's **WS/T 404 (biochemistry)** and **WS/T 405 (haematology)**
  series, clinical guidelines, and the national laboratory procedures manual;
- within- and between-subject biological variation from the public EFLM / Westgard database;
- 60 people across 8 archetypes (healthy, prediabetes→T2DM, dyslipidaemia on statin,
  iron-deficiency anaemia, thyroid disorder, CKD progression, fatty liver, hypertension), each
  with an event timeline (a statin started, an infection, a month of overtime) whose effects
  carry onset delays, magnitudes and half-lives; derived quantities (BMI, LDL, MCH/MCHC, eGFR,
  differential absolutes) computed by their defining identities — the public CV<sub>G</sub> values
  4.85%/2.8%/5.2% for MCV/MCHC/MCH are mutually consistent only under MCH = MCHC × MCV, so we
  enforce it;
- document *shape* (column sets, reference-range dialects, unit spellings, flag markers, page
  furniture) distilled from a private de-identified reference set that is not distributed; only
  format tokens and aggregate statistics entered the repo, each tagged with its `_source`, under
  an allow-list `.gitignore`, a pre-commit privacy hook, and an n-gram replay gate run on release
  candidates. Threat model: [docs/PRIVACY.md](docs/PRIVACY.md).

## Install

Python 3.11+, in a virtualenv (PyMuPDF and Pillow are only needed for rendering):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[render]"    # PDFs, spreadsheets, image tiers
pip install -e ".[dev]"       # + pytest, ruff
```

Rendering uses the font bundled with PyMuPDF, so output is byte-identical across machines for the
same seed. A badly-named third-party `fitz` package shadows PyMuPDF's import; if `import fitz`
resolves to anything but PyMuPDF, uninstall the impostor (`pip uninstall fitz`) — the real one is
`pymupdf`.

## Quickstart

```bash
# 60 people, ~720 files, 12 minimal pairs (~38 s without image tiers; slower with them)
mirobody-gen build --seed 7 --out out/p3 --render --pairs 12

# Audits are acceptance gates, not optional post-processing
mirobody-gen audit-clinical     out/p3/manifest.jsonl                  # identities, bounds, flags, RCV, diagnoses
mirobody-gen audit-readability  out/p3/files.jsonl out/p3/pairs.jsonl  # every printed truth is on the page
mirobody-gen audit-privacy      --targets mirobody_gen/resources out/p3 # PII predicates, allow-lists, replay
mirobody-gen audit-fidelity     out/p3/files.jsonl                     # shape vs the reference aggregates

# Score an extractor against the two truth layers (predictions in MedRepBench format)
mirobody-gen baselines rules out/p3/files.jsonl --out out/p3/pred_rules.jsonl
mirobody-gen score     out/p3/files.jsonl out/p3/pred_rules.jsonl
mirobody-gen score     out/p3/pairs.jsonl out/p3/pred_rules_pairs.jsonl --pairs
```

Cohort language composition is a build parameter, not a resource change:
`--lang-mix 'zh:0.45,en:0.4,ja:0.15'` reweights who writes in which language channel (device
timezones, brand shares and, for genomics, ancestry-appropriate allele frequencies follow the
group; narrative wording falls back to the English dictionary for non-zh groups — Japanese
medical templating is a resource-layer project of its own).

Smoke builds: `--people 8`. `python -m mirobody_gen <command>` is equivalent to
`mirobody-gen <command>`.

## What a build produces

| Path | Content | Truth |
| --- | --- | --- |
| `manifest.jsonl` | one record per visit: readings in mirobody's extraction field names, diagnoses, events since the previous visit, package, complaints, findings | root of all truth |
| `people.jsonl` | one record per person: archetype, conditions, event timeline with magnitude / onset / half-life | attribution |
| `files/` + `files.jsonl` | one record per file: `printed_rows[]` (MedRepBench five fields), `readings[]` (LOINC, UCUM, observation date), layout summary, `hazards[]` with row attribution, tier / scene / operator parameters, `distractors[]` | extraction + standardisation |
| `pairs/` + `pairs.jsonl` | one clean base per visit, one variant per hazard class, aligned scan / photo / copy / screenshot views sharing the base truth | causal effect of one hazard |
| `devices/` + `devices.jsonl` | phone health-store batches | LOINC per record |
| `vendor_signals/` + `vendor_signals.jsonl` | vendor cloud payloads (see above) | expected catalogue metrics per record |
| `journal.jsonl` | one-sentence diaries in the person's words | entries a sentence should split into, with ICPC-3 codes |
| `genomics/` + `genomics.jsonl` | consumer-genomics exports | per-site genotype, call status, catalogue membership |

Field-by-field definitions: [docs/SCHEMA.md](docs/SCHEMA.md).

## Determinism and audits

The corpus is a function of generator + seed (+ `--lang-mix`, + `--paraphrase`): two builds with
the same parameters are byte-identical, so `out/` is disposable and the generator plus the seed
**is** the artefact. Three audits gate a build (clinical, readability, privacy); fidelity reports
against reference aggregates; the test suite runs clinical + readability on a small build, and CI
runs the privacy gate with `--skip-replay` (the replay index needs the reference set and runs on
release machines, per [docs/PRIVACY.md](docs/PRIVACY.md)).

## Language models

No model runs in the data path: values, findings, layouts and images come from resources, code
and a seed. An optional layer (`mirobody-gen build --paraphrase`) asks a model for **rewordings
of narrative templates only**; candidates must keep every slot, number and locked term, stay in
the same language and carry no identifier, and the accepted paraphrases become a *resource* the
privacy gate scans as untrusted text. It is off by default; `mirobody-gen compare` shows what it
changed. Design and contract: [docs/zh-CN/llm-integration-2026-09-29.md](docs/zh-CN/llm-integration-2026-09-29.md).

## Status and the mirobody relationship

- This repository ships the generator, its audits and the scoring harness. mirobody ingests the
  builds through an environment variable pointing at a build directory; **`feat/1.5.4` does not
  yet read one**, so today the corpus is consumed through the `score` / `baselines` CLIs here and
  by feeding `vendor_signals/` payloads to mirobody's decoder test-suite shapes by hand.
- The catalogue covers 172 indicators (153 quantitative, 17 qualitative, 2 categorical), 52 order
  groups, 5 package tiers, 7 departments, 17 auxiliary examinations and 55 named findings; every
  reference interval cites a public standard or guideline.
- The working article (中文工作稿, [docs/zh-CN/paper.md](docs/zh-CN/paper.md)) records the
  benchmark positioning — provisionally **ESL-Doc**, composed with ESL-Bench — with a verified
  47-entry bibliography at [docs/paper/refs.bib](docs/paper/refs.bib). The generator keeps this
  repository's name whatever the benchmark ends up called.

## Contributing, security, citation

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md) — including what to do if you
believe a file in this repository is not synthetic — and [CITATION.cff](CITATION.cff). Apache 2.0.
