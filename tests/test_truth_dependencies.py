"""Regression checks for ordered derivations, intervention truth and genotype phase claims."""

from __future__ import annotations

import json
import random
from dataclasses import replace
from datetime import date, timedelta

import pytest

from mirobody_gen import genomics, person as person_mod, physiology, spec
from mirobody_gen.model import Event, Person


def _person(**changes) -> Person:
    person = Person(
        person_id="dependency-check", sex="male", birth_year=1980, height_cm=175.0,
        archetype="healthy", region="上海",
        weight_anchors=((date(2024, 1, 1), 70.0), (date(2025, 1, 1), 77.0)),
    )
    return replace(person, **changes)


def _weight_event() -> Event:
    return Event(
        name="减重干预", event_type="exercise_change", start=date(2024, 4, 1),
        duration_days=180, health_effect="positive", impact_level="medium",
        effects={"weight": (-0.08, 90, 30)},
    )


@pytest.mark.parametrize("sex", ["male", "female"])
def test_ordered_panels_keep_second_pass_readings_and_printed_identities(sex):
    """A requested result must survive the dependency chain, with sex exclusions preserved."""
    person = _person(sex=sex)
    orders = ["vitals", "liver", "tumor_full"]
    encounter = person_mod.encounter_for(person, date(2024, 7, 1), "specialty", orders, random.Random(7))
    values = {r.key: float(r.value) for r in encounter.readings}
    requested = {
        key for order in orders for key in person_mod.ORDERS[order]
        if spec.indicators()[key].get("sex_specific") in (None, sex)
    }
    assert set(values) == requested
    assert values["waist"] == float(spec.format_value("waist", 2.8 * values["bmi"] + (19 if sex == "male" else 13)))
    assert values["ag_ratio"] == float(spec.format_value("ag_ratio", values["alb"] / values["glb"]))
    if sex == "male":
        assert values["fpsa_ratio"] == float(spec.format_value("fpsa_ratio", values["fpsa"] / values["psa"]))
    else:
        assert not {"psa", "fpsa", "fpsa_ratio"} & set(values)


def test_scheduled_cohort_does_not_silently_lose_requested_second_pass_results():
    """Exercise real packages and follow-ups, beyond a manually constructed complete panel."""
    seen = set()
    for person in person_mod.build_cohort(7):
        for encounter in person_mod.encounters_for(person, 7):
            ordered = {key for panel in encounter.panels for key in person_mod.ORDERS.get(panel, [])}
            requested = {"waist", "ag_ratio", "fpsa_ratio"} & ordered
            if person.sex != "male":
                requested.discard("fpsa_ratio")
            actual = {r.key for r in encounter.readings}
            assert requested <= actual, (person.person_id, encounter.exam_date, requested - actual)
            seen.update(requested)
    assert seen == {"waist", "ag_ratio", "fpsa_ratio"}


def test_weight_intervention_counterfactual_has_onset_and_decay():
    event = _weight_event()
    treated = _person(events=(event,))
    untreated = replace(treated, events=())
    checkpoints = [
        (event.start - timedelta(days=1), 1.0),
        (event.start, 1.0),
        (event.start + timedelta(days=45), 0.96),
        (event.start + timedelta(days=90), 0.92),
        (event.start + timedelta(days=210), 0.96),
    ]
    for when, ratio in checkpoints:
        observed = physiology.weight_at(treated, when)
        assert observed == pytest.approx(physiology.weight_at(untreated, when) * ratio)
        assert physiology.expected(treated, "weight", when) == observed
        assert physiology.measure(random.Random(7), treated, "weight", when) == observed
    # After the last anchor, the baseline is held constant but the event still evolves.
    when = date(2025, 2, 1)
    assert 0.92 * 77 < physiology.weight_at(treated, when) < 77


def test_generated_weight_events_reach_measurements_and_bmi():
    """Configuration effects must survive timeline construction and enter report truth."""
    checked_scripts = set()
    for person in person_mod.build_cohort(7):
        scripts = [e for e in person.events if e.note == "原型剧本" and "weight" in e.effects]
        for event in scripts:
            checked_scripts.add(person.archetype)
            magnitude, onset, _ = event.effects["weight"]
            assert "weight_kg" not in event.effects
            treated = replace(person, events=(event,))
            untreated = replace(person, events=())
            when = event.start + timedelta(days=onset)
            assert physiology.weight_at(treated, when) == pytest.approx(
                physiology.weight_at(untreated, when) * (1 + magnitude)
            )
            encounter = person_mod.encounter_for(treated, when, "specialty", ["vitals"], random.Random(7))
            values = {r.key: float(r.value) for r in encounter.readings}
            assert values["weight"] == float(spec.format_value("weight", physiology.weight_at(treated, when)))
            assert values["bmi"] == float(spec.format_value("bmi", values["weight"] / (values["height"] / 100) ** 2))
    assert checked_scripts == {"prediabetes_to_t2dm", "fatty_liver"}


def test_incident_weight_effects_are_not_discarded():
    relevant = {i["name"] for i in person_mod.INCIDENTS if "weight_kg" in i["effects"]}
    seen = set()
    for person in person_mod.build_cohort(7):
        for event in person.events:
            if event.name in relevant:
                assert "weight" in event.effects
                assert "weight_kg" not in event.effects
                seen.add(event.name)
    assert seen == relevant


def test_disease_trend_uses_cohort_start_independently_of_events():
    person = _person(trend={"crea": 0.09})
    when = date(2025, 1, 1)
    expected_factor = 1.09 ** (366 / 365.25)
    unrelated = _weight_event()
    variants = [
        person,
        replace(person, events=(unrelated,)),
        replace(person, events=(replace(unrelated, start=date(2024, 8, 1)),)),
    ]
    for variant in variants:
        assert physiology.trend_factor(variant, "crea", when) == pytest.approx(expected_factor)
        assert physiology.trend_factor(variant, "crea", date(2023, 12, 31)) == 1.0
    # The same latent trend and same measurement noise must give the same creatinine even
    # when all unrelated events are removed, as in a causal counterfactual evaluation.
    measured = [physiology.measure(random.Random(7), p, "crea", when) for p in variants]
    assert measured == [measured[0]] * len(measured)


def test_vcf_export_preserves_unphased_calls_and_matches_truth(tmp_path, monkeypatch):
    monkeypatch.setattr(genomics, "COVERAGE", 1.0)
    monkeypatch.setattr(genomics, "VENDOR_WEIGHTS", {"zh": {"vcf": 1}, "en": {"vcf": 1}})
    monkeypatch.setattr(genomics, "NO_CALL_RATE", 0.1)
    person = _person()
    assert genomics.write_all(tmp_path, [person], 7, {person.person_id: "zh"}) == 1
    truth = json.loads((tmp_path / "genomics.jsonl").read_text(encoding="utf-8"))
    rows = [line.split("\t") for line in (tmp_path / truth["file"]).read_text(encoding="utf-8").splitlines()
            if not line.startswith("#")]
    actual = {row[2]: row[9] for row in rows}
    assert truth["n_no_call"] > 0
    assert any(s["zygosity"] == "heterozygous" for s in truth["sites"])
    assert len(actual) == len(truth["sites"])
    for site in truth["sites"]:
        gt = actual[site["rsid"]]
        assert "|" not in gt, "independent SNP draws must not claim haplotype phase"
        assert gt == (site["vcf_gt"] or "./.")
        assert site["vcf_gt"] == site["array_gt"]
        if site["call_status"] != "no_call":
            assert gt in {"0/0", "0/1", "1/1"}
            alleles = [site["ref"], site["alt"]]
            assert site["genotype_raw"] == "".join(alleles[int(index)] for index in gt.split("/"))
