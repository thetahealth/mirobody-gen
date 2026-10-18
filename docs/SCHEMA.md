# Output schema

All files are JSON Lines unless stated. Every record carries `synthetic` (always `true`). Dates are ISO 8601
(`YYYY-MM-DD`; timestamps with offset where a time of day exists). Field names that match the mirobody
extraction contract (`original_indicator`, `value`, `unit`, `reference_range`, `status`,
`detection_method`) keep its meaning.

## Enumerations

| Field | Values |
| --- | --- |
| `exam_type` | `routine` (check-up) · `follow-up` (lab slips only) · `clinic` (outpatient record + lab slips) · `specialty` (single examination) · `home_log` · `export` |
| `exam_location` | `checkup-center` · `hospital` · `clinic` · `home` |
| `package` | `entry` (pre-employment) · `senior` (older-adult public-health examination) · `basic` · `standard` · `premium` · `null` |
| `kind` (document) | `lab_slip` · `checkup_book` · `outpatient_record` · `ecg_report` · `ultrasound_report` · `imaging_report` · `home_log` · `export` · `genetics_report` |
| `tier` | `T0` text-layer PDF · `T1` XLSX/CSV · `T2` scan · `T3` phone photo · `T4` degraded copy · `T6` screen |
| `handwriting.tier` | `H1` neat (regular-script Chinese, print-like English) · `H2` running hand · `H3` hard cursive, always a `T3` phone photo |
| `handwriting.scope` | `page` (everything on the page was written by hand) · `values` (a printed form whose results were filled in by hand) |
| `severity` | `mild` · `moderate` · `severe` · `null` |
| `split` | `main` · `stress` (hazard count above the reference p95, or `severe`) |
| `status` | `normal` · `high` · `low` (`""` on a `previous` reading and on home-log rows) |
| `value_kind` | `quantitative` · `qualitative` (negative/positive, graded `+`, cytology categories) · `categorical` (blood group) |
| `detection_method` | `laboratory` · `Physiological` · `Imaging` |
| `role` (reading) | `current` · `previous` (an "last result" column) · `export` (a row of a multi-date table) |
| `is_abnormal` | `"1"` · `"0"` · `""` (not determinable: numeric, no range and no flag printed) |
| hazard `source` | `layout` · `content` · `incident` |
| hazard `name` | the distilled taxonomy in `resources/hazards.json`, plus the handwriting classes in `resources/handwriting.json` (`hand.written`, `hand.correction`, `hand.ditto`) |
| `expect` (coding) | `coded` · `no-match` · `refused` · `needs-input` — what mirobody's ICPC-3 resolver should return for the surface |
| `call_status` | `called` · `no_call` · `unresolved` (site not in the catalogue) |

## `manifest.jsonl` — one record per visit

Default builds also write `generation.json` with the seed and
`encounter_stream_policy=encounter-streams-v2`. Routine scheduling keeps its
existing seed stream; incident visits use stable event identities, and each
scheduled visit has its own observation stream keyed by seed, person, date,
type, ordered panels, package and exact-slot occurrence. Distinct duplicate
slots retain multiplicity. Editing/deleting a future event or visit no longer
selects past encounter noise through a shared RNG draw count.

The v2 policy intentionally changes generated visits/values from the legacy
shared stream. Preserve frozen v1 inputs and outputs when making comparisons;
seed equality alone does not imply cross-version equality. In particular an
old high-TG/LDL-missing trigger must remain as an explicit regression input,
even if a v2 draw no longer triggers it. This is random-stream isolation, not
physiological calibration or proof that pre-existing clinical findings are fixed.
Whole-source temporal causality still requires separate acceptance; `build --verify` retains its
`temporal_counterfactual=not_run` boundary.

| Field | Meaning |
| --- | --- |
| `file` | placeholder `person/date/eNN`; the file layer replaces it |
| `person_id`, `collected`, `exam_type`, `exam_location`, `panels`, `package` | visit identity; `panels` are the order names from `cohort.json` |
| `condition_knowledge_policy` | `dated-diagnosis-knowledge-v1`: dated source knowledge and rule confirmations; latent archetypes are not clinical history |
| `department` | explicit source/encounter string, or `null` when unknown; it is not sampled from hospital names or patient demographics |
| `ordered_keys` | explicit unique ordered analyte keys before sex exclusions; `null` only for callers that did not declare orders |
| `missing_results[]` | ordered analyte with no result: `key`, `unit`, `reason_code`, `details`, `result_method`, `source`; never a numeric or normal result |
| `person` | `sex`, `age` (at the visit), `birth_year`, `height_cm`, `archetype`, `region`, `conditions[]` (`code`, `display`, optional source/onset; numeric-rule records add `recorded_date`, `evidence_dates`, `policy` and leave biological onset unknown) |
| `events_since_previous[]` | names of events that started, or were still ramping up, between the previous visit and this one |
| `complaints[]` | `text` (surface), `symptom_id`, `icpc3` (expected S-axis code or `null`), `expect`, `duration` |
| `findings[]` | `id`, `where` (section or auxiliary examination), `item`, `surface` (diagnosis wording), `icpc3` (expected D-axis code or `null`), `severity`, `since` |
| `rows[]` | one per reading: `key`, `original_indicator`, `value`, `unit`, `reference_range`, `status`, `detection_method`, `canonical_value`, `unit_ucum`, `loinc`, `value_kind`, `expect_resolvable`, `ref_low`, `ref_high`, `readable` |
| `layout_fingerprint`, `hazards`, `tier`, `format`, `language` | `null`/empty at this layer |

Diagnosis evidence is evaluated with `observed-diagnosis-time-v1` using only
encounters at or before `knowledge_as_of` (the current visit date).
`diagnostic_assessments[]` reports `met`, `pending_confirmation`, `not_assessable`
or `not_met`, the positive evidence dates and unknown/conflicting dates. These
are model-rule states, not independent clinical labels. Same-day repeats count
once. Unmeasured visits are unknown; measured negatives or conflicting days
reset the evidence run. Companion predicates must be met within the same
encounter, not joined from unrelated samples.

The existing low-eGFR branch requires repeat evidence spanning more than 90 days,
following KDIGO 2024 section 1.1.3's explicit duration definition; other CKD causes
and AKI exclusion are not covered. The overt-primary-thyroid branch retains the
existing TSH>=10 model condition and additionally requires FT4 below that result's
own `ref_low`, in compatible pmol/L. Missing FT4/reference/unit is not a normal
FT4 or a confirmed diagnosis. Source URLs are stored with the authored rules.
The existing low-Hb screening threshold now yields generic anemia (`271737000`),
never an inferred iron-deficiency cause (`87522002`). The 110 g/L threshold remains an
unvalidated model screen, not a complete WHO sex/population diagnostic rule.

Numeric-rule conditions have `source=synthetic_numeric_rule`, `recorded_date`
equal to the first confirmation date and `evidence_dates`; biological
`onset_date` stays null. A future dated condition/assessment cannot enter an
earlier record. `people.jsonl` retains full future simulation truth with
`truth_scope=full_simulation_timeline_not_point_in_time`, and must not serve as
a historical answer key. `dated-diagnosis-knowledge-v1` now keeps undated
archetype conditions exclusively as `people.jsonl.archetype_conditions`: latent
simulation design is not a dated diagnosis in an early record. `archetype`
itself is simulation metadata, not a patient-visible clinical assertion.

Optional `Person.diagnosis_history` source records contain `code`, `display`,
`recorded_date` and `source` (plus optional original metadata). Only a valid
calendar date and nonempty source establish time-specific knowledge. Undated
or unsourced declarations remain in the full `diagnosis_history` archive but
are not promoted; malformed supplied dates are rejected without truncation.
Treatment start never supplies a missing diagnosis date or years of illness.
An explicit, known source diagnosis is retained even when current readings
are normal. The generator's visit `conditions` context and standalone rendering
reuse these dated facts; source records are copied rather than overwritten.

Outpatient diagnosis/history and checkup history now use this knowledge scope,
not the person's archetype label. Unknown history prints "not documented",
without asserting a negative history; checkup truth sets
`history_status=not_documented` and `abnormal=null`. A pending assessment is
not a coded diagnosis. A known history shows its recorded date without
inventing biological onset. Started medication records are distinct from
diagnosis evidence. Outpatient bodies show the actual `Visit date` separately
from the existing report/print timestamps; diagnosis knowledge is scoped to
that visit, without shifting source times. An infection event cannot provide symptoms/diagnosis
before its declared start; this does not assert that real prodromes are impossible.

The existing v2 stream repair is validated for the bounded p035 case. Full
cross-source/multi-person time-specific causality remains open and is not
certified by these knowledge/export fixes.

Generated encounters partition every `ordered_keys` member into exactly one
`rows[].key` or `missing_results[].key`. Serialization and `build --verify` reject
omissions, duplicates, double outcomes and undeclared outcomes. Legacy inputs
without declared orders are explicitly `not_run`, not silently certified.
Sex-specific exclusions preserve the order with `not_applicable_sex_specific`.
Other unavailable outputs remain `result_unavailable`, without inventing a clinical cause.

LDL retains the existing Friedewald implementation and its existing `TG <= 4.5`
mmol/L boundary unchanged. An unavailable LDL records
`tg_above_calculation_limit` (including actual TG, its printed text `tg_text` and the limit), or
`calculation_inputs_missing` with the missing keys. No direct result is invented.
Calculated LDL carries `result_method=calculated_friedewald` and
`source=synthetic_calculation`, distinct from the `detection_method` channel.
An explicit `encounter_for(..., direct_ldl=Reading(...))` input must name
`direct_assay`, supply a nonempty source and consistent finite value/text in
mmol/L. It is copied unchanged, even when a calculated value is available.
Other units are rejected rather than converted. This input is not a model of
a direct assay, nor proof that the upstream engine produced such a result.

File manifests carry the rendered subset as `missing_results[]` with
`printed_text`; existing PDF/CSV/XLSX narratives show the explanation, and the
text-readability audit checks it. Non-results are excluded from numeric reading
truth, without removing the corresponding order from the encounter ledger.
Current numeric table rows/readings also retain `result_method` and `source`;
previous-value columns do not infer a method from the current result. Image-only
captures still need independent OCR; metadata preservation does not prove readability.

## `people.jsonl` — one record per person

`person_id`, `sex`, `birth_year`, `height_cm`, `archetype`, `region`, `conditions[]`,
`archetype_conditions[]`, `diagnosis_history[]`, `events[]`
with `name`, `event_type` (`medication` · `health_event` · `diet_change` · `exercise_change` ·
`long_term_habit`), `start_date`, `duration_days`, `health_effect`, `impact_level`,
`affected_indicators[]`, `effects{key: {magnitude, onset_days, half_life_days}}`. The effect triple is
the computable answer to attribution questions. `truth_scope` is
`full_simulation_timeline_not_point_in_time`: conditions and events cover the whole simulated
timeline, so this file is not a historical answer key for any single visit.

## `files.jsonl` — one record per file

The corpus selector's `institution-pool-v2` reuses a previously selected
institution of the same language/type when its fresh pool is exhausted,
weighted by prior selection counts. Before that formerly failing branch, source
records and successful draws keep their prior behavior. No extra default output
field is added solely for this repair; preserve the code/experiment policy
version with generated corpora. The selector does not certify clinical services.

Under `institution-assignment-v2`, every visit's institution is decided in calendar order across
the whole cohort, from a stream addressed by the visit (date, type, orders, package, occurrence);
popularity accumulates over time and a person's usual checkup centre and hospital are chosen at
their first visit. The genetics report, home logs and export draw from streams of their own. No
file therefore depends on a later visit of anyone: editing or deleting visits, events or values
after a cut-off leaves every earlier file byte-identical (checked for seed 7 with a cut-off matrix
over visits, events, effect sizes, measured values and an added incident, with positive controls).
v1 drew in person order from the document stream, so deleting one person's later visits changed
earlier files of people rendered after them.

| Field | Meaning |
| --- | --- |
| `file`, `doc_id`, `person_id`, `kind`, `encounter_dates[]`, `exam_type`, `package` | identity |
| `family`, `institution`, `issuer_kind`, `language` | layout family and fictional issuer; `language` ∈ `zh-Hans` · `zh-Hant` · `en`. A handwritten notebook page has family `hand:<person_id>`, no institution and `issuer_kind` `home` |
| `format`, `source_format` | file extension as delivered (`pdf`, `xlsx`, `csv`, `jpg`, `png`) and the family's native format (`handwriting` for a page written by hand) |
| `tier`, `scene`, `severity`, `ops[]`, `annotations[]`, `dpi`, `image_size` | delivery: scene name, sampled operator parameters in order, values circled by pen, rasterisation dpi |
| `container_fallback` (optional) | `policy_version=all-pages-v1`, `requested` (`jpg`/`png`), `actual=scanpdf`, `reason=multiple_pages`; records an explicit image request promoted to a multi-page image PDF |
| `page_count`, `layout` | pages; `layout` = `columns[]` (roles: `name`, `abbr`, `result`, `result_in`/`result_out` for the two-column US dialect, `reference`, `unit`, `flag`, `previous`, `category`, `note`, `method`, `lab`, `seq`), `reference_templates[]`, `flag_markers[]`, `page_count`, `languages[]` (the fingerprint input) |
| `jitter[]`, `dates[]`, `subject{}`, `cover{}` | family drift applied to this file; printed dates (`label`, `role` ∈ `collected`·`received`·`tested`·`verified`·`reported`·`printed`, `printed`, `iso`); subject fields; cover fields of a book |
| `hazards[]`, `hazard_count`, `split` | `name`, `source`, `rows[]` (indices into `printed_rows`) |
| `printed_rows[]` | the printed truth: `item_name`, `item_value`, `item_unit`, `item_range`, `is_abnormal` (MedRepBench fields), `readings[]` (indices into `readings`), `alternatives{}` (other acceptable spellings, e.g. the full sex-split cell), `readable`, `unreadable_fields[]`, `hazards[]`, `table` |
| `readings[]` | the semantic truth: `key`, `loinc`, `canonical_value`, `value_text`, `unit_ucum`, `value_kind`, `status`, `observed`, `role`, `expect_resolvable`, `printed_row` |
| `distractors[]` | rows that look like readings and are not (`subject_field`, `meta_row`, `flag_row`, `summary_duplicate`) |
| `blocks[]` | non-table content: `kind` (`kv` · `narrative` · `summary`), `title`, `section`, `items[]` (`label`, `value`, `abnormal`, `finding`, `detection_method`), `printed[]` (every string that must appear on the page) |
| `findings[]` | as in the manifest plus `expect` and `summary` (the wording used in the conclusion) |
| `summary[]` | conclusion, advice, judgement and critical-result lines: `kind` (`conclusion` · `advice` · `grade` · `critical`), `index`, `text`, `source` (`finding:<id>`, `lab:<group>`, `normal`, `judgement`, `critical`), optional `icpc3`, `surface`, `keys[]`, `template`; a `grade` line carries `area` and `grade` (`A`–`E`, after the Ningen Dock categories) |
| `complaints[]`, `diagnoses[]` | outpatient records: the surfaces printed under chief complaint and diagnosis with their expected ICPC-3 outcome (`kind` ∈ `symptom` · `condition`) |
| `care_context` (outpatient records) | `policy_version=outpatient-department-v1`, `department` (source text or `null`), `department_source` (`encounter` or `unknown`) |
| `facility_service` (optional) | constrained visit files only: `policy_version=facility-services-v1`, `profile_id`, `profile_sha256`, `catalog_sha256`, `authority=authored_synthetic`, `required_service`, `provider` |
| `ocular_exam` (checkup books with an eye section) | `policy_version=ocular-laterality-v1`, `scope=generated_eye_quantitative_items`, `not_reported[]` with `item_id`, `left_status=unknown`, `right_status=unknown` |

Eye-section quantitative `blocks[].items[]` add `item_id` (`va`, `va_corrected`,
`iop`), `laterality_policy_version=ocular-laterality-v1` and `measurements[]`.
Each of the two existing synthetic draws has explicit `laterality` (left/right),
`status=measured`, source `value` text, `unit` (`mmHg` for IOP, `null` for acuity),
`notation` (decimal/five_point for acuity, otherwise `null`) and
`authority=synthetic_model`. Both printed values now name their side; existing
draws, notation, abnormal/finding fields and random state are retained. IOP v1
assigns the first existing synthetic draw left, the second right; this is not a
rule for interpreting the order in historical or imported reports.

An omitted optional item is only `not_reported` with unknown side states. It does
not establish that either eye was tested, normal or explicitly unmeasured. Books
without an eye section do not assert ocular coverage. This version covers these
generated quantitative items only, not source-record replay, an explicit
`not_measured` input state or bilateral imaging/normal findings.

`--facility-services` requires `--render` and an explicit authored-synthetic JSON
profile; it affects the corpus, not pair files. The profile maps visit types to
required service codes and names catalogue institutions that declare those codes.
Unlisted means unknown support, not implicitly eligible. Selection checks both
fresh and sticky providers; absent language/location coverage fails before build
output. `provider` equals the file's selected institution. A support claim is a
synthetic configuration contract, not clinical validation or a real-provider
claim. Omitted `facility_service` makes no service-support assertion. Turning the
option off retains the original selection; it does not resolve all existing
facility/context realism problems.

An explicitly supplied `Encounter.department` prints verbatim as the first
outpatient body row, independent of the lab-family subject layout. A missing,
empty or whitespace-only value stays unknown; blank source text remains in the
manifest but is not promoted to a printed department. Current synthetic cohorts
do not invent this field. Default documents remain unchanged. Department presence
does not certify a facility's services or validate the note's clinical care model.
Lab slips built from the same encounter also prefer its explicit department over
their legacy random choice, retaining the random draw for layout reproducibility.
When no source department is known, their legacy sampling remains. Visibility
still follows each document's existing subject layout.

`pairs.jsonl` has the same fields plus `pair` (`pair_id`, `variant`); `variant` is `base`, a hazard
class name, or `view:<scene>` for an aligned view. Paired records also carry
`presentation`: `policy_version` (`paired-table-v2` or `paired-checkup-v1`),
`scope` (`table_extraction` or `checkup_document`),
and `purpose` (`ordinary` for the clean base, `stress` for named hazards and aligned
acquisition views). Purpose describes presentation intent; it does not certify
clinical realism. `split` continues to describe hazard-count/severity difficulty
and is independent of purpose. A stress variant can belong to `main`.

`build --pairs N --pair-views ordinary` writes only the same clean table controls
as `--pair-views all` (the default). It leaves clinical facts and the full stress
option available. Use separate output directories for each selection. This option
affects `pairs.jsonl` and `pairs/`; `--render` still writes the mixed corpus to
`files.jsonl`. An indicator without a known reference interval keeps that absence.

`paired-table-v2` replaces `paired-table-v1`: multi-page aligned captures use
an image PDF rather than silently retaining only page one in JPG/PNG. The
`container_fallback` describes the requested and actual container; scene, severity
and clinical truth stay the same. Single-page JPG/PNG bytes are unchanged. The
delivery layer applies this rule to any explicit multi-page JPG/PNG request, and
the low-level image encoder rejects multi-page single-image input. Retain the
policy version with historical evaluation results.

`--pair-scope checkup` selects routine encounters and builds full checkup books;
the default `tables` selects non-routine encounters. Both require at least five
readings. Checkup IDs use `checkupNNN`, while table IDs retain `pairNNN`. Checkup
ordinary/all selections share the same complete control document. Checkup stress
variants clone that frozen document for applicable incidental table corruptions
and six acquisition scenes. They preserve readings, examination blocks, findings,
summary, cover and embedded image bytes. Layout toggles that rebuild a document
are retained only in the table scope. Checkup capture scenes, including screenshot,
use a multi-page image PDF to retain every page; they do not use single-image
JPG/PNG containers. A missing table result can still appear in
the book's summary; the row's missingness does not establish whole-book question
unanswerability. Illustrations remain the existing synthetic props, without new
clinical-image truth or validation.

### `handwriting` (handwritten files only, `build --handwriting`)

| Field | Meaning |
| --- | --- |
| `tier`, `scope`, `paper` | writing tier, `page` or `values`, and the stock: `ruled_notebook` · `clinic_booklet` · `printed_form` |
| `hand` | `face` (font id, see `mirobody_gen/render/fonts/fonts.json`), `latin_face` (a Chinese hand's face for Latin characters its own face lacks), `ink` (`blue` · `blue_black` · `black`), `slant_deg`, `digit_px` (digit height on the page) |
| `corrections[]` | `row` (index into `printed_rows`), `struck` (the value written and crossed out), `written` (the correction beside it, which is the truth) |
| `dittos[]` | `row`, `column` (`date`), `stands_for` (the ISO date the ditto mark repeats; the row's readings carry it as `observed`) |
| `value_boxes[]` | `row`, `text`, `box` (x0, y0, x1, y1 on the page before capture, pixels at 200 dpi) — where each truth value was written |
| `page_size` | page width and height before capture, pixels |
| `legibility` | measured on the delivered image over every written value: `min_digit_px` (digit height, delivered pixels), `min_contrast` and `median_contrast` (luminance the ink removes, 0–255), and `rejected` (capture scenes tried first and refused for falling below the floor). A page that no capture keeps above the floor is not delivered |
| `transcript[]` | the page as a careful reader would type it, line by line: printed furniture, handwriting, ditto marks as `〃`; struck values are left out (they are in `corrections`) |

A handwritten file's `printed_rows` follow the conventions above with one reading of "printed": what the
hand wrote. `item_name` is the label as written (a log's column heading with the unit if the unit was
written there), `item_unit` is the unit that applies to the row wherever it was written (header, after the
value, or nowhere: `""`), `item_range` and `is_abnormal` are empty unless a printed form prints a range.
Home-log readings have `role` `export` and `status` `""` like the printed home logs; a note's or a form's
readings are the visit's own (`current`).

## `devices.jsonl` and `devices/<person>/<vendor>_batchNN.json`

Device truth also carries `pulse_model`: `policy_version=device-pulse-v2`,
`authority=synthetic_model`, and `contexts` mapping `rhr` to
`daily_resting_summary` and `hr` to `home_cuff`. This metadata stays out of vendor
batch payloads. Version 2 applies the existing `pulse` timeline factor to the
cuff's signal centre, retaining its baseline, 1.08 context multiplier, additive
noise, timestamps and sampling. Daily resting summaries retain their separate
noise and infection adjustment; clinic pulse retains its catalogue/person model.
These contexts do not imply equal numbers or validated clinical effect sizes.
Imported observations must retain their original authority and values rather
than pass through this synthetic event adjustment.

Per person: `vendor`, `source` (the `source` value a phone client sends), `files[]`, `habits`
(`weigh_rate`, `wearable`, `cuff`), `n_records`, `records[]` with `indicator` (the vendor field name),
`metric` (`weight` · `rhr` · `hr` · `steps` · `sbp` · `dbp` · `sleep`), `loinc`, `value`, `unit`,
`time`. Each batch file is `{"records": [{indicator, value, unit, time, source, end_time?}]}` with at
most 500 records.

## `vendor_signals.jsonl` and `vendor_signals/<person>/<vendor>.json`

The person's device series (the one `devices.jsonl` is written from) in each vendor's API shape. Vendors:
`apple` (every Apple Health store, HealthKit JSON) and `garmin` · `oura` · `whoop` (only for someone with
`habits.wearable` whose store is Apple Health or Health Connect). Each file is `{person_id, synthetic, vendor,
tz, records[]}`; a HealthKit record is `{type, input}`, any other `{data_type, payload}`, in the shape of the
acceptance records in mirobody's `kernel/decoders/samples/<vendor>/`. Per file, `vendor_signals.jsonl` has
`person_id`, `synthetic`, `vendor`, `file`, `n_records`, and `records[]` with `data_type` (the HealthKit type
for `apple`), `input` (the payload) and `expected_metrics` (the catalogue metrics the decoder should produce).

## `continuous.jsonl` and `continuous/<person>/…` (`build --continuous`)

One record per stream: a CGM sensor session or a heart-rate window. Every file it names is written under
`continuous/<person>/` in the device's own export shape; `docs/DEVICE_FORMATS.md` says what each shape rests
on.

| Field | Meaning |
| --- | --- |
| `person_id`, `synthetic`, `stream`, `tz` | `stream` ∈ `cgm` · `heart_rate`; `tz` the device clock's offset |
| `days[]` | the day plans the curve was drawn from: `day`, `wake`, `bed` (local, no offset), `meals[]` (`time`, `kind` ∈ `breakfast` · `lunch` · `dinner` · `snack`, `load` relative to lunch), `run` (`start`, `minutes`) or `null`, `walk_minutes`, `steps` (the store's daily total or model fallback), `rhr` (resting input from the store or model fallback), `rhr_provenance` (see below) |
| `hazards[]` | `name`, `count`; names from `resources/streams.json` `hazard_classes` (`stream.*`) |
| `exports[]` | the person's `apple_health_export/export.xml`, when they have one: it holds this stream's records among everything else in the store |

Day plans also declare `activity_policy_version=daily-activity-v3`, `steps_source`
(`phone_store` or `baseline_model`; manually constructed plans may be
`unspecified`), and `step_allocation` (`run`, `walk`, `other`, all in steps).

`rhr_provenance` carries `policy_version=resting-hr-provenance-v1`, `source`
(`phone_store`, `baseline_model`, or `unspecified` for a manual plan), `context`
(`daily_resting_summary`, `resting_model_expectation`, or `unspecified`), and
`record_time` (the chosen source record's original offset timestamp, or `null`
without one). Selection follows the existing local-date and last-record-in-input
policy; it does not convert the record to UTC or apply event effects again.
The phone-store records in this synthetic build remain synthetic, not clinical
observations. A fallback has no fabricated observation time. This additive
metadata changes neither the resting input nor the generated minute curves.

The synthetic model's fixed cadence (run 160, walk 105 steps/min) allocates run/walk steps within the unchanged
daily total. Proposed runs are shortened within the existing 25–50 minute model
range, or omitted when that minimum cannot fit. Exact `walks[]` (`start`,
`minutes`) are recorded alongside `walk_minutes`. Sampled walking bouts keep their
durations; conflicting starts are moved to the nearest available whole-bout slot
inside the local day and waking window, excluding the run and other walks.
Already feasible proposals retain their times. A sampled run outside its waking
day is moved without shortening. Placement failure raises an error instead of
deleting minutes. Day-plan creation, serialization and Apple export validate
explicit activity intervals and step conservation. Apple export rejects an inconsistent plan
instead of shrinking cadence after the heart-rate curve has used the longer run.
Fallback totals are model values, not measurements. This policy does not classify
low resting heart rate, infer fitness or couple symptoms to the curve. This is a
constraint on generated activities, not a rule for changing imported observations.

A CGM session adds:

| Field | Meaning |
| --- | --- |
| `session`, `device`, `maker`, `model` | `device` is a key of `streams.json` `devices` (`dexcom_g7` · `dexcom_g6` · `libre_2` · `libre_3` · `sibionics_gs1` · `guardian_4` · `ican_i3` · `yuwell_ct3` · `aidex`) |
| `unit`, `interval_min`, `start`, `end`, `ended` | display unit (`mg/dL` · `mmol/L`); `ended` ∈ `wear_period` · `sensor_failed` |
| `files[]` | `file`, `format`, `channel` (`file_upload` · `vendor_api` · `phone_store`), `unit` of the values in that file, `loinc` mirobody's resolver should give them (`2339-0` for mg/dL, `15074-8` for mmol/L); `indicator` for a phone-store batch, `uploader` (`share2` · `librelinkup` · `xdrip`) for Nightscout files, `at` (the moment a follower looked) for Share and LibreLinkUp snapshots. Formats: `dexcom_clarity_csv` · `dexcom_api_v3_egvs` · `libreview_csv` · `sibionics_clinic_csv` · `sibionics_app_xlsx` · `carelink_csv` · `nightscout_entries_json` · `nightscout_entries_csv` · `xdrip_sidiary_csv_zip` · `tidepool_export_xlsx` · `tidepool_export_json` · `dexcom_share_json` · `librelinkup_graph_json` · `api_data_batch` · `cgm_report_pdf`. Empty only for a session nothing leaves the app from (`stream.no_export`) |
| `files[]` of a report (`cgm_report_pdf`) | also `style` (`agp_v5` · `ican` · `yuwell_cn` · `aidex` · `sibionics_cn` · `cgm_sheet_2017` · `agp_cn2023`), `language` (`zh` · `en`), `created`, and `printed_rows[]`: `key`, `item_name`, `item_value`, `item_unit` as printed, `item_range` (the goal or normal value printed beside it, or `""`), `is_abnormal` (`"1"` when the value misses it, `""` without one). `key` is a metric (`days` · `active` · `mean` · `gmi` · `eag_a1c` · `cv` · `sd` · `mage` · `modd` · `lage` · `hypo_risk` · `tir` · `tbr_low` · `tbr_very_low` · `tbr_total` · `tar_high` · `tar_very_high` · `tar_total`, and on the hospital sheet `count` · `max` · `min` · `ge_13_9` · `ge_10` · `ge_7_8` · `le_3_9` · `le_2_8` · `in_3_9_10`); a per-day cell is `<metric>@<YYYY-MM-DD>` and a two-hour mean `period_mean@<HH>`. AiDEX's three bands are `tar` · `tir` · `tbr` (cut at 13.3 and 3.9 mmol/L), with `lbgi` and `lbgi_level`; iCan's event counts are `events_<hypo|serious_hypo|hyper|serious_hyper>` (and `…_minutes`, the average duration) and its postprandial cells `pp_<pre|pg1h|pg2h|peak|tpeak|ppge>@<meal time>`; Yuwell's time-of-day shares are `slot_<low|normal|high>@<slot>`. An AGP report covers the last 14 days of the sensor, a hospital sheet all of it |
| `expected` | `metric` (`bloodGlucoses`), `specimen` (`interstitial fluid`: a CGM reads tissue fluid, the catalogue code is blood's) |
| `readings[]` | `time` (local with offset), `kind` (`historic` · `scan`), `mgdl` and `mmol` as the device reported them (`null` past its range), `flag` (`low` · `high` · `null`), `true_mmol` (the blood glucose behind the reading), `artifact` (`compression_low` · `null`) |
| `gaps[]` | `start`, `end`, `cause` (`warmup` · `signal_loss` · `not_scanned`), `filled` (an outage the receiver backfilled: readings present) |
| `compressions[]` | `start`, `minutes`, `depth` (the fraction a reading fell) |
| `summary` | from the delivered historic readings (Low/High at the range limit): `n_readings`, `coverage_pct`, `mean_mmol`, `mean_mgdl`, `sd_mmol`, `cv_pct`, `gmi_pct`, `tbr_lt_3_0_pct`, `tbr_3_0_3_8_pct`, `tir_3_9_10_0_pct`, `tar_10_1_13_9_pct`, `tar_gt_13_9_pct`; and from the model, `hba1c_expected_pct` and `eag_mmol` on the first day |

A heart-rate window adds:

| Field | Meaning |
| --- | --- |
| `window`, `reason`, `start`, `end` | `reason` ∈ `cgm` (the days of the first sensor) · `infection` (around a cold) · `routine`; dates |
| `expected` | `metric` (`heartRates`), `loinc` (`8867-4`) |
| `multi_source` | the person wears two devices (a watch and a ring) recording the same minutes |
| `devices[]` | `wearable` (`apple_watch` · `huawei_watch` · `mi_band` · `android_watch` · `oura_ring`), `files[]` (`format` ∈ `api_data_batch` · `oura_api_v2_heartrate` · `zepp_life_heartrate_auto_csv`), `hazards[]`, `off_body[]` (`start`, `end`, `cause` ∈ `charging` · `off_overnight`), `samples[]` (`time`, `bpm` as recorded, `true_bpm`, `state` ∈ `sleep` · `rest` · `walk` · `run`) |

## `ocular.jsonl` — source-only ocular replay

Separate from generated checkup truth: `replay-ocular INPUT.jsonl --out DIR`
writes one PDF per source case, `ocular.jsonl` and a derived `audit.json` summary.
Input root fields are exactly `schema_version=ocular-observations-v1`,
`synthetic=true`, `case_id` (safe ASCII filename alias), `person_id`, `language`
(zh/en), `document_date`, `source_system`, and nonempty `observations[]`.

Each observation requires unique `id`, `source_id`, `item_id` (va/va_corrected/iop),
`laterality` (left/right/unspecified) and `status` (measured/not_measured/unknown).
Optional `value`, `unit`, `notation`, `effective_time` preserve their source text
and null/absence. Measured requires a nonblank supported quantitative string;
other states cannot carry a value. Missing units or observation time are not
invented. Times accept ISO dates or explicitly offset timestamps, preserved
without UTC conversion. No source scalar is assigned to both eyes. Repeated
item/side/time requires an explicit revision policy, which v1 does not supply.

Output keeps every input field and adds `policy_version=ocular-source-v1`,
`completion_performed=false`, `source_sha256` (canonical JSON source-packet hash),
`pdf_sha256`, and relative PDF `file`.
The PDF has source-only observation blocks, with no invented person demographics,
institution, diagnosis or normal finding. The document date is separate from the
individual observation times. `audit.json` reports source-state counts,
`audit_ok` for fidelity and `clinical_accuracy_measured=false`; it is not a clinical
gold label. `--audit-existing DIR` independently reads original inputs and saved
records/PDFs, including physical text; it does not trust the stored audit summary.
Unreferenced files in the output directory are outside that audit's scope.

This narrow packet is not a FHIR adapter. Extra codes, methods/reference intervals,
qualitative values or unknown fields are rejected, not silently discarded.

## Source-field review questions

`review-scenarios INPUT.jsonl --ocular-source --out OUTPUT.jsonl` reads the same
source packets without rendering or completing them. The opt-in question policy
is `ocular-source-questions-v1`. Local review cases retain `source_packet`; facts
must exactly match its observation fields. Outbound facts contain observation id
and JSON text of item_id/status/laterality/value/unit/notation/effective_time only,
with original null/absence. Person/source-system/source-location/document-date
metadata is excluded from the model packet.

Output adds `question_policy`, `source_packet_sha256` and two deterministic
`source_question_candidates[]` (status/laterality of the first source record).
Each candidate contains generated `text`, `fact_ids`, `question_policy`, `scope`,
`source_query: {fact_id, field}`, and `source_lookup: {field_present, value,
observation_status}`. A missing field yields false/null; an explicit null yields
true/null. Recorded unknown/unspecified states retain those literal values.

Optional model `answer.candidate_questions[]` in this mode consists only of at
most two distinct `{fact_id, field}` selectors. Free prose/extra keys/unknown ids
or fields fail the review. Valid selectors are materialized separately under
`llm.source_question_candidates`, preserving the raw model answer. Code owns the
wording; the model only selects source fields. Clinical criteria and arbitrary
input-question semantics remain unverified. The default free-text review format
is unchanged when this mode is off.

## FHIR Observation source snapshots

`snapshot-fhir INPUT.json --synthetic --out NEW_DIR` writes three files under
`fhir-source-v1`: `source.fhir.json` (original bytes), `observations.jsonl` (one
record per top-level entry Observation), and derived `snapshot.json`. Input is a
UTF-8 Bundle; this command does not validate a complete FHIR implementation guide.

Each record uses `schema_version=fhir-observation-source-v1`, `synthetic=true`,
`source_sha256`, `source_pointer` (`/entry/N/resource`), `entry_index`, source
`resource_id`/`version_id` (nullable projections), `observation_status`, recognized
top-level `value_keys`, exact `resource_json` string and `resource_sha256`,
`completion_performed=false`, and `clinical_use=not_adjudicated` or
`excluded_entered_in_error`. The raw string is the complete resource, including
numeric lexical precision and absent/null distinctions that nullable identity
projections do not expose. The record itself is an index envelope, not an FHIR
Observation or a completed clinical fact. No side, measurement state, current
version or unit conversion is inferred.

The summary counts entries, Observations, non-Observation entries, lifecycle
statuses and repeated resource IDs; it explicitly records no version selection,
no complete FHIR validation, and no assessed clinical validity. Full entry and
other-resource context remains in the source archive. References are not resolved;
modifier extensions are retained without interpretation.

`--audit-existing DIR` requires the original input, compares archived bytes and
recomputed record contents without writing, and ignores saved summary assertions.
It checks JSON types and raw resource spelling, not clinical correctness. Existing
output directories are never overwritten. Original-source snapshots remain
separate from `ocular-observations-v1` and the synthetic generation path.

`--reference-audit` emits a separate `policy_version=fhir-local-references-v1`
JSON diagnostic to stdout. It does not create or modify snapshot files. The
report has `source_sha256`, declared `scope`, `links[]`, `status_counts`,
`unscanned_resource_boundaries`, `external_requests=0`, no inferred head version,
and `clinical_validity=not_assessed`.

Each link contains `source_pointer`, escaped JSON `reference_pointer`, the literal
string `reference` (null for invalid non-string input), `resolution_status`,
`reason`, `candidate_pointers`, nullable `target_pointer` and
`target_clinical_use`. States include resolved, unresolved, ambiguous, unsupported,
type_mismatch and identity_conflict. Only a unique identity-consistent match can
yield a target pointer; resolved does not mean medically valid. Entered-in-error
Observation targets remain explicitly excluded. The scan examines reference-named
fields, including invalid/null ones; it does not resolve identifier-only/canonical
references or traverse nested Bundle/Parameters scopes.

`--body-patient-audit` emits a separate `policy_version=fhir-body-patient-v1`
diagnostic with `source_sha256`, declared `scope`, `observations_scanned`,
`observations_without_body_reference`, `checks[]`, `status_counts`,
`external_requests=0`, `projection_performed=false` and
`clinical_validity=not_assessed`. Its scope is the canonical root bodySite
BodyStructure-reference extension on top-level Observations, not all eye data.

Checks preserve Observation/body-reference pointers and reference diagnostics for
the body and both patient roles when assessable. `status` is same_patient_record,
different_patient_records, not_assessable or excluded; `same_patient_record` is
true/false/null. Successful identity comparison additionally records both identity
keys and `patient_version_relation` (not_provided/same_explicit_version/
different_explicit_versions). Every check has `projection_eligible=false` and
`clinical_error=not_adjudicated`. Same fullUrl across explicit versions is logical
record identity only; contained pointers do not escape their container. Distinct
patient records are not biological-person or clinical-error labels. Empty role
evidence and context exceptions remain unassessed. Existing snapshot/reference
formats and raw input resources are unchanged.

## FHIR source-field HTML views

`render-fhir-view INPUT.json --synthetic --entry N [--entry M] --out NEW_DIR`
uses `fhir-source-view-v1`. Three files are written: original-byte
`source.fhir.json`, self-contained `view.html`, and `view.json`. The entire input
Bundle is validated by the existing limited source checks before any selection
is rendered. Duplicate/non-Observation/missing indices fail; no version is sampled
or merged, and output directories are never overwritten.

The manifest contains `policy_version`, `synthetic`, `source_sha256`,
`component_policies` (source/references/patient_context), ordered pointer
`selection[]`, `records[]`, `html_sha256`, `semantic_projection=not_performed`,
`completion_performed=false` and `clinical_validity=not_assessed`. Each selected
record retains its full source snapshot record, relevant reference diagnostics,
patient checks, and linked body-reference target `resource_json`/source pointers.
Numeric spelling and unknown metadata remain in raw JSON; no lossy ocular schema
conversion is performed. HTML preserves absent versus null, shows raw source
fields and expandable originals, and escapes untrusted source markup.

`--audit-existing DIR --entry N` recomputes expected content from the original
input and explicit selection, comparing archive bytes, complete HTML bytes and
typed manifest content. Updating a saved HTML hash does not authorize a changed
value. This is deterministic source fidelity, not browser/OCR or clinical validity.

## `journal.jsonl`

`person_id`, `date`, `lang`, `text`, `expected[]`: either `{kind: "symptom", name, symptom_id, icpc3,
expect}` or `{kind: "measurement", key, value, unit, loinc}`.

## `genomics.jsonl` and `genomics/<person>_<vendor>.<ext>`

Per person: `vendor` (`wegene` · `23andme` · `ancestry` · `myheritage` · `vcf`), `build`, `file`,
`n_sites`, `n_pgx`, `n_off_catalog`, `n_no_call`, `sites[]` with `rsid`, `chrom`, `pos37`, `pos38`,
`ref`, `alt`, `gene`, `array_gt` (`0/0` · `0/1` · `1/1` · `null`), `vcf_gt`, `call_status`, `zygosity`,
`in_catalog`, `genotype_raw` (the literal printed in the file).

## `medications.jsonl`

One record per person who has at least one medication plan (a person with no treatment event has no
line at all). `person_id`, `synthetic`, `plans[]`:

| Field | Meaning |
| --- | --- |
| `plan_id` | `<person_id>-m<N>`, 1-based per person |
| `generic`, `name_zh`, `name_en`, `strength`, `route`, `frequency` | the drug; `frequency` ∈ `qd` · `bid` · `tid` · `qn` · `prn` |
| `dose_text_zh`, `dose_text_en` | the dose as a person or a label would phrase it |
| `start`, `end` | `start` is always the triggering event's own start date; `end` is `null` for an ongoing chronic medication |
| `reason_event` | the `people.jsonl` / manifest event name this plan was started for (`开始他汀治疗` etc., or `急性上呼吸道感染` for a short prn course) |
| `changes[]` | later dose adjustments: `date`, `strength`, `dose_text_zh`, `dose_text_en`, and `frequency` only on the one change where the frequency itself changes (metformin bid to tid) |

Mapped from the archetype's scripted intervention event (`resources/cohort.json`'s
`medication_plans`): statin to atorvastatin, antihypertensive to amlodipine, metformin (with an
optional later uptitration), iron to the same polysaccharide-iron-complex the clinic-note narrative
already names, and levothyroxine titrated 25 to 50 to 75 ug. A "lifestyle intervention" archetype
event has no drug and so produces no plan. Some people who are already being treated also get a
short ibuprofen prn course layered onto a cold.

Scripted treatments, including iron, remain ongoing (`end: null`) throughout the modelled
follow-up, matching the treatment events and clinic-note narratives. The generator has no
anaemia-resolution or iron-stop event from which to infer an independent finite iron course.

### A genetics report file (`kind: genetics_report`) adds

One per person with a genomics profile (`genomics.jsonl`'s own coverage, `COVERAGE = 0.4`), a
rendered summary page in WeGene's style for `zh` and a generic consumer-PGx-report style for `en`
(23andMe/Ancestry), going through the same per-person upload habit and `tier`/`scene`/`severity`
delivery fields as every other file -- some people's report is a scan or a phone photo rather than
a text-layer PDF. `issuer_kind` is `genomics_vendor`, `exam_type` is `genomics`, `family`/`package`
are `null`.

| Field | Meaning |
| --- | --- |
| `vendor` | same vendor as `genomics.jsonl`'s record for this person (`wegene` · `23andme` · `ancestry` · `myheritage` · `vcf`) |
| `pgx_rows[]` | the printed and truth rows, one per PGx-annotated site (always all 41): `gene`, `rsid`, `label` (star allele or clinical tag), `genotype` (printed two-letter call, or `--` for a no-call), `call_status`, `phenotype_zh`, `phenotype_en` |

A row's phenotype is derived straight from the genotype: `pgx_function` (`resources/genomics.json`)
names the allele's CPIC/PharmGKB function class for a deliberate subset of sites only, and a site
outside that subset prints "no established phenotype" rather than a guess. There is no polygenic
risk score or trait probability anywhere in this record -- the generator has no basis for one, since
sites are called independently and never phased into a diplotype.

`score`, the `oracle`/`rules` laboratory baselines, `medrep-view`, and `audit-fidelity` exclude
`genetics_report` records from their laboratory-row/layout metrics. Scoring reports excluded counts
as `skipped_documents` (for example, `{"genetics_report": 2}`); baseline and fidelity CLI output
also states the excluded count. `audit-readability` checks the PGx fields against the rendered
report through its dedicated genetics branch.
