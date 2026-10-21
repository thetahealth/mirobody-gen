"""Offline source-only ocular reports; no cohort, completion or clinical inference."""
from __future__ import annotations

import argparse
from collections import Counter
import copy
from dataclasses import replace
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import unicodedata
import re

POLICY = "ocular-source-v1"
SCHEMA = "ocular-observations-v1"
ROOT_FIELDS = {"schema_version", "synthetic", "case_id", "person_id", "language", "document_date",
               "source_system", "observations"}
OBS_REQUIRED = {"id", "source_id", "item_id", "laterality", "status"}
OBS_FIELDS = OBS_REQUIRED | {"value", "unit", "notation", "effective_time"}
LABELS = {
    "en": {"title": "Eye observation record", "person": "Subject", "date": "Document date",
           "system": "Source system", "side": "Side", "status": "Status", "value": "Value",
           "unit": "Unit", "notation": "Notation", "time": "Observation time", "source": "Source ID",
           "missing": "Not provided", "left": "Left eye", "right": "Right eye", "unspecified": "Side unspecified",
           "measured": "Measured", "not_measured": "Explicitly not measured", "unknown": "Status unknown",
           "va": "Visual acuity (unaided)", "va_corrected": "Corrected visual acuity", "iop": "Intraocular pressure"},
    "zh": {"title": "眼科观测记录", "person": "对象标识", "date": "文书日期", "system": "来源系统",
           "side": "侧别", "status": "状态", "value": "结果", "unit": "单位", "notation": "记法",
           "time": "观测时间", "source": "来源ID", "missing": "未提供", "left": "左眼", "right": "右眼",
           "unspecified": "侧别未注明", "measured": "已测量", "not_measured": "明确未测", "unknown": "状态未知",
           "va": "视力（裸眼）", "va_corrected": "矫正视力", "iop": "眼压"},
}


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _visible(text: str) -> str:
    # Only typographic Latin ligatures: do not apply NFKC or normalize units/numbers/time zones.
    ligatures = str.maketrans({"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"})
    return "".join(text.translate(ligatures).split())


def _string(value, field: str) -> None:
    # Control characters (C0 and C1, e.g. U+0085) and Unicode line/paragraph separators would split a
    # JSONL record or the rendered text, so they are rejected rather than altered.
    if (not isinstance(value, str) or not value.strip() or "OBS[" in value
            or any(unicodedata.category(c) in ("Cc", "Zl", "Zp") for c in value)):
        raise ValueError(f"Invalid nonblank source text: {field}")


def _same(a, b) -> bool:
    """Equality with JSON types: Python alone treats True == 1 == 1.0."""
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)


def jsonl_lines(path: Path) -> list[str]:
    # JSONL records are separated by LF only, not every separator str.splitlines() recognizes.
    return [line for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]


def _time(value, field: str) -> None:
    _string(value, field)
    try:
        if len(value) == 10:
            if date.fromisoformat(value).isoformat() != value: raise ValueError()
        else:
            parsed = datetime.fromisoformat(value)
            if "T" not in value or parsed.tzinfo is None or parsed.utcoffset() is None: raise ValueError()
    except ValueError:
        raise ValueError(f"Invalid source ISO date or offset timestamp: {field}") from None


def validate_packet(packet: dict) -> dict:
    if not isinstance(packet, dict) or set(packet) != ROOT_FIELDS:
        raise ValueError("Source packet has missing or unknown fields")
    if packet["schema_version"] != SCHEMA or packet["synthetic"] is not True:
        raise ValueError("Source packet requires supported schema and explicit synthetic=true")
    if not isinstance(packet["case_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", packet["case_id"]):
        raise ValueError("Unsafe or invalid case_id")
    for field in ("person_id", "source_system"): _string(packet[field], field)
    if not isinstance(packet["language"], str) or packet["language"] not in LABELS:
        raise ValueError("Unsupported source report language")
    _time(packet["document_date"], "document_date")
    observations = packet["observations"]
    if not isinstance(observations, list) or not observations: raise ValueError("Source observations must be nonempty")
    ids, keys = set(), set()
    for obs in observations:
        if not isinstance(obs, dict) or not OBS_REQUIRED <= set(obs) or set(obs)-OBS_FIELDS:
            raise ValueError("Observation has missing or unknown fields")
        for field in ("id", "source_id"): _string(obs[field], field)
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", obs["id"]) or obs["id"] in ids:
            raise ValueError("Invalid or duplicate observation id")
        ids.add(obs["id"])
        if not isinstance(obs["item_id"], str) or obs["item_id"] not in {"va", "va_corrected", "iop"}:
            raise ValueError("Unsupported ocular item")
        if not isinstance(obs["laterality"], str) or obs["laterality"] not in {"left", "right", "unspecified"}:
            raise ValueError("Invalid source laterality")
        if not isinstance(obs["status"], str) or obs["status"] not in {"measured", "not_measured", "unknown"}:
            raise ValueError("Invalid source status")
        if obs["status"] == "measured":
            _string(obs.get("value"), "value")
            if not re.fullmatch(r"(?:[<>≤≥]=?\s*)?[+-]?\d+(?:[.,]\d+)?(?:\s*/\s*\d+(?:[.,]\d+)?)?", obs["value"]):
                raise ValueError("Unsupported measured value format; preserve source through a supported adapter")
        elif obs.get("value") is not None:
            raise ValueError("Unknown/not_measured observation cannot carry a measurement value")
        for field in ("unit", "notation"):
            if obs.get(field) is not None: _string(obs[field], field)
        if obs.get("effective_time") is not None: _time(obs["effective_time"], "effective_time")
        key = (obs["item_id"], obs["laterality"], obs.get("effective_time"))
        if key in keys: raise ValueError("Ambiguous duplicate item/side/time; no silent revision selection")
        keys.add(key)
    return copy.deepcopy(packet)


def _build_doc(packet: dict):
    from . import layout
    from .book import Block
    from .document import Doc

    lang, labels = packet["language"], LABELS[packet["language"]]
    registry = layout.Registry(0)
    family = replace(registry.family(registry.pools[(lang, "checkup_center")][0]),
                     family_id=f"ocular-source:{lang}", institution="", kind="app_export", fmt="pdf",
                     language="en" if lang == "en" else "zh-Hans", subject_in_table=False,
                     furniture=(), signatures=(), watermark=False, page="a4")
    doc = Doc(packet["case_id"], packet["person_id"], family, labels["title"], [], [], [], kind="ocular_source_record")
    doc.blocks.append(Block(kind="narrative", rows=[(labels["person"], packet["person_id"]),
        (labels["date"], packet["document_date"]), (labels["system"], packet["source_system"])]))
    for obs in packet["observations"]:
        doc.blocks.append(Block(kind="heading", title=f"OBS[{obs['id']}] {labels[obs['item_id']]}"))
        rows = [(labels["side"], labels[obs["laterality"]]), (labels["status"], labels[obs["status"]])]
        if obs["status"] == "measured": rows.append((labels["value"], obs["value"]))
        rows.append((labels["unit"], obs.get("unit") or labels["missing"]))
        if obs["item_id"] != "iop" or obs.get("notation") is not None:
            rows.append((labels["notation"], obs.get("notation") or labels["missing"]))
        rows += [(labels["time"], obs.get("effective_time") or labels["missing"]),
                 (labels["source"], obs["source_id"])]
        doc.blocks.append(Block(kind="narrative", rows=rows))
    return doc


def audit_record(packet: dict, record: dict, pdf_bytes: bytes) -> dict:
    """Check exact source fields and expected PDF text-layer content; no visual/clinical scoring."""
    import pymupdf
    from .render.pdf import BANNER

    source = validate_packet(packet)
    checks = {"source_fields": all(_same(record.get(k), v) for k,v in source.items()),
              "no_added_record_fields": not set(record)-ROOT_FIELDS-{
                  "policy_version", "completion_performed", "source_sha256", "pdf_sha256", "file"},
              "source_hash": record.get("source_sha256") == _hash(source),
              "policy": record.get("policy_version") == POLICY and record.get("completion_performed") is False,
              "pdf_hash": record.get("pdf_sha256") == hashlib.sha256(pdf_bytes).hexdigest()}
    labels = LABELS[source["language"]]
    colon = ":" if source["language"] == "en" else "："
    expected = [labels["title"]]
    for label, value in [(labels["person"], source["person_id"]), (labels["date"], source["document_date"]),
                         (labels["system"], source["source_system"])]: expected.append(label+colon+value)
    for obs in source["observations"]:
        expected.append(f"OBS[{obs['id']}] {labels[obs['item_id']]}")
        expected += [labels["side"]+colon+labels[obs["laterality"]], labels["status"]+colon+labels[obs["status"]]]
        if obs["status"] == "measured": expected.append(labels["value"]+colon+obs["value"])
        expected.append(labels["unit"]+colon+(obs.get("unit") or labels["missing"]))
        if obs["item_id"] != "iop" or obs.get("notation") is not None:
            expected.append(labels["notation"]+colon+(obs.get("notation") or labels["missing"]))
        expected += [labels["time"]+colon+(obs.get("effective_time") or labels["missing"]),
                     labels["source"]+colon+obs["source_id"]]
    try:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as document:
            actual = "".join(page.get_text() for page in document).replace(BANNER, "")
        checks["physical_text_exact"] = _visible(actual) == _visible("".join(expected))
    except (ValueError, RuntimeError):
        checks["physical_text_exact"] = False
    return {"ok": all(checks.values()), "checks": checks, "clinical_accuracy_measured": False}


def render_packet(packet: dict) -> tuple[dict, bytes]:
    from .render import pdf

    source = validate_packet(packet)
    payload, _ = pdf.render(_build_doc(source), source["document_date"])
    record = {**source, "policy_version": POLICY, "completion_performed": False,
              "source_sha256": _hash(source), "pdf_sha256": hashlib.sha256(payload).hexdigest()}
    if not audit_record(source, record, payload)["ok"]:
        raise ValueError("Source report failed deterministic physical-content audit")
    return record, payload


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def load_packets(input_path: str | Path) -> list[dict]:
    packets = [validate_packet(json.loads(line, object_pairs_hook=unique_object))
               for line in jsonl_lines(Path(input_path))]
    # case_id names the output PDF: ids differing only in case collide on case-insensitive filesystems
    if not packets or len({p["case_id"].casefold() for p in packets}) != len(packets):
        raise ValueError("Empty batch or duplicate case_id (compared case-insensitively)")
    return packets


def audit_existing(input_path: str | Path, out_dir: str | Path) -> dict:
    packets = load_packets(input_path)
    root = Path(out_dir)
    records = [json.loads(line, object_pairs_hook=unique_object) for line in jsonl_lines(root/"ocular.jsonl")]
    if any(not isinstance(r, dict) or not isinstance(r.get("case_id"), str) for r in records):
        raise ValueError("Malformed ocular record metadata")
    by_id = {r["case_id"]:r for r in records}
    failures = []
    if len(by_id) != len(records) or set(by_id) != {p["case_id"] for p in packets}:
        failures.append({"case_id": None, "checks": ["missing_extra_or_duplicate_case"]})
    for packet in packets:
        record = by_id.get(packet["case_id"])
        name = packet["case_id"]+".pdf"
        if record is None or record.get("file") != name or not (root/name).is_file():
            failures.append({"case_id": packet["case_id"], "checks": ["missing_or_wrong_file"]})
            continue
        audit = audit_record(packet, record, (root/name).read_bytes())
        if not audit["ok"]:
            failures.append({"case_id": packet["case_id"], "checks": [k for k,v in audit["checks"].items() if not v]})
    return {"policy_version": POLICY, "cases": len(packets), "audit_ok": not failures,
            "failures": failures, "clinical_accuracy_measured": False}


def replay(input_path: str | Path, out_dir: str | Path) -> dict:
    packets = load_packets(input_path)
    rendered = [render_packet(p) for p in packets]  # Prepare and audit the whole batch before any writes.
    counts = Counter(o["status"] for p in packets for o in p["observations"])
    summary = {"policy_version": POLICY, "cases": len(packets), "observations": sum(counts.values()),
               "statuses": dict(counts), "audit_ok": True, "clinical_accuracy_measured": False,
               "completion_performed": False}
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    records = []
    for record, payload in rendered:
        name = record["case_id"]+".pdf"
        (root/name).write_bytes(payload)
        records.append({**record, "file": name})
    (root/"ocular.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False)+"\n" for r in records), encoding="utf-8")
    (root/"audit.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay synthetic source eye observations without completion or clinical inference")
    parser.add_argument("input", help="Known-synthetic source packets (JSONL)")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--out", help="Generate and audit source-only PDFs and ocular.jsonl")
    target.add_argument("--audit-existing", help="Audit saved PDFs/ocular.jsonl against source; no generation or writes")
    args = parser.parse_args()
    try:
        result = audit_existing(args.input, args.audit_existing) if args.audit_existing else replay(args.input, args.out)
    except (OSError, ValueError, TypeError, RuntimeError) as error:  # RuntimeError: a layout failure
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False))
    if not result["audit_ok"]: raise SystemExit(1)


if __name__ == "__main__": main()
