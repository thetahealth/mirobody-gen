"""C: latent archetypes are not a dated clinical history in truth or documents."""
from dataclasses import asdict, replace
from datetime import date
import json
import random

import pytest

from mirobody_gen import book, layout, manifest, person, spec
from mirobody_gen.model import Event
from mirobody_gen.pairs import clean_family
from mirobody_gen.render import pdf

DIABETES = "44054006"


def case():
    p = next(p for p in person.build_cohort(7) if p.person_id == "p019")
    es = person.encounters_for(p, 7)
    e = next(e for e in es if e.exam_date == date(2022, 10, 21))
    return p, es, e


def family(lang="en"):
    registry = layout.build_registry(7)
    return clean_family(registry.family(registry.pools[(lang, "hospital")][0]))


def note(p, e, lang="en"):
    return book.build_outpatient(random.Random(7), "dated-diagnosis", p, e, family(lang))


def body(d):
    return "\n".join(text for b in d.blocks for row in b.rows for text in row if text)


def source_diagnosis(day="2022-10-22", **change):
    return {"code": DIABETES, "display": "2型糖尿病", "recorded_date": day,
            "source": "authored-synthetic-diagnosis:1", **change}


def test_original_p019_prior_is_not_a_historical_diagnosis():
    p, es, e = case()
    assert next(a for a in manifest.diagnostic_assessments(es,e.exam_date) if a["code"] == DIABETES)["status"] == "not_met"
    assert DIABETES not in {c["code"] for c in manifest._conditions_of(p, es, e.exam_date)}
    assert not note(p,e).diagnoses_truth
    text = body(note(p,e))
    assert "type 2 diabetes" not in text.lower() and "1 years" not in text
    assert "not documented" in text and "No history of" not in text
    assert "Continue current treatment" not in text


@pytest.mark.parametrize("lang", ["en", "zh"])
def test_real_outpatient_pdf_does_not_print_the_unsupported_diagnosis(lang):
    import fitz
    p, _, e = case()
    d = note(p,e,lang)
    data, _ = pdf.render(d, e.exam_date.isoformat())
    with fitz.open(stream=data,filetype="pdf") as doc:
        text = "\n".join(p.get_text() for p in doc)
    assert "type 2 diabetes" not in text.lower() and "2型糖尿病" not in text
    assert ("not documented" if lang=="en" else "未记录") in text
    assert e.exam_date.isoformat() in text


def test_full_simulation_design_and_future_events_are_not_erased(tmp_path):
    p, es, _ = case()
    manifest.write(tmp_path,[p],{p.person_id:es})
    record = json.loads((tmp_path/"people.jsonl").read_text())
    assert record["archetype_conditions"] == spec.cohort()["archetype_conditions"][p.archetype]
    assert len(record["events"]) == len(p.events)
    assert record["truth_scope"] == "full_simulation_timeline_not_point_in_time"


def test_explicit_dated_source_is_preserved_even_when_current_values_are_normal():
    p, _, e = case()
    entry = source_diagnosis("2022-10-20")
    known = replace(p, diagnosis_history=(entry,))
    early = manifest._conditions_of(known,[],date(2022,10,19))
    now = manifest._conditions_of(known,[],e.exam_date)
    assert not early and len(now)==1
    assert all(now[0][k] == v for k,v in entry.items())
    assert "onset_date" not in now[0] or now[0]["onset_date"] is None
    text = body(note(known,e))
    assert "type 2 diabetes" in text.lower() and "2022-10-20" in text
    assert "years" not in text


@pytest.mark.parametrize("entry", [source_diagnosis(None), source_diagnosis("2022-10-20",source="")])
def test_undated_or_unsourced_declared_history_is_archived_but_not_promoted(entry):
    p, _, e = case()
    q = replace(p,diagnosis_history=(entry,))
    assert not manifest._conditions_of(q,[],e.exam_date)
    assert manifest.person_record(q)["diagnosis_history"] == [entry]
    assert not note(q,e).diagnoses_truth


def test_future_source_diagnosis_edit_delete_does_not_change_past_pdf_or_manifest():
    p, es, e = case()
    a = replace(p,diagnosis_history=(source_diagnosis(),))
    b = replace(p,diagnosis_history=(source_diagnosis("2025-01-01",display="未来更正诊断"),))
    variants=[a,b,replace(p,diagnosis_history=())]
    records=[manifest.encounter_record(q,e) for q in variants]
    materials=[pdf.render(note(q,e),e.exam_date.isoformat())[0] for q in variants]
    assert records == [records[0]]*len(records)
    assert materials == [materials[0]]*len(materials)
    assert [asdict(x) for x in es] == [asdict(x) for x in person.encounters_for(p,7)]


def test_treatment_start_does_not_invent_diagnosis_date_or_years_of_history():
    p, _, e = case()
    treatment=replace(p.events[0],start=date(2022,10,20))
    q=replace(p,events=(treatment,))
    assert not manifest._conditions_of(q,[],e.exam_date)
    d=note(q,e)
    assert not d.diagnoses_truth and "years" not in body(d)


def test_generated_encounter_conditions_and_manifest_agree_at_each_date():
    p, es, _ = case()
    for e in es:
        assert e.conditions == manifest._conditions_of(p,es,e.exam_date)
        assert all(c.get("recorded_date") and c.get("source") for c in e.conditions)


def test_low_hb_pattern_is_generic_anemia_in_document_not_latent_iron_cause():
    p, _, e = case()
    q=replace(p,archetype="iron_deficiency_anemia")
    generic={"code":"271737000","display":"贫血","recorded_date":"2022-10-20","source":"synthetic_numeric_rule"}
    e=replace(e,conditions=[generic])
    d=note(q,e)
    assert "anaemia" in body(d) and "iron" not in body(d).lower()
    assert all(c.get("code")!="87522002" for c in d.diagnoses_truth)


def test_checkup_history_does_not_use_the_treatment_date_as_diagnosis_evidence():
    p, _, e = case()
    treatment=replace(p.events[0],start=date(2022,10,20))
    q=replace(p,events=(treatment,))
    e=replace(e,exam_type="routine",package="standard")
    # Section history belongs to the same existing renderer as whole checkup books.
    probe=book.Doc("history","p019",family(),"",[],[],[])
    blocks=book.section_blocks(random.Random(7),probe,q,e,family())
    text="\n".join(t for b in blocks for row in b.rows for t in row)
    assert "Type 2 diabetes" not in text and "not documented" in text
    history=[item for b in blocks for item in b.truth if item.get("history_status")]
    assert history and all(item["history_status"]=="not_documented" and item["abnormal"] is None for item in history)


def test_unstarted_infection_cannot_add_current_symptoms_or_diagnosis():
    p, _, e = case()
    future=Event("急性上呼吸道感染","health_event",date(2022,10,22),7,"negative","low",note="随机事件")
    a,b=replace(p,events=()),replace(p,events=(future,))
    assert person.profile.complaints_for(a,e.exam_date,"clinic","en",7)==person.profile.complaints_for(b,e.exam_date,"clinic","en",7)
    assert body(note(a,e))==body(note(b,e))


def test_independent_audit_detects_restored_undated_prior():
    from mirobody_gen.audit import clinical
    p, _, e = case()
    r=manifest.encounter_record(p,e)
    r["person"]["conditions"]=[{"code":DIABETES,"display":"2型糖尿病"}]
    assert any(f.kind=="诊断时序" for f in clinical.check_diagnosis_coherence([r],spec.cohort()["diagnostic_criteria"]))


@pytest.mark.parametrize("day", ["2022-02-31", "20221020", "2022-10-20T00:00:00"])
def test_invalid_source_date_is_not_silently_sorted_or_truncated(day):
    p, _, e=case()
    q=replace(p,diagnosis_history=(source_diagnosis(day),))
    with pytest.raises(ValueError):
        manifest._conditions_of(q,[],e.exam_date)


def test_future_treatment_before_past_treatment_in_input_cannot_change_sections_or_symptoms():
    p, _, e=case()
    early=replace(p.events[0],start=date(2022,10,20))
    future=replace(p.events[0],start=date(2025,1,1))
    a,b=replace(p,events=(early,)),replace(p,events=(future,early))
    def sections(who):
        probe=book.Doc("history","p019",family(),"",[],[],[])
        return book.section_blocks(random.Random(7),probe,who,replace(e,package="standard"),family())
    assert sections(a)==sections(b)
    assert person.profile.complaints_for(a,e.exam_date,"clinic","en",7)==person.profile.complaints_for(b,e.exam_date,"clinic","en",7)
