"""Deterministic consistency checks over laboratory observations in FHIR R4 bundles.

对一批 FHIR R4 bundle 做检验观测的确定性一致性检查。

    mirobody-gen synthea-check <dir-with-*.json>            # 报告
    mirobody-gen synthea-check <dir> --strict               # 有问题即非零退出
    mirobody-gen synthea-check <dir> --json report.json

这是给 TIET-AI/tietai-synthea issue #113 承诺的那个校验器（docs/issues/pysynthea-observation-fidelity.md
末尾"Happy to help"一段）。它**只读 FHIR bundle**，不依赖本仓的生成器，也不依赖 PySynthea 的包——
任何能导出 R4 `Observation` 的合成器都能用它自查。

查五件事，每一件都是检验医学的定义或 FHIR 的结构约束，不是风格偏好：

1. **覆盖**：`Observation` 里带 `referenceRange` / `interpretation` 的比例（按 LOINC 分组）；
2. **标记与区间一致**：`interpretation` 是 H/L/N 时，必须与 `valueQuantity` 落在 `referenceRange` 的哪一侧相符；
3. **红细胞指数恒等式**：同一次 encounter 里 MCV = HCT×10/RBC、MCH = HGB/RBC×10、MCHC = HGB/HCT×100
   （单位按 FHIR 里印的换算到 fL / pg / g/dL）；
4. **蛋白与分类**：球蛋白 = 总蛋白 − 白蛋白；白细胞分类百分比之和 = 100；
5. **印刷精度**：`valueQuantity.value` 的小数位数按分析物统计——`85.34241844236215 fL` 这种值是
   合成数据最显眼的破绽，真实仪器按分析物固定位数出数。

阈值与 issue 里的一致：MCV ±3 fL、MCHC ±1.5 g/dL、MCH ±1 pg、球蛋白 ±0.3 g/dL、分类合计 ±1.5。

实测（tietai-synthea 1.4.1，`synthea -p 8 --seed 7`，2026-09-29）：
referenceRange 1020/2175、标记冲突 0、红细胞面板 8/8 自洽、球蛋白无面板、
>3 位小数的值 63/1858。前四项修好了，第五项还在。
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

#: LOINC → (键, 目标单位)。单位换算只做 issue 里涉及的这几种，其余照原样比较。
LOINC = {
    "789-8": ("rbc", "10*6/uL"), "718-7": ("hgb", "g/dL"), "4544-3": ("hct", "%"),
    "787-2": ("mcv", "fL"), "785-6": ("mch", "pg"), "786-4": ("mchc", "g/dL"),
    "2885-2": ("tp", "g/dL"), "1751-7": ("alb", "g/dL"), "10834-0": ("glb", "g/dL"),
    "770-8": ("neut_pct", "%"), "736-9": ("lymph_pct", "%"), "5905-5": ("mono_pct", "%"),
    "713-8": ("eos_pct", "%"), "706-2": ("baso_pct", "%"),
}
#: 单位换算到目标单位的倍数。
CONVERT = {
    ("hgb", "g/L"): 0.1, ("hgb", "g/dL"): 1.0,
    ("mchc", "g/L"): 0.1, ("mchc", "g/dL"): 1.0,
    ("tp", "g/L"): 0.1, ("alb", "g/L"): 0.1, ("glb", "g/L"): 0.1,
    ("rbc", "10*12/L"): 1.0, ("rbc", "10*6/uL"): 1.0,
}
IDENTITIES = [
    ("mcv", ("hct", "rbc"), lambda v: v["hct"] * 10 / v["rbc"], 3.0, "fL"),
    ("mch", ("hgb", "rbc"), lambda v: v["hgb"] / v["rbc"] * 10, 1.0, "pg"),
    ("mchc", ("hgb", "hct"), lambda v: v["hgb"] / v["hct"] * 100, 1.5, "g/dL"),
    ("glb", ("tp", "alb"), lambda v: v["tp"] - v["alb"], 0.3, "g/dL"),
]
DIFFERENTIAL = ("neut_pct", "lymph_pct", "mono_pct", "eos_pct", "baso_pct")


def observations(root: pathlib.Path):
    for path in sorted(root.rglob("*.json")):
        try:
            bundle = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        for entry in bundle.get("entry", []) if isinstance(bundle, dict) else []:
            res = entry.get("resource", {})
            if res.get("resourceType") == "Observation":
                yield path.name, res


def _code(obs: dict) -> str | None:
    for coding in (obs.get("code") or {}).get("coding") or []:
        if coding.get("code"):
            return coding["code"]
    return None


def _value(obs: dict) -> tuple[float | None, str]:
    q = obs.get("valueQuantity") or {}
    v = q.get("value")
    return (float(v) if isinstance(v, (int, float)) else None), str(q.get("unit") or q.get("code") or "")


def _interp(obs: dict) -> str | None:
    for item in obs.get("interpretation") or []:
        for coding in item.get("coding") or []:
            if coding.get("code"):
                return coding["code"]
    return None


def _range(obs: dict) -> tuple[float | None, float | None] | None:
    rr = obs.get("referenceRange")
    if not rr:
        return None
    lo = (rr[0].get("low") or {}).get("value")
    hi = (rr[0].get("high") or {}).get("value")
    return (float(lo) if lo is not None else None, float(hi) if hi is not None else None)


def _decimals(value: float) -> int:
    text = repr(value)
    return len(text.split(".")[1]) if "." in text and "e" not in text else 0


def check(root: pathlib.Path) -> dict:
    total = with_range = with_interp = 0
    by_code = collections.defaultdict(lambda: [0, 0])
    flag_checked = flag_bad = 0
    flag_examples: list[str] = []
    panels: dict[tuple[str, str], dict[str, float]] = collections.defaultdict(dict)
    precision: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for fname, obs in observations(root):
        total += 1
        code = _code(obs)
        rr = _range(obs)
        interp = _interp(obs)
        with_range += rr is not None
        with_interp += interp is not None
        if code:
            by_code[code][0] += 1
            by_code[code][1] += rr is not None
        value, unit = _value(obs)
        if value is None:
            continue
        precision[code or "?"][_decimals(value)] += 1
        if rr is not None and interp in ("H", "L", "N"):
            lo, hi = rr
            expected = "H" if (hi is not None and value > hi) else ("L" if (lo is not None and value < lo) else "N")
            flag_checked += 1
            if expected != interp:
                flag_bad += 1
                if len(flag_examples) < 5:
                    flag_examples.append(f"{fname} {code} value={value} range=[{lo},{hi}] interp={interp} expected={expected}")
        if code in LOINC:
            key, target = LOINC[code]
            factor = CONVERT.get((key, unit), 1.0 if unit in (target, "") else None)
            if factor is None:
                continue
            enc = (obs.get("encounter") or {}).get("reference") or obs.get("effectiveDateTime") or fname
            panels[(fname, enc)][key] = value * factor

    identity_stats = {name: [0, 0, []] for name, *_ in IDENTITIES}
    diff_sums: list[float] = []
    for (fname, enc), v in panels.items():
        for name, inputs, fn, tol, unit in IDENTITIES:
            if name in v and all(k in v for k in inputs):
                implied = fn(v)
                identity_stats[name][0] += 1
                if abs(implied - v[name]) > tol:
                    identity_stats[name][1] += 1
                    if len(identity_stats[name][2]) < 3:
                        identity_stats[name][2].append(f"{fname}: printed {v[name]:.1f} / implied {implied:.1f} {unit}")
        if all(k in v for k in DIFFERENTIAL):
            diff_sums.append(sum(v[k] for k in DIFFERENTIAL))

    long_values = {code: sum(n for d, n in c.items() if d > 3) for code, c in precision.items()}
    return {
        "observations": total,
        "with_referenceRange": with_range,
        "with_interpretation": with_interp,
        "coverage_by_code": {c: {"n": n, "with_range": r} for c, (n, r) in sorted(by_code.items())},
        "flag_vs_range": {"checked": flag_checked, "disagree": flag_bad, "examples": flag_examples},
        "identities": {name: {"panels": n, "inconsistent": bad, "examples": ex}
                       for name, (n, bad, ex) in identity_stats.items()},
        "differential": {"panels": len(diff_sums),
                         "off_by_more_than_1_5": sum(abs(s - 100) > 1.5 for s in diff_sums)},
        "precision": {"values_with_more_than_3_decimals": sum(long_values.values()),
                      "values_total": sum(sum(c.values()) for c in precision.values()),
                      "worst_codes": sorted(long_values.items(), key=lambda kv: -kv[1])[:8]},
    }


def problems(report: dict) -> list[str]:
    out = []
    if report["observations"] and report["with_referenceRange"] == 0:
        out.append("no Observation carries referenceRange")
    if report["flag_vs_range"]["disagree"]:
        out.append(f"{report['flag_vs_range']['disagree']} interpretation flags disagree with their referenceRange")
    for name, st in report["identities"].items():
        if st["inconsistent"]:
            out.append(f"{name}: {st['inconsistent']}/{st['panels']} panels inconsistent")
    if report["differential"]["off_by_more_than_1_5"]:
        out.append(f"differential percentages do not sum to 100 in {report['differential']['off_by_more_than_1_5']} panels")
    if report["precision"]["values_with_more_than_3_decimals"]:
        out.append(f"{report['precision']['values_with_more_than_3_decimals']} values printed with more than 3 decimals")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="directory containing FHIR bundles (*.json)")
    ap.add_argument("--strict", action="store_true", help="exit non-zero on any finding")
    ap.add_argument("--json", help="write the full report to this file")
    args = ap.parse_args()
    report = check(pathlib.Path(args.root))
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"observations           : {report['observations']}")
    print(f"with referenceRange    : {report['with_referenceRange']} / {report['observations']}")
    print(f"with interpretation    : {report['with_interpretation']} / {report['observations']}")
    fv = report["flag_vs_range"]
    print(f"flag vs range disagree : {fv['disagree']} / {fv['checked']}")
    for name, st in report["identities"].items():
        print(f"{name:<6} inconsistent     : {st['inconsistent']} / {st['panels']} panels")
        for ex in st["examples"]:
            print(f"      {ex}")
    d = report["differential"]
    print(f"differential != 100    : {d['off_by_more_than_1_5']} / {d['panels']} panels")
    p = report["precision"]
    print(f">3 decimals            : {p['values_with_more_than_3_decimals']} / {p['values_total']} values")
    issues = problems(report)
    for issue in issues:
        print(f"  ! {issue}")
    if args.strict and issues:
        sys.exit(1)


if __name__ == "__main__":
    main()
