"""Biological variation and the RCV audit share one declared lognormal model (resources/measurement.json).

Before: the generator used the CV itself as the log-scale SD (CV 42% printed as ~44%), and the audit
gated |ln ratio| with ln(1 + 2.77 c), narrower than the model's spread. Its "theoretical ~5%" was
really 7.8% in a pre-registered simulation; seed 7 printed 10.1% short-interval and 10.5% long-interval
exceedance, gated only by a [1%, 15%] band.
"""

import contextlib
import io
import json
import math
import random
import statistics
from datetime import date

import pytest

from mirobody_gen import manifest, person, physiology, spec
from mirobody_gen.audit import clinical


def test_printed_spread_has_the_declared_cv():
    """crp: CVI 42.2% with CVA 3% gives c = 0.423; using c as the log SD would give CV 0.443."""
    p = next(x for x in person.build_cohort(7) if x.person_id == "p001")
    when = date(2021, 1, 1)  # before any of p001's events
    rng = random.Random("cv-check")
    xs = [physiology.measure(rng, p, "crp", when) for _ in range(200_000)]
    item = spec.indicators()["crp"]
    c = math.sqrt(item["cvi"] ** 2 + physiology.CVA ** 2) / 100
    assert statistics.pstdev(xs) / statistics.fmean(xs) == pytest.approx(c, abs=0.004)


def _simulate(threshold, n=1500, seed="rcv-prereg-v1"):
    """The pre-registered Monte Carlo: every catalogue key with a two-sided reference interval, at its
    centre and printed precision, drawn from the declared model."""
    rng = random.Random(seed)
    exceed = total = 0
    for item in spec.indicators().values():
        ref = item.get("reference") or []
        if item["value_kind"] != "quantitative" or item.get("derived_from") or not (
                isinstance(ref, list) and len(ref) == 3 and ref[0] == "range"):
            continue
        y0, d = (ref[1] + ref[2]) / 2, item["decimals"]
        c = math.sqrt((item.get("cvi") or physiology.DEFAULT_CVI) ** 2 + physiology.CVA ** 2) / 100
        s = math.sqrt(math.log(1 + c * c))
        for _ in range(n):
            y = [round(y0 * math.exp(rng.gauss(0, s) - s * s / 2), d) for _ in range(2)]
            if min(y) <= 0:
                continue
            q = [10 ** -d / (math.sqrt(12) * v) for v in y]
            if max(q) > s:
                continue
            total += 1
            exceed += abs(math.log(y[1] / y[0])) > threshold(c, s, q)
    return exceed / total


def test_preregistered_simulation_gives_five_percent():
    rate = _simulate(lambda c, s, q: 1.96 * math.sqrt(2 * s * s + q[0] ** 2 + q[1] ** 2))
    assert rate == pytest.approx(0.05, abs=0.005)


def test_old_threshold_is_detectably_wrong():
    assert _simulate(lambda c, s, q: math.log(1 + 2.77 * c)) > 0.065


@pytest.fixture(scope="module")
def seed7_records(tmp_path_factory):
    out = tmp_path_factory.mktemp("rcv")
    people = person.build_cohort(7)
    manifest.write(out, people, {p.person_id: person.encounters_for(p, 7) for p in people})
    return [json.loads(x) for x in (out / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]


def _longitudinal(records):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        findings = clinical.check_longitudinal(records, clinical.load_cvi(), clinical.load_decimals(),
                                               clinical.load_derived())
    return findings, buf.getvalue()


def test_cohort_stable_pairs_match_the_model(seed7_records):
    findings, printed = _longitudinal(seed7_records)
    assert not [f for f in findings if f.where == "overall"], printed
    stable = int(printed.split("stable ")[1].split(",")[0])
    assert stable > 5000 and "excluded" in printed and "derived:" in printed


def test_injected_jumps_are_found(seed7_records):
    jumped = json.loads(json.dumps(seed7_records))
    for i, record in enumerate(jumped):
        if i % 4 == 0:
            for row in record["rows"]:
                if row.get("key") in ("alt", "glu", "crea", "ua", "tsh") and isinstance(row.get("canonical_value"), float):
                    row["canonical_value"] *= 1.8
    findings, printed = _longitudinal(jumped)
    assert any(f.where == "overall" for f in findings), printed
    assert any(f.kind == "纵向" and f.where != "overall" for f in findings)


def test_exact_binomial_p_value():
    assert clinical.binomial_two_sided(50, 1000, 0.05) == pytest.approx(1.0, abs=1e-9)
    assert clinical.binomial_two_sided(80, 1000, 0.05) < 1e-4
