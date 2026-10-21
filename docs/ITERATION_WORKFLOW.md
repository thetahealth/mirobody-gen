# Expert-guided iteration

The generator and deterministic audits must remain usable without credentials,
network access or a language model. Optional models propose wording, questions,
scenarios and review findings; they do not establish clinical truth. Accepted
improvements become versioned code/resources and regression cases that work offline.

## Repeatable iteration

Each iteration addresses one falsifiable hypothesis: reproduce a defect, identify
the owning fact or rule, make a bounded change, run relevant checks, and record both
improvements and regressions. Keep the original cohort and seed for paired comparisons.
Do not narrow all distributions to normal or manufacture missing clinical facts to
make a reviewer happy. Record unmeasured dimensions explicitly.

Private development material, run state and command logs belong in an ignored
output directory, never in the repository. Concurrent iterations on one working
tree need an exclusive lease so they do not edit the same files; an iteration must
not reset a branch or overwrite unrelated changes. Remote publication is separate
from local iteration. Record executed evidence (commands, exit codes, before/after
metrics) rather than the fact that a run was triggered, and work toward a complete
consumer milestone across several iterations instead of adding a module every run.

## Semantic acceptance

`mirobody-gen score-semantic FILES.jsonl PREDICTIONS.jsonl --json REPORT.json`
scores observable numeric codes, canonical values/units, dates, roles and supplied
file context separately from the existing five-field extraction score. Use the
strict prediction and visibility contract in [SEMANTIC_SCORING.md](SEMANTIC_SCORING.md).
Unknown files/duplicate IDs fail the submission; missing/extra observations affect
recall/precision. One assignment drives all field scores and the joint metric.
Do not use hidden encounter dates, unrepresented previous values or numerical
bounds as if they were visible exact values. Report unscored and ambiguous cases.
Fault injection verifies the scorer, not an extractor or clinical label quality.

The next end-to-end milestone should connect an existing source or generated
scenario to ordinary/stress materials, observable user questions and independent
predictions. Keep clinical validity, source fidelity, visible-evidence coverage and
model performance as separate results; do not replace the missing consumer with
another broad source-interface audit.

## Expert feedback and evidence

Separate scenario realism feedback, human-authored questions, reference answers
and model-answer labels. Missing annotation fields are unknown. A batch used to
develop a rubric is a development replay set, not an independent test set. New
generated examples without human labels support exploration, not measured clinical
accuracy. Keep patient/scenario families together when creating future holdouts.

Keep the original documents unchanged and preserve source hashes and locations
locally. Derive generic rules rather than copying raw annotations into public
resources or model prompts. Ordinary clinical presentations and intentional
extraction stressors need different acceptance criteria. Missing units in a stress
view may be intentional; it still changes what a downstream agent can know.

For ordinary **table presentation** review, reuse the minimal-contrast controls:

```sh
mirobody-gen build --seed 7 --people 8 --pairs 3 --pair-views ordinary \
  --out out/ordinary-tables
mirobody-gen build --seed 7 --people 8 --pairs 3 \
  --out out/full-table-pairs
```

`pairs.jsonl` explicitly marks clean controls as `presentation.purpose=ordinary`
and named hazard/acquisition variants as `stress`, versioned `paired-table-v2`.
The ordinary selection is the identical base subset of the full selection. Known
units and reference ranges remain visible, subject fields stay outside result
rows, and incidental corruption is absent. Unknown reference intervals are not
invented. This scope is table extraction, not a claim of full scenario realism or
clinical validity. Non-routine visits with at least five readings are eligible.
The existing `main/stress` difficulty split is not a realism label. `--pair-views`
does not sanitize `--render` output. Keep selections in separate output directories
and preserve every view of a scenario family in the same future evaluation split.

Table policy v2 fixes the v1 multi-page capture loss: explicit JPG/PNG requests
are delivered as multi-page image PDFs under `all-pages-v1`, recorded in
`container_fallback`. Single-page image bytes remain unchanged. The low-level
encoder rejects multi-page single-image input instead of discarding later pages.
Use actual delivered page counts in validation; identical semantic records and a
readability audit that skips images do not prove all pages reached the user.

For full routine checkup books, add `--pair-scope checkup`:

```sh
mirobody-gen build --seed 7 --people 8 --pairs 2 --pair-scope checkup \
  --pair-views ordinary --out out/ordinary-checkups
mirobody-gen build --seed 7 --people 8 --pairs 2 --pair-scope checkup \
  --out out/full-checkup-pairs
```

This scope uses `paired-checkup-v1` and `scope=checkup_document`. Build each clean
book once, then clone it for incidental table corruption and acquisition stress.
Do not rebuild examination sections for each layout: random choices can change
non-table clinical facts. Layout toggles remain in the existing table scope;
checkup stress is limited to the incident/capture set. Checkup variants preserve
the semantic readings, examination blocks, findings, conclusions, cover and
embedded illustrations. Ordinary remains a presentation label, without certifying
clinical realism. Existing ultrasound/ECG illustrations are visual props, not
validated clinical-image evidence. Every checkup capture view uses a multi-page
image PDF, including the screenshot scene: the single-image encoders would drop
all but the first page. A hidden table value can remain visible in a
summary, so assess whole-document answerability against every visible section.
Device/symptom context, other document types and faithful imported facts still
need separate work. `--render` output is unchanged by either pair selection.

## Continuous activity context

The continuous stream's `daily-activity-v3` policy conserves the synthetic daily
step total shared with the phone store. It changes a sampled run duration when
the existing cadence would exceed that budget; it does not raise the measured
step count or clamp resting heart rate. Source provenance and run/walk/other step
allocation are recorded in each day plan, and Apple export refuses inconsistent
plans. Preserve the original daily-device series when comparing versions. Missing
step observations retain a labeled model fallback. This is model consistency,
not validation of cadence, fitness, symptom causes or sensor reliability. Do not
infer an athlete profile or a disease solely to explain a low heart-rate value.

Version 3 also records every walking interval and resolves activity-clock
collisions. Keep sampled durations and raw daily facts; move synthetic conflicting
walks into free waking-day slots. Day plans validate disjoint run/walk intervals,
local-day boundaries and step conservation before creation, truth serialization
and Apple export. A placement that cannot fit is an error, not an excuse to drop
an activity. Imported authoritative workout or observation times require faithful
replay; they must not be rescheduled by this synthetic activity planner.

## Synthetic pulse contexts

`device-pulse-v2` records synthetic authority and distinguishes daily resting
summaries from home cuff pulses in `devices.jsonl`. The cuff now uses the same
configured `pulse` event factor as resting and clinic paths, without equating
their baselines or measurement noise. Use an event ablation with identical
random seeds to verify the configured effect; do not calibrate effect sizes by
forcing channel values to agree. The existing resting-only infection adjustment,
baseline differences, symptom coupling and clinical effect calibration remain
open. Imported authoritative measurements are replayed unchanged, not adjusted
by this generator policy.

Continuous day truth separately records `resting-hr-provenance-v1`: a day with an
actual `rhr` record in the supplied synthetic series is `phone_store`, retaining
that record's original timestamp; a day without one is `baseline_model` with no
observation timestamp. Determine this per day from records, not vendor capability
or wearable adoption. A manual plan defaults to `unspecified`. Keep all numeric
inputs and existing local-date/last-record selection unchanged when adding this
provenance. It exposes model completion; it does not validate the model's baseline,
resting-only infection adjustment, training effects or symptom attribution.

## Optional scenario review

`review-scenarios` consumes JSONL packets with `case_id`, `synthetic: true`,
`purpose` (`realistic` or `stress`), `facts` and optional `questions`. Every fact and
question needs a unique `id` and `text`. A quantitative fact may additionally carry
`type: measurement`, `unit_required: true` and `unit` for deterministic checks.
The outbound allow-list contains only case identity, purpose, text facts and
questions; human feedback, answer keys, local paths and other metadata are omitted.
The flag is a caller assertion, not a de-identification algorithm: only construct
packets from known synthetic evidence.

```sh
# No API request, even when a key is present.
mirobody-gen review-scenarios out/cases.jsonl --out out/review-offline.jsonl

# Explicit read-only provider discovery.
mirobody-gen review-scenarios --list-models

# Explicit optional network review. Substitute an available model id.
mirobody-gen review-scenarios out/cases.jsonl --model MODEL_ID \
  --variant expert --out out/review-model.jsonl
```

An entire input batch is checked before sending a request. The critic must report
every criterion and question, cite existing facts and quote exact evidence for
substantive judgments. Missing evidence can be `not_assessable`. Invalid JSON,
truncation, omissions, unknown references and fabricated quotations fail the review;
they are not omitted from the failure denominator. `evidence_validated` means the
schema and references passed, not that the clinical judgment is correct. Candidate
questions are unverified proposals: valid fact IDs alone do not prove that a
question has not introduced an unsupported premise.

For source-only ocular packets, opt into a narrower candidate grammar:

```sh
mirobody-gen review-scenarios out/source-eyes.jsonl --ocular-source \
  --out out/source-questions.jsonl
```

Input is the same `ocular-observations-v1` JSONL accepted by `replay-ocular`.
`ocular-source-questions-v1` produces two deterministic baseline queries about
the first observation's recorded status and laterality, without a model or
rendering dependencies. Field lookups preserve unknown/not_measured/unspecified,
raw values/units/times, and distinguish absent fields from explicit nulls.

With explicit `--model`, the optional critic can propose at most two distinct
`{fact_id, field}` selectors for item_id/status/laterality/value/unit/notation/
effective_time. Code generates the Chinese question wording and reads the exact
source field. Unknown observations/fields, extra prose or duplicate selectors
fail the model review; its deterministic baseline remains available. This mode
trades free wording for a verifiable source-field scope. It cannot propose a
diagnosis, normal finding, inferred eye or treatment question. Omitting the flag
retains the existing free-text candidate path and its unverified semantics.

Only the case id, observation ids and the selected source-field texts enter the
outbound packet; person alias, source system/location and document date are omitted.
The packet remains known-synthetic data, not an anonymization mechanism. The
local source hash includes the full original packet. All source inputs and
derived facts are validated before any batch output or model request.

The critic's criteria/reasons and assessments of arbitrary input questions remain
semantically unverified, even in this mode. Source lookup correctness does not
validate a clinical judgment, useful question selection or live model compliance.
Do not count authored contract mutations as observed model errors or an independent
clinical benchmark. No paid request is necessary to verify this constrained grammar.

`generic` and `expert` variants permit a paired prompt experiment on the same facts.
Report failures, normal controls, reviewer disagreement, unsupported allegations,
question answerability and token usage separately. A higher issue count is not an
improvement. Do not report expert agreement without actual independent expert labels.

## Provider and budget

`DEEPSEEK_API_KEY` may be read from a local ignored `.env` or environment. It is
used only with the official DeepSeek endpoint. Explicit `LLM_BASE_URL` and
`LLM_API_KEY` reach other OpenAI-compatible providers for `paraphrase` and
`llm-panel`; `review-scenarios --model` makes paid requests only to DeepSeek, the
one provider with a verified CNY tariff. Never print credentials or include them in
artifacts, command arguments or HTTP error bodies. Model requests do not follow
redirects, and a credential containing whitespace or control characters is refused
rather than sent. Model names are discovered rather than assumed from old
aliases. DeepSeek reviews disable thinking explicitly and limit output to 2,048
tokens.

The shared DeepSeek transport caches the complete request identity: provider,
model, messages, sampling parameters, format and output limit. It retains response
model, usage and request hash. The spending cap is **CNY 10 per Asia/Shanghai
calendar day**; `--daily-limit` can optionally add a stricter call-count limit
without bypassing the money cap.

Before network I/O, SQLite atomically reserves peak-price cost for the model's full
input context plus the bounded output. Complete, consistent usage settles that
reservation using the pinned peak CNY tariff. Missing/inconsistent usage retains
the full amount, including on errors; unresolved amounts carry across midnight.
Cache hits do not spend money. The ledger is always
`.cache/scenario_review/usage.sqlite3` in the checkout, whatever response cache
directory a caller passes. It is per checkout: another clone or worktree keeps its
own ledger, and deleting the directory loses the accounting, so it must not be
cleared to recover allowance. This is conservative project accounting, not the
provider's invoice or an account-wide limit on unrelated applications using the
same key.

Prices and their verification/expiry date live in `llm/budget.py`, sourced from
[DeepSeek's official CNY table](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/).
Unknown models/providers and expired price snapshots refuse new paid requests.
Recheck official prices and update the snapshot before expiry; do not extend the
verification date without checking. Do not raise the cap without the budget owner's
authorization, or delete/reset the ledger to recover allowance. Reconcile unknown
charges against provider evidence before releasing their reservations.

DeepSeek calls from `paraphrase`, `llm-panel` and `review-scenarios` share this transport
and global ledger. Templates remain optional, with their existing fact/slot contract
and offline fallback. Rejected answers are kept separately for inspection and are
never reused as successful reviews. Budget exhaustion leaves offline work available.

## Encounter department context

`outpatient-department-v1` prints an explicit `Encounter.department` in the
outpatient body and preserves the source string in the manifest. File truth
records `care_context` with department/source, including `unknown` for absent or
blank input. Do not infer a specialty from an institution name, sex or archetype,
translate source department text silently, or fabricate reproductive/TCM history
to justify a sampled issuer. The current cohort leaves department unknown; this
is a faithful-source seam, not a validated facility selector. The fictional
institution catalogue still lacks service-capability facts. Same-encounter lab
slips prefer an explicit source department too, while retaining legacy sampling
when none is known. A department label alone does not
prove that a narrative matches its care model.

## Optional fictional facility services

The default selector's `institution-pool-v2` preserves the existing successful
draws and random state. If a draw requests a fresh institution after that
language/type pool is exhausted, reuse a previously selected member of the same
pool, weighted by its existing count. Do not borrow another language/type or an
unused reserved big client. A pool with neither an available nor reusable member
raises a clear error. This repairs finite-catalogue generation, not clinical
facility suitability. Service-constrained selection still obeys its separate
explicit profile. The policy is recorded in code/experiment versions without
adding a field to unchanged default file records.

`build --render --facility-services path/to/profile.json` enables
`facility-services-v1` for corpus visit files; it does not constrain `--pairs`.
Without the option the existing selection and output bytes are unchanged.
Profiles use `schema_version=fictional-facility-services-v1`, a nonblank
`profile_id`, `authority=authored_synthetic`, a `required_by_exam_type` mapping
(routine/follow-up/clinic/specialty to service codes), and `institutions` entries
with exact fictional catalogue `name` and an explicit `services` list. Unknown
or duplicate provider names, invalid service lists and unknown visit types are
rejected. Unlisted institutions have no declared support.

The service meaning and visit-to-service mapping are authored synthetic facts,
not medical recommendations or verified real-provider capacity. Do not infer
them from names, sex or archetypes, or invent a patient history to justify a
selection. The catalogue is not automatically annotated by this option.

Before output, each requested location kind needs declared providers in both
Chinese and English document groups because visits may cross languages. Missing
coverage fails explicitly. Sticky/home and big-client choices must pass the same
service check. Small declared pools reuse supported providers deterministically;
they never fall back to unknown support. When a person's usual checkup centre or
hospital does not declare the required service, each such visit draws an eligible
provider afresh and the usual institution is not replaced, so continuity of care is
not modelled under a profile; the large preset clients are not held back from these
constrained draws. Each constrained visit file records
`facility_service` with the policy, profile id/hash, catalogue hash, service,
provider and synthetic authority. Other files make no support claim. Preserve
the profile with experiments and compare the same source manifest with the
unconstrained baseline. Layouts and rendered file counts may change. This does
not validate care-model narratives, departmental equivalence or clinical realism.

## Generated eye laterality

`ocular-laterality-v1` keeps the existing two independent acuity/IOP draws and
adds explicit left/right printing plus structured measurements in eye-block
truth. Preserve each pair, notation, unit, flags, findings and random state when
comparing the previous version. Do not interpret this as copying one scalar to
both eyes or as permission to infer side order from imported unlabelled values.
The generated IOP policy explicitly assigns its first draw left, second right.

Checkup file truth separately lists omitted quantitative eye items as
`not_reported`, with unknown left/right states. Do not count missing items as
normal, measured or clinically unmeasured. Explicit unmeasured/source observation
states and faithful imported eye facts still need their own input contract;
current metadata is synthetic-model truth. Only unaided/corrected acuity and IOP
are in this version; normal eye narratives and bilateral imaging need separate
work. Clinical distributions and thresholds were not recalibrated.

## Source-only ocular replay

`replay-ocular INPUT.jsonl --out DIR` implements `ocular-source-v1` separately
from the synthetic checkup builder. Packets use `ocular-observations-v1` and an
explicit known-synthetic assertion, case/person aliases, language (zh/en), source
system, document date and nonempty observations. Each source record retains its
id, source_id, item (va/va_corrected/iop), side (left/right/unspecified), state
(measured/not_measured/unknown), optional raw value/unit/notation and effective
date or offset timestamp. Omitted fields remain omitted in JSON; display marks
missing units/time as not provided. A document date never fills observation time.

The path neither samples eyes nor invokes an archetype, diagnosis or completion.
A single or unspecified-side value stays one record; not_measured/unknown cannot
carry a numerical value. Duplicate JSON fields, ids and ambiguous item/side/time
keys, unknown fields, unsupported formats and invalid/naive timestamps fail before
any batch output. Supported quantitative strings are decimal/comma-decimal or
simple visual-acuity fractions, optionally with comparison signs; unsupported
source forms are rejected rather than coerced.

Generation checks exact JSON source retention and the complete expected PDF text layer.
Text checking ignores whitespace and seven Latin font ligatures, not numeric,
unit or timezone differences. `--audit-existing DIR` rechecks saved PDFs/ocular.jsonl
against the original source without generating or writing files; it returns exit1
on a mismatch, while invalid input returns exit2. Hash-consistent but wrong-side
PDFs must fail the physical text check. Every batch is validated, rendered and
audited in memory before output begins. This does not make the final disk writes
transactional against an I/O interruption.

The synthetic flag is a caller assertion, not de-identification. Use synthetic
packets only; do not upload expert files. `audit_ok` is a fidelity/content result,
not clinical correctness; unknown/missing-unit cases remain unknown. Current
inputs do not support FHIR coding/method/reference intervals, qualitative acuity,
full-patient replay or completion. Unknown keys are rejected to prevent silent
discarding of such source facts. The existing `build` path is unchanged.

## Synthea integration boundary

`snapshot-fhir INPUT.json --synthetic --out NEW_DIR` provides the first offline
source seam (`fhir-source-v1` / `fhir-observation-source-v1`). It archives the
original UTF-8 Bundle bytes and indexes every top-level entry Observation by JSON
pointer, storing its exact `resource_json` substring and digest. Decimal spelling,
all codings, methods, body sites, ranges, components, extensions and references
remain in that raw JSON. Non-Observation resources and history/delete entry context
remain in the complete source archive. No external references are fetched.

Do not feed this snapshot into Person/Reading or the old ocular packet by dropping
unsupported fields. There is no code/side mapping, unit/time conversion, completion,
clinical rendering or version selection. Repeated logical IDs stay in input order;
FHIR version IDs do not provide a numeric/alphabetic ordering. All records are
`clinical_use=not_adjudicated`, except entered-in-error explicitly excluded. Lifecycle
status, dataAbsentReason and value presence remain distinct; cancelled does not
prove a procedure was never started. These boundaries follow the
[R4 status definitions](https://hl7.org/fhir/R4/codesystem-observation-status.html)
and [resource version semantics](https://hl7.org/fhir/R4/resource.html#Meta).

The command rejects duplicate JSON keys, non-JSON numbers, unsupported lifecycle
status, multiple recognized value choices, and parallel value/dataAbsentReason
keys in an observation/component before output creation. This is a limited
structural check, not full FHIR/profile validation or clinical acceptance. Raw
modifiers are preserved but not interpreted. An existing output directory is
rejected; final disk writes are not transactional against I/O interruption.

`snapshot-fhir INPUT.json --audit-existing DIR` independently recomputes records
against the original bytes, checks the archived bytes and exact raw-resource/
metadata content, and returns exit1 on mismatch. It does not write files or trust
the derived snapshot.json summary. JSONL splitting uses LF and comparison retains
JSON types, so a Unicode line separator in source text stays content and false
does not match numeric0. Unreferenced files are outside this fidelity audit.
The synthetic flag is a caller assertion, not a de-identification function; the
archive retains the entire input Bundle. This path uses the standard library and
never invokes a model. Ocular source replay and the synthetic builder are unchanged.

`snapshot-fhir INPUT.json --reference-audit` adds the separate, read-only
`fhir-local-references-v1` diagnostic. Exact fullUrl/URN matching is local only;
REST-relative references use the containing entry's consistent RESTful fullUrl
base. Explicit history versions match meta.versionId exactly. Multiple candidates
remain ambiguous, without picking a first/latest resource or borrowing another
server's same id. Contained references stay in their containing resource's scope.
These rules follow [FHIR R4 Bundle reference resolution](https://hl7.org/fhir/R4/bundle.html#references).

The JSON report includes source/reference/target pointers, candidate pointers and
resolution states. Unknown, conditional, identifier-only or unsupported forms are
not guessed. Null/non-string reference fields are explicitly unsupported; nested
Bundle/Parameters scopes are listed as unscanned. Canonical references are outside
scope. Declared target type and REST URL/resource identity conflicts cannot resolve
silently. Referencing an entered-in-error Observation does not make it usable.

This diagnostic fetches no URL and writes no file. Exit0 means the scan completed,
not that every reference resolved or that the resource is clinically valid. A
resolved BodyStructure is only an identity link; it does not establish eye side,
interpret modifiers, validate terminology or choose a current clinical version.
Future ocular projection must check those separately. Omitting this option keeps
the original snapshot bytes/schema/policy unchanged.

`snapshot-fhir INPUT.json --body-patient-audit` adds the read-only
`fhir-body-patient-v1` context diagnostic. For each top-level Observation root
bodySite extension with the canonical R4 BodyStructure-reference URL, it resolves
the site, Observation.subject and BodyStructure.patient separately. Both patient
targets must be uniquely resolved Patient records. Same literal fullUrl denotes
the same record across explicit versions; contained identities remain scoped to
their exact container. No name, business identifier, Patient.link or basename
merge is performed, and no current version is selected.

The report distinguishes same_patient_record, different_patient_records,
not_assessable and excluded. Distinct records do not prove different people or a
medical source error. Missing/ambiguous roles, explicit focus, uninterpreted
modifier extensions and inactive/invalid BodyStructure active state remain
unassessed for this simple projection; withdrawn Observations are excluded.
Contained body resources do not inherit the parent subject. These boundaries
follow [R4 BodyStructure](https://hl7.org/fhir/R4/bodystructure-definitions.html)
and [Observation subject/focus](https://hl7.org/fhir/R4/observation-definitions.html#Observation.subject).

This is only declared patient-record context: absence of a focus field does not
establish that the actual focus equals the subject, since code can convey focus.
Every result keeps projection_eligible=false and clinical_error=not_adjudicated;
no eye side, notation, unit or clinical interpretation is produced. Observations
without the scoped body reference count as uncovered, not passed. It is a
standard-library diagnostic with no network or writes; exit0 means scan completed.
The snapshot and original reference-audit modes retain their prior outputs.

## FHIR source-field consumer

`render-fhir-view` connects the source archive and existing diagnostics to a
self-contained HTML reader (`fhir-source-view-v1`):

```sh
mirobody-gen render-fhir-view INPUT.json --synthetic --entry 0 --out NEW_DIR
mirobody-gen render-fhir-view INPUT.json --entry 0 --audit-existing NEW_DIR
```

Select top-level Observation entry indices explicitly; repeat `--entry` to retain
multiple source versions in the supplied order. There is no random selection,
current-head inference or conversion into the older ocular packet. Validate the
whole Bundle and selection before creating a new output directory. Existing
directories are rejected. Final disk writes are not transactional against I/O
interruption.

The consumer writes original `source.fhir.json` bytes, `view.html`, and `view.json`.
The manifest records selection/pointers, complete source Observation JSON, linked
body-reference target JSON, reference/patient diagnostics, their policy versions,
source and HTML hashes. HTML shows raw results/units, all codes/methods/ranges,
exact source times, linked originals and expandable full Observation/narrative
text. Missing fields are grouped explicitly; supplied null stays null. Unknown
and withdrawn states remain visible. All source markup is escaped as text, with
no scripts or remote resources and a restrictive content security policy.

`--audit-existing` recomputes expected HTML and typed metadata from the original
input and explicit selection, checks the archived bytes, and never trusts a saved
hash alone. It performs no writes; mismatches return exit1, invalid input exit2.
An altered HTML value with an updated digest must still fail. The audit covers
these three files, not unrelated directory contents or clinical correctness.

This is a usable source-review artifact, not an ordinary clinical report or an
enabled medical semantic projection. It keeps `semantic_projection=not_performed`
and `clinical_validity=not_assessed`; the prior diagnostic projection flags remain
false. No eye side, correction state, notation, unit or clinical conclusion is
inferred. New fields survive in the full source JSON rather than being silently
dropped. The path uses the standard library, requires no key/model, and leaves
the generator and prior source/audit outputs unchanged. The synthetic assertion
is not de-identification; the local archive retains the complete input Bundle.

Keep current scenarios and external FHIR sources behind a shared clinical timeline.
Distinguish faithful replay of an existing observation from explicit enrichment of
missing channels. Preserve source IDs, units, dates, reference intervals and
derivations. Rendering must not replace an upstream diagnosis or observation using
a local archetype. Validate a small vertical slice before increasing disease coverage;
FHIR validity, engine semantics, within-patient consistency and clinical validity
are distinct tests.
