"""Opt-in clinical presentation profile (`build --clinical`).

Rules that make generated documents read the way a clinician expects a real report to read, written in
answer to clinician review of generated scenarios. Each rule set lives in its own `clinical_*.py` module
and acts only while `ENABLED` is set, so a default build never enters it and existing seeds keep their
bytes. These are presentation choices for review material, not validated clinical facts.
"""

from __future__ import annotations

ENABLED = False


def enable() -> None:
    global ENABLED
    ENABLED = True
