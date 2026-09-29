"""Write the truth manifest (manifest.jsonl, people.jsonl) in the contract the clinical audit checks.

真值清单。契约由 `audit/clinical.py` 定死，这里只负责填。

一条记录 = 一次就诊。渲染成文件之后（`corpus.py`），一次就诊会拆成一到几份文件，
每份文件一条记录、带自己的版式指纹与注入的陷阱——**记录的形状不变**，
只是 `file` 从占位符变成真实路径、`rows` 变成那份文件上真正印出来的子集。

字段分两类：
* 与 mirobody 提取契约同名的（`original_indicator / value / unit / reference_range /
  status / detection_method`）——评分时逐字段比对；
* 评分才用、不印在纸上的（`key / canonical_value / loinc / ref_low / ref_high /
  expect_resolvable / readable`）。

`readable` 是召回分母的开关：注入的陷阱让某一行读不出来时，它移出分母、
进入"必须弃权"集合。**一条读不出来还要求答对的题，测的是幻觉倾向，不是提取能力。**
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import asdict

from . import spec
from .model import Encounter, Person


def _conditions_of(person: Person, encounters: list[Encounter] | None = None) -> list[dict]:
    """这个人的诊断集合 = 原型自带的 + **由数值推出来的**。

    第二项是一个反馈回路：一个人的血压连续三次 ≥140/90，纸面上他就是高血压病人，
    不管队列当初把他归成什么原型。没有这一环，生成的档案里会出现"值达到诊断标准
    却没有诊断"的病历——审计会（正确地）判它漏诊。

    实测：ESL-Bench 公开数据的 20 个用户里，值连续达标 7 次、本人慢病列表里
    没有对应诊断 2 次（都是高血压）。他们的管线是自上而下的（画像 → 事件 → 数值），
    数值漂进诊断区间不会回流到画像。我们这里补上回流。

    真实世界里"未诊断的高血压"当然大量存在——所以这不是说那样就错。
    但对一个要考归因的基准来说，真值里该有的东西不能靠推断补，得写下来。
    """
    base = list(spec.cohort().get("archetype_conditions", {}).get(person.archetype, []))
    if not encounters:
        return base
    have = {c["code"] for c in base}
    for rule in spec.cohort().get("diagnostic_criteria", []):
        code = rule["condition"]["code"]
        if code in have:
            continue
        run = 0
        for encounter in sorted(encounters, key=lambda e: e.exam_date):
            values = {r.key: r.canonical_value for r in encounter.readings
                      if r.canonical_value is not None}
            met = any(_meets(values.get(c["key"]), c["op"], c["value"]) for c in rule["any_of"])
            run = run + 1 if met else 0
            if run >= rule["persistence"]:
                base.append({**rule["condition"], "onset_date": encounter.exam_date.isoformat(),
                             "source": "由数值推出（" + rule["source"].split("：")[0] + "）"})
                have.add(code)
                break
    return base


def _meets(value, op: str, threshold: float) -> bool:
    if value is None:
        return False
    return {">=": value >= threshold, ">": value > threshold,
            "<=": value <= threshold, "<": value < threshold}.get(op, False)


def encounter_record(person: Person, encounter: Encounter, file: str | None = None,
                     index: int = 0, conditions: list[dict] | None = None) -> dict:
    # 占位 id 用就诊序号，不要把医嘱名拼进去：`vitals+inflammation` 归一化之后是
    # `vitalsinflammation`，正好落在真实语料的某个 12 字窗口里，隐私闸门会报一条
    # 并不存在的"逐字搬运"。文件层（`corpus.record`）会把它换成真实文件路径。
    return {
        "file": file or f"{person.person_id}/{encounter.exam_date}/e{index:02d}",
        "synthetic": True,
        "person_id": person.person_id,
        "person": {
            "sex": person.sex,
            "age": person.age_at(encounter.exam_date),
            "birth_year": person.birth_year,
            "height_cm": person.height_cm,
            "archetype": person.archetype,
            "region": person.region,
            # 诊断集合。现在由原型查表得到；接入 PySynthea 之后直接来自它的
            # Condition 资源，这一行就是那个接缝。
            "conditions": conditions if conditions is not None else _conditions_of(person),
        },
        "collected": encounter.exam_date.isoformat(),
        "exam_type": encounter.exam_type,
        "exam_location": encounter.exam_location,
        "panels": list(encounter.panels),
        "package": encounter.package,
        "events_since_previous": list(encounter.events_since_previous),
        # 主诉（mirobody 症状轴的期望码）与具名所见（诊断轴的期望码）。
        "complaints": [{"text": c.text, "symptom_id": c.symptom_id, "icpc3": c.icpc3, "expect": c.expect,
                        "duration": c.duration} for c in encounter.complaints],
        "findings": [{"id": x.id, "where": x.where, "item": x.item, "surface": x.surface, "icpc3": x.icpc3,
                      "severity": x.severity, "since": x.since.isoformat() if x.since else None}
                     for x in encounter.findings],
        # 文件层填：版式指纹、注入的陷阱、文件格式与难度层。
        "layout_fingerprint": None,
        "hazards": [],
        "tier": None,
        "format": None,
        "language": None,
        "rows": [asdict(r) for r in encounter.readings],
    }


def person_record(person: Person, conditions: list[dict] | None = None) -> dict:
    return {
        "person_id": person.person_id,
        "sex": person.sex,
        "birth_year": person.birth_year,
        "height_cm": person.height_cm,
        "archetype": person.archetype,
        "region": person.region,
        "conditions": conditions if conditions is not None else _conditions_of(person),
        "events": [
            {
                "name": e.name,
                "event_type": e.event_type,
                "start_date": e.start.isoformat(),
                "duration_days": e.duration_days,
                "health_effect": e.health_effect,
                "impact_level": e.impact_level,
                # 归因问题的可判定答案就在这里：哪个事件、对哪些指标、多大幅度、多久起效。
                "affected_indicators": sorted(e.effects),
                "effects": {k: {"magnitude": m, "onset_days": o, "half_life_days": d}
                            for k, (m, o, d) in e.effects.items()},
            }
            for e in person.events
        ],
    }


def write(out_dir: pathlib.Path, people: list[Person],
          encounters: dict[str, list[Encounter]]) -> tuple[int, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    by_person = {p.person_id: p for p in people}

    # 诊断集合按人算一次（要看完整条时间线），再发给每一份记录。
    conditions = {pid: _conditions_of(by_person[pid], items)
                  for pid, items in encounters.items()}

    rows = 0
    with (out_dir / "manifest.jsonl").open("w", encoding="utf-8") as fh:
        for person_id, items in encounters.items():
            for index, encounter in enumerate(items, start=1):
                record = encounter_record(by_person[person_id], encounter, index=index,
                                          conditions=conditions[person_id])
                rows += len(record["rows"])
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    with (out_dir / "people.jsonl").open("w", encoding="utf-8") as fh:
        for person in people:
            fh.write(json.dumps(person_record(person, conditions.get(person.person_id)),
                                ensure_ascii=False) + "\n")

    return sum(len(v) for v in encounters.values()), rows
