"""生成器的测试。`python3 tests/test_generator.py`（也可以用 pytest）。

只测**契约与不变量**，不测具体数值：某个人的血糖是 5.3 还是 5.4 不重要，
重要的是同一个 seed 两次跑出同一个 5.3、manifest 的字段齐全、临床审计是干净的。
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

SMOKE_PEOPLE = 8


def _build(seed: int, out: pathlib.Path, people: int = SMOKE_PEOPLE):
    from mirobody_gen import manifest, person as person_mod

    cohort = person_mod.build_cohort(seed, people)
    encounters = {p.person_id: person_mod.encounters_for(p, seed) for p in cohort}
    manifest.write(out, cohort, encounters)
    return cohort, encounters


def test_same_seed_is_byte_identical():
    """同 seed 两次生成必须逐字节一致。

    这是"可重放"的全部含义，也是 `out/` 可以随手删掉的前提：判据是生成器加 seed，
    不是那堆字节。一旦有一处用了无序集合或系统随机数，这条会立刻失败。
    """
    with tempfile.TemporaryDirectory() as tmp:
        a, b = pathlib.Path(tmp) / "a", pathlib.Path(tmp) / "b"
        _build(7, a)
        _build(7, b)
        for name in ("manifest.jsonl", "people.jsonl"):
            assert (a / name).read_bytes() == (b / name).read_bytes(), name


def test_different_seed_gives_a_different_corpus():
    """换 seed 必须换一批。这是"基准是生成器而不是固定文件"的前提——

    评分用封存的 seed，开发用开放的 seed，两者不同才谈得上"没有对着榜单调参"。
    """
    with tempfile.TemporaryDirectory() as tmp:
        a, b = pathlib.Path(tmp) / "a", pathlib.Path(tmp) / "b"
        _build(7, a)
        _build(8, b)
        assert (a / "manifest.jsonl").read_bytes() != (b / "manifest.jsonl").read_bytes()


def test_manifest_rows_carry_the_audit_contract():
    """审计要用的字段必须都在。缺一个的后果是审计静默跳过那一类检查。"""
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp)
        _build(7, out)
        records = [json.loads(line) for line in
                   (out / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
        assert records
        for record in records:
            for field in ("file", "person_id", "person", "collected", "rows",
                          "hazards", "events_since_previous", "synthetic"):
                assert field in record, field
            assert record["person"]["sex"] in ("male", "female")
            for row in record["rows"]:
                for field in ("key", "original_indicator", "value", "unit",
                              "reference_range", "status", "detection_method",
                              "canonical_value", "value_kind", "readable"):
                    assert field in row, (row.get("key"), field)
                assert row["status"] in ("normal", "high", "low")


def test_clinical_audit_is_clean_on_generated_output():
    """生成的真值层必须**零 findings**。

    这条是真值层的验收标准本身。它会连带测到恒等式、生理边界、人口学、标记一致、
    以及纵向的超出率——任何一处模型出错都会在这里响。
    """
    from mirobody_gen.audit import clinical

    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp)
        _build(7, out)
        records = [json.loads(line) for line in
                   (out / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
        findings = clinical.audit(records)
        assert not findings, "\n".join(str(f) for f in findings[:10])


def test_cohort_matches_the_spec():
    from mirobody_gen import person as person_mod, spec

    cohort_spec = spec.cohort()
    names = [a["name"] for a in cohort_spec["archetypes"]]
    assert len(names) == len(set(names)), "原型名重复"
    assert sum(a["n"] for a in cohort_spec["archetypes"]) == 60
    # 缩小采样必须**跨原型**，不能只取前 N 个——否则冒烟测试永远只看到健康人。
    small = person_mod.build_cohort(7, 8)
    assert len({p.archetype for p in small}) >= 4, \
        f"8 人的缩小队列只覆盖了 {len({p.archetype for p in small})} 种原型"


def test_events_are_visible_in_the_timeline():
    """干预事件必须落在观察窗口内，且前后都有就诊点——否则归因问题无从考起。"""
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp)
        cohort, encounters = _build(7, out)
        with_script = [p for p in cohort if p.archetype != "healthy"]
        assert with_script, "缩小队列里一个慢病原型都没有"
        for person in with_script:
            dates = [e.exam_date for e in encounters[person.person_id]]
            scripted = [e for e in person.events if e.note == "原型剧本"]
            for event in scripted:
                assert any(d < event.start for d in dates), \
                    f"{person.person_id} 的『{event.name}』之前没有就诊点，拿不到基线"
                assert any(d > event.start for d in dates), \
                    f"{person.person_id} 的『{event.name}』之后没有就诊点，看不到效果"


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
