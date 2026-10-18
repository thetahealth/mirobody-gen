"""`--people N` must yield exactly N people, apportioned across archetypes by prevalence weight.

The old rule (`max(1, round(n * scale))` per archetype, then truncation) produced 60 people for
N=61 and 99 for N=100, and for N=7/8 silently dropped whichever archetypes came last in the table.
"""

import collections

import pytest

from mirobody_gen import person


ARCHETYPE_N = {a["name"]: a["n"] for a in person.ARCHETYPES}


@pytest.mark.parametrize("n", [1, 7, 8, 9, 60, 61, 100])
def test_requested_people_are_generated_exactly(n):
    people = person.build_cohort(7, n)
    assert len(people) == n
    assert len({p.person_id for p in people}) == n


def test_full_cohort_is_unchanged_by_explicit_size():
    full = person.build_cohort(7)
    explicit = person.build_cohort(7, sum(ARCHETYPE_N.values()))
    assert [(p.person_id, p.archetype, p.baseline) for p in full] == \
        [(p.person_id, p.archetype, p.baseline) for p in explicit]
    assert collections.Counter(p.archetype for p in full) == ARCHETYPE_N


@pytest.mark.parametrize("n", [8, 9, 61, 100])
def test_every_archetype_present_when_seats_allow(n):
    assert {p.archetype for p in person.build_cohort(7, n)} == set(ARCHETYPE_N)


def test_small_cohort_does_not_demand_impossible_coverage():
    # 7 seats cannot hold 8 archetypes; the allocation stays proportional instead of erroring.
    counts = collections.Counter(p.archetype for p in person.build_cohort(7, 7))
    assert sum(counts.values()) == 7 and len(counts) == 6
    assert counts["healthy"] == 2


def test_allocation_is_proportional_within_one_seat():
    full = sum(ARCHETYPE_N.values())
    for n in (61, 100, 137):
        counts = collections.Counter(p.archetype for p in person.build_cohort(7, n))
        for name, weight in ARCHETYPE_N.items():
            assert abs(counts[name] - weight * n / full) < 1 + 1e-9, (n, name, counts[name])


def test_same_size_is_reproducible():
    a = person.build_cohort(11, 61)
    b = person.build_cohort(11, 61)
    assert [(p.person_id, p.archetype, p.baseline, p.events) for p in a] == \
        [(p.person_id, p.archetype, p.baseline, p.events) for p in b]


@pytest.mark.parametrize("bad", [0, -1, 2.5, True, "8"])
def test_illegal_size_is_rejected(bad):
    with pytest.raises(ValueError):
        person.build_cohort(7, bad)


@pytest.mark.parametrize("bad", ["0", "-3", "abc"])
def test_cli_rejects_illegal_people(bad, tmp_path):
    from mirobody_gen import build
    with pytest.raises(SystemExit) as exc:
        build.main(["--seed", "7", "--people", bad, "--out", str(tmp_path / "o")])
    assert exc.value.code == 2
    assert not (tmp_path / "o").exists()
