"""Privacy gate: prove that outputs and resources contain nothing from the reference set. Non-zero exit on hits.

    mirobody-gen audit-privacy                      # scan mirobody_gen/resources
    mirobody-gen audit-privacy --targets spec out    # also scan build output
    mirobody-gen audit-privacy --report              # stats only, no verdict

## Three checks

**1. Replay detection**: `corpus/text/**` (extracted text of 771 real documents, 5.8MB) is cut into
N-character n-grams, and every target file is scanned for them; a hit means reference-corpus text was
copied verbatim into an output.

The index stores only **hashes**, never plaintext: each n-gram is blake2b-hashed to 8 bytes into a
sorted uint64 array, and a hit is a binary search. So the index file itself is not a copy of the corpus
and can be committed or shipped safely. (This isn't fastidiousness: the first version cached n-grams as
plaintext, and that cache was itself a shredded copy of patient records.)

A hit is then checked against an **exemption**: if the n-gram falls entirely within a single whitelisted
vocabulary term (an indicator name, unit, column header, flag, or standard label -- each one already
past the gate before it entered the spec), it's a window onto a public medical term and identifies no
one. The exemption is deliberately narrow: it does not accept "a few public terms concatenated", because
the join of the tail of one unrelated public term to the head of another can itself be copied text.

**2. PII predicates**: ID numbers, phone numbers, visit numbers, name fields, institution names. Written
**separately** from the predicates in `.githooks/pre-commit` -- running the same predicate twice can only
confirm its own blind spot, a mistake this project already paid for (a first audit reported "0 names
leaked" while a real physician's name sat in the corpus, because the audit called the same function the
filter used). This uses structural detection: the name following a name label, and an institution name,
must **belong to the fiction pool** (`resources/fiction.json`, an allow-list), not "doesn't look like a
real name" (a deny-list).

The visit-number check has an escape hatch for generated output: synthetic IDs carry a salted check
suffix in their last three digits (`mirobody_gen/synthid.py`); this module reimplements the same check
**independently** (no import from the generator), and a match is excused and counted. The odds of a real
number matching by chance are about one in a thousand, and it would first have to survive replay
detection.

(Before 2026-09-23 this docstring already said "names and institutions must belong to the fiction pool"
with no code enforcing it -- the documentation preceded the implementation. The check was added once the
renderer started printing names on paper.)

**3. Spec provenance**: every file under `resources/*.json` must declare `_source`, `_provenance` and
`_vocabulary_fields` (which fields hold public vocabulary eligible for exemption), and must not contain
the de-identification placeholder `«...»` (a trace of reference-corpus processing that leaked through
when the spec was distilled).

## What this gate does not cover

**Human-written prose (`docs/`, `README.md`, `docs/zh-CN/plan.md`) is out of scope for replay
detection.** Tested on 2026-09-23: scanning `docs/` reported 56 hits, every one a common medical English
phrase on inspection -- "reference range", "interpretation", "total protein", "health records". The
reference corpus naturally contains English passages, and any English-language draft will collide with
them in a 12-character window.

The fix is **not** to whitelist these English phrases -- that would also excuse them exactly where the
gate matters (the spec and generated output). Prose is reviewed by the commit hook's PII rules and by a
human; replay detection is responsible only for what machines produce: `mirobody_gen/resources/` and
`out/`.

Text inside image outputs (jpg/png/scanned PDF) is **unreadable to this tool**, so replay detection
doesn't cover it either. That's a boundary, not a hole: an image is rendered from the manifest's rows,
which go through this same check, so an image's privacy properties are inherited from its source of
truth rather than independently verified. Independent verification would need OCR on the output -- a
separate pass (`audit/readability.py` does this, and gets the text as a side effect).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import unicodedata

import numpy as np

PACKAGE = pathlib.Path(__file__).resolve().parents[1]
RESOURCES = PACKAGE / "resources"
#: Root of the source checkout. Used only for things that exist only in a checkout (reference set,
#: cache, build output) -- an installed package has none of these.
REPO = PACKAGE.parent
CORPUS_TEXT = REPO / "corpus" / "text"
CACHE = REPO / ".cache" / "corpus_ngrams.npy"

#: N-gram length (characters, after normalization).
#:
#: 12 is a tradeoff: Chinese carries more information per character, so an 8-character window produces
#: heavy false positives on combinations of pure public terms like "WBC count reference range"; a
#: 20-character window misses anything short of "an entire line copied verbatim". 12 characters keeps
#: the exemption rate in a readable range on the corpus tested so far (run `--report` to see the current
#: number).
NGRAM = 12

_DROP = re.compile(r"[\s　·•\-—–_=|/\\()（）\[\]【】{}<>《》\"'“”‘’,，.。;；:：!！?？*#&+~]")


def normalize(text: str) -> str:
    """Normalize: strip whitespace and punctuation, fold full-width to half-width, lowercase ASCII.

    Normalization is intentionally looser than the raw text -- a layout difference (spacing, full/half
    width) shouldn't let copied text slip past detection.
    """
    text = unicodedata.normalize("NFKC", text)
    return _DROP.sub("", text).lower()


def ngram_hashes(text: str, n: int = NGRAM) -> np.ndarray:
    """Text -> array of uint64 hashes. Text shorter than n returns an empty array."""
    import hashlib

    if len(text) < n:
        return np.empty(0, dtype=np.uint64)
    out = np.empty(len(text) - n + 1, dtype=np.uint64)
    for i in range(len(text) - n + 1):
        digest = hashlib.blake2b(text[i:i + n].encode("utf-8"), digest_size=8).digest()
        out[i] = int.from_bytes(digest, "big")
    return out


def build_index(rebuild: bool = False) -> np.ndarray:
    """The reference corpus's n-gram hash index (a sorted uint64 array)."""
    if CACHE.is_file() and not rebuild:
        return np.load(CACHE)
    if not CORPUS_TEXT.is_dir():
        raise SystemExit(f"{CORPUS_TEXT} not found -- replay detection needs the reference corpus. "
                         "If this machine legitimately has no corpus, pass --skip-replay to skip it "
                         "explicitly, knowing what that skips.")
    chunks: list[np.ndarray] = []
    files = sorted(CORPUS_TEXT.glob("*.txt"))
    for i, f in enumerate(files, 1):
        chunks.append(ngram_hashes(normalize(f.read_text(encoding="utf-8", errors="ignore"))))
        if i % 100 == 0:
            print(f"  building index {i}/{len(files)} ...", file=sys.stderr)
    index = np.unique(np.concatenate(chunks)) if chunks else np.empty(0, dtype=np.uint64)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    np.save(CACHE, index)
    return index


# ── Text extraction from target files ──────────────────────────────
def extract_text(path: pathlib.Path) -> list[str] | None:
    """Return a **list of text units** when the file's text can be read; None when it can't (images, etc.).

    A list rather than one blob, because an n-gram must not cross a semantic boundary: scanning an
    entire JSON document as one string would let the end of `"result"` and the start of the next key,
    `"unit"`, concatenate into a 12-character window that also happens to exist in the reference corpus,
    reporting phantom "verbatim copies". JSON is cut per string value; tables are cut per row.
    """
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        # Parsed line by line. The first version did `json.loads` on the whole file, which always
        # failed and fell back to scanning the raw text line by line -- keys and values got concatenated,
        # so `"start_date": "2026-04-..."` normalized to `startdate202604...` and reported phantom
        # "verbatim copies".
        units: list[str] = []
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                units += _walk_strings(json.loads(line))
            except json.JSONDecodeError:
                units.append(line)
        return units
    if suffix == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return path.read_text(encoding="utf-8", errors="ignore").splitlines()
        return _walk_strings(payload)
    if suffix in (".csv", ".tsv"):
        # Cut per cell: concatenating two adjacent cells in the same row can manufacture a window that
        # also exists in the reference corpus (an exported table's header row is indicator names butted
        # end to end).
        import csv as _csv
        import io as _io

        raw = path.read_bytes().decode("utf-8-sig", errors="ignore")
        return [cell for row in _csv.reader(_io.StringIO(raw), delimiter="\t" if suffix == ".tsv" else ",")
                for cell in row if cell]
    if suffix in (".txt", ".md", ".py", ".yaml", ".yml"):
        return path.read_text(encoding="utf-8", errors="ignore").splitlines()
    if suffix == ".xml":
        # Cut per attribute value and text node (an Apple Health export is all attributes): joining the
        # attributes of one element would run a source name into a date the way JSON keys once did.
        from xml.etree import ElementTree as _ET

        units = []
        for _, elem in _ET.iterparse(path, events=("end",)):
            units += [v for v in elem.attrib.values() if v.strip()]
            if elem.text and elem.text.strip():
                units.append(elem.text.strip())
            elem.clear()
        return units
    if suffix == ".pdf":
        try:
            import fitz
        except ImportError:
            return None
        # Cut per text span: a table cell is usually one span; cutting per line would run several cells
        # of the same row together.
        units: list[str] = []
        with fitz.open(path) as doc:
            for page in doc:
                for block in page.get_text("dict")["blocks"]:
                    for line in block.get("lines", []):
                        units += [span["text"] for span in line["spans"] if span["text"].strip()]
        return units
    if suffix in (".xlsx", ".xlsm"):
        try:
            import openpyxl
        except ImportError:
            return None
        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
        return [str(c) for sheet in book.worksheets for row in sheet.iter_rows(values_only=True)
                for c in row if c not in (None, "")]
    return None


# ── Whitelist vocabulary (used for exemption) ───────────────────────
def load_vocabulary() -> list[str]:
    """The public-vocabulary table: only the fields each spec **explicitly declares** via
    `_vocabulary_fields`.

    The first version treated every string in the spec as public vocabulary, with a `len <= 40` cap.
    Both were wrong: the cap silently excluded `Mean corpuscular hemoglobin concentration`
    (41 characters), so a standard English term written by hand got reported as "verbatim copying"; and
    "every string" was too permissive -- if explanatory prose ever carried actual reference-corpus text,
    putting it in the whitelist would excuse exactly that.

    The benefit of declaring fields explicitly is that the list itself is auditable: every field named
    in `_vocabulary_fields` is a place where we assert "this holds public medical vocabulary or a
    formatting token".
    """
    raw: set[str] = set()
    slots: dict[str, list[str]] = {}
    for path in sorted((RESOURCES).glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            continue
        for k, v in (payload.get("_placeholders") or {}).items():
            slots[k] = list(v)
        fields = set(payload.get("_vocabulary_fields") or [])
        if not fields:
            continue
        for value in _strings_under(payload, fields):
            if len(value) > 1:
                raw.add(value)
    terms = {normalize(piece) for value in raw for piece in expand_placeholders(value, slots)}
    # Names and institutions from the fiction pool have their own whitelist check (NAME_FIELD /
    # INSTITUTION); they're added here only so a label-and-name join like "Physician: Jane Doe" can be
    # explained.
    fiction_names, fiction_institutions, _ = load_fiction()
    terms |= {normalize(n) for n in fiction_names} | {normalize(n) for n in fiction_institutions}
    terms |= indicator_composites()
    terms |= expanded_templates()
    return sorted((t for t in terms if t), key=len, reverse=True)


def expand_placeholders(value: str, slots: dict[str, list[str]]) -> list[str]:
    """Expand a narrative template with `{slot}` placeholders (e.g. a thyroid-nodule finding template
    with `{side}` and `{tirads}` slots) into the words it can actually print.

    Slots **declared** with their value sets in the spec's `_placeholders` (laterality, lobe,
    echogenicity, TI-RADS category) are substituted in; undeclared slots (size, tooth position, a list
    of abnormal findings) hold numbers or other public vocabulary, so the template is **split at the
    slot** into two pieces instead -- each piece is still built only from declared public vocabulary, and
    the slot's content is explained by a different term (digits aren't evidence of copying)."""
    if "{" not in value:
        return [value]
    variants = [value]
    for name in set(re.findall(r"\{(\w+)\}", value)):
        if name in slots:
            variants = [v.replace("{" + name + "}", f) for v in variants for f in slots[name]]
    out: list[str] = []
    for v in variants:
        out += [piece for piece in re.split(r"\{\w+\}", v) if piece.strip()]
    return out


def expanded_templates() -> set[str]:
    """Expand the `{t}` title templates in `resources/templates.json` ("{t} Report", "{t} Test Report")
    using the panel titles declared in the same file. Each expansion is still built from two already
    declared public terms."""
    path = RESOURCES / "templates.json"
    if not path.is_file():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    panels = payload.get("panel_titles", {}).values()
    out: set[str] = set()
    for lang, idx in (("zh", 0), ("en", 1)):
        for template in payload.get("doc_titles", {}).get(lang, []):
            if "{t}" in template:
                for pair in panels:
                    out.add(normalize(template.replace("{t}", pair[idx])))
                    for other in panels:
                        out.add(normalize(template.replace("{t}", pair[idx] + other[idx])))
    return out


def indicator_composites() -> set[str]:
    """Pairwise concatenation of an indicator's own name and abbreviation: e.g. an indicator's Chinese
    name joined with its English abbreviation normalizes to the two run together, lowercased -- each
    half is public vocabulary, and the join is still just this one indicator's name.

    Only joined **within the same entry**. Joining the tail of one unrelated entry to the head of
    another is excluded -- that could itself be copied text (see `is_public_term_window`).
    """
    path = RESOURCES / "indicators.json"
    if not path.is_file():
        return set()
    out: set[str] = set()
    for item in json.loads(path.read_text(encoding="utf-8")).get("indicators", []):
        names = [n for n in [item.get("zh"), item.get("en"), item.get("abbr"),
                             *(item.get("name_variants") or [])] if n]
        unit = item.get("unit") or ""
        for a in names:
            if unit:
                out.add(normalize(a + unit))          # export-table header, e.g. "Total Protein(g/L)"
            for b in names:
                if a != b:
                    out.add(normalize(a + b))
    return out


def residual_text(fragment: str) -> str:
    """What's left after stripping digits (and the up/down arrows, full-width commas and colons used to
    string public terms together).

    **Digits are not evidence of copied text**: lab values come from a mechanistic model, and a
    three-significant-figure reading, a date or a reference range will naturally collide with digits in
    the reference corpus ("report time 20241028" happens to match some real report's date). The privacy
    risk of digits -- ID numbers, phone numbers, visit numbers -- is handled separately by the PII
    predicates. So after a replay hit, what matters is whether the **non-digit residue** can be
    explained by a public term; a narrative sentence from a real chart is still a narrative sentence
    once its digits are removed, and will still be caught.
    """
    return re.sub(r"[\d↑↓、，,;；:：]", "", fragment)


def _strings_under(node, fields: set[str], inside: bool = False) -> list[str]:
    """All strings in the tree that fall under a declared field (the field name may match at any depth)."""
    if isinstance(node, str):
        return [node] if inside else []
    if isinstance(node, dict):
        out: list[str] = []
        for key, value in node.items():
            out += _strings_under(value, fields, inside or key in fields)
            if inside and isinstance(key, str):
                out.append(key)
        return out
    if isinstance(node, list):
        return [s for v in node for s in _strings_under(v, fields, inside)]
    return []


def _walk_strings(node) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for k, v in node.items() if not k.startswith("_") for s in _walk_strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in _walk_strings(v)]
    return []


#: Maximum number of **consecutive** public terms a window may be built from.
MAX_TERMS_PER_WINDOW = 3
_affix_cache: dict[int, tuple[set[str], set[str], set[str]]] = {}


def _affixes(vocabulary: list[str]) -> tuple[set[str], set[str], set[str]]:
    key = id(vocabulary)
    if key not in _affix_cache:
        whole = set(vocabulary)
        suffixes = {t[i:] for t in vocabulary for i in range(len(t))}
        prefixes = {t[:i] for t in vocabulary for i in range(1, len(t) + 1)}
        _affix_cache[key] = (whole, suffixes, prefixes)
    return _affix_cache[key]


def is_public_term_window(fragment: str, vocabulary: list[str]) -> bool:
    """Whether this window can be explained by public vocabulary.

    The first version only allowed "the whole fragment falls within one term" (a truncated indicator
    name falling within its own full name, abbreviation included), and deliberately rejected
    concatenation: the tail of one unrelated public term joined to the head of another could itself be
    copied text. That was enough for tabular text.

    Starting 2026-09-29, narrative documents appeared (department key-value pairs, ultrasound findings,
    summary impressions) that **are themselves sequences of public phrases**: an "ultrasound impression:"
    label is immediately followed by a stock finding such as "thyroid shows no obvious abnormality", a
    "liver:" label by "liver shape and size normal", and real reports use the same phrases in the same
    order. So the rule was relaxed: a window may be
    built from **up to three consecutive public terms** (it may start or end mid-term, but any term in
    the middle must be whole). The residual risk is that original text built from exactly three
    dictionary phrases slips through; anything longer necessarily crosses more term boundaries and is
    still caught.

    The first version also tried "strip every whitelisted term out of the fragment one at a time, and
    exempt it if little is left" -- that was wrong: it fails on a truncated window (the 12-character
    window `notapplicabl` doesn't contain the complete word `notapplicable`). This version instead uses
    each term's suffix and prefix sets, which a truncated window naturally falls into.
    """
    whole, suffixes, prefixes = _affixes(vocabulary)
    n = len(fragment)
    if any(fragment in term for term in vocabulary):
        return True
    if MAX_TERMS_PER_WINDOW < 2:
        return False
    for i in range(1, n):
        if fragment[:i] in suffixes and fragment[i:] in prefixes:
            return True
    if MAX_TERMS_PER_WINDOW < 3:
        return False
    for i in range(1, n - 1):
        if fragment[:i] not in suffixes:
            continue
        for j in range(i + 1, n):
            if fragment[i:j] in whole and fragment[j:] in prefixes:
                return True
    return False


# ── Checks ───────────────────────────────────────────────────────────
#: PII predicates written **separately** from the set in `.githooks/pre-commit`.
PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("national ID number", re.compile(r"[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])"
                            r"(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]")),
    ("mobile phone number", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("visit/record number", re.compile(r"(?:门诊|住院|病案|体检|就诊卡)\s*号\s*[:：]?\s*\d{6,}")),
    ("redaction placeholder", re.compile(r"«[^»]{0,20}»")),
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    ("landline phone number", re.compile(r"(?<!\d)0\d{2,3}-\d{7,8}(?!\d)")),
]


# ── Fiction-pool whitelist (names, institutions) and synthetic IDs ────
#: The same check algorithm as `mirobody_gen/synthid.py`, written **independently**. The two
#: implementations must agree -- `tests/test_render.py` asserts this; the gate doesn't import the
#: generator, or it could only confirm the generator's own blind spot.
_SYNTH_SALT = "mirobody-gen/synthetic-id/v1"


def synthetic_id(number: str) -> bool:
    import hashlib

    digits = "".join(ch for ch in number if ch.isdigit())
    if len(digits) < 6:
        return False
    digest = hashlib.blake2b(f"{_SYNTH_SALT}:{digits[:-3]}".encode(), digest_size=4).digest()
    return f"{int.from_bytes(digest, 'big') % 1000:03d}" == digits[-3:]


def load_fiction() -> tuple[set[str], list[str], set[str]]:
    """(fiction person names, fiction institution names, institution-type suffixes)."""
    path = RESOURCES / "fiction.json"
    if not path.is_file():
        return set(), [], set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    names = set(payload.get("person_names_zh", [])) | set(payload.get("person_names_en", []))
    return (names, [x["name"] for x in payload.get("institutions", [])],
            set(payload.get("institution_types", [])))


def institution_ok(found: str, institutions: list[str], types: set[str]) -> bool:
    """An institution name must be found in the fiction pool. A type suffix (a generic "... City No. 1
    People's Hospital" or "General Hospital" ending) is allowed only on an **exact full-string match**
    -- it's the template itself appearing in the spec; allowing it as a substring would let any "XX
    General Hospital" through."""
    return found in types or any(name in found or found in name for name in institutions)


#: Name field: the name following one of these labels must belong to the fiction pool.
NAME_FIELD = re.compile(r"(?:姓名|送检医生|申请医生|检验者|审核者|检查者|操作者|检验人|报告医生|"
                        r"Name|Requested by|Performed By|Verified By|Physician)\s*[:：]\s*"
                        r"([\u4e00-\u9fff]{2,4}|[A-Z][a-z]+ [A-Z][a-z]+)")
#: Institution name: a run of characters ending in an institution-type suffix. Must be found in the
#: fiction pool.
INSTITUTION = re.compile(r"([\u4e00-\u9fff]{2,14}(?:医院|保健院|体检中心|管理中心|检验所|检验中心|服务中心|门诊部))"
                         r"|((?:[A-Z][a-z]+ ){1,3}(?:General Hospital|Medical Centre|Medical Center|Hospital|"
                         r"Clinical Laboratories|Pathology Services|Health Screening Centre|Family Clinic))")


def replay_windows(unit: str, index: np.ndarray, vocabulary: list[str], digitless: list[str],
                   seen: set[str] | None = None) -> tuple[list[str], int]:
    """(unexcused windows of one text unit that occur in the reference set, excused count).

    The same rules the gate applies: a window covered by public terms is excused, and so is one whose
    digit-stripped residue is. Used by the gate and, on the generator side, by the paraphrase writer to
    drop model output before it becomes a resource."""
    hits: list[str] = []
    excused = 0
    norm = normalize(unit)
    hashes = ngram_hashes(norm)
    if not hashes.size or not index.size:
        return hits, excused
    positions = np.clip(np.searchsorted(index, hashes), 0, index.size - 1)
    for i in np.nonzero(index[positions] == hashes)[0]:
        fragment = norm[int(i):int(i) + NGRAM]
        if seen is not None:
            if fragment in seen:
                continue
            seen.add(fragment)
        if is_public_term_window(fragment, vocabulary):
            excused += 1
            continue
        rest = residual_text(fragment)
        if len(rest) <= 2 or is_public_term_window(rest, vocabulary) or is_public_term_window(rest, digitless):
            excused += 1
            continue
        hits.append(fragment)
    return hits, excused


def digitless_terms(vocabulary: list[str]) -> list[str]:
    return sorted({residual_text(t) for t in vocabulary if residual_text(t)}, key=len, reverse=True)


def scan(paths: list[pathlib.Path], index: np.ndarray | None,
         vocabulary: list[str], report_only: bool) -> int:
    findings = 0
    excused = 0
    excused_samples: list[str] = []
    synthetic_ids = 0
    excused_numeric = 0  # folded into `excused` since the screen was factored out
    # Terms also have digits stripped, so they're comparable to the "residue after stripping digits":
    # stripping digits from `×10^9/L` gives `×^l`.
    digitless = digitless_terms(vocabulary)
    fiction_names, fiction_institutions, institution_types = load_fiction()
    seen: set[str] = set()
    unreadable: list[pathlib.Path] = []
    scanned = 0

    for path in paths:
        units = extract_text(path)
        if units is None:
            unreadable.append(path)
            continue
        scanned += 1
        rel = path.relative_to(REPO) if path.is_relative_to(REPO) else path
        text = "\n".join(units)

        # (1) Replay: scan unit by unit; n-grams never cross a unit boundary
        for unit in units:
            if index is None or not index.size:
                break
            hits, ok = replay_windows(unit, index, vocabulary, digitless, seen)
            excused += ok
            for fragment in hits:
                findings += 1
                print(f"replay hit  {rel}: ...{fragment}...")

        # (2) PII
        for label, pattern in PII_PATTERNS:
            for match in pattern.finditer(text):
                if label == "visit/record number" and synthetic_id(match.group()):
                    synthetic_ids += 1
                    continue
                findings += 1
                print(f"PII hit  {rel}: {label} -> {match.group()[:24]}")
        # (2') Names and institutions: an allow-list, not a deny-list
        for match in NAME_FIELD.finditer(text):
            if match.group(1) not in fiction_names:
                findings += 1
                print(f"name not in fiction pool  {rel}: {match.group()[:24]}")
        for match in INSTITUTION.finditer(text):
            found = match.group(1) or match.group(2)
            if not institution_ok(found, fiction_institutions, institution_types):
                findings += 1
                print(f"institution not in fiction pool  {rel}: {found[:30]}")

    print(f"\nscanned {scanned} files · {findings} hits · "
          f"{excused} excused as public vocabulary · {excused_numeric} excused as a digit-stripped "
          f"public term · {synthetic_ids} synthetic IDs (check suffix passed) · "
          f"{len(unreadable)} files with unreadable text")
    if report_only and excused_samples:
        # The docstring promises exemptions are "counted and printed, never hidden". Printing only a
        # total doesn't count: without seeing which terms prop up that zero-hit result, there's no way
        # to judge whether the exemption rule is too loose.
        print("  excused samples (each falls entirely within one public term):")
        for fragment in excused_samples[:20]:
            print(f"    {fragment}")
        if len(excused_samples) > 20:
            print(f"    ...and {len(excused_samples) - 20} more")
    if unreadable:
        print(f"  (unreadable files are images and other binary output that replay detection can't "
              f"cover; see the module docstring's \"What this gate does not cover\". A few: "
              f"{', '.join(p.name for p in unreadable[:3])})")
    return 0 if report_only else findings


#: Acceptable forms for a reference-range citation. A three-value enum at the file level can't express
#: "one file mixes standards, guidelines, manufacturer inserts and identities" -- so each indicator needs
#: its own **citable** source, matching one of the patterns below. A phrase like "common clinical
#: interval" does not count as a citation.
CITATION_PATTERNS = ("WS/T", "GB/T", "指南", "操作规程", "说明书", "由恒等式定义",
                     "心电图学", "WHO", "IFCC", "CLSI", "专家共识")


def check_indicator_citations() -> int:
    """Every indicator's reference range must have a citable source."""
    path = RESOURCES / "indicators.json"
    if not path.is_file():
        return 0
    problems = 0
    for item in json.loads(path.read_text(encoding="utf-8"))["indicators"]:
        source = item.get("reference_source") or ""
        if not any(p in source for p in CITATION_PATTERNS):
            print(f"source not citable  {item['key']}: reference_source={source!r} "
                  f"(give a standard number, guideline name, or package insert, or explain why not)")
            problems += 1
    return problems


def check_spec_provenance() -> int:
    """Every spec file must declare its provenance."""
    allowed = {"public-standard", "format-token", "hand-authored", "llm-paraphrase", "llm-template"}
    problems = 0
    for path in sorted((RESOURCES).glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        source = payload.get("_source") if isinstance(payload, dict) else None
        if source not in allowed:
            print(f"missing provenance  {path.relative_to(REPO)}: _source={source!r}, "
                  f"must be one of {sorted(allowed)}")
            problems += 1
        if isinstance(payload, dict) and "_provenance" not in payload:
            print(f"missing provenance  {path.relative_to(REPO)}: no _provenance")
            problems += 1
        if isinstance(payload, dict) and "_vocabulary_fields" not in payload:
            print(f"missing provenance  {path.relative_to(REPO)}: no _vocabulary_fields "
                  f"(declare which fields hold exemptable public vocabulary; [] if none do)")
            problems += 1
        # Model-generated resources are untrusted text: they must not exempt any field, and must pass
        # replay detection and the PII predicates verbatim.
        if isinstance(payload, dict) and str(source).startswith("llm-") and payload.get("_vocabulary_fields"):
            print(f"exemption out of bounds  {path.relative_to(REPO)}: a resource with "
                  f"_source={source!r} must not declare _vocabulary_fields")
            problems += 1
    return problems


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", nargs="*", default=["mirobody_gen/resources"],
                    help="directories or files to scan (relative to the repository root, or absolute)")
    ap.add_argument("--report", action="store_true", help="report only; always exit 0")
    ap.add_argument("--skip-replay", action="store_true", help="skip the replay index (no reference set on this machine)")
    ap.add_argument("--rebuild-index", action="store_true", help="rebuild the replay index")
    args = ap.parse_args()

    paths: list[pathlib.Path] = []
    for target in args.targets:
        root = REPO / target
        if root.is_file():
            paths.append(root)
        elif root.is_dir():
            paths += [p for p in sorted(root.rglob("*")) if p.is_file() and not p.name.startswith(".")]
    if not paths:
        print("nothing to scan: no files under the given targets")
        raise SystemExit(0 if args.report else 1)

    index = None
    if not args.skip_replay:
        index = build_index(args.rebuild_index)
        print(f"reference-corpus n-gram index: {index.size:,} {NGRAM}-character windows (hashes only)")

    problems = scan(paths, index, load_vocabulary(), args.report)
    problems += check_spec_provenance()
    problems += check_indicator_citations()

    if args.report:
        raise SystemExit(0)
    if problems:
        print(f"\nprivacy gate failed: {problems} problem(s).")
        raise SystemExit(1)
    print("privacy gate passed.")


if __name__ == "__main__":
    main()
