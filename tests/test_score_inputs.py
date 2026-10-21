"""The printed five-field scorer rejects prediction files it cannot attribute.

Before: `score([], ghost) == score([], {})` held (an unknown file was silently ignored), and a repeated
`image` line silently replaced the earlier one.
"""

import json

import pytest

from mirobody_gen.harness import score


def _item(name, value):
    return {"item_name": name, "item_value": value, "item_unit": "", "item_range": "", "is_abnormal": ""}


def _rec(file, *rows):
    return {"file": file, "kind": "lab_slip", "hazards": [], "printed_rows": [
        {**_item(n, v), "readable": True, "unreadable_fields": []} for n, v in rows]}


def _write(path, lines):
    path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")
    return path


def test_unknown_prediction_file_is_rejected():
    with pytest.raises(ValueError, match="unknown prediction files: ghost.pdf"):
        score.score([_rec("a.pdf", ("WBC", "6.2"))], {"ghost.pdf": [_item("WBC", "6.2")]})
    with pytest.raises(ValueError):
        score.score([], {"ghost.pdf": []})


def test_duplicate_prediction_file_is_rejected(tmp_path):
    path = _write(tmp_path / "p.jsonl", [{"image": "a.pdf", "items": [_item("WBC", "6.2")]},
                                         {"image": "a.pdf", "items": []}])
    with pytest.raises(ValueError, match="p.jsonl:2: duplicate prediction file: a.pdf"):
        score.load_predictions(path)


def test_duplicate_truth_file_is_rejected():
    with pytest.raises(ValueError, match="duplicate truth file"):
        score.score([_rec("a.pdf", ("WBC", "6.2")), _rec("a.pdf", ("HGB", "130"))], {})


@pytest.mark.parametrize("line", [{"items": []}, {"image": "", "items": []}, {"image": "a.pdf", "items": {"x": 1}},
                                  {"image": "a.pdf", "items": ["WBC"]}])
def test_malformed_prediction_line_is_rejected(tmp_path, line):
    with pytest.raises(ValueError, match="p.jsonl:1"):
        score.load_predictions(_write(tmp_path / "p.jsonl", [line]))


def test_missing_and_extra_readings_move_recall_and_precision():
    rec = _rec("a.pdf", ("WBC", "6.2"), ("HGB", "130"))
    full = score.score([rec], {"a.pdf": [_item("WBC", "6.2"), _item("HGB", "130")]})["aligned"]
    missing = score.score([rec], {"a.pdf": [_item("WBC", "6.2")]})["aligned"]
    extra = score.score([rec], {"a.pdf": [_item("WBC", "6.2"), _item("HGB", "130"), _item("PLT", "250")]})["aligned"]
    absent = score.score([rec], {})
    assert (full["row_recall"], full["row_precision"]) == (1.0, 1.0)
    assert (missing["row_recall"], missing["row_precision"]) == (0.5, 1.0)
    assert (extra["row_recall"], extra["row_precision"]) == (1.0, pytest.approx(2 / 3))
    assert absent["aligned"]["row_recall"] == 0.0 and absent["documents_without_prediction"] == 1


def test_kinds_filter_keeps_manifest_files_known(tmp_path):
    lab, book = _rec("a.pdf", ("WBC", "6.2")), {**_rec("b.pdf", ("HGB", "130")), "kind": "checkup_book"}
    manifest = _write(tmp_path / "files.jsonl", [lab, book])
    preds = _write(tmp_path / "p.jsonl", [{"image": "a.pdf", "items": [_item("WBC", "6.2")]},
                                          {"image": "b.pdf", "items": [_item("HGB", "130")]}])
    report_path = tmp_path / "r.json"
    score.main([str(manifest), str(preds), "--kinds", "lab_slip", "--json", str(report_path)])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["documents"] == 1 and report["unscored_predictions"] == 1


def test_cli_exits_2_on_unknown_file(tmp_path, capsys):
    manifest = _write(tmp_path / "files.jsonl", [_rec("a.pdf", ("WBC", "6.2"))])
    preds = _write(tmp_path / "p.jsonl", [{"image": "files/a.pdf", "items": []}])
    with pytest.raises(SystemExit) as exc:
        score.main([str(manifest), str(preds)])
    assert exc.value.code == 2 and "unknown prediction files: files/a.pdf" in capsys.readouterr().err


def test_medrep_labels_keep_filtered_images_known(tmp_path):
    import csv

    labels = tmp_path / "labels.csv"
    with labels.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["image", "meta", "items"])
        writer.writeheader()
        writer.writerow({"image": "lab.png", "meta": json.dumps({"type": "Laboratory"}),
                         "items": json.dumps([_item("WBC", "6.2")])})
        writer.writerow({"image": "ct.png", "meta": json.dumps({"type": "Imaging"}), "items": "[]"})
        writer.writerow({"image": "empty.png", "meta": json.dumps({"type": "Laboratory"}), "items": "[]"})
    preds = _write(tmp_path / "p.jsonl", [{"image": name, "items": [_item("WBC", "6.2")]}
                                          for name in ("lab.png", "ct.png", "empty.png")])
    report_path = tmp_path / "r.json"
    score.main([str(labels), str(preds), "--medrep-labels", "--json", str(report_path)])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["documents"] == 1 and report["unscored_predictions"] == 2

    # a label CSV is a subset by construction: whole-corpus predictions are reported, not rejected
    ghost = _write(tmp_path / "g.jsonl", [{"image": "lab.png", "items": [_item("WBC", "6.2")]},
                                          {"image": "ghost.png", "items": []}])
    score.main([str(labels), str(ghost), "--medrep-labels", "--json", str(report_path)])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["predictions_outside_labels"] == {"count": 1, "sample": ["ghost.png"]}
    assert "unscored_predictions" not in report
    with pytest.raises(SystemExit) as exc:
        score.main([str(labels), str(_write(tmp_path / "d.jsonl", [{"image": "lab.png", "items": []}] * 2)),
                    "--medrep-labels"])
    assert exc.value.code == 2
