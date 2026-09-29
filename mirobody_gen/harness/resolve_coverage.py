"""Offline vocabulary coverage: how many printed names mirobody's resolver can code.

离线词表覆盖：manifest 里的打印名，mirobody 能解析出多少。

    mirobody-gen resolve-coverage out/p3/manifest.jsonl
    mirobody-gen resolve-coverage out/p3/manifest.jsonl --fail-on-regression

**不花一分钱、不跑一次提取**：直接把真值行喂给 `mirobody.engine.resolve`。
它测的是链路里"词表"这一段的水平，与"从文件里读得出来吗"完全分开——
两件事混在一个数字里的话，提取变差和词表变差看起来一模一样。

## 三个数字，各回答一个问题

| 数字 | 问题 |
|---|---|
| 覆盖率 | 这批语料的打印名，有多少能落到 LOINC 上 |
| 回归 | manifest 说该解析得出、实际却解析不出的——**词表退步了** |
| 意外解析 | manifest 说解析不出、实际却解析出来的——**词表进步了，spec 该重建了** |

第三个数字容易被当成好消息忽略掉，但它是有害的：`expect_resolvable` 是某一天测出来的，
mirobody 的词表一改它就过时，而评测正是靠它区分"召回"与"应当弃权"。
过时之后，考弃权的那些题会开始把正确答案判成错误。
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

PACKAGE = pathlib.Path(__file__).resolve().parents[1]
RESOURCES = PACKAGE / "resources"
#: 源码检出的根目录。只有需要检出才有的东西（参考集、缓存、产物）才用它；安装后的包里没有这些。
REPO = PACKAGE.parent


def load_rows(path: pathlib.Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows += json.loads(line)["rows"]
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("--fail-on-regression", action="store_true",
                    help="exit non-zero on regression (for CI where mirobody is installable)")
    args = ap.parse_args()

    try:
        from mirobody.engine import resolve
    except Exception as e:                       # noqa: BLE001
        raise SystemExit(
            f"导入 mirobody.engine 失败：{type(e).__name__}: {e}\n"
            f"用 mirobody 的解释器跑：../mirobody/.venv/bin/python harness/resolve_coverage.py …")

    rows = load_rows(pathlib.Path(args.manifest))
    # 按打印名去重：同一个名字在几千行里重复，逐行统计会让常见指标主导这个数字。
    by_name: dict[str, dict] = {}
    occurrences: collections.Counter[str] = collections.Counter()
    for row in rows:
        occurrences[row["original_indicator"]] += 1
        by_name.setdefault(row["original_indicator"], row)

    resolved = regressions = surprises = 0
    regression_names: list[str] = []
    surprise_names: list[str] = []
    for name, row in sorted(by_name.items()):
        result = resolve(name)
        ok = bool(result.resolved)
        resolved += ok
        if row.get("expect_resolvable") and not ok:
            regressions += 1
            regression_names.append(name)
        if not row.get("expect_resolvable") and ok:
            surprises += 1
            surprise_names.append(f"{name} → {result.loinc}")

    total = len(by_name)
    print(f"打印名 {total} 种（{len(rows)} 行）· 解析得出 {resolved} 种 = {resolved / total:.1%}")
    print(f"回归（该解析出却没解析出）{regressions} 种")
    for name in regression_names:
        print(f"  ✗ {name}（出现 {occurrences[name]} 次）")
    print(f"意外解析（spec 说解析不出，实际解析出了）{surprises} 种")
    for name in surprise_names:
        print(f"  ! {name}")
    if surprises:
        print("  → mirobody 的词表进步了。重跑 `build_indicators.py --write` 更新 spec，"
              "否则考弃权的题会把正确答案判成错误。")

    if args.fail_on_regression and regressions:
        sys.exit(1)


if __name__ == "__main__":
    main()
