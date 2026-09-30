"""Offline paraphrase enrichment of the narrative templates (`mirobody-gen paraphrase`).

    mirobody-gen paraphrase --dry-run                       # write the request bundle, send nothing
    mirobody-gen paraphrase --models <id>[,<id>] --write    # call, validate, write resources/paraphrases.json
    mirobody-gen paraphrase --apply responses.jsonl --write # validate answers produced elsewhere

Templates are collected from `narratives.json` and `complaints.json`; every candidate must pass the
contract in `llm/contract.py`. The result is a resource with `_source: llm-paraphrase` and an empty
`_vocabulary_fields`, so the privacy gate treats every string in it as untrusted text.

这一层是可选叠加：不装 `paraphrases.json`，生成器的行为与现在完全相同。
"""

from __future__ import annotations

import argparse
import json
import pathlib
from collections import Counter
from datetime import date

from .. import spec
from . import client
from .contract import LOCKED_REGISTERS, Request, accept, locked_terms

PACKAGE = pathlib.Path(__file__).resolve().parents[1]
RESOURCES = PACKAGE / "resources"
REPO = PACKAGE.parent
DEFAULT_BUNDLE = REPO / ".cache" / "llm" / "paraphrase_requests.jsonl"


def _placeholders() -> dict[str, list[str]]:
    return {k: list(v) for k, v in (spec.narratives().get("_placeholders") or {}).items()}


def _slots_of(template: str, pools: dict[str, list[str]]) -> dict[str, list[str]]:
    from .contract import SLOT

    out: dict[str, list[str]] = {}
    for name in dict.fromkeys(SLOT.findall(template)):
        out[name] = pools.get(name, ["<number>"] if name in ("mm", "t", "items", "sbp", "dbp", "pulse",
                                                                "weight", "temp", "steps") else ["<text>"])
    return out


def collect(n: int = 3) -> list[Request]:
    """Every paraphrasable template with its register, slots and locked terms."""
    narr, comp = spec.narratives(), spec.complaints()
    pools = _placeholders()
    out: list[Request] = []

    def add(path: str, lang: str, register: str, template: str) -> None:
        if not isinstance(template, str) or not template.strip() or register in LOCKED_REGISTERS:
            return
        out.append(Request(id=path, lang=lang, register=register, template=template,
                           slots=_slots_of(template, pools), locked=locked_terms(register, template), n=n))

    for fid, fdef in narr["findings"].items():
        for key, register in (("finding", "narrative_finding"), ("text", "department_note")):
            block = fdef.get(key)
            if isinstance(block, dict):
                for lang, value in block.items():
                    if isinstance(value, dict):                     # severity-graded
                        for sev, text in value.items():
                            add(f"narratives.findings.{fid}.{key}.{lang}.{sev}", sev if sev in ("zh", "en") else lang, register, text)
                    else:
                        add(f"narratives.findings.{fid}.{key}.{lang}", lang, register, value)
    for aid, aux in narr["aux"].items():
        for lang, texts in (aux.get("finding_normal") or {}).items():
            for i, text in enumerate(texts):
                add(f"narratives.aux.{aid}.finding_normal.{lang}.{i}", lang, "narrative_normal", text)
        for organ in aux.get("organs") or []:
            for lang, texts in (organ.get("normal") or {}).items():
                for i, text in enumerate(texts):
                    add(f"narratives.aux.{aid}.organs.{organ['id']}.normal.{lang}.{i}", lang, "narrative_normal", text)
    for key, adv in narr["advice"].items():
        if key == "closing":
            continue
        for lang, text in adv.items():
            add(f"narratives.advice.{key}.{lang}", lang, "advice", text)
    for lang, phr in comp["phrasing"].items():
        for kind, register in (("journal", "diary"), ("cc", "chief_complaint")):
            for i, text in enumerate(phr.get(kind) or []):
                add(f"complaints.phrasing.{lang}.{kind}.{i}", lang, register, text)
    return out


def _fix_severity_lang(reqs: list[Request]) -> list[Request]:
    """Severity-graded findings are keyed finding.{sev}.{lang}; normalise the language field."""
    fixed = []
    for r in reqs:
        parts = r.id.split(".")
        lang = next((p for p in parts if p in ("zh", "en")), r.lang)
        fixed.append(Request(id=r.id, lang=lang, register=r.register, template=r.template,
                             slots=r.slots, locked=r.locked, n=r.n))
    return fixed


def build_prompt(req: Request) -> tuple[str, str]:
    """(system, user) from the hand-authored prompt resource."""
    prompts = spec.llm_prompts()
    system = prompts["system"][req.lang]
    register = prompts["registers"].get(req.register, {}).get(req.lang, "")
    slots = "\n".join(f"- {{{k}}}: {', '.join(v)}" for k, v in req.slots.items()) or "-"
    user = prompts["user"][req.lang].format(n=req.n, template=req.template, slots=slots,
                                            locked=", ".join(req.locked) or "-", register=register)
    return system, user


def refine_prompt(req: Request, rejected: list[tuple[str, list[str]]], still_needed: int) -> tuple[str, str]:
    """Second-round prompt: the rejected candidates with their one-line reasons (progressive refinement
    after Kramer et al. 2026: feed the validator's findings back, ask only for what is still missing)."""
    prompts = spec.llm_prompts()
    system, user = build_prompt(Request(id=req.id, lang=req.lang, register=req.register, template=req.template,
                                        slots=req.slots, locked=req.locked, n=still_needed))
    sep = "；" if req.lang == "zh" else "; "
    lines = "\n".join(f"- {text}\n  {sep.join(reasons)}" for text, reasons in rejected)
    return system, user + "\n\n" + prompts["refine"][req.lang].format(rejected=lines, n=still_needed)


def write_bundle(reqs: list[Request], path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in reqs:
            system, user = build_prompt(r)
            fh.write(json.dumps({**r.to_json(), "system": system, "user": user}, ensure_ascii=False) + "\n")


def apply_responses(reqs: list[Request], responses: dict[str, list[str]]) -> tuple[dict[str, list[str]], Counter]:
    """responses: request id → raw candidates. Returns (accepted per id, reject counts)."""
    by_id = {r.id: r for r in reqs}
    accepted: dict[str, list[str]] = {}
    rejects: Counter = Counter()
    for rid, cands in responses.items():
        req = by_id.get(rid)
        if req is None:
            rejects["unknown request id"] += len(cands)
            continue
        ok, why = accept(req, cands)
        if ok:
            accepted[rid] = ok
        rejects.update(why)
    return accepted, rejects


def screen_replay(accepted: dict[str, list[str]], rejects: Counter) -> bool:
    """Drop every candidate with an unexcused 12-character window that occurs in the reference set.

    Model output is untrusted text: it may reproduce a real report. On a machine without the replay
    index nothing is screened and the resource says so; the gate then runs on the release machine."""
    from ..audit import privacy

    if not privacy.CACHE.is_file():
        return False
    index = privacy.build_index()
    vocabulary = privacy.load_vocabulary()
    digitless = privacy.digitless_terms(vocabulary)
    for rid, cands in list(accepted.items()):
        kept = []
        for c in cands:
            hits, _ = privacy.replay_windows(c, index, vocabulary, digitless)
            (rejects.__setitem__("replay hit", rejects["replay hit"] + 1) if hits else kept.append(c))
        if kept:
            accepted[rid] = kept
        else:
            del accepted[rid]
    return True


def write_resource(accepted: dict[str, list[str]], rejects: Counter, models: list[str], n_requests: int,
                   path: pathlib.Path, screened: bool = False) -> None:
    payload = {
        "_source": "llm-paraphrase",
        "_note": ("Paraphrases of narrative templates produced by a language model and filtered by "
                  "mirobody_gen/llm/contract.py. Untrusted text: not vocabulary, scanned by the privacy gate as is."),
        "_provenance": {"script": "mirobody_gen/llm/paraphrase.py", "models": models, "date": date.today().isoformat(),
                        "prompt_version": spec.llm_prompts()["_provenance"].get("version"),
                        "replay_screened": screened, "requests": n_requests, "accepted_ids": len(accepted),
                        "accepted": sum(len(v) for v in accepted.values()), "rejected": dict(rejects)},
        "_vocabulary_fields": [],
        "paraphrases": {k: accepted[k] for k in sorted(accepted)},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Offline paraphrase enrichment of the narrative templates")
    ap.add_argument("--dry-run", action="store_true", help="write the request bundle and send nothing (default without --models/--apply)")
    ap.add_argument("--models", default="", help="comma-separated model ids to call through LLM_BASE_URL")
    ap.add_argument("--apply", default="", help="JSONL of {id, candidates[]} or {id, text} produced elsewhere")
    ap.add_argument("--n", type=int, default=3, help="paraphrases requested per template")
    ap.add_argument("--refine", type=int, default=1, help="refinement rounds: rejects go back with their reasons (0 disables)")
    ap.add_argument("--workers", type=int, default=8, help="concurrent model calls")
    ap.add_argument("--limit", type=int, default=0, help="only the first N templates (smoke)")
    ap.add_argument("--bundle", default=str(DEFAULT_BUNDLE), help="where the request bundle is written")
    ap.add_argument("--resource", default=str(RESOURCES / "paraphrases.json"), help="output resource path")
    ap.add_argument("--write", action="store_true", help="write the resource (otherwise only report)")
    ap.add_argument("--no-screen-replay", action="store_true", help="skip the replay screen even when the index is available")
    args = ap.parse_args()

    reqs = _fix_severity_lang(collect(args.n))
    if args.limit:
        reqs = reqs[:args.limit]
    print(f"{len(reqs)} templates · registers {dict(Counter(r.register for r in reqs))} · "
          f"languages {dict(Counter(r.lang for r in reqs))}")

    responses: dict[str, list[str]] = {}
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if args.apply:
        for line in pathlib.Path(args.apply).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            cands = rec.get("candidates") if isinstance(rec.get("candidates"), list) else client.parse_list(rec.get("text", ""))
            responses.setdefault(rec["id"], []).extend(str(c) for c in cands)
        models = models or ["external"]
    elif models:
        from concurrent.futures import ThreadPoolExecutor

        from .contract import check

        def ask(r: Request, m: str) -> tuple[str, list[str]]:
            system, user = build_prompt(r)
            cands = client.parse_list(client.complete(m, system, user)["text"])
            # progressive refinement: the rejects go back with their reasons, once per --refine round
            for _ in range(args.refine):
                ok: list[str] = []
                rejected: list[tuple[str, list[str]]] = []
                for c in cands:
                    reasons = check(r, c, ok)
                    (rejected.append((c, reasons)) if reasons else ok.append(c.strip()))
                if len(ok) >= r.n or not rejected:
                    break
                system2, user2 = refine_prompt(r, rejected[-6:], r.n - len(ok))
                cands = cands + client.parse_list(client.complete(m, system2, user2)["text"])
            return r.id, cands

        # requests are independent: run them concurrently (the cache is one file per prompt)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for rid, cands in pool.map(lambda rm: ask(*rm), [(r, m) for r in reqs for m in models]):
                responses.setdefault(rid, []).extend(cands)
    else:
        bundle = pathlib.Path(args.bundle)
        write_bundle(reqs, bundle)
        print(f"dry run: request bundle written to {bundle} ({bundle.stat().st_size} bytes); nothing was sent")
        return

    accepted, rejects = apply_responses(reqs, responses)
    screened = False if args.no_screen_replay else screen_replay(accepted, rejects)
    print("replay screen:", "applied" if screened else "not available on this machine (run the privacy gate before release)")
    n_acc = sum(len(v) for v in accepted.values())
    print(f"accepted {n_acc} paraphrases for {len(accepted)} templates · rejected {sum(rejects.values())} "
          f"{dict(rejects)}")
    if args.write:
        out = pathlib.Path(args.resource)
        write_resource(accepted, rejects, models, len(reqs), out, screened)
        print(f"written {out}; run `mirobody-gen audit-privacy` before using it")


if __name__ == "__main__":
    main()
