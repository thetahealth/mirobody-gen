# Privacy model

This document is the only place the privacy design is explained at length. Everything else links here.

## Claim

No real person's record is on the generation path. The corpus is not de-identified data and it is not
a model fitted to data; it is computed from public standards and a hand-written cohort design. The
argument is structural (there is no information flow from any real record to any generated value),
not a strength argument ("the de-identification is thorough").

The distinction matters. Stadler, Oprisanu and Troncoso (*Synthetic Data — Anonymisation Groundhog
Day*, USENIX Security 2022) show that generators fitted to real data either leak under inference
attacks or lose utility. That result applies to fitted generators; this project does not fit one.

## What entered the repository from real documents

Document *shape* was learned from a private reference set of de-identified laboratory and check-up
documents. The reference set is not distributed and is not on the value path. What entered the
repository is:

| Kind | Example | Gate |
|---|---|---|
| Format tokens | a reference-range dialect `{lo}-{hi}`, a unit spelling `fl`, a flag marker `偏高`, a column set `name/result/reference/unit/flag` | present in at least three documents, matches a format allow-list (unit tokens, punctuation, label vocabularies, date formats), stored as a template rather than an instance |
| Hazard classes | `unit.glued_to_value` with its document rate | class name and count only; the source snippets were discarded |
| Aggregate statistics | row-count quantiles, page-count distribution, image long-edge histogram, JPEG-quality quantiles | counts and quantiles only, produced by `scripts/build_numbers.py` and `scripts/measure_images.py` |

Every resource file under `mirobody_gen/resources/` declares `_source` (`public-standard`,
`format-token` or `hand-authored`), `_provenance` and `_vocabulary_fields`. The privacy gate fails on
a resource without them.

What never entered: indicator values, reference ranges as printed by a specific laboratory, names,
identifiers, dates, institution names, instrument or batch identifiers, narrative sentences.

## Threat model

| Risk | Control |
|---|---|
| Verbatim leak: a fragment of a real document appears in a generated file | The generator does not read the reference set. An n-gram replay index over the reference set's text is scanned over every generated text unit and every resource file; a hit fails the gate unless the window is covered by declared public vocabulary (at most three consecutive terms). |
| Statistical leak: real value distributions can be inferred | Values do not come from the reference set. Intervals come from WS/T 404 / WS/T 405 and guidelines, variation from the Westgard database, individual values from the mechanistic model. Reference-set quantiles are used only to compare distributions after generation, never to sample from. |
| Spec leak: a rare real string survives distillation | The three-document floor, the format allow-list, template rewriting, and human review of every resource file. Free-text fields of the distillation input (`value_examples`, `snippets`, `wrong_results`) are excluded wholesale. |
| Misidentification: a generated file is later mistaken for a real record | A visible banner on every page, PDF `/Subject SYNTHETIC`, XLSX document properties, JPEG EXIF `ImageDescription` and `Software`, PNG text chunks, and `synthetic: true` on every record. Handwritten pages carry the same banner and image metadata; a hand never writes a name, and a signature is a scribble with no letters. |

## Repository controls

1. **Allow-list `.gitignore`.** Everything under the root is ignored by default; only listed paths can
   be tracked. A new directory cannot reach the repository by accident.
2. **Pre-commit gate (`.githooks/pre-commit`).** Rejects paths under the reference-set directories,
   document and image files, files over 1 MB, and content matching identity-number, phone-number and
   name-field predicates. `git add -f` bypasses `.gitignore`; it does not bypass the hook. Install with
   `git config core.hooksPath .githooks`.
3. **Privacy audit (`mirobody-gen audit-privacy`).** Implemented independently of the generator so that it
   cannot share the generator's blind spots: replay detection, PII predicates, name and institution
   allow-lists (the fiction pools), synthetic-identifier check digits, resource provenance.
4. **No document files in git.** Generated reports and reference documents are indistinguishable to
   git; neither is tracked. A curated sample requires an explicit force-add and a note in the PR.
5. **Clean object store.** Before publishing, `git fsck --unreachable` and `git count-objects` must show
   no stray large objects; a reset does not delete a staged blob.
6. **Model output is untrusted text.** The optional language-model layer (`mirobody_gen/llm/`) only
   ever sends synthetic content out and only ever brings phrasing back; it never produces a value, a
   name or an identifier. A resource it produces carries `_source: llm-*` and must declare no
   vocabulary exemptions, so every string in it passes the replay index and the PII predicates as is;
   the privacy audit rejects a model-produced resource that claims an exemption.

## What CI can and cannot verify

CI runs the clinical, readability and privacy gates on a small build. The replay index cannot be
built in CI because the reference set is not in the repository, so the replay half of the privacy gate
and the spec-regeneration (drift) test run only on a machine that holds the reference set, before every
release. CI therefore proves: the contract tests pass, generated output carries no PII pattern, every
name and institution comes from the fiction pools, every resource declares its provenance, and the
package builds with its resources.

## Reporting

If you believe any file in this repository, in a release, or in a build produced by it is not
synthetic, follow [SECURITY.md](../SECURITY.md).
