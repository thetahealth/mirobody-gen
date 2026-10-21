"""Self-contained source-field HTML consumer; no clinical semantic projection or LLM."""
from __future__ import annotations

import argparse
import hashlib
from html import escape
import json
from pathlib import Path

from .fhir_patient_context import POLICY as PATIENT_POLICY, audit_body_patient
from .fhir_references import POLICY as REFERENCE_POLICY, audit_references
from .fhir_source import POLICY as SOURCE_POLICY, DECODER, _constant, _elements, _members, _space, _unique, plan

POLICY = "fhir-source-view-v1"
FIELDS = [("code", "项目编码（全部原值）"), ("effectiveDateTime", "观测时间 effectiveDateTime"),
          ("effectivePeriod", "观测时段 effectivePeriod"), ("effectiveTiming", "观测时间 effectiveTiming"),
          ("effectiveInstant", "观测时间 effectiveInstant"), ("issued", "发布时间 issued"),
          ("dataAbsentReason", "结果缺失原因"), ("method", "方法"), ("referenceRange", "参考范围（全部原值）"),
          ("interpretation", "来源解释 / 标记"), ("bodySite", "来源部位 / 引用"), ("component", "组件"),
          ("modifierExtension", "来源 modifier"), ("meta", "来源版本与元数据")]
STYLE = """
:root{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#f3f5f7;color:#1c2736;
font:16px/1.55 system-ui,sans-serif}main{max-width:1100px;margin:32px auto;padding:0 24px 48px}
h1{font-size:28px;margin:0 0 8px}h2{font-size:22px}h3{font-size:17px;margin:0 0 10px}
.lead,.muted{color:#536173}.banner,article{background:white;border:1px solid #d8e0e7;border-radius:12px;padding:22px;margin:20px 0}
.banner{border-left:5px solid #536d9c}.badge{display:inline-block;background:#e8edf5;border-radius:5px;padding:3px 10px;margin:0 8px 6px 0}
.excluded{background:#fbe3df;color:#842e24}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.field{border:1px solid #e1e6ec;border-radius:8px;padding:14px;min-width:0}.wide{grid-column:1/-1}
pre{white-space:pre-wrap;overflow-wrap:anywhere;margin:8px 0 0;font:13px/1.5 ui-monospace,monospace;
background:#f5f7fa;border-radius:6px;padding:12px}summary{cursor:pointer;font-weight:600}
details{margin-top:18px}.missing{color:#687481}code{overflow-wrap:anywhere}
@media(max-width:700px){main{padding:0 14px}.grid{grid-template-columns:1fr}article{padding:16px}}
"""


def _raw_at(text: str, pointer: str) -> str | None:
    begin, end = _space(text, 0), len(text)
    for token in pointer.split("/")[1:]:
        key = token.replace("~1", "/").replace("~0", "~")
        if text[begin] == "{":
            match = next(((a, b) for k, a, b in _members(text, begin) if k == key), None)
        elif text[begin] == "[" and key.isdigit():
            match = next(((a, b) for n, (a, b) in enumerate(_elements(text, begin)) if n == int(key)), None)
        else: return None
        if match is None: return None
        begin, end = match
    return text[begin:end]


def _pre(text: str) -> str:
    return "<pre>" + escape(text) + "</pre>"


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def prepare(payload: bytes, entries: list[int]) -> dict:
    prepared = plan(payload)  # Validate the whole Bundle, including unselected observations.
    if not isinstance(entries, list) or not entries or any(type(n) is not int or n < 0 for n in entries):
        raise ValueError("Explicit nonempty integer Observation entry selection required")
    if len(entries) != len(set(entries)): raise ValueError("Duplicate Observation entry selection")
    by_index = {r["entry_index"]: r for r in prepared["records"]}
    if any(n not in by_index for n in entries): raise ValueError("Selection contains a missing or non-Observation entry")
    text = payload.decode("utf-8")
    reference_report, patient_report = audit_references(payload), audit_body_patient(payload)
    records, cards = [], []
    for n in entries:
        record, pointer = by_index[n], f"/entry/{n}/resource"
        links = [r for r in reference_report["links"] if r["source_pointer"] == pointer]
        patients = [r for r in patient_report["checks"] if r["observation_pointer"] == pointer]
        targets = []
        for row in patients:
            target = row["body_reference"].get("target_pointer")
            if target is not None and target not in targets: targets.append(target)
        linked = [{"source_pointer": p, "resource_json": _raw_at(text, p)} for p in targets]
        records.append({"source_record": record, "references": links, "patient_checks": patients, "linked_resources": linked})
        fields = []
        values = record["value_keys"]
        for key in values:
            fields.append('<section class="field wide"><h3>来源结果 ' + escape(key) + "</h3>"
                          + _pre(_raw_at(text, pointer + "/" + key)) + "</section>")
        if not values:
            fields.append('<section class="field wide"><h3>来源结果</h3><span class="missing">未提供顶层 value[x] 字段</span></section>')
        missing = []
        for key, label in FIELDS:
            raw = _raw_at(text, pointer + "/" + key)
            # Keep absent distinct from explicit null; both stay unfilled.
            if raw is None:
                missing.append("<li>" + label + " <code>" + key + "</code></li>")
            else:
                fields.append('<section class="field"><h3>' + label + "</h3>" + _pre(raw) + "</section>")
        if missing:
            fields.append('<section class="field wide"><h3>未提供的来源字段</h3><ul class="missing">'
                          + "".join(missing) + "</ul></section>")
        statuses = [p["status"] for p in patients] or ["no_scoped_body_reference"]
        excluded = record["clinical_use"] == "excluded_entered_in_error"
        card = '<article data-source-pointer="' + escape(pointer) + '"><h2>Observation ' + escape(record["resource_id"] or pointer) + "</h2>"
        card += '<p class="muted"><code>' + escape(pointer) + '</code></p>'
        card += '<span class="badge' + (' excluded' if excluded else '') + '">来源状态：' + escape(record["observation_status"]) + "</span>"
        card += '<span class="badge">来源使用：' + escape(record["clinical_use"]) + "</span>"
        card += '<p class="muted">患者记录上下文：' + escape(", ".join(statuses)) + '</p><div class="grid">' + "".join(fields) + "</div>"
        for target in linked:
            card += "<details open><summary>引用目标原文 " + escape(target["source_pointer"]) + "</summary>" + _pre(target["resource_json"]) + "</details>"
        card += "<details><summary>引用与患者记录诊断</summary>" + _pre(json.dumps({"references": links, "patient_checks": patients}, ensure_ascii=False, indent=2)) + "</details>"
        narrative = _raw_at(text, pointer + "/text/div")
        if narrative is not None:
            decoded = DECODER.decode(narrative)
            card += "<details><summary>来源叙事标记（文本）</summary>" + _pre(decoded if isinstance(decoded, str) else narrative) + "</details>"
        card += "<details><summary>完整 Observation 原 JSON（含其他字段）</summary>" + _pre(record["resource_json"]) + "</details></article>"
        cards.append(card)
    digest = prepared["summary"]["source_sha256"]
    html = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
            '<title>FHIR 来源核对视图</title><style>' + STYLE + '</style></head><body><main>'
            '<h1>FHIR 来源核对视图</h1><p class="lead">原始观测、关联来源与确定性诊断</p>'
            '<section class="banner"><strong>来源字段展示 · 临床有效性未评估 · 语义投影未执行</strong>'
            '<p>选择 ' + str(len(entries)) + ' 条 Observation；所有原值、单位、时间和未知状态保留。关联诊断仅描述来源记录关系。</p>'
            '<code>' + POLICY + ' · SHA256 ' + digest + '</code></section>' + "".join(cards) + '</main></body></html>\n')
    manifest = {"policy_version": POLICY, "synthetic": True, "source_sha256": digest,
                "component_policies": {"source": SOURCE_POLICY, "references": REFERENCE_POLICY, "patient_context": PATIENT_POLICY},
                "selection": [f"/entry/{n}/resource" for n in entries], "records": records,
                "semantic_projection": "not_performed", "completion_performed": False,
                "clinical_validity": "not_assessed", "html_sha256": hashlib.sha256(html.encode()).hexdigest()}
    return {"html": html, "manifest": manifest}


def render(input_path: str | Path, out_dir: str | Path, entries: list[int], *, synthetic: bool = False) -> dict:
    if synthetic is not True: raise ValueError("Explicit known-synthetic assertion required")
    payload = Path(input_path).read_bytes()
    view = prepare(payload, entries)
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=False)
    (root / "source.fhir.json").write_bytes(payload)
    (root / "view.html").write_bytes(view["html"].encode())
    (root / "view.json").write_text(json.dumps(view["manifest"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"policy_version": POLICY, "observations_rendered": len(entries), "source_sha256": view["manifest"]["source_sha256"],
            "semantic_projection": "not_performed", "clinical_validity": "not_assessed"}


def audit_existing(input_path: str | Path, out_dir: str | Path, entries: list[int]) -> dict:
    payload = Path(input_path).read_bytes()
    expected = prepare(payload, entries)
    root, checks = Path(out_dir), {}
    for name, content, key in [("source.fhir.json", payload, "source_bytes"),
                               ("view.html", expected["html"].encode(), "html_content")]:
        try: checks[key] = (root / name).read_bytes() == content
        except OSError: checks[key] = False
    try:
        saved = json.loads((root / "view.json").read_text(encoding="utf-8"), object_pairs_hook=_unique, parse_constant=_constant)
        checks["view_metadata"] = _json(saved) == _json(expected["manifest"])
    except (OSError, ValueError, TypeError): checks["view_metadata"] = False
    return {"policy_version": POLICY, "checks": checks, "audit_ok": all(checks.values()), "clinical_validity": "not_assessed"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="known-synthetic FHIR Bundle JSON")
    parser.add_argument("--entry", action="append", type=int, required=True, help="Observation entry index; repeat for each selection")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--out", help="new view directory")
    group.add_argument("--audit-existing", help="read-only audit against original input and explicit selection")
    parser.add_argument("--synthetic", action="store_true", help="assert known-synthetic input; not de-identification")
    args = parser.parse_args()
    try:
        result = (audit_existing(args.input, args.audit_existing, args.entry) if args.audit_existing
                  else render(args.input, args.out, args.entry, synthetic=args.synthetic))
    except (OSError, ValueError, TypeError) as exc: parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False))
    if result.get("audit_ok") is False: raise SystemExit(1)


if __name__ == "__main__": main()
