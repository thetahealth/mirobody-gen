"""Propose written forms for the expression layer with a language model (`mirobody-gen propose`).

    mirobody-gen propose --axis synonym                                     # dry run: write the requests
    mirobody-gen propose --axis abbr --models <id>[,<id>] --judge <id> --write
    mirobody-gen propose --axis synonym --apply answers.jsonl --judge <id> --write

A model is asked, one catalogue panel at a time, for other native names (`synonym`) or abbreviations
(`abbr`) that laboratory reports print for each indicator. A prompt holds the catalogue's own
vocabulary and nothing from the reference set. A candidate is accepted only when

1. it passes the deterministic contract (`reasons`): script, length, no value, unit, method word or
   qualifier, no specimen the indicator's own name lacks, not a spelling the indicator already has;
2. it is no spelling of another indicator: such a candidate is kept apart in `collisions`, never drawn,
   as material for `confusable_with`;
3. a second model (`--judge`) reads it as the same test: same analyte, specimen and kind of result;
4. it matches no 12-character window of the reference set, where the replay index is available (so
   only a candidate of 12 characters or more can be caught: a long synonym, not an abbreviation).

Accepted forms are written to `resources/surface_generated.json`, whose choices `spec.surface()` appends
to `abbr_variants` and `name_synonyms` as the `generated` tier: only `--surface fuzz` builds draw them, and
the privacy gate reads every string there as untrusted text. Deleting the file removes the tier.
"""

from __future__ import annotations

import argparse
import functools
import json
import pathlib
import re
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from .. import spec
from . import client
from .paraphrase import screen_replay

AXES = {"synonym": "name_synonyms", "abbr": "abbr_variants"}

CJK = re.compile(r"[一-鿿]")
#: An abbreviation as reports print one: Latin or Greek letters and digits, a few marks, single spaces
ABBR = re.compile(r"(?=.*[A-Za-zα-ωΑ-Ω])[A-Za-z0-9α-ωΑ-Ω](?:[A-Za-z0-9α-ωΑ-Ω+\-#%/().:]| (?! )){1,15}(?<! )")
#: A unit inside a candidate: the unit belongs in its own column
UNIT = re.compile(r"(?i)/[a-zμ]*l\b|\b[mnpμu]?mol\b|\bmmhg\b|\bkcal\b")
#: Words that make a name say something other than the test: a result, a method, a derived quantity
QUALIFIERS = ("阳性", "阴性", "正常", "异常", "偏高", "偏低", "升高", "降低", "定性", "定量", "测定", "检测", "检查",
              "结果", "参考", "比值", "比率", "百分比", "百分率", "绝对值", "计数", "%")
#: Specimens a name may add only when the indicator's own name has them
SPECIMENS = ("尿", "粪", "便", "脑脊液", "唾液", "胸水", "腹水")


def _norm(word: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", word)).upper()


def spellings(item: dict) -> set[str]:
    """Every way the catalogue and the expression layer already write an indicator, normalized."""
    s = spec.surface()
    words = [item["zh"], item["en"], item.get("abbr") or "", *(item.get("name_variants") or [])]
    words += [c["word"] for axis in AXES.values() for c in s[axis]["indicators"].get(item["key"], [])]
    return {_norm(w) for w in words if w}


def reasons(axis: str, item: dict, word: str) -> list[str]:
    """Why a candidate fails the deterministic contract (empty when it passes)."""
    own = item["zh"]
    if _norm(word) in spellings(item):
        return ["already a spelling of this indicator"]
    if axis == "abbr":
        if not ABBR.fullmatch(word):
            return ["not an abbreviation of 2-16 letters, digits and marks"]
        return ["adds a unit"] if UNIT.search(word) else []
    out = []
    if not CJK.search(word) or not 2 <= len(word) <= 20:
        out.append("not a Chinese name of 2-20 characters")
    if re.search(r"[A-Za-z]", word) and not re.search(r"[A-Za-z]", own):
        out.append("adds Latin letters")
    if re.search(r"\d", word) and not re.search(r"\d", own):
        out.append("adds a digit")
    out += [f"adds {w}" for w in QUALIFIERS if w in word and w not in own]
    out += [f"adds the specimen {w}" for w in SPECIMENS if w in word and w not in own]
    return out


@functools.lru_cache(maxsize=None)
def _owners() -> dict[str, frozenset[str]]:
    """Normalized spelling → the indicators that write it."""
    out: dict[str, set[str]] = defaultdict(set)
    for key, item in spec.indicators().items():
        for word in spellings(item):
            out[word].add(key)
    return {w: frozenset(k) for w, k in out.items()}


def collisions(axis: str, item: dict, word: str) -> list[str]:
    """The other indicators a candidate is a spelling of (abbreviations ignore case: TG and Tg collide),
    or whose Chinese name a synonym contains when the indicator's own name does not."""
    out = set(_owners().get(_norm(word), ())) - {item["key"]}
    if axis == "synonym":
        out |= {key for key, other in spec.indicators().items()
                if len(other["zh"]) > 2 and other["zh"] in word and other["zh"] not in item["zh"]}
    return sorted(out)


def requests(axis: str, n: int) -> list[dict]:
    """One request per catalogue panel: its indicators with the spellings they already have."""
    by_panel: dict[str, list[dict]] = defaultdict(list)
    for item in spec.indicators().values():
        if axis == "abbr" and not item.get("abbr"):
            continue
        by_panel[item["panel"]].append({"key": item["key"], "name_zh": item["zh"], "name_en": item["en"],
                                        "abbr": item.get("abbr") or "", "unit": item["unit"],
                                        "known": sorted({item["zh"], item["en"], item.get("abbr") or "",
                                                         *(item.get("name_variants") or [])} - {""})})
    return [{"id": f"{axis}:{panel}", "axis": axis, "panel": panel, "n": n, "items": items}
            for panel, items in sorted(by_panel.items())]


def prompt(req: dict) -> tuple[str, str]:
    p = spec.llm_prompts()["propose"]
    return p["system"], p["user"][req["axis"]].format(n=req["n"], items=json.dumps(req["items"], ensure_ascii=False))


def judge_prompt(axis: str, candidates: dict[str, list[str]]) -> tuple[str, str]:
    catalogue = spec.indicators()

    def siblings(key: str) -> list[str]:
        """The panel's other tests, which a candidate must not name (骨骼肌量 is not 肌肉量)."""
        panel = catalogue[key]["panel"]
        return [f"{i['zh']} ({i['en']})" for k, i in catalogue.items() if i["panel"] == panel and k != key]

    items = [{"key": k, "name_zh": catalogue[k]["zh"], "name_en": catalogue[k]["en"], "abbr": catalogue[k]["abbr"],
              "unit": catalogue[k]["unit"], "not": siblings(k), "candidates": words}
             for k, words in sorted(candidates.items())]
    p = spec.llm_prompts()["propose"]["judge"]
    return p["system"], p["user"].format(items=json.dumps(items, ensure_ascii=False))


def parse_object(text: str) -> dict:
    """A model answer's JSON object, or {} when it holds none."""
    body = text[text.find("{"):text.rfind("}") + 1]
    try:
        data = json.loads(body)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def screen(axis: str, answers: dict[str, dict[str, set[str]]]) -> tuple[dict, dict, Counter]:
    """(passing candidates by key, collisions by key, reject counts) from the models' answers
    (model → key → candidates)."""
    catalogue = spec.indicators()
    passing: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))   # key → word → models
    collided: dict[str, dict[str, list[str]]] = defaultdict(dict)
    rejects: Counter = Counter()
    for model, by_key in answers.items():
        for key, words in by_key.items():
            if key not in catalogue:
                rejects["unknown indicator"] += len(words)
                continue
            for word in (w.strip() for w in words if isinstance(w, str)):
                if why := reasons(axis, catalogue[key], word):
                    rejects[why[0]] += 1
                elif others := collisions(axis, catalogue[key], word):
                    collided[key][word] = others
                else:
                    passing[key][word].add(model)
    return passing, collided, rejects


def judged(axis: str, passing: dict[str, dict[str, set[str]]], judge: str, workers: int = 8) -> tuple[dict, int]:
    """The candidates the judge reads as the same test, one call per panel; the rest are dropped."""
    catalogue = spec.indicators()
    by_panel: dict[str, dict[str, list[str]]] = defaultdict(dict)
    for key, words in passing.items():
        by_panel[catalogue[key]["panel"]][key] = sorted(words)
    kept: dict[str, dict[str, set[str]]] = {}
    dropped = 0
    def ask(candidates: dict[str, list[str]]) -> tuple[dict, dict]:
        return candidates, parse_object(client.complete(judge, *judge_prompt(axis, candidates), temperature=0)["text"])

    with ThreadPoolExecutor(max_workers=workers) as pool:
        answered = list(pool.map(ask, [by_panel[p] for p in sorted(by_panel)]))
    for candidates, verdicts in answered:
        for key, words in candidates.items():
            yes = {w for w in words if (verdicts.get(key) or {}).get(w) is True}
            dropped += len(words) - len(yes)
            if yes:
                kept[key] = {w: passing[key][w] for w in yes}
    return kept, dropped


def write(axis: str, kept: dict, collided: dict, run: dict) -> None:
    """Merge one run into `spec.GENERATED`: earlier choices stay, new words follow them in order."""
    path = spec.GENERATED
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {
        "_source": "llm-propose",
        "_note": ("Written forms proposed by a language model and accepted by mirobody_gen/llm/propose.py: "
                  "the `generated` tier of surface.json's axes, drawn only by --surface fuzz builds. "
                  "Untrusted text: not vocabulary, scanned by the privacy gate as is."),
        "_vocabulary_fields": [], "_provenance": {"script": "mirobody_gen/llm/propose.py", "runs": []},
        "axes": {}, "collisions": {}}
    name = AXES[axis]
    listed = data["axes"].setdefault(name, {})
    for key in sorted(kept):
        have = {c["word"] for c in listed.get(key, [])}
        listed.setdefault(key, []).extend(
            {"word": w, "tier": "generated", "share": 0,
             "sources": [*(f"llm:{m}" for m in sorted(kept[key][w])), f"judge:{run['judge']}", run["date"]]}
            for w in sorted(kept[key]) if w not in have)
    pool = data["collisions"].setdefault(name, {})
    for key in sorted(collided):
        pool.setdefault(key, {}).update(collided[key])
    data["_provenance"]["runs"].append(run)
    for part in ("axes", "collisions"):
        data[part] = {a: dict(sorted(v.items())) for a, v in sorted(data[part].items())}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Propose written forms for the expression layer")
    ap.add_argument("--axis", choices=sorted(AXES), required=True)
    ap.add_argument("--models", default="", help="comma-separated proposer model ids (through LLM_BASE_URL)")
    ap.add_argument("--apply", default="", help="JSONL of {key, candidates[]} produced elsewhere")
    ap.add_argument("--judge", default="", help="model that must read each candidate as the same test")
    ap.add_argument("--n", type=int, default=4, help="candidates asked per indicator")
    ap.add_argument("--workers", type=int, default=8, help="concurrent model calls")
    ap.add_argument("--bundle", default=str(client.CACHE / "propose_requests.jsonl"), help="where a dry run writes")
    ap.add_argument("--write", action="store_true", help="merge the accepted forms into resources/surface_generated.json")
    args = ap.parse_args()
    if args.write and not args.judge:
        raise SystemExit("--write needs --judge: an unjudged candidate may name another test")

    reqs = requests(args.axis, args.n)
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    answers: dict[str, dict[str, list[str]]] = defaultdict(dict)
    if args.apply:
        for line in pathlib.Path(args.apply).read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                answers["external"].setdefault(rec["key"], []).extend(rec["candidates"])
    elif models:
        def ask(job: tuple[str, dict]) -> tuple[str, dict]:
            return job[0], parse_object(client.complete(job[0], *prompt(job[1]))["text"])

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for model, by_key in pool.map(ask, [(m, r) for m in models for r in reqs]):
                answers[model].update({k: v for k, v in by_key.items() if isinstance(v, list)})
    else:
        bundle = pathlib.Path(args.bundle)
        bundle.parent.mkdir(parents=True, exist_ok=True)
        bundle.write_text("".join(json.dumps({**r, "system": prompt(r)[0], "user": prompt(r)[1]}, ensure_ascii=False)
                                  + "\n" for r in reqs), encoding="utf-8")
        print(f"dry run: {len(reqs)} requests written to {bundle}; nothing was sent")
        return

    passing, collided, rejects = screen(args.axis, answers)
    print(f"{sum(len(v) for v in passing.values())} candidates pass the contract · "
          f"{sum(len(v) for v in collided.values())} collide with another indicator · rejected {dict(rejects)}")
    for key, words in sorted(collided.items()):
        print(f"  collision {key}: " + ", ".join(f"{w} = {'/'.join(o)}" for w, o in words.items()))
    if not args.judge:
        return
    kept, dropped = judged(args.axis, passing, args.judge, args.workers)
    words = {k: set(v) for k, v in kept.items()}
    screened = screen_replay(words, rejects)
    kept = {k: {w: kept[k][w] for w in words[k]} for k in words}
    print(f"judge {args.judge} dropped {dropped}; replay screen {'applied' if screened else 'unavailable'}; "
          f"accepted {sum(len(v) for v in kept.values())} for {len(kept)} indicators")
    for key, by_word in sorted(kept.items()):
        print(f"  {key}: " + ", ".join(sorted(by_word)))
    if args.write:
        write(args.axis, kept, collided, {
            "axis": args.axis, "models": models or ["external"], "judge": args.judge, "date": date.today().isoformat(),
            "prompt_version": spec.llm_prompts()["propose"]["version"], "requests": len(reqs),
            "accepted": sum(len(v) for v in kept.values()), "judge_dropped": dropped,
            "replay_screened": screened, "rejected": dict(rejects)})
        print(f"written {spec.GENERATED}; run `mirobody-gen audit-privacy` before a release")


if __name__ == "__main__":
    main()
