# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

- Keep undated archetype conditions as full-simulation design instead of
  historical diagnoses. Visits and existing clinical documents use dated,
  sourced diagnosis knowledge (`dated-diagnosis-knowledge-v1`); unknown history
  remains undocumented, without invented years of illness or a negative history.
  Future infection/treatment entries no longer choose current symptoms or
  history-section draws. Full cross-source temporal acceptance remains open.

- Isolate scheduled incident decisions and per-visit measurement randomness
  (`encounter-streams-v2`), so future event edits no longer select past noise.
  Builds record the policy in `generation.json`. This intentionally changes
  legacy seed outputs; archived counterexamples remain explicit regression
  inputs. Whole-source temporal isolation and undated diagnostic priors remain
  open and are not certified by this repair.

### Added
- `build --verify`: runs the clinical, readability and privacy audits on the build just written and
  writes `verification.json` with passed/failed/skipped/not_run per check. A gating failure exits 1;
  replay detection without the reference index, image-tier OCR and unaudited source families are
  reported `not_run`, making the overall status `incomplete`, never `passed`.
- `score-semantic`: a separate offline numeric-reading scorer for authored LOINC, normalized values
  and units, observable dates, roles and supplied file context. One assignment drives all field and
  joint metrics; extra/missing predictions affect precision/recall and ambiguous submissions fail.
  Hidden previous results, incomplete dates and censored values have explicit eligibility limits.
  Existing five-field `score` results are unchanged; these metrics do not validate clinical labels.
- `review-scenarios`: offline evidence checks with an optional model critic, generic/expert rubric
  variants, exact evidence references, explicit unassessable results and candidate user questions.
  Model findings are not a clinical gate. The optional review transport supports DeepSeek credentials,
  explicit model discovery, bounded output, a shared CNY 10/day Asia/Shanghai ledger, usage reporting and validated
  request-addressed caching. See `docs/ITERATION_WORKFLOW.md` for limits and calibration boundaries.
  `--ocular-source` restricts candidate questions to code-worded source-field lookups.
- Paired presentation views: `build --pairs N --pair-views ordinary` writes only the clean table
  controls, and `--pair-scope checkup` builds full routine checkup books, freezes each one, and clones
  it for incident and capture stress. Records carry `presentation` (`paired-table-v2` /
  `paired-checkup-v1`, purpose `ordinary` or `stress`); purpose is not a clinical-realism label.
- `build --render --facility-services PROFILE`: optional authored-synthetic service profile; visit
  files are drawn only from institutions that declare the visit's required service and record
  `facility_service`. Off by default, without changing default selection.
- Offline source-only tools that never sample or complete facts: `replay-ocular` (per-eye observations
  with explicit laterality and measured/not_measured/unknown states, plus `--audit-existing`),
  `snapshot-fhir` (lossless Observation index over the original Bundle bytes, with read-only
  `--reference-audit` and `--body-patient-audit` diagnostics) and `render-fhir-view` (escaped,
  self-contained HTML source view with its own audit). None maps codes, picks a version or infers
  eye side.
- Manifest records declare `ordered_keys` and `missing_results[]` (reason, method, source) so every
  ordered analyte has exactly one result row or one stated non-result; rows carry `result_method`
  and `source`. `build --verify` rejects omissions, duplicates and undeclared outcomes.
- Manifest `diagnostic_assessments[]` (`observed-diagnosis-time-v1`): per-visit rule states (`met`,
  `pending_confirmation`, `not_assessable`, `not_met`) with evidence dates, computed only from visits
  up to that date. Numeric-rule conditions carry `recorded_date` and `evidence_dates`, leaving onset
  unknown; `people.jsonl` is marked `truth_scope=full_simulation_timeline_not_point_in_time`.
- Optional explicit `Encounter.department`, printed in outpatient records and preferred by same-visit
  lab slips; file truth `care_context` records it or `unknown`. Nothing infers it from names.
- Provenance and side metadata: device truth `pulse_model` (resting summary vs home cuff contexts),
  continuous day `rhr_provenance` (store record vs model fallback), `walks[]`, `steps_source` and
  `step_allocation`; checkup `ocular_exam.not_reported[]` and per-eye `measurements[]`; delivery
  `container_fallback`.
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
- The paraphrase resource is wired into the generator: one wording per template per institution (sticky,
  drawn from a stream of its own); diary entries vary entry by entry. The resource is opt-in
  (`mirobody-gen build --paraphrase`): two independent judge models did not prefer the rewordings over the
  hand-written templates, so the default corpus keeps the canonical wording. `mirobody-gen compare A B`
  reports wording diversity per narrative layer and checks that the truth layer is identical. The client
  reads `.env` (`LLM_API_KEY` may name `OPENROUTER_API_KEY`) and uses certifi or the system CA bundle.
- The paraphrase writer screens every accepted candidate against the reference-set replay index when
  the index is on the machine (`replay_windows`, factored out of the privacy gate) and records
  `replay_screened` in the provenance; `--workers N` runs model calls concurrently.
- First paraphrase resource shipped: `resources/paraphrases.json`, 591 wordings for 228 of 237 templates
  from `openai/gpt-6-luna` (68 candidates dropped by the replay screen, 397 by the contract).
- `mirobody-gen paraphrase --meaning-check MODEL`: a second model drops accepted candidates whose
  meaning drifted (frequency, degree, causality, referral); the contract additionally locks units on
  word boundaries, rejects ASCII punctuation between Chinese characters, keeps English sentence-initial
  slots in place, and never paraphrases chief complaints. Three-round blind comparison in the design
  note §8.4: more diverse, not judged more realistic; zero meaning changes flagged after the check.
- English documents print qualitative results in English (`value_localization`), including exports.
- `mirobody-gen paraphrase --refine N`: rejected candidates go back to the model with their reasons
  (progressive refinement after Kramer et al. 2026); design note §7 records the revised plan (L0: knowledge
  profile → cohort spec) and nine newly surveyed references.
- Handwritten files, opt-in (`mirobody-gen build --render --handwriting`): notebook logs of home blood
  pressure (`128/82` in one cell, two readings), glucose (fasting and after meals) and morning weight; a
  doctor's note in a clinic booklet with the vitals inline; and the institution's own printed form with its
  result column filled in by hand. Chinese and English, following the person's and the institution's
  language; three writing tiers (H1 neat, H2 running hand, H3 hard cursive, always a phone photo). Values come
  from the same device series, physiology model and visits as the printed files; home glucose is fasting from
  `physiology.measure` and post-meal from the ADAG relation between HbA1c and mean glucose. Records gain a
  `handwriting` field (tier, hand, corrections, ditto marks, value boxes, transcript, legibility) and three
  hazard classes in `resources/handwriting.json` (`hand.written`, `hand.correction`, `hand.ditto`), next to the
  distilled classes they also carry (`value.pair_in_one_cell`, `unit.in_header_or_reference_only`,
  `unit.missing`, `value.multiple_per_row`, …). Pages are scanned or photographed through the existing scenes and
  a capture is kept only if every written value still clears a legibility floor (digit height, ink contrast)
  measured on the delivered image. With the option off a build is byte-identical to before; with it on,
  printed files and records are unchanged and the handwritten records follow them in `files.jsonl`.
  Fonts: subsets of eight Google Fonts handwriting families (OFL 1.1; Homemade Apple Apache 2.0) in
  `mirobody_gen/render/fonts/`, pinned by upstream commit and sha256, renamed, licences alongside, rebuilt by
  `scripts/build_handwriting.py --fonts`. `audit-readability` checks handwritten records against their
  transcripts. Tests: `tests/test_handwriting.py`.
- Vendor-cloud payloads (`vendor_signals/`, `vendor_signals.jsonl`): the person's device series as HealthKit
  JSON for every Apple Health store, and as Garmin Health API, Oura v2 and WHOOP v2 objects for someone who
  wears a device and whose store is Apple Health or Health Connect, each with the catalogue metrics its
  mirobody decoder should produce.
- `mirobody-gen build --lang-mix 'zh:0.45,en:0.4,ja:0.15'`: the cohort's language groups as a build parameter.
  A group other than zh/en has its own device time zone, brand shares and allele-frequency column; its
  documents use the English templates.
- Continuous device streams, opt-in (`mirobody-gen build --continuous`; `continuous/`, `continuous.jsonl`):
  - **CGM sensor sessions.**
    - Devices: Dexcom G7/G6, FreeStyle Libre 2/3, Sibionics GS1, iCan i3.
    - Glucose minute by minute from the physiology model. The daily mean is the eAG of the expected
      HbA1c, so a sensor's GMI follows the laboratory's HbA1c and the metformin course.
    - The sensor adds interstitial lag, calibration drift, noise at the device's published MARD,
      compression lows, warm-up, backfilled and lost outages, and 8-hour scan memory.
  - **Intraday heart rate.**
    - Devices: Apple Watch, Huawei and Android watches, Xiaomi band, Oura ring.
    - Built around the store's own resting heart rate and sleep record. A run is a heart-rate peak and a
      glucose dip at the same minutes, because both streams read one day plan.
  - **Files, in each device's own shape** (`continuous_formats.py`):
    - Dexcom Clarity CSV and Web API v3 `egvs`; LibreView CSV.
    - Sibionics hospital CSV and app workbook.
    - Apple Health `export.xml`, including the store's daily records; Oura API v2 `heartrate`.
    - Zepp Life `HEARTRATE_AUTO`; mirobody `/api/data` batches.
  - **Truth and sources.**
    - The truth holds every reading with the glucose or heart rate behind it, gaps and their causes,
      consensus metrics (TIR/TBR/TAR, CV, GMI) and named `stream.*` hazards.
    - Device facts and format tokens are in `resources/streams.json` (`scripts/build_streams.py`), each
      with sources and a confidence. `docs/DEVICE_FORMATS.md` is the URL ledger, including the formats
      not modelled yet.
  - A build without the option is byte-identical to before. Tests: `tests/test_continuous.py`.
- CGM reports, for sensors whose app gives no readings file (`cgm_report_pdf`; `cgm_metrics.py`, `cgm_reports.py`,
  `render/agp.py`):
  - Styles:
    - each vendor's own: iCan (from its report portal's strings: events, postprandial table, multi-day details),
      Yuwell (TIR达标情况, time-of-day shares) and AiDEX (three bands cut at 13.3 mmol/L, 10–90% AGP, LBGI);
    - the core pages of Sibionics' Chinese report;
    - the IDC AGP Report v5 (English);
    - the 2017 Chinese guideline's hospital CGM report sheet;
    - the 2023 Chinese AGP consensus template, kept for an app whose layout is unknown.
  - Metrics by the consensus (ranges, GMI, CV) and the Chinese guideline (SD, MAGE per 24 h, MODD, LAGE).
  - Every printed value is recorded as `printed_rows` in the MedRepBench fields, with the goal printed beside it.
  - New devices: Yuwell Anytime CT3 and MicroTech AiDEX, which, like iCan i3, deliver only reports.
- More CGM formats:
  - Medtronic Guardian 4 via CareLink Personal CSV (sectioned, newest first, Index running across sections).
  - A wearer's own set-up: Nightscout `entries` JSON and CSV as the server normalises them (share2,
    LibreLink-Up or xDrip+ uploader), xDrip+'s SiDiary CSV zip, Tidepool's Excel and JSON exports (mmol/L storage,
    unrounded conversion, ExcelJS workbook layout), and a follower's Dexcom Share or LibreLinkUp snapshot.
  - Sources and confidence for each in `docs/DEVICE_FORMATS.md`.
- `audit-privacy` reads zip members and OOXML workbooks named `.xls`.
- `physiology.expected`: an indicator's level on a day without measurement noise (shared by the
  handwritten glucose logs and the CGM).
- `audit-privacy` reads `.xml` files (an Apple Health export), one attribute value or text node per unit.
- `medications.jsonl`: a plan per person's scripted treatment event (statin, antihypertensive,
  metformin with optional uptitration, iron, levothyroxine titration), mapped to a first-line drug
  and starting dose consistent with the clinic-note narrative, plus an optional short ibuprofen prn
  course during a cold. Previously a treatment only ever showed up as a timeline event, so an agent's
  medication tool found nothing to read.
- `--render` now also writes one vendor-style pharmacogenomics report page (WeGene layout for `zh`,
  a generic consumer-PGx layout for `en`) per person with a genomics profile, added to `files.jsonl`
  as `kind: genetics_report`. Each row prints the gene, rsID, genotype and, only where
  `resources/genomics.json`'s `pgx_function` table supports it, a CPIC/PharmGKB-sourced phenotype
  sentence; no polygenic score or trait probability is invented.

### Fixed
- `--continuous` scheduling no longer depends on later events: each heart-rate window and glucose
  sensor session draws from a stream of its own, overlaps resolve in calendar order and windows are
  numbered in it, and the baseline sensor of the metformin archetype sits 130-180 days into the
  window (before the earliest scripted start) instead of 14-40 days before the actual metformin
  start. Before, adding a future cold moved a past ordinary heart-rate week (seed 7: p021, p029,
  p058), and deleting a future metformin start changed the past baseline sensor (8 of 8 users).
  Continuous outputs change for existing seeds. A CGM reading's clock jitter no longer places the
  last reading after its session's end.
- A cold's ibuprofen prn course depends on treatment in progress on the day of the cold, each plan
  draws from its own stream and plans are numbered in date order: deleting a future metformin start
  used to remove or renumber an earlier course (seed 7, p020).
- HbA1c now follows glucose history instead of driving it: the glycaemic state (mean glucose as a
  steady-state HbA1c) sets each CGM day's mean and the handwritten logs, and laboratory HbA1c is that
  state's history over the preceding 100 days with a linearly falling weight (Tahara & Shima, Diabetes
  Care 1995). Before, the CGM mean was solved to the same day's HbA1c, and a 90-day onset on
  metformin's HbA1c effect stood in for the lag; that effect now acts on the state with glucose's
  30 days. After metformin starts, sensor mean and GMI fall within weeks while HbA1c follows over
  months (half-way about 30 days later; the paper measured 34.6 days). Visits in that lag name the
  treatment. Red-cell lifespan, haemoglobin variants and glycation-rate differences are not modelled.
- Atorvastatin exposure is one shared state: the statin event's course (dose steps with adherence, an
  optional stop) drives LDL/TG, the `medications.jsonl` plan and the clinic note. The response per
  dose is the LIPITOR label's Table 8 (20 mg: LDL-C -43%, TG -26%, previously a hand-set -38%/-12%)
  with full effect at 4 weeks; curve shape, washout, adherence scaling and population transfer are
  declared assumptions. `person.with_course` sets titration, adherence or a stop for a scenario. The
  default course is unchanged: 20 mg daily from the start, never stopped.
- `events_since_previous` no longer names an event whose effect is flat across the interval: a
  treatment started years earlier was cited at every later visit (p029's statin at all 12 visits
  after it started), and so was an ended event whose effects never decay. Attribution answers and
  the RCV audit's event stratum change accordingly.
- Measurement noise and the longitudinal RCV audit now share one declared lognormal model
  (`resources/measurement.json`): the log-scale SD is sqrt(ln(1 + CV^2)), so printed values have the
  catalogue CV (CV 42% printed as 44% before), and the audit's 95% threshold is
  1.96 * sqrt(2 s^2 + rounding) on the trend-adjusted log ratio of pairs with no intervening event.
  The old ln(1 + 2.77 CV) gate was narrower than the model (7.8% in a pre-registered simulation; seed 7
  printed 10% against a stated ~5%). Seeds 7/11/23 now give 5.3%/4.6%/4.7% (exact binomial p >= 0.12);
  every excluded pair is counted by reason. Measured values change slightly for existing seeds.
- Rendered files no longer depend on later visits of other people: institutions are assigned in
  calendar order across the cohort from per-visit streams (`institution-assignment-v2`), and the
  genetics report, home logs and export use streams of their own. Deleting p001's visits after
  2023-03-01 had changed 14 earlier files of 10 other people. Rendered bytes change for existing seeds.
- The configured statin LDL effect (-38%) is now the simulated LDL change: LDL cholesterol is the
  modelled lipid state and total cholesterol is derived as LDL + HDL + TG/2.2, so the printed values
  keep the Friedewald identity. Before, LDL was computed from a separately sampled total cholesterol
  with its own statin effect, giving -31% to -52% by baseline. The TC effect and TC archetype shift
  are removed rather than left unused; lipid values change for existing seeds.
- `--people N` now yields exactly N people (largest-remainder apportionment by archetype weight; one
  per archetype once N ≥ the number of archetypes). Previously 61 gave 60, 100 gave 99, and 7–9
  dropped the archetypes last in the table. The full cohort is unchanged; other sizes draw different
  people than before. Non-positive or non-integer sizes are rejected.
- `--lang-mix ja:1 --render` no longer dies with `KeyError ('ja', 'hospital')`: ordinary visits pick
  institutions by document-wording group, while the demographic group (device time zone, genetics)
  stays `ja`. zh/en builds are unaffected.
- `score` rejects prediction files absent from the manifest, repeated `image` lines and malformed
  lines instead of ignoring or overwriting them; it reports `documents_without_prediction` and
  `unscored_predictions`. With `--medrep-labels`, whose label CSV is a subset by construction,
  predictions outside it are counted in `predictions_outside_labels` instead of failing the run.
- Generic OpenAI-compatible calls (`paraphrase`, `llm-panel` with `LLM_BASE_URL`) no longer follow
  redirects, which would re-send the Authorization header, and refuse a credential containing
  whitespace or control characters instead of quoting it in an error.
- Ordered LDL no longer disappears silently when triglycerides exceed the existing Friedewald limit
  (4.5 mmol/L) or an input is missing: the visit records `tg_above_calculation_limit` (with the
  actual TG) or `calculation_inputs_missing`. The formula and limit are unchanged, and no direct
  assay result is invented; an explicitly supplied direct LDL is copied unchanged.
- Diagnoses from numeric rules no longer leak into earlier visits: each visit sees only evidence up
  to its date, so a condition first confirmed later is not written into earlier records.
  Low-eGFR CKD now needs repeat evidence spanning more than 90 days (KDIGO 2024 §1.1.3); overt
  hypothyroidism needs a same-visit FT4 below its reference limit in addition to TSH ≥ 10; low
  haemoglobin yields generic anaemia rather than an inferred iron-deficiency cause. Regenerating
  after editing future events can still change past values (a shared random stream); see
  `docs/SCHEMA.md`.
- Continuous streams (`daily-activity-v3`): a sampled run can no longer need more steps than the
  day's total, and runs and walks no longer overlap each other or leave the waking day; durations
  and the daily device facts are kept, and an impossible placement is an error.
- Home cuff pulse (`device-pulse-v2`) now applies the configured `pulse` event factor like resting
  and clinic pulse; its baseline, context multiplier and noise are unchanged.
- An explicit JPG/PNG capture of a multi-page document no longer silently keeps only page one: it is
  delivered as a multi-page image PDF and recorded in `container_fallback`; the low-level encoder
  rejects multi-page single-image input.
- An exhausted institution pool no longer raises `StopIteration` (`institution-pool-v2`): it reuses an
  institution already drawn from the same language and type; earlier draws are unchanged.
- Visual acuity and intraocular pressure rows name the eye (`ocular-laterality-v1`); the two existing
  draws are kept, the first IOP draw being left by definition.
- Evaluate derived laboratory quantities in dependency order: waist, albumin/globulin and free/total
  PSA ratios now appear when ordered. Weight interventions now affect the shared weight trajectory
  and BMI; disease trends start at the observation-window anchor, independently of unrelated events.
- Independent per-site genotype draws no longer assert VCF haplotype phase. Removed the incorrect
  `HK` label from HBsAg name variants in both the authoring script and generated catalogue.
  These corrections change generated output for existing seeds; keep code/resource versions with
  archived corpora and regenerate affected comparisons.
- Mixed `files.jsonl` manifests containing `genetics_report` no longer crash the laboratory scorer,
  oracle baseline, MedRepBench export, or fidelity audit. PGx records stay outside laboratory
  metrics; scorer, baselines and fidelity output state the excluded count. A genetics-only
  fidelity input reports that there are no compatible documents.
- Diary symptom streaks count secondary symptom mentions too, so a symptom mentioned between
  primary entries no longer resets from months to days merely because it was the second item.
- Iron plans no longer invent a 3-6 month stop date independently of the ongoing treatment event
  and later clinic notes. They remain ongoing throughout the modelled follow-up.
- SLCO1B1*5 (`rs4149056`) uses CPIC's `no function` allele class in PGx truth and report text;
  `decreased function` is an individual's diplotype phenotype, not this allele's class.
- Random incidents now respect age and the calendar: "连续加班一个月" and "献血 400mL" no longer land
  on people past working age or past the legal donation window, and "春节假期饮食" starts within a
  few days of that year's actual Lunar New Year instead of an arbitrary month. A diary symptom
  streak's stated duration ("3天", then "2周" days later) is now derived from elapsed time since the
  streak's first mention rather than drawn fresh each entry.
- Urine specific gravity stays physiological: 79% of values were below 1.000 or above 1.040
  (0.84–1.25) because noise scaled the whole 1.0xx and the audit exempted the key. Variation now acts
  on SG − 1 (1.004–1.040, median 1.015) and the audit bounds it to 1.000–1.050.
- English complaints and diary lines no longer print ICPC-3 rubric titles as a person's words
  (`Been Neck symptom or complaint for a year`, 201 lines in one corpus). 35 symptoms gained lay
  English that `resolve_symptom` codes exactly; the three it cannot code yet use plain wording and
  expect abstention. Corpora built after this differ from earlier ones; pinned-commit benchmarks are unaffected.
- The default language draw is 0.3.0's again (`--lang-mix` had reversed it, so every person of a seed
  changed language group). `build --seed 7 --people 6 --render` again reproduces the `files.jsonl`
  (sha256 `5bad865b…`) mirobody's OCR evaluation pins at 248df0f; builds made from 2026-10-06 to this fix
  differ from both. A mix now draws the same cohort however its groups are ordered, and a bad mix is a
  usage error.
- Vendor-cloud payloads now carry the same series as `devices.jsonl`: before, they were drawn from a second,
  forced series, so a person could have a Garmin with no wearable in the phone store and different values
  for the same day. HealthKit payloads, documented but never written, are written.
- `.gitignore` re-includes `docs/` so that new top-level documents can be tracked; the companion
  article's working material (`docs/zh-CN/`, `docs/paper/`) stays local.
- Links into the untracked `docs/zh-CN/` from `docs/README.md` and `README.zh-CN.md`; links to mirobody's
  decoder samples point at `main` instead of a feature branch.
- Complaint vocabulary re-checked against mirobody main's #151 lay-English additions: `sweating`,
  `sputum` and FRONTIER's `cramps` had quietly started coding (AS10, RS15, LS13) while still marked
  as abstentions. `cramps` moved into SYMPTOMS since LS13 is a genuine ICPC-3 inclusion for it; a
  wider lay-English sweep added further surfaces across 30 symptoms that now resolve exactly.
- FRONTIER's `pain_generic` and `hiccup` gained English wording (`ache`, `can't stop hiccuping`) so
  an English diary no longer falls back to the Chinese surface when `en` is empty: 9 of 1031 English
  diary lines in a zh:0.6,en:0.4 build carried a bare Chinese word before this fix.

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
- `docs/paper/refs.bib` from the public tree: like `docs/zh-CN/`, it is working material for the
  companion article and is kept locally.

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
