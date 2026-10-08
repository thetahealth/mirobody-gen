"""Typed access to the resources under mirobody_gen/resources.

This layer exists so the spec's structure isn't re-parsed everywhere it's used: reference intervals
come in five shapes (range / range_sex / upper / lower / qualitative), and if every caller
interpreted them itself, one of those interpretations would eventually get it wrong — surfacing only
as "the generated value doesn't make sense", which is hard to trace back.
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
        raise FileNotFoundError(f"missing resource file {path}: the package is incomplete, or its "
                                 "build script under scripts/ needs to be run first")
    return json.loads(path.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=None)
def indicators() -> dict[str, dict]:
    """Indicator key -> catalogue entry."""
    return {item["key"]: item for item in _load("indicators.json")["indicators"]}


@functools.lru_cache(maxsize=None)
def panels() -> dict[str, list[str]]:
    """Panel name -> list of indicator keys, in catalogue order (the order items print on a report isn't random)."""
    out: dict[str, list[str]] = {}
    for item in _load("indicators.json")["indicators"]:
        out.setdefault(item["panel"], []).append(item["key"])
    return out


@functools.lru_cache(maxsize=None)
def typical_centers() -> dict[str, float]:
    """Population centre values for one-sided reference intervals. See `scripts/build_indicators.TYPICAL_CENTER`."""
    return _load("indicators.json").get("typical_center", {})


@functools.lru_cache(maxsize=None)
def cohort() -> dict:
    """Cohort design. See `scripts/build_cohort.py`."""
    return _load("cohort.json")


def layout() -> dict:
    return _load("layout.json")


def hazards() -> dict:
    return _load("hazards.json")


def templates() -> dict:
    """Fixed text printed on the page. See the notes in `resources/templates.json`."""
    return _load("templates.json")


def doc_lang(lang: str) -> str:
    """The document-wording layer's language key: zh stays zh, every other group falls back to en.

    A group introduced via `--lang-mix` (e.g. ja) changes **population composition** — how many
    people in the cohort go through which language channel, how device time zones are set — not the
    document vocabulary itself. Having only zh/en vocabularies is deliberate: a new language's
    medical-document templates are a separate project at the resource layer (a third key under
    `resources/*.json`), not something a runtime flag can solve."""
    return lang if lang == "zh" else "en"


def fiction() -> dict:
    """Fictional pool: person names, place names, institution names. See `scripts/build_fiction.py`."""
    return _load("fiction.json")


def narratives() -> dict:
    """Check-up report sections, findings, auxiliary exams and summary text; clinic-note fixed text, etc.
    See `scripts/build_profile.py`."""
    return _load("narratives.json")


def complaints() -> dict:
    """Symptom vocabulary for chief complaints and diary entries (matched against mirobody's ICPC-3
    symptom axis). See `scripts/build_profile.py`."""
    return _load("complaints.json")


def delivery() -> dict:
    """Difficulty-tier/scenario/severity weights for the delivered document shapes. See `scripts/build_profile.py`."""
    return _load("delivery.json")


def llm_prompts() -> dict:
    """Versioned prompts of the optional language-model layer."""
    return _load("llm_prompts.json")


def handwriting() -> dict:
    """What a hand may write, the writing tiers, inks and paper. See `scripts/build_handwriting.py`."""
    return _load("handwriting.json")


_PARAPHRASES_ENABLED = False


def set_paraphrases(enabled: bool) -> None:
    """Switch the optional paraphrase resource on (``mirobody-gen build --paraphrase``); off by default."""
    global _PARAPHRASES_ENABLED
    _PARAPHRASES_ENABLED = enabled


@functools.lru_cache(maxsize=None)
def paraphrases() -> dict[str, list[str]]:
    """Template path → accepted paraphrases; empty when the optional resource is absent."""
    path = RESOURCES / "paraphrases.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("paraphrases", {})


def phrasings(path: str, original: str) -> list[str]:
    """The wordings available for one template: the original first, then its accepted paraphrases.

    With the resource absent or switched off this is ``[original]``, so callers that pick from it with
    their own seeded stream produce exactly the pre-paraphrase output."""
    if not _PARAPHRASES_ENABLED:
        return [original]
    return [original] + [p for p in paraphrases().get(path, []) if p != original]


def vocab() -> dict:
    """Machine identifiers the generator emits (vendor fields, scene and operator names, enumerations)."""
    return _load("vocab.json")


def genomics() -> dict:
    """Pharmacogenomic sites and vendor export formats. See `scripts/build_profile.py`."""
    return _load("genomics.json")


def reference_bounds(key: str, sex: str) -> tuple[float | None, float | None]:
    """(lower bound, upper bound). The side that doesn't exist is None.

    This is **the sole basis for deciding abnormal**: the generator flags against it, and the audit
    checks those flags against it too. Both sides reading from here is fine — it's a fact of the
    spec, not either side's own algorithm.
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
    """The **default** wording of a reference range as printed on a report. Dialect variants
    (`--`/`~`/`<=`/with units) are rewritten later by the layout layer."""
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
    """normal / high / low. Qualitative items and items with no reference range are always normal.

    Note the bounds are a **closed interval**: equal to the upper bound doesn't count as high. Real
    reports aren't fully consistent on this convention, but the generator must pick exactly one, or
    the audit's "flag matches the reference range" check has nothing to judge against.
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
    """Format to this indicator's printed significant digits. The audit's tolerance is derived from this same figure."""
    decimals = indicators()[key]["decimals"]
    return f"{value:.{decimals}f}" if decimals else f"{value:.0f}"
