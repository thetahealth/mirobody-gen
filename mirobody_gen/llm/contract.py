"""The paraphrase contract: what a model may change in a template and what it may not.

A request carries one template with its slots; a candidate is accepted only if every rule below holds.
The rules are deliberately mechanical so that a rejected candidate can be explained in one line and the
same check can run in a test without a model.

一条模板的同义改写允许改句式、语序、连接词，不允许改槽位、数字、锁定词、语言、长度量级，
不允许出现机构名、人名或编号形状，也不允许与已接受的候选重复。
"""

from __future__ import annotations

import difflib
import re
from collections import Counter
from dataclasses import dataclass, field

from ..audit import privacy
from ..layout import script_of

SLOT = re.compile(r"\{([a-z_0-9]+)\}")
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
CJK = re.compile(r"[一-鿿]")
LATIN_RUN = re.compile(r"(?:[A-Za-z]{2,}\s+){3,}[A-Za-z]{2,}")      # four or more Latin words in a row
STRAY_BRACE = re.compile(r"[{}]")
UNITS = ("mmHg", "mmol/L", "μmol/L", "umol/L", "g/L", "U/L", "ng/mL", "mm", "cm", "kg", "‰", "%")
#: Registers whose text is a coded surface, a diagnosis line, or a chief complaint whose every non-slot
#: word carries meaning (反复 / 间断 / 伴 / 加重): never paraphrased.
LOCKED_REGISTERS = ("impression", "summary", "surface", "diagnosis", "chief_complaint")
#: ASCII punctuation between two CJK characters is an artefact ("评估-并安排"), never wording.
CJK_ASCII_PUNCT = re.compile(r"[\u4e00-\u9fff][-_*#|^\\<>=+][\u4e00-\u9fff]")   # "/" and "~" are legitimate (龋齿/牙结石, 3~5)
MIN_LENGTH_RATIO, MAX_LENGTH_RATIO = 0.6, 1.6
MIN_NOVELTY = 0.15


@dataclass(frozen=True)
class Request:
    id: str                         # resource path, e.g. narratives.findings.thyroid_nodule.finding.zh
    lang: str                       # zh / en
    register: str                   # ultrasound_finding / advice / diary / chief_complaint / ...
    template: str
    slots: dict[str, list[str]] = field(default_factory=dict)
    locked: tuple[str, ...] = ()
    n: int = 3

    def to_json(self) -> dict:
        return {"id": self.id, "lang": self.lang, "register": self.register, "template": self.template,
                "slots": self.slots, "locked": list(self.locked), "n": self.n}

    @classmethod
    def from_json(cls, d: dict) -> "Request":
        return cls(id=d["id"], lang=d["lang"], register=d["register"], template=d["template"],
                   slots=dict(d.get("slots") or {}), locked=tuple(d.get("locked") or ()), n=int(d.get("n", 3)))


def slot_counts(text: str) -> Counter:
    return Counter(SLOT.findall(text))


def numbers(text: str) -> set[str]:
    return set(NUMBER.findall(text))


def _has_unit(unit: str, text: str) -> bool:
    """A unit counts only as a token: "mm" inside "Common" or "{mm}" is not the unit."""
    stripped = SLOT.sub("", text)
    if unit.isalpha():
        return re.search(rf"(?<![A-Za-z]){re.escape(unit)}(?![A-Za-z])", stripped) is not None
    return unit in stripped


def locked_terms(register: str, template: str) -> tuple[str, ...]:
    """Terms that must survive verbatim: the finding name before a colon in advice, and any unit."""
    out: list[str] = []
    if register == "advice":
        head = re.split(r"[：:]", template, maxsplit=1)[0].strip()
        if head and "{" not in head:
            out.append(head)
    out += [u for u in UNITS if _has_unit(u, template)]
    return tuple(dict.fromkeys(out))


def novelty(a: str, b: str) -> float:
    """1 − similarity ratio on the slot-stripped texts; 0 means identical."""
    strip = lambda t: SLOT.sub("", t).strip()  # noqa: E731
    return 1.0 - difflib.SequenceMatcher(None, strip(a), strip(b)).ratio()


def check(req: Request, candidate: str, accepted: list[str] = ()) -> list[str]:
    """Return the reasons a candidate fails; an empty list means it passes."""
    reasons: list[str] = []
    text = candidate.strip()
    if not text:
        return ["empty"]
    if req.register in LOCKED_REGISTERS:
        return ["locked register: not paraphrasable"]

    # 1. slots
    want, got = slot_counts(req.template), slot_counts(text)
    if want != got:
        reasons.append(f"slots differ: template {dict(want)} candidate {dict(got)}")
    if len(STRAY_BRACE.findall(text)) != 2 * sum(got.values()):
        reasons.append("stray brace")

    # 2. numbers
    extra = numbers(text) - numbers(req.template)
    if extra:
        reasons.append(f"new numbers: {sorted(extra)}")

    # 3. locked terms (checked on the slot-stripped text: "{mm}" must not count as the unit "mm")
    stripped = SLOT.sub("", text)
    missing = [t for t in req.locked if not (_has_unit(t, text) if t in UNITS else t in stripped)]
    if missing:
        reasons.append(f"locked terms missing: {missing}")
    # 3b. in English a sentence-initial slot stays sentence-initial (its value is capitalised, "{s_cap}");
    #     Chinese has no capitalisation, so "{side}肾…" may move freely
    first = SLOT.match(req.template.strip())
    if first and (req.lang == "en" or first.group(1).endswith("_cap")) and not text.startswith("{" + first.group(1) + "}"):
        reasons.append(f"sentence-initial slot {{{first.group(1)}}} moved")
    # 3c. artefacts: ASCII punctuation glued between Chinese characters; a lowercase start where the
    #     template starts with a capital
    if CJK_ASCII_PUNCT.search(text):
        reasons.append("stray punctuation between Chinese characters")
    if req.template[:1].isupper() and text[:1].islower():
        reasons.append("lowercase sentence start")

    # 4. language and script
    has_cjk = bool(CJK.search(text))
    if req.lang == "zh":
        if not has_cjk:
            reasons.append("zh candidate without CJK")
        elif LATIN_RUN.search(text):
            reasons.append("zh candidate contains a Latin sentence")
        elif CJK.search(req.template) and script_of(text) != script_of(req.template):
            reasons.append("script changed between simplified and traditional")
    elif req.lang == "en" and has_cjk:
        reasons.append("en candidate contains CJK")

    # 5. length
    ratio = len(SLOT.sub("", text)) / max(1, len(SLOT.sub("", req.template)))
    if not MIN_LENGTH_RATIO <= ratio <= MAX_LENGTH_RATIO:
        reasons.append(f"length ratio {ratio:.2f} outside [{MIN_LENGTH_RATIO}, {MAX_LENGTH_RATIO}]")

    # 6. names, institutions, identifier shapes
    if privacy.INSTITUTION.search(text):
        reasons.append("institution-like name")
    if privacy.NAME_FIELD.search(text):
        reasons.append("name field")
    if re.search(r"\d{6,}", text):
        reasons.append("identifier-like digit run")

    # 7. novelty against the template and the already accepted candidates
    if novelty(req.template, text) < MIN_NOVELTY:
        reasons.append("too close to the template")
    for other in accepted:
        if novelty(other, text) < MIN_NOVELTY:
            reasons.append("duplicate of an accepted candidate")
            break
    return reasons


def reason_key(reason: str) -> str:
    """The rule a reason belongs to, without its particulars (numbers, lists)."""
    head = reason.split(":")[0]
    return re.sub(r"\s*\d+(?:\.\d+)?\s*", " ", head).replace("outside [ , ]", "").strip()


def accept(req: Request, candidates: list[str]) -> tuple[list[str], dict[str, int]]:
    """Filter candidates in order; return (accepted, reject counts by first reason)."""
    accepted: list[str] = []
    rejects: Counter = Counter()
    for c in candidates:
        reasons = check(req, c, accepted)
        if reasons:
            rejects[reason_key(reasons[0])] += 1
        else:
            accepted.append(c.strip())
        if len(accepted) >= req.n:
            break
    return accepted, dict(rejects)
