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
| `kind` (document) | `lab_slip` · `checkup_book` · `outpatient_record` · `ecg_report` · `ultrasound_report` · `imaging_report` · `home_log` · `export` |
| `tier` | `T0` text-layer PDF · `T1` XLSX/CSV · `T2` scan · `T3` phone photo · `T4` degraded copy · `T6` screen |
| `severity` | `mild` · `moderate` · `severe` · `null` |
| `split` | `main` · `stress` (hazard count above the reference p95, or `severe`) |
| `status` | `normal` · `high` · `low` (`""` on a `previous` reading and on home-log rows) |
| `value_kind` | `quantitative` · `qualitative` (negative/positive, graded `+`, cytology categories) · `categorical` (blood group) |
| `detection_method` | `laboratory` · `Physiological` · `Imaging` |
| `role` (reading) | `current` · `previous` (an "last result" column) · `export` (a row of a multi-date table) |
| `is_abnormal` | `"1"` · `"0"` · `""` (not determinable: numeric, no range and no flag printed) |
| hazard `source` | `layout` · `content` · `incident` |
| `expect` (coding) | `coded` · `no-match` · `refused` · `needs-input` — what mirobody's ICPC-3 resolver should return for the surface |
| `call_status` | `called` · `no_call` · `unresolved` (site not in the catalogue) |

## `manifest.jsonl` — one record per visit

| Field | Meaning |
| --- | --- |
| `file` | placeholder `person/date/eNN`; the file layer replaces it |
| `person_id`, `collected`, `exam_type`, `exam_location`, `panels`, `package` | visit identity; `panels` are the order names from `cohort.json` |
| `person` | `sex`, `age` (at the visit), `birth_year`, `height_cm`, `archetype`, `region`, `conditions[]` (`code` SNOMED CT, `display`, optional `onset_date` and `source` when inferred from values) |
| `events_since_previous[]` | names of events that started, or were still ramping up, between the previous visit and this one |
| `complaints[]` | `text` (surface), `symptom_id`, `icpc3` (expected S-axis code or `null`), `expect`, `duration` |
| `findings[]` | `id`, `where` (section or auxiliary examination), `item`, `surface` (diagnosis wording), `icpc3` (expected D-axis code or `null`), `severity`, `since` |
| `rows[]` | one per reading: `key`, `original_indicator`, `value`, `unit`, `reference_range`, `status`, `detection_method`, `canonical_value`, `unit_ucum`, `loinc`, `value_kind`, `expect_resolvable`, `ref_low`, `ref_high`, `readable` |
| `layout_fingerprint`, `hazards`, `tier`, `format`, `language` | `null`/empty at this layer |

## `people.jsonl` — one record per person

`person_id`, `sex`, `birth_year`, `height_cm`, `archetype`, `region`, `conditions[]`, `events[]`
with `name`, `event_type` (`medication` · `health_event` · `diet_change` · `exercise_change` ·
`long_term_habit`), `start_date`, `duration_days`, `health_effect`, `impact_level`,
`affected_indicators[]`, `effects{key: {magnitude, onset_days, half_life_days}}`. The effect triple is
the computable answer to attribution questions.

## `files.jsonl` — one record per file

| Field | Meaning |
| --- | --- |
| `file`, `doc_id`, `person_id`, `kind`, `encounter_dates[]`, `exam_type`, `package` | identity |
| `family`, `institution`, `issuer_kind`, `language` | layout family and fictional issuer; `language` ∈ `zh-Hans` · `zh-Hant` · `en` |
| `format`, `source_format` | file extension as delivered (`pdf`, `xlsx`, `csv`, `jpg`, `png`) and the family's native format |
| `tier`, `scene`, `severity`, `ops[]`, `annotations[]`, `dpi`, `image_size` | delivery: scene name, sampled operator parameters in order, values circled by pen, rasterisation dpi |
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

`pairs.jsonl` has the same fields plus `pair` (`pair_id`, `variant`); `variant` is `base`, a hazard
class name, or `view:<scene>` for an aligned view.

## `devices.jsonl` and `devices/<person>/<vendor>_batchNN.json`

Per person: `vendor`, `source` (the `source` value a phone client sends), `files[]`, `habits`
(`weigh_rate`, `wearable`, `cuff`), `n_records`, `records[]` with `indicator` (the vendor field name),
`metric` (`weight` · `rhr` · `hr` · `steps` · `sbp` · `dbp` · `sleep`), `loinc`, `value`, `unit`,
`time`. Each batch file is `{"records": [{indicator, value, unit, time, source, end_time?}]}` with at
most 500 records.

## `journal.jsonl`

`person_id`, `date`, `lang`, `text`, `expected[]`: either `{kind: "symptom", name, symptom_id, icpc3,
expect}` or `{kind: "measurement", key, value, unit, loinc}`.

## `genomics.jsonl` and `genomics/<person>_<vendor>.<ext>`

Per person: `vendor` (`wegene` · `23andme` · `ancestry` · `myheritage` · `vcf`), `build`, `file`,
`n_sites`, `n_pgx`, `n_off_catalog`, `n_no_call`, `sites[]` with `rsid`, `chrom`, `pos37`, `pos38`,
`ref`, `alt`, `gene`, `array_gt` (`0/0` · `0/1` · `1/1` · `null`), `vcf_gt`, `call_status`, `zygosity`,
`in_catalog`, `genotype_raw` (the literal printed in the file).
