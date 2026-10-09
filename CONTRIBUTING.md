# Contributing

## Before your first commit

```bash
git clone https://github.com/thetahealth/mirobody-gen && cd mirobody-gen
git config core.hooksPath .githooks       # the privacy gate; see docs/PRIVACY.md
pip install -e ".[dev]"
python -m pytest tests -q
```

The hook rejects document and image files, files over 1 MB, paths under the reference-set
directories, and content matching identity, phone or name-field predicates. `.gitignore` is
allow-list based: a new top-level path must be listed there before it can be tracked.

## What goes where

| Change | Where |
| --- | --- |
| a value, interval, finding, template, weight | `scripts/build_*.py` → regenerate `mirobody_gen/resources/*.json`; never edit the JSON by hand |
| a layout dialect learned from documents | `scripts/distill_layout.py` (needs the reference set) or a hand-authored entry |
| generation logic | `mirobody_gen/` |
| a check on the output | `mirobody_gen/audit/` — must not import the generator |
| a scoring or export tool | `mirobody_gen/harness/` |
| handwritten wording, writing tiers, inks, paper | `scripts/build_handwriting.py` → `--write` regenerates `resources/handwriting.json` |
| a CGM or wearable, its device facts, an export shape | `scripts/build_streams.py` → `--write` regenerates `resources/streams.json`; every fact names its source, and `docs/DEVICE_FORMATS.md` records the URL and how sure we are |
| a handwriting font, or a character a hand must be able to write | `scripts/build_handwriting.py --fonts DIR --write` rebuilds the subsets in `mirobody_gen/render/fonts/` from the pinned upstream files (see that folder's README); fontTools is needed only for this |

Every string that can be printed on a page must belong to a resource field listed in that file's
`_vocabulary_fields`; otherwise the privacy gate reports it as a string of unknown origin. That is the
intended behaviour.

## Pull requests

- Run the contract tests and, for changes to resources, the regeneration test on a machine that
  holds the reference set (`tests/test_spec.py::test_spec_is_regenerable_without_drift`).
- For changes to rendering or degradation, build a small corpus and run the readability audit; attach
  the audit summary to the PR.
- Never commit generated files. A curated sample needs an explicit `git add -f`, a note in the PR, and
  the maintainers' review.
- Commit messages state what changed and why in the first line; the body records the measurement or
  the failure that motivated the change.

## Style

- Python 3.11+, `ruff check` clean for the rules in `pyproject.toml`.
- Comments explain the reason, not the statement. A comment that records the bug or the measurement
  that motivated a line is worth keeping; one that paraphrases the code is not.
- Numbers that come from the reference set live in resources with provenance, not in code.
- Documentation states current behaviour. Dated working notes go under `docs/zh-CN/` and say so at
  the top.

## Reporting problems

Bugs and feature requests go to GitHub issues. Anything that looks like real personal data in a file,
a build or the history goes through [SECURITY.md](SECURITY.md), not a public issue.
