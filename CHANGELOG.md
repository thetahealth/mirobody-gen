# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Schema enlarged from public sources (expert consensus on basic check-up items 2022, Ningen Dock
  judgement categories 2026, Shenzhen report standard DB4403/T 551.2-2024, critical-result consensus,
  US commercial laboratory report layouts): 73 new indicators (hepatitis B/C serology, extended
  tumour markers, coagulation, ABO/Rh, early renal injury, cardiac markers, insulin/C-peptide/2-h
  glucose, thyroglobulin and its antibody, rheumatology and immunoglobulins, pancreatic enzymes,
  non-HDL cholesterol, faecal occult blood, H. pylori antibody, spirometry, baPWV/ABI, body
  composition, echocardiography, HPV/cytology, SpO₂), 24 new order groups, `entry` and `senior`
  package tiers, a TCM constitution section, six auxiliary examinations (spirometry, arterial
  stiffness, body composition, echocardiography, fundus photography, mammography), 15 named findings.
- `categorical` value kind; population positive rates and per-person stickiness for qualitative items.
- A–E judgement grades and critical-result notices in check-up summaries (truth kinds `grade`,
  `critical`).
- Two-column US laboratory layout (`In Range` / `Out Of Range`, laboratory code column) for English
  institutions; hazard class `table.two_result_columns` is now live.
- Traditional-Chinese and Hong Kong item-name variants for common analytes.

- `tests/test_schema_extensions.py`: contracts for the new value kinds, sticky results, package
  tiers, parameter examinations, judgement grades, critical results and the two-column layout.
- Optional language-model layer `mirobody_gen/llm/`: paraphrase contract (`contract.py`), cached
  OpenAI-compatible client (`client.py`), and `mirobody-gen paraphrase` (dry run, call, or apply
  external answers; writes `resources/paraphrases.json` with `_source: llm-paraphrase`). Prompts are
  versioned in `resources/llm_prompts.json`. The privacy gate accepts `llm-*` sources only without
  vocabulary exemptions. Design note `docs/zh-CN/llm-integration-2026-09-29.md`; close reading of
  PureDocBench, OmniDocBench and Synthetic Hospital with pipeline v2 in
  `docs/zh-CN/research-2026-09-29-puredocbench.md`.
- `mirobody-gen paraphrase --refine N`: rejected candidates go back to the model with their reasons
  (progressive refinement after Kramer et al. 2026); design note §7 records the revised plan (L0: knowledge
  profile → cohort spec) and nine newly surveyed references in `docs/paper/refs.bib`.

### Changed
- Readings are routed to tables by the most complete order group, so basic and premium liver panels
  share one table type.
- Physical ceilings on bounded measurements (SpO₂, FEV1/FVC, ABI).
- `files.jsonl` now carries the documented `dpi` and `image_size` delivery fields.
- Event attribution (`events_since_previous`) also names events still decaying at the previous visit.
- `mirobody-gen audit-privacy` defaults to scanning `mirobody_gen/resources` and exits non-zero when
  a target contains nothing to scan; targets may be outside the repository.
- Command-line help and module summaries are in English; Chinese design notes stay below them.
- Public helpers replace cross-module private imports (`layout.script_of`, `layout.spellings`,
  `layout.date_role`, `profile.complaint_surface`, `spec.vocab`, `Family.lang_group`); the language
  group and home institutions are drawn from one random stream (`person.home_stream`).
- English documents print English department names.
- Readability audit strips the repeated institution header before matching, so a paragraph that
  crosses a page break is matched as one piece.

### Removed
- Dead code: `layout.hazards_of_family`, `Registry.families`, `synthid.is_synthetic`,
  `degrade.scenes_of_tier`, `corpus.EXT`, the `SPEC` alias, and stale stage references (P1–P4,
  `generator.*`, `spec/`) in docstrings.

## [0.3.0] — 2026-09-29

### Added
- Check-up books with cover, summary and advice, general examination, department key-value sections
  (internal medicine, surgery, ophthalmology, ENT, dental, gynaecology), ECG strip, ultrasound and
  radiography narratives, laboratory tables; three package tiers.
- Outpatient records carrying chief complaints coded against ICPC-3's symptom axis; ECG, ultrasound
  and imaging reports; home blood-pressure and weight logs.
- Image delivery tiers: 24 scenes (scan, app-enhanced capture, phone photo, photocopy, fax, aged
  archive, re-forwarded compression, screenshot, screen photo) as operator chains after PureDocBench,
  calibrated against aggregate statistics of the reference set's images; aligned views in pairs.
- Device batches in phone health-store format, one-sentence diaries with split-and-code truth, and
  consumer-genomics exports in five vendor formats with per-site truth.
- `harness/synthea_check.py`: consistency checker over FHIR bundles (reference ranges, flags,
  red-cell identities, precision).
- Console script `mirobody-gen` with `build`, `audit-*`, `score` and harness commands.
- Body temperature indicator; `delivery.json` and `vocab.json` resources.

### Changed
- Package restructured as `mirobody_gen` with resources shipped inside the wheel.
- Calibrated constants (delivery weights, unit-position weights) moved from code into resources.
- Readability audit also verifies blocks, complaints and diagnoses, strips page furniture before
  matching, and can report OCR recovery for image tiers.
- Clinical audit ignores near-zero quantisation and applies the pairwise RCV rule to short gaps only.
- Privacy gate accepts windows covered by up to three consecutive public terms, expands narrative
  templates by their declared slots, and requires generator identifiers to be declared.
- PDF renderer places one Story per block with verify-and-retry (MuPDF drops content across page
  breaks otherwise).
- Documentation reorganised: English README, architecture, privacy model and output schema; dated
  Chinese design notes under `docs/zh-CN/`.

### Removed
- Respiratory rate and glycated albumin from check-up packages; internal incident reviews and
  handoff memos from the tree.

## [0.2.0] — 2026-09-23

### Added
- Layout families per fictional institution sampled from format-token distributions; PDF text layer,
  XLSX and CSV rendering; hazard injection with row attribution; minimal-contrast pairs.
- Readability and fidelity audits; MedRepBench-format scoring with an alignment-based variant.

## [0.1.0] — 2026-09-22

### Added
- Indicator catalogue with public reference intervals and biological variation; cohort engine with
  archetypes and event timelines; physiology with identity-derived quantities; clinical and privacy
  audits; allow-list `.gitignore` and pre-commit privacy gate.
