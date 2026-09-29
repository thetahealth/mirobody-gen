"""Layout families: an institution is a sticky set of layout parameters sampled from the measured distributions.

版式家族：机构 = 一套粘性的版式。docs/zh-CN/plan.md §3.6.3。

真实语料里约四分之三的文档版式只出现一次，但也有两个几十份的大家族。要复现的是
**产生这种形状的机制**，不是一个"多样性系数"：

* 若干虚构机构，每个机构一套固定的版式参数（列组、列头写法、参考值写法、标记写法、
  单位放哪、日期格式、页面构件……），这些参数都从 `resources/layout.json` 的实测分布里抽；
* 两个"大客户"机构占的文档多，其余是长尾；
* 同一机构的每份文档还有小抖动（换了打印批次、多一列少一列）。

指纹比例是这个机制的**产物**。对不上真实值时，要改的是机构数与抖动率，不是在这里加系数
（docs/zh-CN/plan.md 旧教训第 5 条）。

**这里的每个参数都对应一个或几个具名陷阱**（认定在 `hazards.detect`）。一份文档带哪些陷阱，
一部分就是由它的版式决定的——陷阱不是贴上去的标签，是版式选择的后果。
"""

from __future__ import annotations

import dataclasses
import random
import re
from dataclasses import dataclass

from . import spec

#: 繁体专用字。用来把列头词表分成简/繁两堆。
_TRAD = set("項結參單檢驗標誌異範區狀態備註測樣報醫衛價與類統計數據")
_CJK = re.compile(r"[一-鿿]")


def script_of(text: str) -> str:
    if not _CJK.search(text):
        return "en"
    return "zh-Hant" if any(ch in _TRAD for ch in text) else "zh-Hans"


@dataclass(frozen=True)
class Family:
    family_id: str
    institution: str
    kind: str                       # hospital / checkup_center / lab / clinic / app_export
    language: str                   # zh-Hans / zh-Hant / en
    fmt: str                        # pdf / xlsx / csv
    columns: tuple[str, ...]        # 列角色，顺序即印刷顺序
    headers: dict[str, str]         # 角色 → 列头写法
    bilingual_header: bool          # 中英双表头
    header_en: dict[str, str]       # 双表头时的英文行
    reference_dialect: str          # 区间模板，如 "{lo}-{hi}"
    upper_dialect: str              # 单侧上限模板，如 "<{hi}"
    lower_dialect: str              # 单侧下限模板，如 ">{lo}"
    flag_high: str
    flag_low: str
    flag_normal: str                # 正常行印什么（"" 表示不印）
    flag_at: str                    # column / glued / spaced / paren / none
    unit_at: str                    # column / none / reference / reference_amp / value / header
    unit_case: str                  # as_is / lower
    power_style: str                # 10^9/L 的写法
    name_style: str                 # native / native(abbr) / abbr / variant
    paren_style: str                # ascii / fullwidth
    sex_partitioned: bool           # 性别分层参考范围印在一格里
    subject_in_table: bool          # 受检者字段印成表格行
    previous_column: bool           # 带"上次结果"列
    date_labels: tuple[str, ...]
    date_format: str
    furniture: tuple[str, ...]
    signatures: tuple[str, ...]
    watermark: bool
    decimal_comma: bool
    page: str                       # a4 / a5l / letter
    font_size: float
    rules: str = "grid"             # 表格线：grid / horizontal / none
    weight: float = 1.0             # 抽中这个机构的相对权重（大客户权重大）
    lab_code: str = ""              # 美式化验单的实验室代码列印什么（有 lab 列时才用）

    @property
    def lang_group(self) -> str:
        """`zh` for both Chinese scripts, `en` otherwise: the key used by bilingual resources."""
        return "en" if self.language == "en" else "zh"


# ── 从 spec 抽参数 ────────────────────────────────────────────────
def _weighted(rng: random.Random, items: list[dict], key: str = "documents"):
    return rng.choices([x["value"] for x in items], weights=[x[key] for x in items])[0]


def spellings(role: str, language: str) -> list[str]:
    roles = {**spec.layout()["column_roles"], **spec.templates().get("column_roles_extra", {})}
    same = [s for s, r in roles.items() if r == role and script_of(s) == language]
    if not same and language == "zh-Hant":
        same = [s for s, r in roles.items() if r == role and script_of(s) == "zh-Hans"]
    return sorted(same)


#: 区间写法：只取"干净"的模板。带 `. {n}` 空格的是 OCR 痕迹（`reference.space_inside_number`，
#: 属于图像层），带单位的由 `unit_at` 另行组合，不在这里重复。
def _range_dialects() -> list[dict]:
    out = []
    for x in spec.layout()["reference_dialects"]:
        v = x["value"]
        if v.count("{n}") != 2 or ". " in v or re.search(r"[A-Za-z%&*^/]", v.replace("{n}", "")):
            continue
        out.append({"value": v.replace("{n}", "{lo}", 1).replace("{n}", "{hi}", 1),
                    "documents": x["documents"]})
    return out


def _one_sided(sign: str) -> list[dict]:
    out = []
    for x in spec.layout()["reference_dialects"]:
        v = x["value"]
        if v.count("{n}") == 1 and sign in v and not re.search(r"[A-Za-z%&*^/]", v.replace("{n}", "")):
            out.append({"value": v.replace("{n}", "{hi}" if sign in "<≤" else "{lo}"),
                        "documents": x["documents"]})
    return out


_FLAG_PAIRS = {
    "zh": [(("↑", "↓"), 117), (("偏高", "偏低"), 52), (("H", "L"), 9), (("高", "低"), 2),
           (("升高", "降低"), 2)],
    "en": [(("H", "L"), 9), (("High", "Low"), 3), (("↑", "↓"), 20), (("A", "A"), 1)],
}
_DATE_OK = {"zh-Hans": lambda v: "DD/MM" not in v and "MM/DD" not in v,
            "zh-Hant": lambda v: "DD/MM" not in v and "MM/DD" not in v,
            "en": lambda v: "年" not in v}


_DATE_WORD = re.compile(r"日期|时间|時間|Date|Time|Collected|Reported|Printed|Received")
_NOT_REPORT_DATE = re.compile(r"出院|出生|入院|Birth|DOB")


_DATE_ROLE = [("采", "collected"), ("收", "received"), ("接收", "received"), ("Received", "received"),
              ("Collection", "collected"), ("检验", "tested"),
              ("检测", "tested"), ("审核", "verified"), ("打印", "printed"), ("报告", "reported"),
              ("報告", "reported"), ("体检", "collected"), ("检查", "collected"),
              ("Collected", "collected"), ("Printed", "printed"), ("Reported", "reported"),
              ("Report", "reported")]


def date_role(label: str) -> str:
    for needle, role in _DATE_ROLE:
        if needle in label:
            return role
    return "reported"


def date_label_pool(language: str) -> list[dict]:
    """spec 里的日期标签过闸时只按"至少三份文档出现"筛过，混着 OCR 乱码（叠字）、
    时间占位（`T:##:##`）、自带冒号的写法与"出生日期"这类不是报告日期的标签。这里再筛一道。"""
    out: dict[str, int] = {}
    for x in spec.layout()["date_labels"]:
        label = x["value"].rstrip(":：").strip()
        if script_of(label) != language or "#" in label or not _DATE_WORD.search(label):
            continue
        if _NOT_REPORT_DATE.search(label) or re.search(r"(.)\1(.)\2", label):
            continue
        out[label] = out.get(label, 0) + x["documents"]
    return [{"value": k, "documents": v} for k, v in sorted(out.items())]


def _unit_at_weights() -> dict[str, float]:
    """没有单位列时，单位印在哪里。权重就是四类单位陷阱在参考集里的**文档率**（resources/hazards.json）：
    unit.missing → none；unit.glued_to_value → value；unit.glued_to_reference → reference_amp（`&` 相连）；
    unit.in_header_or_reference_only → reference（空格相连）与 header 按 8.4 : 1 分（表头单位只在整张表
    同一单位时才成立，实际很少）。四者合计约六成，余下该有单位列——与实测列组里带单位列的份额（约 41%）两相印证。"""
    rate = {c["name"]: c["document_rate"] * 100 for c in spec.hazards()["classes"]}
    only = rate.get("unit.in_header_or_reference_only", 9.4)
    return {"none": rate.get("unit.missing", 9.6), "value": rate.get("unit.glued_to_value", 22.0),
            "reference_amp": rate.get("unit.glued_to_reference", 18.3),
            "reference": only * 8.4 / 9.4, "header": only * 1.0 / 9.4}


def _reference_fill_rate() -> float:
    """实测列组里没有参考范围列的份额偏高（含一般检查、叙述型报告的列组）。
    按这个比例给检验单补上参考范围列，使整体缺失率落在真实文档率上。"""
    orders = _role_orders()
    total = sum(x["documents"] for x in orders)
    absent = sum(x["documents"] for x in orders if "reference" not in x["value"]) / total
    target = next((c["document_rate"] for c in spec.hazards()["classes"] if c["name"] == "reference.absent"), 0.121)
    return max(0.0, 1 - target / absent) if absent else 0.0


def _role_orders() -> list[dict]:
    """实测列组（按角色），只留检验单能用的：有名称有结果，不含导出元数据列与影像所见列。"""
    out = []
    for x in spec.layout()["column_sets_by_role"]:
        roles = x["value"]
        if "name" in roles and "result" in roles and not any(
                r.startswith("meta_") or r in ("finding", "analyte+", "examiner") for r in roles):
            out.append(x)
    return out


def sample_family(rng: random.Random, family_id: str, institution: dict,
                  weight: float = 1.0) -> Family:
    layout = spec.layout()
    language = "en" if institution["language"] == "en" else (
        "zh-Hant" if rng.random() < 0.06 else "zh-Hans")
    lang_group = "en" if language == "en" else "zh"

    # 实测列组 + 美式化验单的列组（只给英文机构；两列结果、实验室代码列在中文报告里没有）
    pool = _role_orders() + [x for x in spec.templates().get("column_sets_extra", []) if x.get("language") == language]
    columns = list(_weighted(rng, pool))
    two_up = columns.count("name") > 1
    if "reference" not in columns and rng.random() < _reference_fill_rate():
        columns.insert(columns.index("result") + 1, "reference")
    if not two_up and "result_out" not in columns:
        if "seq" not in columns and rng.random() < 0.3:
            columns.insert(0, "seq")
        if rng.random() < 0.108:                     # value.blank_column 的实测文档率
            columns.insert(rng.randrange(2, len(columns) + 1), rng.choice(["note", "method"]))
        if rng.random() < 0.075:                     # value.multiple_per_row
            columns.insert(columns.index("result") + 1, "previous")
        if rng.random() < 0.048:                     # table.row_label_as_column
            columns.insert(0, "category")
        if "abbr" not in columns and rng.random() < 0.12:
            columns.insert(columns.index("name") + 1, "abbr")

    headers, header_en = {}, {}
    for role in dict.fromkeys(columns):
        if role == "previous":
            headers[role] = spec.templates()["previous_header"][language]
            header_en[role] = spec.templates()["previous_header"]["en"]
            continue
        pool = spellings(role, language)
        headers[role] = rng.choice(pool) if pool else role
        en_pool = spellings(role, "en")
        header_en[role] = rng.choice(en_pool) if en_pool else role

    unit_weights = _unit_at_weights()
    unit_at = "column" if "unit" in columns else rng.choices(list(unit_weights), weights=list(unit_weights.values()))[0]
    flag_at = "column" if "flag" in columns else rng.choices(
        ["none", "glued", "spaced", "paren"], weights=[40, 30, 20, 10])[0]
    if "result_out" in columns:
        flag_at = "none"                             # 两列结果本身就是标记
    (hi, lo) = rng.choices([p for p, _ in _FLAG_PAIRS[lang_group]],
                           weights=[w for _, w in _FLAG_PAIRS[lang_group]])[0]
    flag_normal = ""
    if flag_at == "column" and rng.random() < 0.35:
        flag_normal = {"zh": "正常", "en": rng.choice(["Normal", "N"])}[lang_group]

    dates = date_label_pool(language)
    n_dates = rng.choices([1, 2, 3, 4], weights=[35, 40, 18, 7])[0]
    date_labels: list[str] = []
    roles: set[str] = set()
    for _ in range(60):
        if len(date_labels) >= min(n_dates, len(dates)):
            break
        label = _weighted(rng, dates)
        if label not in date_labels and date_role(label) not in roles:
            date_labels.append(label)
            roles.add(date_role(label))
    formats = [x for x in layout["date_formats"] if _DATE_OK[language](x["value"])
               and x["value"].startswith(("Y", "D", "M")) and "HH:MM" not in x["value"]]

    furniture = sorted({_weighted(rng, layout["furniture_kinds"], "occurrences")
                        for _ in range(rng.randint(1, 4))})
    signatures = tuple(sorted({_weighted(rng, layout["signature_roles"])
                               for _ in range(rng.randint(0, 2))})) if lang_group == "zh" else \
        tuple(rng.sample(["Performed By", "Verified By"], rng.randint(0, 2)))

    return Family(
        family_id=family_id, institution=institution["name"], kind=institution["kind"],
        language=language,
        fmt=rng.choices(["pdf", "xlsx", "csv"], weights=[74, 17, 9])[0],
        columns=tuple(columns), headers=headers,
        bilingual_header=lang_group == "zh" and rng.random() < 0.18, header_en=header_en,
        reference_dialect=_weighted(rng, _range_dialects()),
        upper_dialect=_weighted(rng, _one_sided("<") + _one_sided("≤")),
        lower_dialect=_weighted(rng, _one_sided(">")),
        flag_high=hi, flag_low=lo, flag_normal=flag_normal, flag_at=flag_at,
        unit_at=unit_at,
        unit_case="lower" if rng.random() < 0.12 else "as_is",
        power_style=rng.choices(list(spec.templates()["power_styles"]["_weights"]),
                                weights=list(spec.templates()["power_styles"]["_weights"].values()))[0],
        name_style=rng.choices(["native", "native(abbr)", "abbr", "variant"],
                               weights=[50, 20, 10, 20])[0],
        paren_style="fullwidth" if lang_group == "zh" and rng.random() < 0.4 else "ascii",
        sex_partitioned=rng.random() < 0.06,
        subject_in_table=rng.random() < 0.188,
        previous_column="previous" in columns,
        date_labels=tuple(date_labels),
        date_format=_weighted(rng, formats),
        furniture=tuple(furniture), signatures=signatures,
        watermark=rng.random() < 0.148,
        decimal_comma=lang_group == "en" and rng.random() < 0.06,
        page=rng.choices(["a4", "a5l", "letter"],
                         weights=[55, 45, 0] if lang_group == "zh" else [50, 10, 40])[0],
        font_size=rng.choice([8.5, 9, 9.5, 10, 10.5]),
        rules=rng.choices(["grid", "horizontal", "none"], weights=[45, 40, 15])[0],
        weight=weight,
        lab_code=rng.choice(spec.templates()["lab_code_pool"]) if "lab" in columns else "",
    )


def jitter(rng: random.Random, f: Family) -> tuple[Family, list[str]]:
    """同一机构的单份抖动：换了打印批次、改了日期格式、单位大小写漂了。返回改了什么。"""
    changes: dict = {}
    if rng.random() < 0.10:
        formats = [x for x in spec.layout()["date_formats"] if _DATE_OK[f.language](x["value"])
                   and x["value"].startswith(("Y", "D", "M")) and "HH:MM" not in x["value"]]
        changes["date_format"] = _weighted(rng, formats)
    if rng.random() < 0.06:
        changes["unit_case"] = "lower" if f.unit_case == "as_is" else "as_is"
    if rng.random() < 0.05 and "seq" not in f.columns and f.columns.count("name") == 1:
        changes["columns"] = ("seq",) + f.columns
        changes["headers"] = {**f.headers, "seq": (spellings("seq", f.language) or ["#"])[0]}
    if rng.random() < 0.05:
        changes["watermark"] = not f.watermark
    return (dataclasses.replace(f, **changes), sorted(changes)) if changes else (f, [])


def revised(f: Family, year: int, rate: float = 0.25) -> tuple[Family, list[str]]:
    """机构的模板会改版（LIS 升级、换了打印模板）。每年以 `rate` 的概率改一版，
    同一版本内的所有单子长得一样。改版号只由（机构, 年份）决定，与谁去看病无关。"""
    version = sum(1 for y in range(2018, year + 1)
                  if random.Random(f"rev:{f.family_id}:{y}").random() < rate)
    if version == 0:
        return f, []
    rng = random.Random(f"rev:{f.family_id}:v{version}")
    changes: dict = {}
    for _ in range(rng.randint(1, 2)):
        what = rng.choice(["date_format", "reference_dialect", "header", "rules", "font_size", "flags"])
        if what == "date_format":
            formats = [x for x in spec.layout()["date_formats"] if _DATE_OK[f.language](x["value"])
                       and x["value"].startswith(("Y", "D", "M")) and "HH:MM" not in x["value"]]
            changes["date_format"] = _weighted(rng, formats)
        elif what == "reference_dialect":
            changes["reference_dialect"] = _weighted(rng, _range_dialects())
        elif what == "header":
            role = rng.choice([c for c in f.columns if c in ("name", "result", "reference", "unit", "flag")])
            pool = spellings(role, f.language)
            if pool:
                changes["headers"] = {**f.headers, role: rng.choice(pool)}
        elif what == "rules":
            changes["rules"] = rng.choice(["grid", "horizontal", "none"])
        elif what == "font_size":
            changes["font_size"] = rng.choice([8.5, 9, 9.5, 10, 10.5])
        elif what == "flags" and f.flag_at != "none":
            group = f.lang_group
            (hi, lo) = rng.choices([p for p, _ in _FLAG_PAIRS[group]],
                                   weights=[w for _, w in _FLAG_PAIRS[group]])[0]
            changes.update(flag_high=hi, flag_low=lo)
    return dataclasses.replace(f, **changes), [f"revision:v{version}"] + sorted(changes)


class Registry:
    """机构 → 版式家族。家族按需生成（池子有上千家机构，一批语料只用到其中一部分），
    同一机构无论何时被抽到，版式都一样。"""

    def __init__(self, seed: int):
        self.seed = seed
        self.institutions = spec.fiction()["institutions"]
        self._cache: dict[int, Family] = {}
        self.pools: dict[tuple[str, str], list[int]] = {}
        for i, inst in enumerate(self.institutions):
            group = "en" if inst["language"] == "en" else "zh"
            self.pools.setdefault((group, inst["kind"]), []).append(i)
        # 大客户：中文体检中心、中文医院各一家（对应真实语料里 60 份与 37 份的两个大家族）
        self.big = {"checkup_center": self.pools[("zh", "checkup_center")][0],
                    "hospital": self.pools[("zh", "hospital")][0]}

    def family(self, index: int) -> Family:
        if index not in self._cache:
            rng = random.Random(f"family:{self.seed}:{index}")
            self._cache[index] = sample_family(rng, f"f{index:04d}", self.institutions[index])
        return self._cache[index]


def build_registry(seed: int) -> Registry:
    return Registry(seed)
