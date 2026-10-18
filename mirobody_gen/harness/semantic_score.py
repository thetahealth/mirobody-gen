"""Offline semantic scoring of observable, printed numeric readings.

    python -m mirobody_gen.harness.semantic_score files.jsonl predictions.jsonl --json report.json

This is separate from the existing five-field extraction score. See
docs/SEMANTIC_SCORING.md for the prediction contract and conservative visibility
rules. It measures agreement with the corpus's authored labels, not clinical
validity, patient linkage, or correctness of the catalogue's LOINC/UCUM choices.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import re
from datetime import date

from .score import hungarian, name_similarity, relaxed

VERSION = "observable-semantics-v1"
FIELDS = ("loinc", "canonical_value", "unit_ucum", "observed", "role", "source_file")
PRED_KEYS = frozenset(("id", "item_name", *FIELDS))
ROLES = {"current", "previous", "export"}


def _string(value, label: str, *, nullable: bool = False, empty: bool = False) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValueError(f"{label}: expected {'string or null' if nullable else 'nonempty string'}")


def _iso_date(value, label: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{label}: expected YYYY-MM-DD or null")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label}: invalid calendar date") from exc


def _finite_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _visible_date(text) -> str | None:
    """Only dates whose full year/month/day can be recovered without locale guesses."""
    if not isinstance(text, str):
        return None
    parts = None
    for pattern in (r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:\s|T|$)",
                    r"^(\d{4})年(\d{1,2})月(\d{1,2})日(?:\s|$)",
                    r"^(\d{4})(\d{2})(\d{2})(?:\s|$)"):
        if match := re.match(pattern, text.strip()):
            parts = tuple(map(int, match.groups()))
            break
    if parts is None and (match := re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})(?:\s|$)", text.strip())):
        a, b, year = map(int, match.groups())
        # 01/02/2025 is ambiguous without a declared locale/date-format contract.
        if a > 12 >= b:
            parts = (year, b, a)
        elif b > 12 >= a:
            parts = (year, a, b)
    if parts is not None:
        try:
            return date(*parts).isoformat()
        except ValueError:
            pass
    return None


def validate_predictions(documents: list[dict]) -> dict[str, list[dict]]:
    """Reject ambiguous/invalid submissions before scoring; never silently overwrite."""
    out = {}
    for index, obj in enumerate(documents):
        label = f"prediction document {index}"
        if not isinstance(obj, dict) or set(obj) != {"file", "readings"}:
            raise ValueError(f"{label}: expected exactly file and readings")
        _string(obj["file"], f"{label}.file")
        filename = obj["file"]
        if filename in out:
            raise ValueError(f"duplicate prediction file: {filename}")
        if not isinstance(obj["readings"], list):
            raise ValueError(f"{label}.readings: expected list")
        ids = set()
        for i, pred in enumerate(obj["readings"]):
            where = f"{filename}.readings[{i}]"
            if not isinstance(pred, dict) or set(pred) != PRED_KEYS:
                raise ValueError(f"{where}: expected exactly {', '.join(sorted(PRED_KEYS))}")
            for field in ("id", "item_name"):
                _string(pred[field], f"{where}.{field}")
            if pred["id"] in ids:
                raise ValueError(f"{where}: duplicate prediction id {pred['id']!r}")
            ids.add(pred["id"])
            for field in ("loinc", "role", "source_file"):
                _string(pred[field], f"{where}.{field}", nullable=True)
            _string(pred["unit_ucum"], f"{where}.unit_ucum", nullable=True, empty=True)
            if pred["role"] is not None and pred["role"] not in ROLES:
                raise ValueError(f"{where}.role: expected current, previous, export, or null")
            value = pred["canonical_value"]
            if value is not None and not _finite_number(value):
                raise ValueError(f"{where}.canonical_value: expected finite JSON number or null")
            _iso_date(pred["observed"], f"{where}.observed", nullable=True)
        out[filename] = obj["readings"]
    return out


def _truth(record: dict) -> tuple[list[dict], collections.Counter, collections.Counter]:
    """Apply visibility masks; do not use hidden fields even to improve alignment."""
    filename = record["file"]
    rows = record.get("printed_rows", [])
    readings = record.get("readings", [])
    if not isinstance(rows, list) or not isinstance(readings, list):
        raise ValueError(f"{filename}: printed_rows/readings must be lists")
    collected = [d for d in record.get("dates", []) if d.get("role") == "collected"]
    parsed_dates = [_visible_date(d.get("printed")) for d in collected]
    visible_collected = set(parsed_dates) if all(
        parsed and isinstance(d.get("iso"), str) and parsed == d["iso"][:10]
        for d, parsed in zip(collected, parsed_dates)
    ) else set()
    refs, skipped, masked = [], collections.Counter(), collections.Counter()
    for i, reading in enumerate(readings):
        if not isinstance(reading, dict):
            raise ValueError(f"{filename}.readings[{i}]: expected object")
        # This version intentionally does not claim to evaluate qualitative or
        # narrative results. Their normalization needs a different contract.
        if reading.get("value_kind") != "quantitative":
            skipped["non_numeric_reading"] += 1
            continue
        # A previous result points to the CURRENT PrintedRow. Neither that
        # pointer nor a previous-column header proves a second value is visible.
        # Exclude the observation itself, not just its value/date fields.
        if reading.get("role") == "previous":
            skipped["previous_observation_visibility_unrepresented"] += 1
            continue
        row_index = reading.get("printed_row")
        if row_index is None:
            skipped["no_printed_row"] += 1
            continue
        if type(row_index) is not int or not 0 <= row_index < len(rows):
            raise ValueError(f"{filename}.readings[{i}]: invalid printed_row")
        row = rows[row_index]
        unreadable = set(row.get("unreadable_fields", []))
        if not row.get("readable", False) or {"item_name", "item_value"} & unreadable:
            skipped["unreadable_row"] += 1
            continue
        if not isinstance(row.get("item_name"), str) or not row["item_name"].strip():
            skipped["missing_visible_name"] += 1
            continue
        if not row.get("item_value"):
            skipped["missing_visible_value"] += 1
            continue
        if reading.get("role") not in ROLES:
            raise ValueError(f"{filename}.readings[{i}]: invalid role")
        unit_visible = bool(row.get("item_unit")) and "item_unit" not in unreadable
        # Empty canonical unit is dimensionless, not evidence of an omitted unit.
        unit_known = unit_visible or reading.get("unit_ucum") == ""
        # A threshold such as <5 is not an exact result of 5. The contract has
        # no comparator field yet; retain the observation but mask exact value.
        censored = bool(re.search(r"[<>≤≥≦≧＜＞]", str(row["item_value"])))
        date_known = len(visible_collected) == 1 and reading.get("observed") in visible_collected
        mask = {
            "loinc": bool(reading.get("expect_resolvable") and reading.get("loinc")),
            "canonical_value": unit_known and not censored and _finite_number(reading.get("canonical_value")),
            "unit_ucum": unit_known,
            "observed": date_known,
            "role": True,
            "source_file": True,
        }
        if mask["observed"]:
            _iso_date(reading["observed"], f"{filename}.readings[{i}].observed")
        reasons = {
            "loinc": "not_declared_resolvable",
            "canonical_value": ("censored_printed_value_not_exact" if censored
                                else "unit_not_visible_or_numeric_truth_missing"),
            "unit_ucum": "unit_not_visible",
            "observed": "no_unique_visible_collection_date_for_reading",
        }
        for field, applies in mask.items():
            if not applies:
                masked[f"{field}:{reasons[field]}"] += 1
        refs.append({"truth_index": i, "item_name": row["item_name"],
                     **{field: reading.get(field) for field in FIELDS},
                     "source_file": filename, "mask": mask})
    return refs, skipped, masked


def align(refs: list[dict], preds: list[dict]) -> list[tuple[int, int]]:
    """One shared assignment; numeric value/unit/source_file NEVER select matches.

    Name compatibility is mandatory. Cardinality is the first priority; visible
    name, code, observable date, then role break ties. IDs are submission-local
    labels, and are never truth indices. Ties finally preserve input order.
    """
    n = max(len(refs), len(preds))
    if not n:
        return []
    weights = [[0.0] * n for _ in range(n)]
    for i, ref in enumerate(refs):
        for j, pred in enumerate(preds):
            similarity = name_similarity(pred["item_name"], ref["item_name"])
            if similarity < 0.8:
                continue
            # A whole extra match outweighs all possible tie-break gains.
            tie = 10 * similarity
            for field, weight in (("loinc", 3), ("observed", 2), ("role", 1)):
                tie += weight * (ref["mask"][field] and ref[field] == pred[field])
            weights[i][j] = 20 * (n + 1) + tie
    assignment = hungarian([[-weight for weight in row] for row in weights])
    return [(i, j) for i, j in enumerate(assignment)
            if i < len(refs) and j < len(preds) and weights[i][j] > 0]


def _equal(field: str, pred, ref) -> bool:
    if field == "canonical_value":
        # Numerical representation tolerance only; no clinical tolerance and no
        # silent unit conversion. Canonical value AND canonical unit must pass.
        return _finite_number(pred) and math.isclose(pred, ref, rel_tol=1e-9, abs_tol=1e-9)
    return pred == ref


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _summary(counts: collections.Counter, fields: dict[str, collections.Counter]) -> dict:
    def metric(correct: int) -> dict:
        total = counts["truth"] + counts["predicted"]
        return {"correct": correct, "recall": _ratio(correct, counts["truth"]),
                "precision": _ratio(correct, counts["predicted"]),
                "f1": 2 * correct / total if total else None}
    return {"counts": dict(counts), "matched": metric(counts["matched"]),
            "all_applicable_fields": metric(counts["all_applicable_fields"]),
            "fields": {field: {**dict(c), "recall": _ratio(c["correct"], c["eligible"]),
                               "accuracy_on_matched": _ratio(c["correct"], c["matched_eligible"])}
                       for field, c in fields.items()}}


def _ambiguous_groups(refs: list[dict]) -> list[list[int]]:
    groups = collections.defaultdict(list)
    for ref in refs:
        identity = (relaxed(ref["item_name"]), *(ref[field] if ref["mask"][field] else None
                                               for field in ("loinc", "observed", "role")))
        groups[identity].append(ref["truth_index"])
    return [indices for indices in groups.values() if len(indices) > 1]


def score(records: list[dict], prediction_documents: list[dict]) -> dict:
    predictions = validate_predictions(prediction_documents)
    files = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("truth documents must be objects")
        _string(record.get("file"), "truth file")
        if record["file"] in files:
            raise ValueError(f"duplicate truth file: {record['file']}")
        files.add(record["file"])
    if unknown := set(predictions) - files:
        raise ValueError(f"unknown prediction files: {', '.join(sorted(unknown))}")
    total = collections.Counter(truth=0, predicted=0, matched=0, all_applicable_fields=0)
    field_total = {field: collections.Counter(eligible=0, matched_eligible=0, correct=0) for field in FIELDS}
    skipped, masked, per_document = collections.Counter(), collections.Counter(), {}
    for record in records:
        filename = record["file"]
        refs, skipped_here, masked_here = _truth(record)
        preds = predictions.get(filename, [])
        pairs = align(refs, preds)
        counts = collections.Counter(truth=len(refs), predicted=len(preds), matched=len(pairs), all_applicable_fields=0)
        fields = {field: collections.Counter(eligible=0, matched_eligible=0, correct=0) for field in FIELDS}
        for ref in refs:
            for field in FIELDS:
                fields[field]["eligible"] += ref["mask"][field]
        details = []
        for i, j in pairs:
            ref, pred = refs[i], preds[j]
            correct = {}
            for field in FIELDS:
                if ref["mask"][field]:
                    correct[field] = _equal(field, pred[field], ref[field])
                    fields[field]["matched_eligible"] += 1
                    fields[field]["correct"] += correct[field]
            counts["all_applicable_fields"] += all(correct.values())
            details.append({"truth_index": ref["truth_index"], "prediction_id": pred["id"], "correct": correct})
        matched_ref, matched_pred = {i for i, _ in pairs}, {j for _, j in pairs}
        doc = _summary(counts, fields)
        doc.update({"matches": details, "missing_truth_indices": [r["truth_index"] for i, r in enumerate(refs)
                                                                  if i not in matched_ref],
                    "extra_prediction_ids": [p["id"] for j, p in enumerate(preds) if j not in matched_pred],
                    "ambiguous_truth_groups": _ambiguous_groups(refs),
                    "unscored_readings": dict(skipped_here), "unscored_fields": dict(masked_here)})
        per_document[filename] = doc
        total.update(counts)
        skipped.update(skipped_here)
        masked.update(masked_here)
        for field in FIELDS:
            field_total[field].update(fields[field])
    return {"schema_version": VERSION, "status": "valid_submission", "documents": len(records),
            **_summary(total, field_total), "unscored_readings": dict(skipped), "unscored_fields": dict(masked),
            "ambiguous_truth_groups": sum(len(doc["ambiguous_truth_groups"]) for doc in per_document.values()),
            "per_document": per_document}


def _read_jsonl(path: pathlib.Path) -> list[dict]:
    def unique_object(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError(f"duplicate JSON key: {key}")
            obj[key] = value
        return obj

    def invalid_constant(value):
        raise ValueError(f"not a JSON number: {value}")

    rows = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line, object_pairs_hook=unique_object, parse_constant=invalid_constant))
            except ValueError as exc:
                raise ValueError(f"{path}:{i}: invalid JSON ({exc})") from exc
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    parser.add_argument("predictions")
    parser.add_argument("--json", type=pathlib.Path)
    args = parser.parse_args()
    try:
        report = score(_read_jsonl(pathlib.Path(args.manifest)), _read_jsonl(pathlib.Path(args.predictions)))
    except (OSError, ValueError) as exc:
        if args.json:
            args.json.write_text(json.dumps({"schema_version": VERSION, "status": "invalid_submission",
                                             "error": str(exc)}, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
        parser.exit(2, f"invalid semantic-scoring input: {exc}\n")
    text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.json:
        args.json.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
