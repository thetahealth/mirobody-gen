"""`build --verify`: run the deterministic audits on the build just written and say, check by check,
what passed, what failed and what was never run.

A build that writes files has not thereby been checked. Each check below ends in exactly one of

* `passed` / `failed` — it ran; `failed` on a gating (`required`) check makes the build exit 1;
* `skipped` — it had nothing to look at (e.g. no image-layer files), with the reason;
* `not_run` — it applies but could not or did not run here (no reference corpus for replay
  detection, no independent OCR of image tiers, no audit written for an output family).

`not_run` is never folded into `passed`: the overall status is `passed` only when every check that
applies actually ran and passed, `incomplete` when nothing failed but something applicable did not
run, and `failed` otherwise. Each check's printed output goes to `verification/<check>.log`, the
summary to `verification.json`.

The clinical, readability and privacy checks are the existing `audit/` modules, called as they are;
nothing here re-decides a finding. `order_results` re-applies the serializer's order/result partition
check to the file as written, so it can only catch a manifest changed or produced outside the
generator. With the audits that exist today the overall status is at best `incomplete`: no
deterministic audit covers device/continuous sources or regenerated cut-off counterfactuals.
"""

from __future__ import annotations

import contextlib
import io
import json
import pathlib

STATUSES = ("passed", "failed", "skipped", "not_run")


class Report:
    def __init__(self, out_dir: pathlib.Path):
        self.out_dir = out_dir
        self.log_dir = out_dir / "verification"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.checks: list[dict] = []

    def add(self, name: str, status: str, reason: str, *, required: bool = True, log: str | None = None,
            **detail) -> None:
        assert status in STATUSES, status
        entry = {"check": name, "status": status, "required": required, "reason": reason, **detail}
        if log is not None:
            path = self.log_dir / f"{name}.log"
            path.write_text(log, encoding="utf-8")
            entry["log"] = str(path.relative_to(self.out_dir))
        self.checks.append(entry)

    def overall(self) -> str:
        if any(c["status"] == "failed" and c["required"] for c in self.checks):
            return "failed"
        if any(c["status"] == "not_run" for c in self.checks):
            return "incomplete"
        return "passed"


def _captured(fn, *args, **kwargs):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = fn(*args, **kwargs)
    return result, buf.getvalue()


def run(out_dir: pathlib.Path, *, requested_people: int | None, expected_people: int, people: int,
        rendered: bool, pairs: bool, continuous: bool) -> dict:
    from .audit import clinical, privacy, readability

    report = Report(out_dir)

    report.add("population", "passed" if people == expected_people else "failed",
               f"requested {requested_people if requested_people is not None else 'the full archetype table'}: "
               f"expected {expected_people}, generated {people}",
               expected=expected_people, generated=people)

    code, log = _captured(clinical.selftest)
    report.add("clinical_selftest", "passed" if code == 0 else "failed",
               "the clinical audit's own built-in pass/fail cases", log=log)

    records = [json.loads(line) for line in (out_dir / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
               if line.strip()]
    from .model import order_result_errors

    undeclared = sum(r.get("ordered_keys") is None for r in records)
    errors = [f"record {i}: {error}" for i, r in enumerate(records) if r.get("ordered_keys") is not None
              for error in order_result_errors(r["ordered_keys"], r.get("rows"), r.get("missing_results"))]
    report.add("order_results", "failed" if errors else "not_run" if undeclared else "passed",
               f"{len(records)} records; {undeclared} lack declared orders; {len(errors)} partition/validity errors",
               log="\n".join(errors), records=len(records), undeclared_records=undeclared, errors=len(errors))
    findings, notes = _captured(clinical.audit, records)
    by_kind: dict[str, int] = {}
    for f in findings:
        by_kind[f.kind] = by_kind.get(f.kind, 0) + 1
    report.add("clinical_manifest", "failed" if findings else "passed",
               f"{len(records)} encounter records, {len(findings)} findings",
               log="\n".join(str(f) for f in findings) + ("\n" if findings else "") + notes,
               records=len(records), findings=len(findings), findings_by_kind=by_kind)
    report.add("temporal_counterfactual", "not_run",
               "build --verify does not edit/delete future inputs and regenerate past material; "
               "point-in-time condition checks alone do not certify generator causality", required=False)

    image_skipped = 0
    for name, manifest, present in (("readability_files", out_dir / "files.jsonl", rendered),
                                    ("readability_pairs", out_dir / "pairs.jsonl", pairs)):
        if not present:
            report.add(name, "skipped", f"{manifest.name} not produced by this build", required=False)
            continue
        stats: dict = {}
        problems, log = _captured(readability.audit, manifest, stats=stats)
        image_skipped += stats["image_files_skipped"] + stats["handwritten_transcript_only"]
        reason = (f"{stats['checked_rows']} printed truth rows/blocks looked up in the text layer of "
                  f"{stats['records']} records; {stats['image_files_skipped']} image-layer files have no text "
                  f"layer, and {stats['handwritten_transcript_only']} handwritten pages were checked only "
                  "against their own transcript")
        if problems:
            status = "failed"
        elif stats["checked_rows"]:
            status = "passed"
        else:
            # nothing was looked up: an empty manifest, or only pages without a text layer
            status = "not_run" if stats["image_files_skipped"] or stats["handwritten_transcript_only"] else "skipped"
        report.add(name, status, reason, log=log, **stats)
    if not (rendered or pairs):
        report.add("image_ocr", "skipped", "no files rendered", required=False)
    elif image_skipped:
        report.add("image_ocr", "not_run",
                   f"{image_skipped} image-layer or handwritten files are not independently OCR-verified; "
                   "`audit-readability --ocr` reports recognition rates but is not a gate", required=False)
    else:
        report.add("image_ocr", "skipped", "no image-layer files", required=False)

    paths = [p for p in sorted(out_dir.rglob("*"))
             if p.is_file() and not p.name.startswith(".") and report.log_dir not in p.parents]
    index = privacy.build_index() if privacy.CACHE.is_file() else None
    stats = {}
    hits, log = _captured(privacy.scan, paths, index, privacy.load_vocabulary(), False, stats=stats)
    scope = "PII patterns, name and institution allow-lists" + (" and replay detection" if index is not None else "")
    report.add("privacy_output", "failed" if hits else "passed",
               f"{scope} over {stats['scanned_files']} text-readable output files", log=log, **stats)
    if index is None:
        report.add("privacy_replay", "not_run",
                   f"no reference-corpus n-gram index ({privacy.CACHE.name}) on this machine, so copied "
                   "reference text cannot be detected", required=False)
    if stats["unreadable_files"]:
        report.add("privacy_images", "not_run",
                   f"{stats['unreadable_files']} image/binary output files cannot be text-scanned for PII",
                   required=False)

    families = ["devices", "journal", "genomics", "medications", "vendor_signals"] + (["continuous"] if continuous else [])
    report.add("other_sources_audit", "not_run",
               f"no deterministic audit exists for {', '.join(families)} outputs (covered only by unit tests)",
               required=False)

    summary = {"overall": report.overall(), "checks": report.checks}
    (out_dir / "verification.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def render(summary: dict) -> str:
    lines = [f"verification: {summary['overall']}"]
    for c in summary["checks"]:
        flag = "" if c["required"] else " [does not gate]"
        lines.append(f"  {c['status']:<8} {c['check']}{flag}: {c['reason']}")
    return "\n".join(lines)
