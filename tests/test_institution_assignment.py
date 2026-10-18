"""Institutions are assigned in calendar order across the cohort (`institution-assignment-v2`).

Before, the corpus drew institutions person by person from each person's document stream, so a later
visit of someone rendered earlier changed earlier visits of everyone after them: deleting p001's
visits after 2023-03-01 changed 14 pre-cutoff files of 10 other people (seed 7, first 20 people).
"""

from datetime import date

import pytest

from mirobody_gen import corpus, layout, person


def assignments(people, encounters):
    process = corpus.Institutions(layout.build_registry(7), 7)
    homes, chosen, _ = corpus.assign_institutions(7, people, encounters, process)
    return homes, chosen


@pytest.mark.parametrize("target,cutoff", [("p001", date(2023, 3, 1)), ("p019", date(2024, 3, 1)),
                                           ("p035", date(2022, 12, 31))])
def test_deleting_future_visits_never_changes_an_earlier_assignment(target, cutoff):
    people = person.build_cohort(7)
    encounters = {p.person_id: person.encounters_for(p, 7) for p in people}
    homes, chosen = assignments(people, encounters)
    edited = dict(encounters)
    edited[target] = [e for e in encounters[target] if e.exam_date <= cutoff]
    homes2, chosen2 = assignments(people, edited)
    earlier = [(pid, idx) for pid, visits in encounters.items() for idx, e in enumerate(visits, 1)
               if e.exam_date <= cutoff]
    assert earlier and all(chosen[k] == chosen2[k] for k in earlier)
    first_visit = {pid: visits[0].exam_date for pid, visits in encounters.items() if visits}
    assert all(homes[pid] == homes2[pid] for pid, day in first_visit.items() if day <= cutoff)


def test_assignment_is_reproducible_and_covers_every_visit():
    people = person.build_cohort(7)[:12]
    encounters = {p.person_id: person.encounters_for(p, 7) for p in people}
    a, b = assignments(people, encounters), assignments(people, encounters)
    assert a == b
    assert len(a[1]) == sum(len(v) for v in encounters.values())
