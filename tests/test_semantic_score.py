"""The semantic scorer must detect semantic errors even when print extraction is perfect."""

from __future__ import annotations

import copy
import json
import subprocess
import sys

import pytest

from mirobody_gen.harness import score as printed_score
from mirobody_gen.harness import semantic_score as semantic


def corpus():
    return [{"file": "files/a.pdf", "kind": "lab_slip", "hazards": [],
             "dates": [{"role": "collected", "printed": "2025-01-02", "iso": "2025-01-02T09:00:00"}],
             "layout": {"columns": ["name", "result", "unit"]},
             "printed_rows": [{"item_name": "空腹血糖", "item_value": "5.6", "item_unit": "mmol/L",
                               "item_range": "3.9-6.1", "is_abnormal": "0", "readable": True,
                               "unreadable_fields": [], "readings": [0]}],
             "readings": [{"key": "glu", "loinc": "1558-6", "canonical_value": 5.6,
                           "value_text": "5.6", "unit_ucum": "mmol/L", "value_kind": "quantitative",
                           "observed": "2025-01-02", "role": "current", "expect_resolvable": True,
                           "printed_row": 0}]}]


def submission():
    return [{"file": "files/a.pdf", "readings": [
        {"id": "result-1", "item_name": "空腹血糖", "loinc": "1558-6", "canonical_value": 5.6,
         "unit_ucum": "mmol/L", "observed": "2025-01-02", "role": "current", "source_file": "files/a.pdf"}]}]


def test_perfect():
    report = semantic.score(corpus(), submission())
    assert report["all_applicable_fields"]["f1"] == 1
    assert all(f["recall"] == 1 for f in report["fields"].values())


@pytest.mark.parametrize(("field", "wrong"), [
    ("loinc", "4548-4"), ("canonical_value", 56), ("unit_ucum", "mg/dL"),
    ("observed", "2025-01-03"), ("role", "previous"), ("source_file", "files/b.pdf"),
])
def test_semantic_corruption_is_not_hidden_by_correct_printed_fields(field, wrong):
    truth, pred = corpus(), submission()
    print_pred = {"files/a.pdf": [{**{k: truth[0]["printed_rows"][0][k] for k in printed_score.FIELDS},
                                  **{field: wrong}}]}
    # Reproduce the old gap: these semantic mutations leave printed metrics perfect.
    assert printed_score.score(truth, print_pred)["v1"]["average"] == 1
    pred[0]["readings"][0][field] = wrong
    report = semantic.score(truth, pred)
    assert report["matched"]["f1"] == 1
    assert report["fields"][field]["recall"] == 0
    assert report["all_applicable_fields"]["f1"] == 0


def test_missing_prediction_counts_against_all_eligible_field_recall():
    report = semantic.score(corpus(), [])
    assert report["matched"]["recall"] == 0
    assert report["all_applicable_fields"]["f1"] == 0
    assert report["fields"]["loinc"]["recall"] == 0
    assert report["fields"]["loinc"]["accuracy_on_matched"] is None


def test_extra_and_duplicate_observation_are_false_positives():
    pred = submission()
    pred[0]["readings"].append({**pred[0]["readings"][0], "id": "duplicate-fact"})
    report = semantic.score(corpus(), pred)
    assert report["counts"]["matched"] == 1
    assert report["all_applicable_fields"]["precision"] == 0.5
    assert len(report["per_document"]["files/a.pdf"]["extra_prediction_ids"]) == 1


def test_code_alone_cannot_match_an_unrelated_visible_name():
    pred = submission()
    pred[0]["readings"][0]["item_name"] = "患者姓名"
    assert semantic.score(corpus(), pred)["matched"]["f1"] == 0


@pytest.mark.parametrize("fault", ["duplicate_file", "duplicate_id", "unknown_file", "truth_index", "key"])
def test_invalid_submission_is_rejected(fault):
    pred = submission()
    if fault == "duplicate_file":
        pred.append(copy.deepcopy(pred[0]))
    elif fault == "duplicate_id":
        pred[0]["readings"].append(copy.deepcopy(pred[0]["readings"][0]))
    elif fault == "unknown_file":
        pred[0]["file"] = "not_in_manifest.pdf"
    else:
        pred[0]["readings"][0][fault] = 0
    with pytest.raises(ValueError):
        semantic.score(corpus(), pred)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "5.6"])
def test_non_json_or_non_numeric_values_rejected(value):
    pred = submission()
    pred[0]["readings"][0]["canonical_value"] = value
    with pytest.raises(ValueError):
        semantic.score(corpus(), pred)


def test_invalid_calendar_date_rejected():
    pred = submission()
    pred[0]["readings"][0]["observed"] = "2025-02-30"
    with pytest.raises(ValueError):
        semantic.score(corpus(), pred)


def test_duplicate_truth_files_rejected():
    truth = corpus()
    with pytest.raises(ValueError, match="duplicate truth file"):
        semantic.score(truth + truth, submission())


def test_bp_pair_one_cell_two_readings_is_not_a_duplicate():
    truth, pred = corpus(), submission()
    truth[0]["printed_rows"][0].update(item_name="BP", item_value="128/82", item_unit="mmHg", readings=[0, 1])
    truth[0]["readings"] = [{**truth[0]["readings"][0], "key": key, "loinc": loinc,
                             "canonical_value": value, "unit_ucum": "mm[Hg]"}
                            for key, loinc, value in (("sbp", "8480-6", 128), ("dbp", "8462-4", 82))]
    pred[0]["readings"] = [{**pred[0]["readings"][0], "id": key, "item_name": "BP", "loinc": loinc,
                           "canonical_value": value, "unit_ucum": "mm[Hg]"}
                          for key, loinc, value in (("dbp", "8462-4", 82), ("sbp", "8480-6", 128))]
    report = semantic.score(truth, pred)
    assert report["counts"]["truth"] == 2
    assert report["all_applicable_fields"]["f1"] == 1
    # Values may not independently choose their best match after code assignment.
    pred[0]["readings"][0]["canonical_value"] = 128
    pred[0]["readings"][1]["canonical_value"] = 82
    report = semantic.score(truth, pred)
    assert report["fields"]["loinc"]["recall"] == 1
    assert report["fields"]["canonical_value"]["recall"] == 0
    assert report["all_applicable_fields"]["f1"] == 0


def test_repeated_date_keeps_multiplicity():
    truth, pred = corpus(), submission()
    truth[0]["readings"].append(copy.deepcopy(truth[0]["readings"][0]))
    truth[0]["printed_rows"][0]["readings"].append(1)
    report = semantic.score(truth, pred)
    assert report["matched"]["recall"] == 0.5
    pred[0]["readings"].append({**pred[0]["readings"][0], "id": "result-2"})
    assert semantic.score(truth, pred)["all_applicable_fields"]["f1"] == 1


def test_ambiguous_same_day_repeats_do_not_align_by_numeric_value():
    truth, pred = corpus(), submission()
    truth[0]["readings"].append({**truth[0]["readings"][0], "canonical_value": 6.8})
    truth[0]["printed_rows"].append({**truth[0]["printed_rows"][0], "item_value": "6.8", "readings": [1]})
    truth[0]["readings"][1]["printed_row"] = 1
    pred[0]["readings"].append({**pred[0]["readings"][0], "id": "result-2", "canonical_value": 6.8})
    pred[0]["readings"].reverse()
    report = semantic.score(truth, pred)
    assert report["fields"]["canonical_value"]["correct"] == 0
    assert report["ambiguous_truth_groups"] == 1
    assert report["per_document"]["files/a.pdf"]["ambiguous_truth_groups"] == [[0, 1]]


def test_unit_missing_masks_unit_and_numeric_without_using_hidden_unit():
    truth, pred = corpus(), submission()
    truth[0]["printed_rows"][0]["item_unit"] = ""
    pred[0]["readings"][0].update(canonical_value=None, unit_ucum=None)
    report = semantic.score(truth, pred)
    assert report["fields"]["canonical_value"]["eligible"] == 0
    assert report["fields"]["unit_ucum"]["eligible"] == 0
    assert report["all_applicable_fields"]["f1"] == 1
    assert report["unscored_fields"]["unit_ucum:unit_not_visible"] == 1


def test_hidden_encounter_date_cannot_create_date_eligibility():
    truth, pred = corpus(), submission()
    truth[0].update(dates=[], encounter_dates=["2025-01-02"])
    pred[0]["readings"][0]["observed"] = None
    report = semantic.score(truth, pred)
    assert report["fields"]["observed"]["eligible"] == 0
    assert report["all_applicable_fields"]["f1"] == 1


def test_report_date_cannot_stand_in_for_collection_date():
    truth, pred = corpus(), submission()
    truth[0]["dates"][0]["role"] = "reported"
    assert semantic.score(truth, pred)["fields"]["observed"]["eligible"] == 0


def test_multiple_collection_dates_without_row_evidence_are_unscored():
    truth = corpus()
    truth[0]["dates"].append({"role": "collected", "printed": "2025-01-03", "iso": "2025-01-03T09:00:00"})
    assert semantic.score(truth, submission())["fields"]["observed"]["eligible"] == 0


def test_a_second_partial_collection_date_prevents_assuming_the_first_applies_to_every_row():
    truth = corpus()
    truth[0]["dates"].append({"role": "collected", "printed": "10:00", "iso": "2025-01-03T10:00:00"})
    assert semantic.score(truth, submission())["fields"]["observed"]["eligible"] == 0


@pytest.mark.parametrize("printed", ["09:00", "2025年01月", "01/02/25", "01/02/2025", "2025-02-30"])
def test_incomplete_ambiguous_or_invalid_printed_date_does_not_leak_iso_truth(printed):
    truth = corpus()
    truth[0]["dates"][0]["printed"] = printed
    assert semantic.score(truth, submission())["fields"]["observed"]["eligible"] == 0


@pytest.mark.parametrize("printed", ["2025年1月2日", "20250102", "2025/1/2 09:00", "2025.01.02"])
def test_unambiguous_full_printed_dates_are_eligible(printed):
    truth = corpus()
    truth[0]["dates"][0]["printed"] = printed
    assert semantic.score(truth, submission())["fields"]["observed"]["eligible"] == 1


@pytest.mark.parametrize("previous_column", [False, True])
def test_previous_reading_cannot_borrow_current_row_visibility(previous_column):
    truth = corpus()
    if previous_column:
        truth[0]["layout"]["columns"].append("previous")
    truth[0]["readings"].append({**truth[0]["readings"][0], "role": "previous",
                                 "observed": "2024-05-06", "canonical_value": 6.8, "value_text": "6.8"})
    # The previous result exists only in semantic truth; PrintedRow describes
    # the current reading. A header alone cannot establish a visible value.
    report = semantic.score(truth, submission())
    assert report["counts"]["truth"] == 1
    assert all(field["eligible"] == 1 for field in report["fields"].values())
    assert report["all_applicable_fields"]["f1"] == 1
    assert report["unscored_readings"] == {"previous_observation_visibility_unrepresented": 1}


def test_previous_only_has_no_recall_denominator_or_perfect_score():
    truth = corpus()
    truth[0]["readings"][0]["role"] = "previous"
    report = semantic.score(truth, [])
    assert report["counts"]["truth"] == 0
    assert all(field["eligible"] == 0 for field in report["fields"].values())
    assert report["all_applicable_fields"]["f1"] is None


@pytest.mark.parametrize("printed", ["<5.6", ">5.6", "≤5.6", "≥5.6", "<=5.6", ">=5.6", "＜5.6", "5.6/>2"])
def test_censored_printed_values_do_not_expose_exact_canonical_number(printed):
    truth, pred = corpus(), submission()
    truth[0]["printed_rows"][0]["item_value"] = printed
    pred[0]["readings"][0]["canonical_value"] = None
    report = semantic.score(truth, pred)
    assert report["counts"]["truth"] == 1
    assert report["fields"]["canonical_value"]["eligible"] == 0
    assert report["fields"]["unit_ucum"]["eligible"] == 1
    assert report["all_applicable_fields"]["f1"] == 1
    assert report["unscored_fields"]["canonical_value:censored_printed_value_not_exact"] == 1


def test_unreadable_row_requires_no_prediction():
    truth = corpus()
    truth[0]["printed_rows"][0]["readable"] = False
    report = semantic.score(truth, [])
    assert report["counts"]["truth"] == 0
    assert report["all_applicable_fields"]["f1"] is None
    assert report["unscored_readings"] == {"unreadable_row": 1}
    assert semantic.score(truth, submission())["all_applicable_fields"]["precision"] == 0


def test_qualitative_not_scored_as_numeric_and_no_match_loinc_not_forced():
    truth = corpus()
    truth[0]["readings"][0].update(expect_resolvable=False, loinc=None)
    report = semantic.score(truth, submission())
    assert report["fields"]["loinc"]["eligible"] == 0
    truth[0]["readings"][0]["value_kind"] = "qualitative"
    report = semantic.score(truth, [])
    assert report["unscored_readings"] == {"non_numeric_reading": 1}


def test_null_is_abstention_not_correct_for_an_evaluable_field():
    pred = submission()
    pred[0]["readings"][0]["loinc"] = None
    assert semantic.score(corpus(), pred)["fields"]["loinc"]["recall"] == 0


def test_empty_submission_does_not_get_a_perfect_score():
    report = semantic.score([], [])
    assert report["matched"]["f1"] is None
    assert report["all_applicable_fields"]["f1"] is None


def test_cli_invalid_exit_replaces_any_stale_success_report(tmp_path):
    truth_path, pred_path, report_path = [tmp_path / name for name in ("truth.jsonl", "pred.jsonl", "report.json")]
    truth_path.write_text(json.dumps(corpus()[0]) + "\n", encoding="utf-8")
    pred_path.write_text(json.dumps(submission()[0]) + "\n", encoding="utf-8")
    command = [sys.executable, "-m", "mirobody_gen.harness.semantic_score", str(truth_path), str(pred_path),
               "--json", str(report_path)]
    valid = subprocess.run(command, capture_output=True, text=True)
    assert valid.returncode == 0, valid.stderr
    assert json.loads(report_path.read_text())["all_applicable_fields"]["f1"] == 1
    pred_path.write_text(json.dumps(submission()[0]) + "\n" + json.dumps(submission()[0]) + "\n", encoding="utf-8")
    invalid = subprocess.run(command, capture_output=True, text=True)
    assert invalid.returncode == 2
    assert json.loads(report_path.read_text())["status"] == "invalid_submission"


def test_jsonl_duplicate_keys_rejected(tmp_path):
    path = tmp_path / "duplicate.jsonl"
    path.write_text('{"file":"first.pdf","file":"second.pdf","readings":[]}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        semantic._read_jsonl(path)
