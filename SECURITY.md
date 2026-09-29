# Security and privacy reporting

## If you believe something is not synthetic

This repository must not contain, and its builds must not produce, any real person's health data.
If you find a file, a string, a commit or an object in the history that you believe came from a real
record — a real name, an identifier, a date of birth, an institution, a fragment of a real report,
or a document file — do not open a public issue.

Email **developer@thetahealth.ai** with the path or commit and what you observed. You will receive an
acknowledgement within three working days. If the report is confirmed, the material is removed from
the tree and from history, a corrected release is published, and the report is credited unless you
ask otherwise.

## Vulnerabilities

The package renders documents locally and makes no network requests. The optional LLM panel in
`mirobody_gen/harness/llm_panel.py` sends only synthetic records, asserts `synthetic: true` on every
record before sending, and refuses the whole batch if any record fails that assertion. Report anything
that contradicts this to the same address.

## Supported versions

The latest release on the `main` branch.
