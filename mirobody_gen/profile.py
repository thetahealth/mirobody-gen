"""Profile layer: named findings per person, chief complaints per visit, check-up package choice.

档案层：一个人有哪些检查所见、每次就诊说了什么主诉、体检选了哪档套餐。

真值来自生成过程，这一点与指标层一样（docs/zh-CN/plan.md §3.3）：

* **所见是按人抽一次、按年推进的。** 囊肿、结石、结节、龋齿一旦出现就一直在（`sticky`），
  只会慢慢长大或升级（脂肪肝轻→中）；生活方式干预之后脂肪肝会退回轻度。于是同一个人
  连续几年的体检报告是**同一条故事**，而不是每年独立抽一遍——mirobody 做纵向合并时，
  "去年就有的甲状腺结节今年 6mm 变 7mm"是一个可判定的问题。
* **主诉跟着原型与事件走。** 糖尿病人在开二甲双胍之前说口渴乏力，之后偶尔手麻；
  感冒那两周说咳嗽咽痛；健康人体检时没有主诉（体检报告本来就没有主诉栏）。
  每条主诉的表面都在 `resources/complaints.json` 里对照过 mirobody 的症状词表，
  所以它该编成什么码、或者该弃权，是写在真值里的。

发生率与 mirobody 无关，只求量级像人（spec 里写明了这一点）。
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from . import spec
from .model import Complaint, Finding, Person

_dl = spec.doc_lang


def _narr() -> dict:
    return spec.narratives()


def _comp() -> dict:
    return spec.complaints()


# ── 所见 ────────────────────────────────────────────────────────
def _eligible(fdef: dict, person: Person, age: int) -> bool:
    if fdef.get("sex") and fdef["sex"] != person.sex:
        return False
    if fdef.get("min_age") and age < fdef["min_age"]:
        return False
    if fdef.get("max_age") and age > fdef["max_age"] and not fdef["rate"].get("after_max_age"):
        return False
    return True


def _rate(fdef: dict, person: Person, age: int) -> float:
    r = fdef["rate"]
    if fdef.get("max_age") and age > fdef["max_age"]:
        rate = r.get("after_max_age", 0.0)
    else:
        rate = r.get("base", 0.0) + r.get("age", 0.0) * max(0, age - 30)
    rate *= r.get("sex", {}).get(person.sex, 1.0)
    rate *= r.get("archetype", {}).get(person.archetype, 1.0)
    return min(rate, 0.97)


_SIDES = {"zh": ["左", "右"], "en": ["left", "right"]}
_LOBES = {"zh": ["左", "右"], "en": ["left", "right"]}
_LUNG_LOBES = {"zh": ["上", "中", "下"], "en": ["upper", "middle", "lower"]}
_ECHO = {"zh": ["低", "等", "囊实性混合"], "en": ["hypoechoic", "isoechoic", "mixed cystic-solid"]}
_WALLS = {"zh": ["前", "后"], "en": ["anterior", "posterior"]}
_DENSITY = {"zh": ["磨玻璃", "实性"], "en": ["ground-glass", "solid"]}
_TEETH = ["16", "26", "36", "46", "17", "27", "37", "47", "14", "24", "34", "44"]


def _params(fdef: dict, rng: random.Random) -> dict:
    """所见文本里要填的槽：侧别、大小、回声、牙位……一次抽定，之后每年沿用（只让尺寸长）。"""
    p: dict = {}
    side = rng.randrange(2)
    p["side"], p["side_en"] = _SIDES["zh"][side], _SIDES["en"][side]
    p["lobe"], p["lobe_en"] = _LOBES["zh"][side], _LOBES["en"][side]
    lung = rng.randrange(3)
    p["lobe_lung"], p["lobe_lung_en"] = _LUNG_LOBES["zh"][lung], _LUNG_LOBES["en"][lung]
    echo = rng.choices([0, 1, 2], weights=[70, 20, 10])[0]
    p["echo"], p["echo_en"] = _ECHO["zh"][echo], _ECHO["en"][echo]
    wall = rng.randrange(2)
    p["wall"], p["wall_en"] = _WALLS["zh"][wall], _WALLS["en"][wall]
    dens = rng.choices([0, 1], weights=[55, 45])[0]
    p["density"], p["density_en"] = _DENSITY["zh"][dens], _DENSITY["en"][dens]
    p["tirads"] = rng.choices(["2", "3", "3", "4A"], weights=[30, 45, 15, 10])[0]
    p["clock"] = str(rng.randint(1, 12))
    p["tooth"] = rng.choice(_TEETH)
    if fdef.get("size_mm"):
        lo, hi = fdef["size_mm"]
        p["mm0"] = round(rng.uniform(lo, hi), 0)
    if fdef.get("t_score"):
        lo, hi = fdef["t_score"]
        p["t"] = round(rng.uniform(lo, hi), 1)
    return p


class FindingState:
    """一个人的一条所见：何时开始、参数、是否黏性。"""

    def __init__(self, fid: str, fdef: dict, since: date, params: dict, sticky: bool):
        self.fid, self.fdef, self.since, self.params, self.sticky = fid, fdef, since, params, sticky

    def at(self, person: Person, when: date, rng: random.Random) -> Finding | None:
        if self.sticky and when < self.since:
            return None
        years = max(0.0, (when - self.since).days / 365.25)
        params = dict(self.params)
        if "mm0" in params:
            params["mm"] = int(round(params["mm0"] + 0.6 * years))     # 结节/囊肿每年长半毫米上下
        severity = None
        if self.fdef.get("severity"):
            levels = self.fdef["severity"]
            idx = min(len(levels) - 1, int(years // 2))
            # 生活方式干预之后退回最轻一档：这是归因问题的可判定答案之一
            improved = any(e.event_type == "exercise_change" and e.start <= when
                           and (when - e.start).days > 120 for e in person.events)
            severity = levels[0] if improved else levels[idx]
        return Finding(id=self.fid, where=self.fdef["where"][0], item=self.fdef["where"][1],
                       surface=self.fdef["surface"], icpc3=self.fdef.get("icpc3"), severity=severity,
                       params=params, since=self.since)


def person_findings(person: Person, seed: int) -> list[FindingState]:
    """按人抽一次。黏性所见抽中即有一个起始日期（观察窗前后都可能）；非黏性的每次就诊再抽。"""
    rng = random.Random(f"findings:{seed}:{person.person_id}")
    start = person.weight_anchors[0][0]
    end = person.weight_anchors[-1][0]
    age_mid = person.age_at(start + (end - start) / 2)
    out: list[FindingState] = []
    for fid, fdef in _narr()["findings"].items():
        if fdef.get("condition"):
            continue                                            # 由数值决定（窦缓），不在这里抽
        if not _eligible(fdef, person, age_mid):
            continue
        rate = _rate(fdef, person, age_mid)
        sticky = fdef.get("sticky", False)
        if sticky:
            if rng.random() < rate:
                since = start + timedelta(days=rng.randint(-5 * 365, (end - start).days))
                out.append(FindingState(fid, fdef, since, _params(fdef, rng), True))
        else:
            out.append(FindingState(fid, fdef, start, _params(fdef, rng), False))
    return out


def _meets(values: dict[str, float], cond: dict) -> bool:
    for key, expr in cond.items():
        v = values.get(key)
        if v is None:
            return False
        op, num = expr[0], float(expr[1:])
        if op == "<" and not v < num:
            return False
        if op == ">" and not v > num:
            return False
    return True


def encounter_findings(person: Person, when: date, states: list[FindingState], package: str,
                       values: dict[str, float], seed: int) -> list[Finding]:
    """这次体检报告上会出现的所见：在这档套餐的科室/辅助检查范围内，且这个人此时有。"""
    rng = random.Random(f"findings:{seed}:{person.person_id}:{when.isoformat()}")
    narr = _narr()
    pkg = narr["packages"][package]
    scope = set(pkg["sections"]) | set(pkg["aux"])
    age = person.age_at(when)
    out: list[Finding] = []
    seen_slots: set[tuple[str, str | None]] = set()
    for st in states:
        where, item = st.fdef["where"]
        if where not in scope:
            continue
        aux = narr["aux"].get(where)
        if aux and (aux.get("sex") and aux["sex"] != person.sex or aux.get("min_age", 0) > age):
            continue
        if not st.sticky and rng.random() >= _rate(st.fdef, person, age):
            continue
        f = st.at(person, when, rng)
        if f is None:
            continue
        # 同一条目只放一条所见（龋齿与缺牙不同时印在"牙齿"里，选先到的）
        if (where, item) in seen_slots and item is not None:
            continue
        seen_slots.add((where, item))
        out.append(f)
    # 由数值决定的所见（心率 <60 → 窦性心动过缓）
    for fid, fdef in narr["findings"].items():
        cond = fdef.get("condition")
        if cond and fdef["where"][0] in scope and _meets(values, cond):
            out.append(Finding(id=fid, where=fdef["where"][0], item=fdef["where"][1],
                               surface=fdef["surface"], icpc3=fdef.get("icpc3")))
    return out


# ── 套餐 ────────────────────────────────────────────────────────
def choose_package(person: Person, kind: str, rng: random.Random) -> str:
    """一个人的体检档次：机构类型的权重 × 年龄（年纪大的更常买深度套餐）× 原型（慢病人群偏标准）。"""
    weights = dict(_narr()["package_weights"].get(kind, _narr()["package_weights"]["hospital"]))
    age = person.age_at(person.weight_anchors[0][0])
    if age >= 50:
        weights["premium"] *= 1.8
        weights["basic"] *= 0.7
    if age >= 65:
        # 老年人健康体检（社区卫生服务中心的免费项目）：项目少、每年一次
        weights["senior"] = weights.get("senior", 0.0) + 0.6
    if person.archetype != "healthy":
        weights["standard"] *= 1.3
    names = list(weights)
    return rng.choices(names, weights=[weights[n] for n in names])[0]


# ── 主诉 ────────────────────────────────────────────────────────
def complaint_surface(symptom_id: str, lang: str, rng: random.Random) -> Complaint:
    comp = _comp()
    if symptom_id in comp["symptoms"]:
        s = comp["symptoms"][symptom_id]
        if lang == "zh":
            text = rng.choice(s["zh"])
            code = s.get("zh_codes", {}).get(text, s["icpc3"])
            return Complaint(text=text, symptom_id=symptom_id, icpc3=code, expect="coded")
        pool = s["en"] or ([s["en_preferred"]] if s.get("en_preferred") else [])
        if pool:
            text = rng.choice(pool)
            return Complaint(text=text, symptom_id=symptom_id, icpc3=s["icpc3"], expect="coded")
        # 英文没有确认过的表面：用中文词表的英文优选词缺失的情况——退回 no-match 的自造表面
        return Complaint(text=symptom_id.replace("_", " "), symptom_id=symptom_id, icpc3=None, expect="no-match")
    f = comp["frontier"][symptom_id]
    pool = f["zh"] if lang == "zh" else (f["en"] or f["zh"])
    return Complaint(text=rng.choice(pool), symptom_id=symptom_id, icpc3=None, expect=f["expect"])


def complaints_for(person: Person, when: date, exam_type: str, lang: str, seed: int) -> list[Complaint]:
    """这次就诊的主诉。体检没有主诉；门诊/复查按原型与事件抽 0–2 条。"""
    if exam_type == "routine":
        return []
    rng = random.Random(f"cc:{seed}:{person.person_id}:{when.isoformat()}")
    comp = _comp()
    pools = comp["pools"]
    arch = pools["archetype"].get(person.archetype, pools["archetype"]["healthy"])
    scripted = [e for e in person.events if e.note == "原型剧本"]
    after = bool(scripted) and scripted[0].start <= when
    candidates = list(arch["after"] if after else arch["before"])
    for e in person.events:
        if e.note == "随机事件" and e.start - timedelta(days=3) <= when <= e.start + timedelta(days=e.duration_days + 7):
            candidates += pools["incident"].get(e.name, [])
    none_rate = arch["none_rate"] if not candidates else min(arch["none_rate"], 0.5)
    if not candidates or rng.random() < none_rate:
        return []
    n = 1 if rng.random() < 0.6 else 2
    picked = rng.sample(candidates, min(n, len(set(candidates))))
    # 主诉措辞是文档措辞层（spec.doc_lang）；症状词表的 zh/en 选择在
    # complaint_surface 里按 lang 直判，ja 走 en 池——两层一致。
    durations = comp["phrasing"][_dl(lang)]["durations"]
    out = []
    for i, sid in enumerate(dict.fromkeys(picked)):
        c = complaint_surface(sid, lang, rng)
        out.append(Complaint(text=c.text, symptom_id=c.symptom_id, icpc3=c.icpc3, expect=c.expect,
                             duration=rng.choice(durations) if i == 0 else ""))
    return out
