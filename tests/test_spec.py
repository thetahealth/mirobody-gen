"""spec 的完整性测试。`python3 -m pytest tests/ -q`，或直接 `python3 tests/test_spec.py`。

这些测试保的是**契约**，不是数值：参考区间对不对是临床问题，由 `scripts/build_indicators.py
--compare` 与人来判断；这里只保证 spec 的形状能被生成器与审计消费，且没有自相矛盾。
"""

from __future__ import annotations

import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
RESOURCES = REPO / "mirobody_gen" / "resources"
sys.path.insert(0, str(REPO))



def load(name: str) -> dict:
    return json.loads((RESOURCES / name).read_text(encoding="utf-8"))


def test_every_spec_declares_provenance():
    """没有来源声明的 spec 不许存在——隐私闸门也查这条，这里让它在测试里先响。"""
    for path in sorted(RESOURCES.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload.get("_source") in {"public-standard", "format-token", "hand-authored", "llm-paraphrase", "llm-template"}, path
        assert "_provenance" in payload, path
        assert "_vocabulary_fields" in payload, path


def test_indicator_fields_are_complete():
    indicators = load("indicators.json")["indicators"]
    assert len(indicators) >= 170
    keys = set()
    for item in indicators:
        for field in ("key", "zh", "unit", "decimals", "panel", "value_kind",
                      "reference_source", "name_variants"):
            assert field in item, (item.get("key"), field)
        assert item["key"] not in keys, f"键重复：{item['key']}"
        keys.add(item["key"])


def test_reference_shapes_are_wellformed():
    """参考区间的五种形状，逐个检查下限不大于上限。"""
    for item in load("indicators.json")["indicators"]:
        ref = item["reference"]
        if ref is None:
            continue
        kind = ref[0]
        assert kind in {"range", "range_sex", "upper", "lower", "qualitative"}, item["key"]
        if kind == "range":
            assert ref[1] <= ref[2], item["key"]
        elif kind == "range_sex":
            for lo, hi in (ref[1], ref[2]):
                assert lo <= hi, item["key"]


def test_derived_indicators_have_their_inputs():
    """派生指标依赖的键必须在目录里，否则生成时会静默跳过恒等式。"""
    indicators = load("indicators.json")["indicators"]
    keys = {i["key"] for i in indicators}
    referenced = {
        "bmi": {"weight", "height"}, "mcv": {"hct", "rbc"}, "mch": {"hgb", "rbc"},
        "mchc": {"hgb", "hct"}, "glb": {"tp", "alb"}, "ag_ratio": {"alb", "glb"},
        "ibil": {"tbil", "dbil"}, "ldl": {"chol", "hdl", "tg"},
        "neut_abs": {"wbc", "neut_pct"}, "lymph_abs": {"wbc", "lymph_pct"},
        "mono_abs": {"wbc", "mono_pct"}, "eos_abs": {"wbc", "eos_pct"},
        "baso_abs": {"wbc", "baso_pct"},
    }
    for key, needs in referenced.items():
        assert key in keys, key
        assert needs <= keys, (key, needs - keys)


def test_audit_identities_match_the_catalogue():
    """审计里写的每个恒等式，两端的键都得真的存在于目录。

    这条测试是为了防一种很难发现的失效：改了目录的键名，审计的恒等式静默失配，
    于是"0 findings"不再意味着数据没问题，而意味着什么都没查。
    """
    from mirobody_gen.audit import clinical

    keys = {i["key"] for i in load("indicators.json")["indicators"]}
    for key, label, needs, _formula, _tol in clinical.IDENTITIES:
        assert key in keys, f"审计恒等式 {label} 的目标 {key} 不在目录里"
        assert set(needs) <= keys, f"审计恒等式 {label} 的依赖 {set(needs) - keys} 不在目录里"
    assert set(clinical.DIFFERENTIAL) <= keys
    assert set(clinical.SEX_ONLY) <= keys
    assert set(clinical.HARD_LIMITS) <= keys


def test_clinical_audit_selftest_passes():
    from mirobody_gen.audit import clinical

    assert clinical.audit([clinical.CLEAN]) == []
    kinds = {f.kind for f in clinical.audit([clinical.BROKEN])}
    assert {"恒等式", "生理边界", "人口学", "标记"} <= kinds


def test_hazard_taxonomy_is_usable():
    hazards = load("hazards.json")
    generatable = [c for c in hazards["classes"] if c["generate"]]
    assert len(generatable) >= 50
    # 注入密度要按"剔除脱敏痕迹后"的分布来，p95 是主榜单的封顶值。
    counts = hazards["per_document_count"]
    assert counts["p50"] >= 1 and counts["p95"] >= counts["p75"] >= counts["p50"]
    assert 0 < counts["zero_share"] < 0.5
    # 不复现的类必须有，且必须只是加工痕迹那一类。
    artifacts = [c["name"] for c in hazards["classes"] if not c["generate"]]
    assert artifacts == ["artifact.redaction_placeholder"], artifacts


def test_layout_space_is_big_enough_to_be_worth_sampling():
    layout = load("layout.json")
    assert len(layout["column_sets_by_role"]) >= 15
    assert len(layout["reference_dialects"]) >= 30
    assert len(layout["flag_markers"]) >= 10
    assert len(layout["date_formats"]) >= 8
    # 转置导出必须在里面：它是产线 xlsx 的主力版式，第一版白名单曾把它整类丢掉。
    roles = [tuple(item["value"]) for item in layout["column_sets_by_role"]]
    assert any("analyte+" in r and "meta_facility" in r for r in roles), \
        "转置导出的角色组合不见了——检查 distill_layout 的上下文接纳规则"


def test_no_redaction_placeholder_survived_into_spec():
    """脱敏占位符是语料的加工痕迹。它出现在 spec 里，说明蒸馏时漏了过滤。"""
    for path in sorted(RESOURCES.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        assert "«" not in text and "»" not in text, path


def test_spec_is_regenerable_without_drift():
    """三个 distill/build 脚本重跑一遍，`mirobody_gen/resources/` 必须一字不变。

    这条抓的是**脚本与产物之间的静默漂移**：有人改了脚本忘了重跑，或者手改了 spec，
    两种情况下 spec 都不再是"从语料按规则蒸馏出来的东西"，而蒸馏规则正是隐私论证的载体。

    需要本机有 `analysis/` 与 `library/`（真实语料的派生物），所以在 CI 上会跳过。
    跳过时会说明原因，而不是静默通过——"测试通过"不该同时意味着"测试没跑"。
    """
    import subprocess

    if not (REPO / "analysis").is_dir():
        print("  skip test_spec_is_regenerable_without_drift：本机没有 analysis/，"
              "这条只能在有语料的机器上跑")
        return

    # build_indicators 要向 mirobody 的解析器查 LOINC；解析器不在时它会**主动报错退出**
    # （2026-09-22 起，见 ResolverUnavailable），所以这一个脚本用 mirobody 的 venv 跑。
    mirobody_python = REPO.parent / "mirobody" / ".venv" / "bin" / "python"
    if not mirobody_python.is_file():
        print(f"  skip test_spec_is_regenerable_without_drift：找不到 {mirobody_python}，"
              "build_indicators 需要 mirobody 的解析器")
        return

    before = {p.name: p.read_bytes() for p in sorted(RESOURCES.glob("*.json"))}
    for script in ("distill_hazards.py", "distill_layout.py", "build_indicators.py",
                   "build_cohort.py", "build_fiction.py", "build_profile.py"):
        python = str(mirobody_python) if script == "build_indicators.py" else sys.executable
        result = subprocess.run([python, str(REPO / "scripts" / script), "--write"],
                                capture_output=True, cwd=REPO)
        assert result.returncode == 0, f"{script} 退出码 {result.returncode}：{result.stderr[-400:]}"
    after = {p.name: p.read_bytes() for p in sorted(RESOURCES.glob("*.json"))}

    drifted = [name for name in before if before[name] != after.get(name)]
    # build_indicators 会向 mirobody 的解析器查 LOINC，解析器不在时 expect_resolvable
    # 全变 false，那不是漂移而是缺依赖——单独说清楚，不要报成失败。
    if drifted == ["indicators.json"]:
        import json as _json
        resolved = sum(1 for i in _json.loads(after["indicators.json"])["indicators"]
                       if i.get("expect_resolvable"))
        if resolved == 0:
            print("  note: indicators.json 变了，因为这次运行没找到 mirobody 解析器"
                  "（expect_resolvable 全为 false）。用 mirobody 的 venv 重跑可复原")
            for name, data in before.items():
                (RESOURCES / name).write_bytes(data)
            return
    assert not drifted, f"spec 与脚本漂移了：{drifted}"


def test_scripts_do_not_shadow_stdlib():
    """`scripts/` 里的文件名不能与标准库模块同名。

    `python3 scripts/xxx.py` 会把 `scripts/` 放在 `sys.path[0]`，于是同名文件会**替换掉**
    标准库模块。这条测试是被真事教出来的：`scripts/numbers.py` 遮蔽了 `numbers`，
    numpy 导入时拿到它、`numbers.Integral` 不存在、numpy 崩、mirobody 解析器 import 失败，
    而失败被 `except Exception` 接住，最后表现为"98 个指标的 LOINC 全解析不出"——
    一个环境故障长成了一个测量结果。
    """
    import sys as _sys

    stdlib = set(getattr(_sys, "stdlib_module_names", ()))
    assert stdlib, "拿不到标准库模块名单（需要 Python 3.10+）"
    clashes = sorted(p.stem for p in (REPO / "scripts").glob("*.py") if p.stem in stdlib)
    assert not clashes, f"这些脚本名遮蔽了标准库模块：{clashes}"


def test_every_quantitative_indicator_has_a_hard_limit():
    """每个定量指标都要么有生理硬边界，要么在豁免表里写明为什么没有。

    这条是被真事教出来的：腰围没在 HARD_LIMITS 里，于是生成器印出 44.2 cm 的腰围
    配 104 kg 的体重，边界检查一声不吭——**检查表缺一项是静默的**，
    "0 findings" 既可能意味着没问题，也可能意味着那一项根本没查。

    同一轮补全之后，间接胆红素 −2.1 当场现形（直接胆红素当时是独立抽的，
    可以超过总胆红素）。一条完备性测试换出一个潜伏缺陷。
    """
    from mirobody_gen.audit import clinical

    quantitative = {i["key"] for i in load("indicators.json")["indicators"]
                    if i["value_kind"] == "quantitative"}
    covered = set(clinical.HARD_LIMITS) | set(clinical.NO_HARD_LIMIT)
    missing = sorted(quantitative - covered)
    assert not missing, f"这些定量指标既没有硬边界也没写豁免理由：{missing}"

    # 豁免表也不能变成垃圾桶：写了豁免却又有边界，说明有一处过时了。
    both = sorted(set(clinical.HARD_LIMITS) & set(clinical.NO_HARD_LIMIT))
    assert not both, f"这些指标同时出现在硬边界与豁免表里：{both}"


def test_derived_chain_is_acyclic_and_rooted():
    """派生链不能成环，且每一环的输入都要存在。

    红细胞指数在开发中被翻转过两次方向（先是 HGB/HCT 派生 MCV/MCHC，
    后来改成 RBC/MCV/MCH，最后定为 RBC/MCV/MCHC）。每翻一次都有可能留下一条
    指向旧方向的边，而成环的表现是生成时静默少几行，不是报错。
    """
    items = {i["key"]: i for i in load("indicators.json")["indicators"]}
    import re

    edges = {}
    for key, item in items.items():
        formula = item.get("derived_from") or ""
        deps = {t for t in re.findall(r"[a-z_]+", formula) if t in items and t != key}
        edges[key] = deps

    seen, stack = set(), set()

    def walk(node: str) -> None:
        if node in stack:
            raise AssertionError(f"派生链成环，经过 {node}")
        if node in seen:
            return
        stack.add(node)
        for dep in edges.get(node, ()):
            walk(dep)
        stack.discard(node)
        seen.add(node)

    for key in items:
        walk(key)


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok  {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL  {name}: {e}")
    print(f"\n{failures} 个失败" if failures else "\n全部通过")
    sys.exit(1 if failures else 0)
