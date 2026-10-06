# Architecture

```
resources/                 truth layer                        file layer                      gates
─────────                  ───────────                        ──────────                      ─────
indicators.json  ┐   spec.py       typed access to resources   layout.py   families (sticky per institution)
cohort.json      │   model.py      Person, Event, Encounter,   document.py table docs, two truth layers
narratives.json  │                 Reading, Finding            book.py     check-up books, clinic notes,
complaints.json  ├─▶ person.py     cohort, timeline, orders ─▶             reports, home logs           ─▶ audit/clinical
genomics.json    │   physiology.py values by identities        export.py   multi-date export tables         audit/readability
layout.json      │   profile.py    findings, complaints        hazards.py  named extraction hazards         audit/privacy
hazards.json     │   devices.py    health-store batches        corpus.py   routing, records                 audit/fidelity
templates.json   │   journal.py    diary sentences             delivery.py tiers, scenes
fiction.json     │   genomics.py   raw genotype exports        render/     pdf, sheet, grid, degrade
vocab.json       │   synthid.py    checksummed synthetic ids   pairs.py    minimal-contrast pairs          harness/ scoring
delivery.json    │   manifest.py   manifest.jsonl, people.jsonl
handwriting.json │                                             handwriting.py handwritten files (opt-in)
numbers*.json    │                                             render/hand.py pen, paper, legibility
llm_prompts.json ┘   llm/          optional: offline paraphrase enrichment (contract, cache, CLI)
```

## Layers

**Resources** (`mirobody_gen/resources/*.json`) are the only learned or hand-written knowledge in the
package. Each file declares `_source` (`public-standard`, `format-token`, `hand-authored`),
`_provenance` and `_vocabulary_fields`. All but `templates.json` (fixed printed wording, authored directly) are produced by the scripts
under `scripts/`; `tests/test_spec.py` regenerates them and fails on drift when the reference set is
present.

**Truth layer.** `person.py` builds a cohort of 60 people across eight disease archetypes with
event timelines, and schedules visits (annual check-up with a package tier, chronic-disease follow-ups,
clinic visits, single-item specialties). `physiology.py` turns a person, an indicator and a date into a
value: interval centre × individual baseline (CVG) × trend × event effects × log-normal noise (CVI,
CVA), rounded to the printed precision, with derived quantities computed from the rounded primaries.
`profile.py` gives each person a set of named findings (sticky, dated, growing) and each visit its
chief complaints, with the code each surface is expected to receive downstream. `manifest.py` writes
the visit-level truth. `devices.py`, `journal.py` and `genomics.py` produce the same person's other
sources from the same model.

**File layer.** `layout.py` samples a layout family per fictional institution from the format-token
distributions (column set, reference-range dialect, flag markers, unit position, date labels, page
furniture) and lets it drift by year and by print batch. `document.py` lays a visit's readings into
tables and records two truth layers per row: the printed cell (MedRepBench fields) and the semantic
reading. `book.py` composes check-up books (cover, summary and advice, general examination, department
key-value sections, auxiliary narratives with ECG strips and ultrasound images, laboratory tables),
outpatient records, ECG / ultrasound / imaging reports and home logs. `hazards.py` injects incident
hazards at their reference document rates and records every hazard with row attribution. `corpus.py`
routes each visit to its documents and writes `files.jsonl`; `delivery.py` chooses the delivery tier
and scene per file and renders it through `render/pdf.py` (HTML → PyMuPDF Story, one Story per block
with verify-and-retry), `render/sheet.py` or `render/degrade.py` (operator chains over the
rasterised page). `pairs.py` renders minimal-contrast pairs and aligned views.

**Handwriting** (opt-in, `build --handwriting`). `handwriting.py` decides which handwritten files a person
has (a notebook page of home blood pressure, glucose or weight; a doctor's note for a clinic visit; the
check-up institution's printed form with its results filled in by hand) and writes their truth: values come
from the same device series, physiology model and visits as the printed files. `render/hand.py` is the pen:
it writes strings glyph by glyph from a font subset (`render/fonts/`) with per-glyph wander in baseline, size,
rotation, spacing, slant, an elastic warp, pressure and ink colour, logs every string with the box it landed
in, draws ditto marks, strike-throughs, ruled columns and signature scribbles, and lays out ruled paper. A
printed form is rendered by `render/pdf.py` with placeholders in its result cells, which are found, redacted
and handwritten over. Capture reuses `render/degrade.py`; the page is also rendered without its values, both
go through the same scene from the same seed, and the difference measures every value's digit height and
ink contrast on the delivered image, replaying the scene's logged geometry to find it. A capture below the
floor falls back to a milder one.

**Gates** (`mirobody_gen/audit/`) do not import the generator. They re-derive identities from
laboratory definitions, re-extract text from the produced files, and re-implement the PII predicates,
so they cannot share the generator's blind spots. `harness/` scores predictions in the MedRepBench
format and in an alignment-based variant, exports labels, checks resolver coverage, and holds the
FHIR consistency checker used against PySynthea.

**Optional language-model layer** (`mirobody_gen/llm/`). Models work only after the truth is fixed and
before the gates: `mirobody-gen paraphrase` collects the narrative templates, asks a model for
rewordings, keeps only candidates that pass the contract in `llm/contract.py` (same slots, no new
numbers, locked terms intact, same language, bounded length, no names or identifiers, novel), and
writes them as a resource with `_source: llm-paraphrase` that the privacy gate scans without any
exemption. The generator picks one wording per template per institution from a stream of its own
(`book._wording`), so the build stays a deterministic function of resources, code and seed. The
resource is opt-in (`mirobody-gen build --paraphrase`); `mirobody-gen compare` verifies that the truth
layer is unchanged and reports wording diversity. Institution templates and candidate proposals follow the same
pattern; see `docs/zh-CN/llm-integration-2026-09-29.md`.

## Invariants

- **Determinism.** Every random stream is seeded from `(seed, person_id, …)` strings; no wall-clock,
  no unordered iteration on the value path. Same seed → byte-identical build.
- **Identities over sampling.** A derived quantity is never drawn; it is computed from the printed
  primaries so that a report is self-consistent to its printed precision.
- **One decision, one place.** Whether a unit is glued to a value or a flag is in its own column is
  decided once in `render/grid.py` and reused by PDF, XLSX and CSV; the truth is recorded at that point.
- **Shared truth across views.** Degradation changes pixels only. All tiers of a document carry the
  same `printed_rows`, `readings` and `blocks`; severe degradations go to the `stress` split.
- **Readable or abstain.** A row that a hazard made unreadable leaves the recall denominator and joins
  the must-abstain set; a value extracted for it counts as a hallucination.
- **Options do not disturb what they do not touch.** Handwriting draws only from streams of its own and
  appends its records after the printed ones: with it off a build is byte-identical to one made without
  the feature; with it on, every printed file and record is unchanged (`tests/test_handwriting.py`).

## Extending

- A new indicator: `scripts/build_indicators.py` (interval with a citable source, CVI/CVG, name
  variants; for qualitative items a population `positive_rate` and the printed positive values, for
  categorical items the `categories` with weights); the clinical audit requires a hard limit or an
  explicit exemption. Put it in an order group in `scripts/build_cohort.py` and, if it is read off an
  instrument rather than a sample, in a `params` auxiliary examination in `scripts/build_profile.py`.
- A new department item, finding, auxiliary examination or advice template: `scripts/build_profile.py`.
- A new layout dialect: `scripts/distill_layout.py` for format tokens from the reference set, or a
  hand-authored entry with `_source: hand-authored`.
- More wordings for a narrative template: `mirobody-gen paraphrase --dry-run` to see the requests, then
  with a model endpoint (`LLM_BASE_URL`, `LLM_API_KEY`) `--models <id> --write`; run the privacy gate on
  the result before using it.
- A new degradation scene: `render/degrade.py` (`_scene(...)`) plus a weight in
  `scripts/build_profile.py::DELIVERY`; the scene name becomes vocabulary automatically.
- New handwritten wording, a writing tier or a face: `scripts/build_handwriting.py`. A string with a
  character outside the font subsets fails the coverage test until the subsets are rebuilt from the pinned
  upstream files (`--fonts DIR --write`, see `render/fonts/README.md`). A scene a tier may use must move
  pixels only by rotation and perspective and resize last, or legibility cannot follow it.
