"""Vocabulary coverage reads catalogue names from a visit manifest and printed names from a file manifest."""
from __future__ import annotations

import json
from types import SimpleNamespace

from mirobody_gen.harness import resolve_coverage as rc

#: A stand-in resolver: knows the canonical names and one glued spelling, codes PCT as plateletcrit
CODES = {"肌酐": "14682-9", "白细胞WBC": "6690-2", "PCT": "51637-7", "腰围": "8280-0"}


def fake_resolve(name):
    return SimpleNamespace(resolved=name in CODES, loinc=CODES.get(name))


def _write(path, records):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    return path


def test_a_manifest_gives_catalogue_names_with_their_expectation(tmp_path):
    rows = rc.load_rows(_write(tmp_path / "manifest.jsonl", [{"rows": [
        {"original_indicator": "肌酐", "loinc": "14682-9", "expect_resolvable": True},
        {"original_indicator": "尿素", "loinc": "22664-7", "expect_resolvable": True},
        {"original_indicator": "腰围", "loinc": "8280-0", "expect_resolvable": False}]}]))
    lines, regressions = rc.report(rows, fake_resolve)
    assert regressions == 1
    assert "  ✗ 尿素 (occurs 1 times)" in lines and "  ! 腰围 → 8280-0" in lines
    assert not any(line.startswith("| name class") for line in lines)


def test_printed_rows_count_per_name_and_code_and_break_down_by_name_class(tmp_path):
    record = {"readings": [{"loinc": "6690-2"}, {"loinc": "33959-8"}, {"loinc": "51637-7"}, {"loinc": "14682-9"}],
              "printed_rows": [
                  {"item_name": "白细胞WBC", "readings": [0], "hazards": ["name.glued_to_abbr", "flag.text_dialect"]},
                  {"item_name": "PCT", "readings": [1], "hazards": []},       # procalcitonin, read as plateletcrit
                  {"item_name": "PCT", "readings": [2], "hazards": []},
                  {"item_name": "肌酐测定", "readings": [3], "hazards": ["name.assay_suffix"]},
                  {"item_name": "肌酐测定", "readings": [3], "hazards": ["name.assay_suffix"]},
                  {"item_name": "备注", "readings": [], "hazards": []},
                  {"item_name": "血压", "readings": [0, 1], "hazards": []}]}           # a pair: no single code
    rows = rc.load_rows(_write(tmp_path / "files.jsonl", [record]))
    assert rows.pop() == {"name": "血压", "loinc": None, "expect": None, "classes": ["plain"]}
    assert len(rows) == 5 and all(r["expect"] is None for r in rows)
    lines, regressions = rc.report(rows, fake_resolve)
    assert regressions == 0
    assert lines[0] == "4 printed names (5 rows) · resolved 3 = 75.0%"
    assert "  ≠ PCT → 51637-7, truth 33959-8" in lines
    assert "| name.glued_to_abbr | 1 | 100% | 100% |" in lines
    assert "| name.assay_suffix | 1 | 0% | 0% |" in lines
    assert "| plain | 2 | 100% | 50% |" in lines


def test_the_loinc_table_splits_another_code_for_the_analyte_from_another_analyte(tmp_path):
    record = {"readings": [{"loinc": "14682-9"}, {"loinc": "33959-8"}],
              "printed_rows": [{"item_name": "肌酐", "readings": [0], "hazards": []},
                               {"item_name": "PCT", "readings": [1], "hazards": []}]}
    rows = rc.load_rows(_write(tmp_path / "files.jsonl", [record]))
    codes = {"肌酐": "2160-0", "PCT": "51637-7"}                     # mass for molar creatinine; plateletcrit

    def resolve(name):
        return SimpleNamespace(resolved=True, loinc=codes[name])

    analyte = {"2160-0": "creatinine", "14682-9": "creatinine", "51637-7": "plateletcrit", "33959-8": "procalcitonin"}
    lines, _ = rc.report(rows, resolve, analyte)
    assert "miscoded (resolved to another code) 2: 1 for the same analyte, 1 for another" in lines
    assert "  ≠ PCT → 51637-7, truth 33959-8 (plateletcrit for procalcitonin)" in lines
