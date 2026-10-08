"""Synthetic identifiers (visit, barcode, specimen numbers) with a verifiable checksum tail.

A lab report always prints some number, and the privacy gate's PII predicate (a visit-number label
followed by six or more digits) is aimed squarely at that kind of string. Two bad fixes: print no
number at all (unrealistic — this is exactly the `meta.subject_field_as_indicator` hazard, a number
mistaken for an indicator), or carve an exemption for `out/` into the gate (which would leave the
gate purely decorative).

The fix here: the last three digits of a synthetic number are a **salted hash** of the digits before
them. The gate recomputes that hash independently (`audit/privacy.py` keeps its own copy rather than
importing this module — running the same function twice would only confirm its own blind spots), and
only passes a number that checks out, counting and printing the result. A real number matching this
checksum by chance has roughly a one-in-a-thousand probability, and a real number would first have to
survive n-gram replay detection to reach the output at all.
"""

from __future__ import annotations

import hashlib
import random

SALT = "mirobody-gen/synthetic-id/v1"


def checksum(body: str) -> str:
    digest = hashlib.blake2b(f"{SALT}:{body}".encode(), digest_size=4).digest()
    return f"{int.from_bytes(digest, 'big') % 1000:03d}"


def make(rng: random.Random, digits: int = 10, prefix: str = "") -> str:
    """`digits` is the total length including the three-digit checksum tail. `prefix` is a letter
    prefix, e.g. `B` for a barcode."""
    body = "".join(str(rng.randrange(10)) for _ in range(digits - 3))
    if body[0] == "0":
        body = str(rng.randrange(1, 10)) + body[1:]
    return f"{prefix}{body}{checksum(body)}"

