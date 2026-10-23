"""Clinical presentation profile, on by default (`build --no-clinical` turns it off).

Rules that make generated documents read the way a clinician expects a real report to read, written in
answer to clinician review of generated scenarios. Each rule set lives in its own `clinical_*.py` module
and acts only while `ENABLED` is set. With it off, a build reproduces the bytes of a build made before
the profile became the default. These are presentation choices for review material, not validated
clinical facts.
"""

from __future__ import annotations

ENABLED = True


def set_enabled(on: bool) -> None:
    global ENABLED
    ENABLED = on
