"""Clinical profile, plausible issuers: no maternal and child health hospital outside the population it
serves, and no TCM hospital under a Western outpatient record."""
from __future__ import annotations

from datetime import date

import pytest

from mirobody_gen import clinical, clinical_issuers, corpus, layout, person, spec
from mirobody_gen.facility_services import ServiceProfile
from mirobody_gen.model import Person

TCM, MATERNAL = clinical_issuers.TCM, clinical_issuers.MATERNAL_CHILD
HOSPITALS = [i for i in spec.fiction()["institutions"] if i["kind"] == "hospital"]


@pytest.fixture
def clinical_on(monkeypatch):
    monkeypatch.setattr(clinical, "ENABLED", True)


def _person(pid, sex="female", born=1990, archetype="healthy"):
    return Person(pid, sex, born, 162, archetype, "test")


def _cohort(n=None):
    people = person.build_cohort(7, n)
    return people, {p.person_id: person.encounters_for(p, 7) for p in people}


def _assign(people, encounters, profile=None):
    process = corpus.Institutions(layout.build_registry(7), 7, profile)
    return process, corpus.assign_institutions(7, people, encounters, process, profile)


def test_a_maternal_and_child_hospital_serves_women_of_reproductive_age_with_matching_care():
    name = next(i["name"] for i in HOSPITALS if MATERNAL in i["name"])
    pids = [f"issuer-{i}" for i in range(60)]
    drawn = next(p for p in pids if clinical_issuers.uses_maternal_child(7, _person(p)))
    other = next(p for p in pids if not clinical_issuers.uses_maternal_child(7, _person(p)))
    assert clinical_issuers.plausible(name, _person(drawn), 2025, "routine", 7)
    assert not clinical_issuers.plausible(name, _person(drawn, born=1970), 2025, "routine", 7)
    assert not clinical_issuers.plausible(name, _person(drawn, archetype="hypertension"), 2025, "followup", 7)
    assert not clinical_issuers.plausible(name, _person(drawn, sex="male"), 2025, "routine", 7)
    assert not clinical_issuers.plausible(name, _person(other), 2025, "routine", 7)


def test_a_tcm_hospital_issues_no_outpatient_record():
    tcm = next(i["name"] for i in HOSPITALS if TCM in i["name"])
    plain = next(i["name"] for i in HOSPITALS if i["language"] == "zh" and TCM not in i["name"]
                 and MATERNAL not in i["name"])
    p = _person("issuer-tcm", sex="male", born=1960)
    assert not clinical_issuers.plausible(tcm, p, 2025, "clinic", 7)
    assert clinical_issuers.plausible(tcm, p, 2025, "followup", 7)
    assert clinical_issuers.plausible(plain, p, 2025, "clinic", 7)


def test_clinical_mode_moves_every_implausible_visit(monkeypatch):
    people, encounters = _cohort()
    by_id = {p.person_id: p for p in people}

    def implausible() -> int:
        process, (_, chosen, _) = _assign(people, encounters)
        names = process.registry.institutions
        return sum(not clinical_issuers.plausible(names[i]["name"], by_id[pid], encounters[pid][idx - 1].exam_date.year,
                                                  encounters[pid][idx - 1].exam_type, 7)
                   for (pid, idx), i in chosen.items())

    monkeypatch.setattr(clinical, "ENABLED", False)
    assert implausible() > 0
    monkeypatch.setattr(clinical, "ENABLED", True)
    assert implausible() == 0


@pytest.mark.parametrize("target,cutoff", [("p001", date(2023, 3, 1)), ("p035", date(2022, 12, 31))])
def test_deleting_future_visits_never_changes_an_earlier_clinical_assignment(target, cutoff, clinical_on):
    people, encounters = _cohort()
    _, (homes, chosen, _) = _assign(people, encounters)
    edited = dict(encounters)
    edited[target] = [e for e in encounters[target] if e.exam_date <= cutoff]
    _, (homes2, chosen2, _) = _assign(people, edited)
    earlier = [(pid, idx) for pid, visits in encounters.items() for idx, e in enumerate(visits, 1)
               if e.exam_date <= cutoff]
    assert earlier and all(chosen[k] == chosen2[k] for k in earlier)


def test_a_required_service_keeps_a_declared_provider(clinical_on):
    tcm = next(i for i in HOSPITALS if i["language"] == "zh" and TCM in i["name"])
    plain = next(i for i in HOSPITALS if i["language"] == "zh" and TCM not in i["name"] and MATERNAL not in i["name"])
    english = [i for i in HOSPITALS if i["language"] == "en"][:2]
    profile = ServiceProfile({"schema_version": "fictional-facility-services-v1", "profile_id": "test-v1",
                              "authority": "authored_synthetic", "required_by_exam_type": {"clinic": "outpatient"},
                              "institutions": [{"name": i["name"], "services": ["outpatient"]}
                                               for i in [tcm, plain, *english]]})
    people, encounters = _cohort(20)
    process, (_, chosen, _) = _assign(people, encounters, profile)
    clinic = [i for (pid, idx), i in chosen.items() if encounters[pid][idx - 1].exam_type == "clinic"]
    assert clinic and all(profile.supports(i, "outpatient") for i in clinic)
    assert all(TCM not in process.registry.institutions[i]["name"] for i in clinic)
