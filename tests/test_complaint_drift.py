"""Regression test: the complaint vocabulary must track mirobody's actual resolver.

`SYMPTOMS`/`FRONTIER` in `scripts/build_profile.py` each record what mirobody's
`resolve_symptom` should return for a surface. mirobody's own curated vocabulary moves
independently (a lay-English addition, an inclusion rewrite), and when it does, a recorded
expectation here goes stale while still reading as a passing test -- this is exactly how the
2026-10-15 drift against mirobody #151 (`sweating`, `sputum`, `cramps` silently started coding)
went unnoticed. This runs the real resolver, via mirobody's own venv, and checks every recorded
surface again.

Needs a mirobody checkout with its own venv at ../mirobody (a sibling of this repo), same
requirement as `test_spec.py::test_spec_is_regenerable_without_drift`. Skipped, loudly, when
that isn't available -- "the test passed" must never also mean "the test didn't run".

The resolver subprocess's `cwd` must be the checkout root, not this repo's. Without it, Python's
own sys.path still finds *a* `mirobody` (whatever the venv has pip-installed, editable or not,
on whatever branch that checkout happens to be on) and silently resolves against that instead --
this cost real debugging time on 2026-10-15: a stale sibling checkout on an unrelated branch
produced "headaches"/"anxious"/"aching joints" mismatches that vanished once `cwd` was pinned.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mirobody_gen import spec  # noqa: E402

_RUNNER = """
import json, sys
from mirobody.translate.icpc3 import resolve_symptom
surfaces = json.loads(sys.stdin.read())
out = {s: {"outcome": (r := resolve_symptom(s)).outcome, "code": r.code} for s in surfaces}
print(json.dumps(out))
"""

#: gen's four-way `expect` label -> the one outcome mirobody's `Coding` can actually carry.
#: mirobody has exactly three outcomes (coded / needs-input / refused); gen's "no-match" is a
#: documentation-only split of "needs-input" -- the vocabulary has nothing at all -- from
#: "needs-input" proper, an unresolved tie between two codes. Both check out the same way here.
_EXPECT_OUTCOME = {"coded": "coded", "refused": "refused", "needs-input": "needs-input", "no-match": "needs-input"}


def _mirobody_checkout() -> pathlib.Path | None:
    override = os.environ.get("MIROBODY_CHECKOUT")
    p = pathlib.Path(override) if override else REPO.parent / "mirobody"
    return p if (p / ".venv" / "bin" / "python").is_file() else None


def _resolve_all(checkout: pathlib.Path, surfaces: list[str]) -> dict:
    python = checkout / ".venv" / "bin" / "python"
    result = subprocess.run([str(python), "-c", _RUNNER], input=json.dumps(surfaces),
                             capture_output=True, text=True, cwd=str(checkout))
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_every_symptom_surface_matches_the_resolver():
    checkout = _mirobody_checkout()
    if checkout is None:
        print("  skip test_every_symptom_surface_matches_the_resolver: no mirobody checkout found "
              "(set MIROBODY_CHECKOUT or put one at ../mirobody)")
        return
    comp = spec.complaints()
    surfaces: set[str] = set()
    for item in comp["symptoms"].values():
        surfaces.update(item["zh"])
        surfaces.update(item["en"])
        if item.get("en_lay"):
            surfaces.add(item["en_lay"])
    resolved = _resolve_all(checkout, sorted(surfaces))

    for sid, item in comp["symptoms"].items():
        for text in item["zh"]:
            code = item.get("zh_codes", {}).get(text, item["icpc3"])
            r = resolved[text]
            assert r["outcome"] == "coded" and r["code"] == code, (sid, text, r)
        for text in item["en"]:
            r = resolved[text]
            assert r["outcome"] == "coded" and r["code"] == item["icpc3"], (sid, text, r)
        if item.get("en_lay"):
            r = resolved[item["en_lay"]]
            assert r["outcome"] != "coded", \
                (sid, item["en_lay"], r, "en_lay now codes -- move it into en and drop en_lay")


def test_every_frontier_surface_still_abstains_as_recorded():
    checkout = _mirobody_checkout()
    if checkout is None:
        print("  skip test_every_frontier_surface_still_abstains_as_recorded: no mirobody "
              "checkout found (set MIROBODY_CHECKOUT or put one at ../mirobody)")
        return
    comp = spec.complaints()
    surfaces = sorted({s for item in comp["frontier"].values() for s in item["zh"] + item["en"]})
    resolved = _resolve_all(checkout, surfaces)

    for sid, item in comp["frontier"].items():
        want = _EXPECT_OUTCOME[item["expect"]]
        for text in item["zh"] + item["en"]:
            r = resolved[text]
            assert r["outcome"] == want, \
                (sid, text, r, f"expected {item['expect']!r} ({want}) -- move or replace this surface")


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok  {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL  {name}: {e}")
    print(f"\n{failures} failed" if failures else "\nall passed")
    sys.exit(1 if failures else 0)
