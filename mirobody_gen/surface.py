"""The expression layer: how an institution writes what a row means.

The simulation decides what a row means (indicator key, value, unit); layout decides where things go;
this module decides how a name is written, from `resources/surface.json`. Every choice there carries a
provenance tier:

* `observed`  - an aggregate rate distilled from the reference set (`scripts/distill_surface.py`);
* `reference` - a published standard, terminology or vendor specification;
* `field`     - a downstream consumer's bug report, not (yet) seen in the reference set;
* `generated` - proposed by a language model and accepted by a contract.

`MODE` decides which tiers a build may draw:

* `off`       - none; names print exactly as they did before this layer existed;
* `realistic` - `observed` only, at the measured rates (the default, and the only mode whose output
  supports a realism claim); an axis observed fewer than `min_support` times keeps its first choice;
* `fuzz`      - every tier, drawn uniformly at `fuzz_rate`: material for finding a consumer's failures,
  never for realism numbers.

Choices are sticky per institution (one LIS has one dictionary) and drawn from a stream of their own,
so the rest of an institution's layout is the same in every mode. A `layout.Registry` draws them once,
so set the mode before building one. Each applied choice names a hazard class, so the manifest's row
attribution is the expression trace.
"""

from __future__ import annotations

import dataclasses
import random

from . import spec
from .layout import Family, revision

MODES = ("off", "realistic", "fuzz")
MODE = "realistic"


def set_mode(mode: str) -> None:
    global MODE
    if mode not in MODES:
        raise ValueError(f"unknown surface mode {mode!r}; choose from {MODES}")
    MODE = mode


def _tiers() -> set[str]:
    return set(spec.surface()["modes"][MODE])


def allowed(choices: list[dict], keep_first: bool = False) -> list[dict]:
    """The choices this mode may draw (with `keep_first`, the canonical first one whatever its tier).
    A realistic build keeps only the first of an axis whose observations add up to less than
    `min_support`: too few to estimate the shares from."""
    out = [c for c in choices if c["tier"] in _tiers() or keep_first and c is choices[0]]
    if MODE == "realistic" and sum(c["share"] for c in out) < spec.surface()["min_support"]:
        return out[:1]
    return out


def pick(rng: random.Random, choices: list[dict]) -> dict:
    """One choice: by measured share when realistic, uniformly when fuzzing."""
    weights = [1] * len(choices) if MODE == "fuzz" else [c["share"] for c in choices]
    return rng.choices(choices, weights=weights)[0]


def _choices(axis: str) -> list[dict]:
    return allowed(spec.surface()[axis]["choices"])


def _rate(axis: str, level: str = "family_rate") -> float:
    """How often an institution uses an affix (`family_rate`), and on what share of its eligible
    names (`row_rate`)."""
    return spec.surface()["fuzz_rate"] if MODE == "fuzz" else spec.surface()[axis][level]


def hazard_classes() -> dict[str, str]:
    """Every hazard class this layer can produce -> its description."""
    s = spec.surface()
    out = {c["hazard"]: c["description"] for c in s["name_join"]["choices"] if c["hazard"]}
    out.update({s[axis]["hazard"]: s[axis]["description"]
                for axis in ("name_suffix", "name_marker", "abbr_variants", "unit_system")})
    return out


def _varies(f: Family) -> bool:
    """Only Simplified Chinese layouts vary their names: the reference set measures nothing else, and
    the assay words are Simplified."""
    return f.language == "zh-Hans"


def draw(f: Family, rng: random.Random) -> Family:
    """One institution's conventions: how it writes names (Simplified Chinese layouts) and which unit
    system it prints (every layout, drawn last so the naming draws stay where they were)."""
    if MODE == "off":
        return f
    names = _names(rng) if _varies(f) else {}
    system = pick(rng, allowed(spec.surface()["unit_system"]["languages"][f.lang_group]))["word"]
    return dataclasses.replace(f, unit_system=system, **names)


def _names(rng: random.Random) -> dict:
    join = pick(rng, _choices("name_join"))["form"]
    affixes = {}
    for axis in ("name_suffix", "name_marker"):
        choices = _choices(axis)
        on = rng.random() < _rate(axis)
        word = pick(rng, choices)["word"] if choices else ""    # drawn either way: the stream stays aligned
        if on and word:
            affixes.update({axis: word, f"{axis}_share": _rate(axis, "row_rate")})
    # Realistic builds always consult the abbreviation axis (its evidence decides); fuzz ones per institution
    own = rng.random() < spec.surface()["fuzz_rate"] or MODE == "realistic"
    return dict(name_join=join, abbr_variants="drawn" if own else "", **affixes)


def revised(f: Family, year: int) -> tuple[Family, list[str]]:
    """A template revision may change how an institution joins names. Year-over-year naming drift is
    known only from field reports, so only `fuzz` builds draw it."""
    version = revision(f, year)
    if MODE != "fuzz" or version == 0 or not _varies(f):
        return f, []
    rng = random.Random(f"surface-rev:{f.family_id}:v{version}")
    if rng.random() >= 0.5:
        return f, []
    join = pick(rng, _choices("name_join"))["form"]
    return (dataclasses.replace(f, name_join=join), ["name_join"]) if join != f.name_join else (f, [])


def _applies(f: Family, item: dict, axis: str, share: float) -> bool:
    if item["panel"] not in spec.surface()["panels"]:
        return False
    return random.Random(f"{axis}:{f.family_id}:{item['key']}").random() < share


def decorate(name: str, item: dict, f: Family) -> tuple[str, list[str]]:
    """Append the institution's assay word and send-out mark to the names its dictionary carries them on."""
    applied = []
    if f.name_suffix and _applies(f, item, "name_suffix", f.name_suffix_share):
        name += f.name_suffix
        applied.append(spec.surface()["name_suffix"]["hazard"])
    if f.name_marker and _applies(f, item, "name_marker", f.name_marker_share):
        name += f.name_marker
        applied.append(spec.surface()["name_marker"]["hazard"])
    return name, applied


def abbreviation(item: dict, f: Family) -> tuple[str, str | None]:
    """The abbreviation this institution prints for an indicator, and the hazard class when it is not
    the catalogue's: sticky per institution and indicator when its `abbr_variants` is "drawn". The class
    has no minimal-contrast toggle: the clean base prints no abbreviations to vary."""
    abbr, axis = item.get("abbr") or "", spec.surface()["abbr_variants"]
    listed = axis["indicators"].get(item["key"])
    if not f.abbr_variants or not listed:
        return abbr, None
    choices = allowed(listed, keep_first=True)
    if len(choices) < 2:
        return abbr, None
    choice = pick(random.Random(f"abbr:{f.family_id}:{item['key']}"), choices)
    return (choice["word"], axis["hazard"]) if choice["word"] != abbr else (abbr, None)


def conventional(item: dict, f: Family) -> dict | None:
    """The conventional unit (with its factor, decimals and LOINC) this layout prints an indicator in,
    or None for SI."""
    return item.get("conventional") if f.unit_system == "conventional" else None


def join_hazard(form: str) -> str | None:
    return {c["form"]: c["hazard"] for c in spec.surface()["name_join"]["choices"]}[form]


def footnotes(f: Family, marked: dict) -> list[str]:
    """The line explaining an institution's send-out mark, on a page that prints it."""
    marker = spec.surface()["name_marker"]
    return [marker["note"].format(mark=f.name_marker)] if f.name_marker and marker["hazard"] in marked else []


def toggles() -> dict:
    """Minimal-contrast toggles, one per hazard class this mode may draw: each applies to every
    eligible row, so a pair always shows the class it names."""
    def only_varying(change: dict):
        return lambda f: change if _varies(f) else {}

    out = {c["hazard"]: only_varying(dict(name_style="native(abbr)", name_join=c["form"]))
           for c in _choices("name_join") if c["hazard"]}
    for axis in ("name_suffix", "name_marker"):
        if choices := _choices(axis):
            out[spec.surface()[axis]["hazard"]] = only_varying({axis: choices[0]["word"], f"{axis}_share": 1.0})
    if MODE != "off":
        out[spec.surface()["unit_system"]["hazard"]] = lambda f: {"unit_system": "conventional"}
    return out
