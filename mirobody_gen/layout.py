"""Layout families: an institution is a sticky set of layout parameters sampled from the measured distributions.

An institution is a sticky layout; see docs/zh-CN/plan.md §3.6.3.

About three-quarters of document layouts in the real corpus occur only once, but there are also two
families with dozens of files each. What's reproduced is **the mechanism that produces this shape**,
not a "diversity coefficient":

* a number of fictional institutions, each with a fixed set of layout parameters (column group, header
  wording, reference-value wording, flag wording, where the unit goes, date format, page furniture...),
  all drawn from the measured distributions in `resources/layout.json`;
* two "big client" institutions account for a disproportionate share of documents; the rest is a long tail;
* each document from the same institution also gets a small jitter (a different print batch, one more
  or fewer columns).

Fingerprint proportions are a **product** of this mechanism. When they don't match the real values,
what to adjust is the institution count and jitter rate, not a coefficient added here (docs/zh-CN/plan.md,
lesson 5 from an earlier pass).

**Every parameter here maps to one or more named hazards** (detected in `hazards.detect`). Which
hazards a document carries is, in part, decided by its layout — a hazard isn't a label stuck on
afterward, it's the consequence of a layout choice.
"""

from __future__ import annotations

import dataclasses
import random
import re
from dataclasses import dataclass

from . import spec

#: Characters exclusive to Traditional Chinese, used to split the header-word pool into simplified and traditional.
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
    columns: tuple[str, ...]        # column roles, in printed order
    headers: dict[str, str]         # role -> header wording
    bilingual_header: bool          # bilingual Chinese/English header
    header_en: dict[str, str]       # the English row when bilingual
    reference_dialect: str          # range template, e.g. "{lo}-{hi}"
    upper_dialect: str              # one-sided upper-bound template, e.g. "<{hi}"
    lower_dialect: str              # one-sided lower-bound template, e.g. ">{lo}"
    flag_high: str
    flag_low: str
    flag_normal: str                # what a normal row prints ("" means nothing)
    flag_at: str                    # column / glued / spaced / paren / none
    unit_at: str                    # column / none / reference / reference_amp / value / header
    unit_case: str                  # as_is / lower
    power_style: str                # how 10^9/L is written
    name_style: str                 # native / native(abbr) / abbr / variant
    paren_style: str                # ascii / fullwidth
    sex_partitioned: bool           # sex-partitioned reference range printed in one cell
    subject_in_table: bool          # subject fields printed as table rows
    previous_column: bool           # has a "previous result" column
    date_labels: tuple[str, ...]
    date_format: str
    furniture: tuple[str, ...]
    signatures: tuple[str, ...]
    watermark: bool
    decimal_comma: bool
    page: str                       # a4 / a5l / letter
    font_size: float
    rules: str = "grid"             # table rules: grid / horizontal / none
    weight: float = 1.0             # relative weight of drawing this institution (big clients weigh more)
    lab_code: str = ""              # what a US-style lab slip's lab-code column prints (only used when there's a lab column)

    @property
    def lang_group(self) -> str:
        """`zh` for both Chinese scripts, `en` otherwise: the key used by bilingual resources."""
        return "en" if self.language == "en" else "zh"


# ── Drawing parameters from spec ──
def _weighted(rng: random.Random, items: list[dict], key: str = "documents"):
    return rng.choices([x["value"] for x in items], weights=[x[key] for x in items])[0]


def spellings(role: str, language: str) -> list[str]:
    roles = {**spec.layout()["column_roles"], **spec.templates().get("column_roles_extra", {})}
    same = [s for s, r in roles.items() if r == role and script_of(s) == language]
    if not same and language == "zh-Hant":
        same = [s for s, r in roles.items() if r == role and script_of(s) == "zh-Hans"]
    return sorted(same)


#: Range wordings: take only "clean" templates. One with a `. {n}` space is an OCR artifact
#: (`reference.space_inside_number`, which belongs to the image layer); one with a unit is composed
#: separately via `unit_at` and not duplicated here.
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
    """spec's date labels were gated only by "appears in at least three documents", so they're mixed in
    with OCR garbage (doubled characters), time placeholders (`T:##:##`), labels that already carry a
    colon, and labels like "date of birth" that aren't report dates at all. This filters once more."""
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
    """Where the unit prints when there's no unit column. The weights are the four unit-hazard classes'
    **document rates** in the reference corpus (resources/hazards.json): unit.missing -> none;
    unit.glued_to_value -> value; unit.glued_to_reference -> reference_amp (joined by `&`);
    unit.in_header_or_reference_only -> split 8.4:1 between reference (joined by a space) and header
    (a header-only unit only works when the whole table shares one unit, which is rare in practice).
    The four together total about 60%; the rest should have a unit column — which checks out against
    the measured share of column groups that have one (about 41%)."""
    rate = {c["name"]: c["document_rate"] * 100 for c in spec.hazards()["classes"]}
    only = rate.get("unit.in_header_or_reference_only", 9.4)
    return {"none": rate.get("unit.missing", 9.6), "value": rate.get("unit.glued_to_value", 22.0),
            "reference_amp": rate.get("unit.glued_to_reference", 18.3),
            "reference": only * 8.4 / 9.4, "header": only * 1.0 / 9.4}


def _reference_fill_rate() -> float:
    """The measured column groups have an inflated share with no reference-range column (it includes
    column groups for general exams and narrative reports). Top up lab slips with a reference-range
    column at this rate so the overall absence rate lands on the real document rate."""
    orders = _role_orders()
    total = sum(x["documents"] for x in orders)
    absent = sum(x["documents"] for x in orders if "reference" not in x["value"]) / total
    target = next((c["document_rate"] for c in spec.hazards()["classes"] if c["name"] == "reference.absent"), 0.121)
    return max(0.0, 1 - target / absent) if absent else 0.0


def _role_orders() -> list[dict]:
    """Measured column groups (by role), kept only if usable for a lab slip: has a name and a result,
    and excludes export-metadata columns and imaging-findings columns."""
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

    # Measured column groups, plus US-style lab-slip column groups (English institutions only; two
    # result columns and a lab-code column don't occur on Chinese reports)
    pool = _role_orders() + [x for x in spec.templates().get("column_sets_extra", []) if x.get("language") == language]
    columns = list(_weighted(rng, pool))
    two_up = columns.count("name") > 1
    if "reference" not in columns and rng.random() < _reference_fill_rate():
        columns.insert(columns.index("result") + 1, "reference")
    if not two_up and "result_out" not in columns:
        if "seq" not in columns and rng.random() < 0.3:
            columns.insert(0, "seq")
        if rng.random() < 0.108:                     # value.blank_column's measured document rate
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
        flag_at = "none"                             # two result columns are themselves the flag
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
    """One file's jitter within the same institution: a different print batch, a changed date format, a
    drifted unit case. Returns what changed."""
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
    """An institution's template gets revised (LIS upgrade, a new print template). Each year has a
    `rate` chance of a new revision; every slip within the same revision looks alike. The revision
    number depends only on (institution, year), never on who the visit belongs to."""
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
    """Institution -> layout family. Families are generated lazily (the pool has thousands of
    institutions; a given corpus only uses some of them); whenever the same institution is drawn, its
    layout is always the same."""

    def __init__(self, seed: int):
        self.seed = seed
        self.institutions = spec.fiction()["institutions"]
        self._cache: dict[int, Family] = {}
        self.pools: dict[tuple[str, str], list[int]] = {}
        for i, inst in enumerate(self.institutions):
            group = "en" if inst["language"] == "en" else "zh"
            self.pools.setdefault((group, inst["kind"]), []).append(i)
        # Big clients: one Chinese checkup center, one Chinese hospital (matching the real corpus's
        # two big families of 60 and 37 documents)
        self.big = {"checkup_center": self.pools[("zh", "checkup_center")][0],
                    "hospital": self.pools[("zh", "hospital")][0]}

    def family(self, index: int) -> Family:
        if index not in self._cache:
            rng = random.Random(f"family:{self.seed}:{index}")
            self._cache[index] = sample_family(rng, f"f{index:04d}", self.institutions[index])
        return self._cache[index]


def build_registry(seed: int) -> Registry:
    return Registry(seed)
