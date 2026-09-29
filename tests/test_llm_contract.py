"""The paraphrase contract and the offline half of `mirobody-gen paraphrase`.

No model is called: candidates are hand-written positives and negatives for each rule, and the dry run
is checked for shape only.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mirobody_gen.llm import contract, paraphrase  # noqa: E402
from mirobody_gen.llm.client import parse_list  # noqa: E402

REQ = contract.Request(
    id="narratives.findings.thyroid_nodule.finding.zh", lang="zh", register="narrative_finding",
    template="甲状腺{side}叶见一{echo}回声结节，大小约{mm}mm，边界清，形态规则，内部回声均匀，CDFI：周边见少许血流信号。",
    slots={"side": ["左", "右"], "echo": ["低", "等"], "mm": ["<number>"]}, locked=("mm",), n=3)


def test_a_faithful_paraphrase_passes():
    good = "甲状腺{side}叶可见{echo}回声结节一枚，约{mm}mm，边界清晰、形态规整、内部回声尚均匀，CDFI 示周边少量血流信号。"
    assert contract.check(REQ, good) == []


def test_each_rule_rejects_its_violation():
    cases = {
        "slots": "甲状腺左叶见一{echo}回声结节，大小约{mm}mm，边界清，形态规则，内部回声均匀，CDFI：周边见少许血流信号。",
        "new numbers": "甲状腺{side}叶见一{echo}回声结节，大小约{mm}mm×8mm，边界清，形态规则，内部回声均匀，CDFI：周边见少许血流信号。",
        "locked": "甲状腺{side}叶见一{echo}回声结节，大小约{mm}毫米，边界清晰，形态规整，内部回声均匀，CDFI：周边少许血流信号。",
        "zh candidate without CJK": "Thyroid {side} lobe {echo} nodule about {mm}mm.",
        "length ratio": "甲状腺{side}叶{echo}结节{mm}mm。",
        "institution-like name": "甲状腺{side}叶见一{echo}回声结节，大小约{mm}mm，边界清，形态规则，内部回声均匀，建议至阳光市人民医院复查，CDFI：周边见少许血流信号。",
        "identifier-like digit run": "甲状腺{side}叶见一{echo}回声结节，大小约{mm}mm，边界清，形态规则，内部回声均匀，报告号 20260929，CDFI：周边见少许血流信号。",
        "too close to the template": "甲状腺{side}叶见一{echo}回声结节，大小约{mm}mm，边界清，形态规则，内部回声均匀，CDFI：周边见少许血流信号",
        "stray brace": "甲状腺{side}叶见一{echo}回声结节，大小约{mm}mm}，边界清楚，形态规整，内部回声均匀，CDFI：周边见少许血流信号。",
    }
    for rule, text in cases.items():
        reasons = contract.check(REQ, text)
        assert reasons, rule
        assert any(rule in r for r in reasons), (rule, reasons)


def test_duplicates_and_locked_registers():
    good = "甲状腺{side}叶可见{echo}回声结节一枚，约{mm}mm，边界清晰、形态规整、内部回声尚均匀，CDFI 示周边少量血流信号。"
    near = "甲状腺{side}叶可见{echo}回声结节一枚，约{mm}mm，边界清晰、形态规整、内部回声尚均匀，CDFI 示周边少量血流信号"
    accepted, rejects = contract.accept(REQ, [good, near])
    assert accepted == [good] and rejects == {"duplicate of an accepted candidate": 1}
    locked = contract.Request(id="x", lang="zh", register="impression", template="甲状腺{side}叶结节")
    assert contract.check(locked, "甲状腺{side}叶结节（良性可能）") == ["locked register: not paraphrasable"]


def test_english_advice_keeps_the_head_and_numbers():
    tmpl = "Fatty liver: dietary control, regular exercise and weight management; limit alcohol; repeat liver function tests in 3–6 months."
    req = contract.Request(id="narratives.advice.fatty_liver.en", lang="en", register="advice", template=tmpl,
                           locked=contract.locked_terms("advice", tmpl))
    assert req.locked == ("Fatty liver",)
    assert contract.check(req, "Fatty liver: manage weight with diet and regular exercise, limit alcohol, and recheck liver function tests in 3–6 months.") == []
    assert contract.check(req, "Steatosis: manage weight with diet and regular exercise, limit alcohol, and recheck liver function tests in 3–6 months.")
    assert contract.check(req, "Fatty liver: manage weight with diet and regular exercise, limit alcohol, and recheck liver function in 2–4 months.")
    assert contract.check(req, "脂肪肝：Fatty liver: manage weight; limit alcohol; recheck in 3–6 months.")


def test_collect_covers_the_narrative_resources_and_skips_coded_surfaces():
    reqs = paraphrase._fix_severity_lang(paraphrase.collect())
    ids = {r.id for r in reqs}
    assert any(i.startswith("narratives.findings.") and i.endswith(".finding.zh") for i in ids)
    assert any(i.startswith("narratives.advice.") for i in ids)
    assert any(i.startswith("complaints.phrasing.zh.journal.") for i in ids)
    assert not any(".impression." in i or ".summary." in i or ".surface" in i for i in ids)
    assert all(r.lang in ("zh", "en") for r in reqs)
    for r in reqs:
        for name in contract.slot_counts(r.template):
            assert name in r.slots, (r.id, name)
    system, user = paraphrase.build_prompt(reqs[0])
    assert reqs[0].template in user and "JSON" in system


def test_dry_run_and_apply_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        bundle = pathlib.Path(tmp) / "req.jsonl"
        out = subprocess.run([sys.executable, "-m", "mirobody_gen.llm.paraphrase", "--dry-run", "--limit", "5",
                              "--bundle", str(bundle)], cwd=REPO, check=True, capture_output=True, text=True).stdout
        assert "nothing was sent" in out
        rows = [json.loads(l) for l in bundle.read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 5 and all({"id", "template", "system", "user", "slots", "locked"} <= set(r) for r in rows)
        # an "external" answer file: one faithful and one broken candidate for the first request
        req = contract.Request.from_json(rows[0])
        faithful = req.template.replace("，", "、").replace("。", "；随访。")
        answers = pathlib.Path(tmp) / "ans.jsonl"
        answers.write_text(json.dumps({"id": req.id, "candidates": [faithful, "12345678"]}, ensure_ascii=False) + "\n", encoding="utf-8")
        resource = pathlib.Path(tmp) / "paraphrases.json"
        out = subprocess.run([sys.executable, "-m", "mirobody_gen.llm.paraphrase", "--apply", str(answers), "--limit", "5",
                              "--resource", str(resource), "--write"], cwd=REPO, check=True, capture_output=True, text=True).stdout
        assert "written" in out
        payload = json.loads(resource.read_text(encoding="utf-8"))
        assert payload["_source"] == "llm-paraphrase" and payload["_vocabulary_fields"] == []
        assert payload["_provenance"]["rejected"] and payload["_provenance"]["accepted"] <= 1


def test_refine_prompt_carries_the_rejects_and_their_reasons():
    rejected = [("甲状腺{side}叶{echo}结节{mm}mm。", ["length ratio 0.30 outside [0.6, 1.6]"])]
    system, user = paraphrase.refine_prompt(REQ, rejected, 2)
    assert "length ratio" in user and rejected[0][0] in user and "2" in user
    assert REQ.template in user and "JSON" in system


def test_parse_list_accepts_json_and_lines():
    assert parse_list('```json\n["a", "b"]\n```') == ["a", "b"]
    assert parse_list('{"candidates": ["x"]}') == ["x"]
    assert parse_list("1. first\n- second\n\n") == ["first", "second"]
