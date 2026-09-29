"""Typed access to the resources under mirobody_gen/resources.

读 `resources/*.json`，给生成器一个有类型的入口。

这一层存在的理由是**别让 spec 的结构散落在各处**：参考区间有五种形状
（range / range_sex / upper / lower / qualitative），谁都要解释一遍的话，
迟早有一处解释错，而解释错的表现是"生成的值不合理"，很难追。
"""

from __future__ import annotations

import functools
import json
import pathlib

PACKAGE = pathlib.Path(__file__).resolve().parent
RESOURCES = PACKAGE / "resources"


@functools.lru_cache(maxsize=None)
def _load(name: str) -> dict:
    path = RESOURCES / name
    if not path.is_file():
        raise FileNotFoundError(f"缺少资源文件 {path}：包不完整，或需要先跑 scripts/ 下对应的构建脚本")
    return json.loads(path.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=None)
def indicators() -> dict[str, dict]:
    """指标键 → 目录条目。"""
    return {item["key"]: item for item in _load("indicators.json")["indicators"]}


@functools.lru_cache(maxsize=None)
def panels() -> dict[str, list[str]]:
    """套餐名 → 指标键列表，顺序与目录一致（报告上的项目顺序不是随机的）。"""
    out: dict[str, list[str]] = {}
    for item in _load("indicators.json")["indicators"]:
        out.setdefault(item["panel"], []).append(item["key"])
    return out


@functools.lru_cache(maxsize=None)
def typical_centers() -> dict[str, float]:
    """单侧参考区间项目的人群中心值。见 `scripts/build_indicators.TYPICAL_CENTER`。"""
    return _load("indicators.json").get("typical_center", {})


@functools.lru_cache(maxsize=None)
def cohort() -> dict:
    """队列设计。见 `scripts/build_cohort.py`。"""
    return _load("cohort.json")


def layout() -> dict:
    return _load("layout.json")


def hazards() -> dict:
    return _load("hazards.json")


def templates() -> dict:
    """印在纸上的固定文字。见 `resources/templates.json` 的说明。"""
    return _load("templates.json")


def fiction() -> dict:
    """虚构池：人名、地名、机构名。见 `scripts/build_fiction.py`。"""
    return _load("fiction.json")


def narratives() -> dict:
    """体检报告书的科室、所见、辅助检查与总检文字；门诊病历等的固定文字。见 `scripts/build_profile.py`。"""
    return _load("narratives.json")


def complaints() -> dict:
    """主诉与日记的症状词表（对照 mirobody 的 ICPC-3 症状轴）。见 `scripts/build_profile.py`。"""
    return _load("complaints.json")


def delivery() -> dict:
    """交付形态的分层/场景/严重度权重。见 `scripts/build_profile.py`。"""
    return _load("delivery.json")


def vocab() -> dict:
    """Machine identifiers the generator emits (vendor fields, scene and operator names, enumerations)."""
    return _load("vocab.json")


def genomics() -> dict:
    """药物基因组位点与厂商导出格式。见 `scripts/build_profile.py`。"""
    return _load("genomics.json")


def reference_bounds(key: str, sex: str) -> tuple[float | None, float | None]:
    """(下限, 上限)。没有的那一侧是 None。

    这是**判定异常的唯一依据**：生成器据此打标记，审计据此反查标记对不对。
    两边都从这里取，是可以的——它是 spec 的事实，不是某一方的算法。
    """
    ref = indicators()[key]["reference"]
    if not ref:
        return None, None
    kind = ref[0]
    if kind == "range":
        return float(ref[1]), float(ref[2])
    if kind == "range_sex":
        lo, hi = ref[1] if sex == "male" else ref[2]
        return float(lo), float(hi)
    if kind == "upper":
        return None, float(ref[1])
    if kind == "lower":
        return float(ref[1]), None
    return None, None


def reference_text(key: str, sex: str) -> str:
    """参考范围印在报告上的**默认**写法。方言（`--`/`~`/`≤`/带单位）由版式层再改写。"""
    ref = indicators()[key]["reference"]
    if not ref:
        return ""
    kind = ref[0]
    decimals = indicators()[key]["decimals"]

    def fmt(x: float) -> str:
        return f"{x:.{decimals}f}" if decimals else f"{x:g}"

    if kind == "qualitative":
        return str(ref[1])
    lo, hi = reference_bounds(key, sex)
    if kind == "upper":
        return f"<{fmt(hi)}"
    if kind == "lower":
        return f">{fmt(lo)}"
    return f"{fmt(lo)}-{fmt(hi)}"


def status_for(key: str, value: float | None, sex: str) -> str:
    """normal / high / low。定性项与无参考范围的项一律 normal。

    注意边界是**闭区间**：等于上限不算高。真实报告的口径不完全统一，
    但生成器必须只有一个口径，否则审计里"标记与参考范围一致"这条就无从判起。
    """
    if value is None:
        return "normal"
    lo, hi = reference_bounds(key, sex)
    if hi is not None and value > hi:
        return "high"
    if lo is not None and value < lo:
        return "low"
    return "normal"


def format_value(key: str, value: float) -> str:
    """按该指标的印刷有效位数格式化。审计的容差就是按这个位数推出来的。"""
    decimals = indicators()[key]["decimals"]
    return f"{value:.{decimals}f}" if decimals else f"{value:.0f}"
