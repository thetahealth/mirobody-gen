"""C first slice: observed evidence dates, required FT4, and per-encounter export."""
import copy
from datetime import date, timedelta
import json

import pytest

from mirobody_gen import manifest, spec
from mirobody_gen.audit import clinical
from mirobody_gen.model import Encounter, Person, Reading

CKD, THYROID, ANEMIA, IRON_DEFICIENCY = "709044004", "40930008", "271737000", "87522002"


def subject():
    return Person("diagnosis-time", "female", 1980, 165, "healthy", "华东",
                  weight_anchors=((date(2024, 1, 1), 65),))


def visit(day, **values):
    if isinstance(day, int): day = date(2024, 1, 1) + timedelta(days=day)
    if isinstance(day, str): day = date.fromisoformat(day)
    rs = []
    for key, value in values.items():
        item = spec.indicators()[key]
        lo, hi = spec.reference_bounds(key, "female")
        rs.append(Reading(key, item["zh"], str(value), item["unit"], "", "normal",
                          canonical_value=value, unit_ucum=item["unit"], ref_low=lo, ref_high=hi))
    return Encounter(subject().person_id, day, "follow-up", "hospital", (), rs)


def codes(visits):
    return {c["code"] for c in manifest._conditions_of(subject(), visits)}


@pytest.mark.parametrize("days,expected", [(1, False), (89, False), (90, False), (91, True)])
def test_ckd_requires_elapsed_chronicity_not_two_visits(days, expected):
    assert (CKD in codes([visit(0, egfr=55), visit(days, egfr=55)])) == expected


def test_duplicate_same_date_does_not_count_as_repeat_evidence():
    assert CKD not in codes([visit(0, egfr=55), visit(0, egfr=55)])
    assert THYROID not in codes([visit(0, tsh=12, ft4=10), visit(0, tsh=12, ft4=10)])


def test_unmeasured_is_not_normal_and_normal_breaks_low_egfr_run():
    assert CKD in codes([visit(0, egfr=55), visit(30, tsh=1), visit(91, egfr=55)])
    assert CKD not in codes([visit(0, egfr=55), visit(30, egfr=65), visit(91, egfr=55)])


def test_conflicting_same_day_observations_are_not_silently_last_wins():
    visits = [visit(0, egfr=55), visit(0, egfr=65), visit(91, egfr=55)]
    assert CKD not in codes(visits)
    assert codes(visits) == codes(list(reversed(visits)))


@pytest.mark.parametrize("ft4", [16, None])
def test_tsh_alone_cannot_establish_overt_hypothyroidism(ft4):
    values = {"tsh": 12} if ft4 is None else {"tsh": 12, "ft4": ft4}
    assert THYROID not in codes([visit(0, **values), visit(30, **values)])


def test_low_ft4_must_have_its_own_reference_limit_and_compatible_unit():
    a, b = visit(0, tsh=12, ft4=10), visit(30, tsh=12, ft4=10)
    a.readings[-1].ref_low = b.readings[-1].ref_low = None
    assert THYROID not in codes([a, b])
    a, b = visit(0, tsh=12, ft4=10), visit(30, tsh=12, ft4=10)
    a.readings[-1].unit_ucum = b.readings[-1].unit_ucum = "unknown-unit"
    assert THYROID not in codes([a, b])


def test_complete_low_ft4_samples_pass_but_fields_are_not_joined_across_samples():
    assert THYROID in codes([visit(0, tsh=12, ft4=10), visit(30, tsh=12, ft4=10)])
    assert THYROID not in codes([visit(0, tsh=12), visit(0, ft4=10), visit(30, tsh=12), visit(30, ft4=10)])


def test_hgb_pattern_does_not_invent_iron_deficiency():
    got = codes([visit(0, hgb=100), visit(30, hgb=100)])
    assert ANEMIA in got and IRON_DEFICIENCY not in got


def write(root, visits):
    p = subject()
    manifest.write(root, [p], {p.person_id: visits})
    return [json.loads(line) for line in (root/"manifest.jsonl").read_text().splitlines()]


def test_export_does_not_copy_future_confirmation_into_past_and_keeps_full_truth(tmp_path):
    records = write(tmp_path, [visit(0, egfr=55), visit(91, egfr=55)])
    assert CKD not in {c["code"] for c in records[0]["person"]["conditions"]}
    confirmed = next(c for c in records[1]["person"]["conditions"] if c["code"] == CKD)
    assert confirmed["recorded_date"] == "2024-04-01" and confirmed["onset_date"] is None
    full = json.loads((tmp_path/"people.jsonl").read_text())
    assert CKD in {c["code"] for c in full["conditions"]}


def test_explicit_future_condition_is_filtered_even_in_direct_record_api():
    record = manifest.encounter_record(subject(), visit(0, egfr=55), conditions=[
        {"code": CKD, "display": "CKD", "onset_date": "2025-01-01"}])
    assert not record["person"]["conditions"]


def test_direct_record_api_cannot_carry_a_future_assessment():
    with pytest.raises(ValueError, match="future evidence"):
        manifest.encounter_record(subject(), visit(0, egfr=55), assessments=[
            {"code": CKD, "status": "met", "recorded_date": "2024-04-01", "evidence_dates": []}])


def test_audit_repeats_with_different_times_on_one_date_count_once():
    a = manifest.encounter_record(subject(), visit(0, tsh=12, ft4=10))
    b = copy.deepcopy(a)
    a["collected"], b["collected"] = "2024-01-01T09:00:00", "2024-01-01T18:00:00"
    assert not clinical.check_diagnosis_coherence([a, b], spec.cohort()["diagnostic_criteria"])


def test_export_cutoff_ignores_modified_deleted_or_reordered_future_visits(tmp_path):
    prefix = [visit(0, egfr=55), visit(30, egfr=55)]
    original = write(tmp_path/"a", prefix+[visit(91, egfr=55)])[:2]
    changed = write(tmp_path/"b", [visit(150, egfr=80), *prefix])[:2]
    deleted = write(tmp_path/"c", prefix)
    assert original == changed == deleted


def test_missing_ft4_and_short_chronicity_remain_explicit_unknown_or_pending(tmp_path):
    records = write(tmp_path, [visit(0, egfr=55, tsh=12), visit(1, egfr=55, tsh=12)])
    assessments = {a["code"]: a for a in records[-1]["diagnostic_assessments"]}
    assert assessments[CKD]["status"] == "pending_confirmation"
    assert assessments[THYROID]["status"] == "not_assessable"


def test_audit_does_not_force_the_same_short_ckd_or_normal_ft4_false_diagnosis(tmp_path):
    records = write(tmp_path, [visit(0, egfr=55, tsh=12, ft4=16), visit(1, egfr=55, tsh=12, ft4=16)])
    for r in records: r["person"]["conditions"] = []
    assert not clinical.check_diagnosis_coherence(records, spec.cohort()["diagnostic_criteria"])


def test_audit_checks_current_record_and_rejects_future_diagnosis(tmp_path):
    records = write(tmp_path, [visit(0, egfr=55), visit(91, egfr=55)])
    missing = copy.deepcopy(records)
    missing[-1]["person"]["conditions"] = []
    assert clinical.check_diagnosis_coherence(missing, spec.cohort()["diagnostic_criteria"])
    leaked = copy.deepcopy(records)
    leaked[0]["person"]["conditions"] = [{"code": CKD, "recorded_date": "2024-04-01"}]
    assert clinical.check_diagnosis_coherence(leaked, spec.cohort()["diagnostic_criteria"])
