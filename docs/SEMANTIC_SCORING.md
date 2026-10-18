# Observable semantic scoring (v1)

The existing `mirobody-gen score` measures five printed fields. It does not score
LOINC, canonical numeric values, units, or observation dates, despite its old
module docstring suggesting that optional semantic fields are evaluated.
This separate, offline module adds a deliberately narrow semantic contract:

```sh
mirobody-gen score-semantic out/corpus/files.jsonl predictions.jsonl --json report.json
```

No model, network access, extra dependency, or change to existing scores is involved.
Scores measure agreement with authored corpus labels. They do not validate the
clinical appropriateness of those labels, diagnose patients, certify valid UCUM
syntax in the existing catalogue, or measure linking different files to a patient.

## Prediction JSONL

Each nonempty line must have exactly `file` and `readings`. Emit current/export
numeric readings only; previous-result observations are outside v1's scope.
Omit an entire unreadable observation, or use `null` to abstain on an
individual field. An empty unit string means dimensionless; it is not abstention.

```json
{"file":"files/p001/report.pdf","readings":[{"id":"local-result-1","item_name":"空腹血糖","loinc":"1558-6","canonical_value":5.6,"unit_ucum":"mmol/L","observed":"2025-01-02","role":"current","source_file":"files/p001/report.pdf"}]}
```

All shown keys are required. `id` is a nonempty, prediction-local ID unique within
that file; it has no relationship to any truth index. `item_name` must be the
visible name from the source. Other fields accept `null`. `canonical_value` must
otherwise be a finite JSON number. For a censored value such as `<5.6`, use
`canonical_value=null`: v1 does not evaluate bounds or comparison operators.
`observed` is a calendar date in `YYYY-MM-DD`
form, not a timestamp. Roles are `current`, `previous`, and `export`.
`source_file` is the filename supplied with the input; it checks retention of
that context only. Hidden person IDs and document IDs are never required.

Truth indices, internal indicator keys, extra fields, malformed dates/numbers,
duplicate file records, duplicate prediction IDs, and files absent from the
manifest are rejected. The CLI exits with status 2 and, if `--json` is supplied,
writes `status=invalid_submission` to replace any stale success report. A
duplicate observation with a different prediction ID is allowed into
scoring and counts as an extra prediction if it cannot match another truth.
Omitted files have zero predictions and are included in recall denominators.

## Matching and scores

One Hungarian assignment is shared by every field and the joint metric. A
compatible visible name is mandatory (exact normalized name, or the existing
scorer's parenthetical-name relaxation). A code alone or a numeric value alone
cannot create a match. Matching prioritizes cardinality, then visible-name
similarity and agreements on evaluable code, date, and role. Numeric value,
unit, source filename, IDs, and internal truth indices do not choose matches.
Unevaluable fields cannot influence matching.

One `128/82` printed cell can produce two readings with the same visible name;
their codes distinguish systolic and diastolic readings. Predictions can be
reordered when the available identity fields distinguish observations. Repeated
observations are never collapsed into a dictionary keyed by code or date.

The report includes:

- Matched reading precision, recall, F1, and raw counts.
- Per-field eligible counts, matched eligible counts, correct counts, recall
  over all eligible truth, and accuracy conditional on a match.
- `all_applicable_fields` precision, recall, and F1: a matched reading counts
  only if every evaluable field passes on that same assignment. Extra readings
  reduce precision; missing readings reduce recall.
- Per-file assignments, ambiguous identity groups, missing truth indices, extra prediction IDs, and counts
  explaining every excluded reading and masked field.

No eligible observations produce `null` metrics, not a perfect score. The report
status `valid_submission` refers only to input validity, not quality or passing
a medical gate. There is no pass threshold or composite field average.

Numbers use only a 1e-9 representation tolerance. The unit string must match
the authored canonical unit exactly; the scorer does not silently convert values
or accept clinically approximate values. A canonical value with the wrong unit
fails the joint metric even if its number happens to match.

## Visibility policy and limits

Only quantitative readings linked to a readable printed row with a visible name
and value are eligible. Qualitative results, narrative findings, diagnoses, PGx,
previous-result observations, and readings without a printed-row link are
explicitly outside this version. Predictions should contain current/export
numeric observations only; predictions outside that
contract cannot earn a match. The source schema's readability annotations are
used as declared; this module does not independently inspect image pixels.

| Field | Eligibility |
| --- | --- |
| `loinc` | Truth has a code and declares `expect_resolvable=true` |
| `canonical_value` | Finite quantitative value, visible unit or declared dimensionless unit, and no comparison operator in the printed value |
| `unit_ucum` | Unit is present and readable in printed truth, or canonical unit is dimensionless |
| `observed` | Exactly one distinct full collected date is recoverable from printed `dates[]` text without a locale guess, and the reading is current/export and matches that date |
| `role` | Current/export |
| `source_file` | Filename is provided as input context |

The scorer never substitutes `encounter_dates` or a report/print date for missing
collection evidence. Time-only, month-only, two-digit years, and ambiguous
day/month ordering do not expose a full date just because `dates[].iso` contains
one. Existing exports do not carry row-specific printed dates
in this schema, so their dates generally remain unscored. Previous observations
point to a current `PrintedRow` rather than independently recording a visible
previous result. All such observations are excluded with reason
`previous_observation_visibility_unrepresented`, even if a previous-column header
exists: a header alone does not prove that another result is printed.
Missing units mask both numeric
normalization and unit correctness, rather than requiring hidden knowledge.
Censored values containing `<`, `>`, or their inclusive/full-width variants
mask exact numeric scoring with reason `censored_printed_value_not_exact`.
Their observable code/unit/date/role/source can still be evaluated; a perfect
joint score on those fields does not certify faithful extraction of the bound.

Two same-name, same-code, same-date, same-role observations with different values
cannot always be disambiguated from this contract. These groups are reported in
`ambiguous_truth_groups`. Exact identity ties retain
input order; values are never used to select the most favorable pairing. This
can understate accuracy for reordered repeated measurements. This is broader
than literal same-date repeats: a record without a printed collection date
(device exports, home logs) masks `observed` for every reading, so each repeated
series there is one ambiguous group, and an exact prediction emitted in a
different row order can lose value credit. Future manifests
need public source locations (page/table/row/column), together with independent
location predictions, before those cases can be scored reliably. The v1 metric
does not claim to solve that ambiguity.

Compare scores only alongside their eligible/unscored counts and the policy
version. A perfect score on fewer observable fields is not evidence that an
occluded document preserves all semantic information.

The existing text-rules baseline (`rules-text-v1`) preserves explicitly printed
`g/dL` and `10*6/uL`, including the source spellings used in the fixed Synthea
blood-count example. This is literal extraction support, without unit conversion
or a general UCUM parser. It leaves missing units blank. A downstream semantic
adapter must abstain on normalization when the unit is unavailable; it must not
read the manifest to fill it. A source `effectiveDateTime` is not evidence of a
collection date under v1, even when its timestamp is fully visible. Report that
date field as unscored rather than relabeling it as `collected`.
