"""Check-up books, outpatient records, ECG/ultrasound/imaging reports and home logs: everything in a file that is not a laboratory table.

体检报告书的科室与总检、门诊病历、心电图报告、超声报告——检验表格之外的那三分之二。

真实语料里体检报告书是最大的一类文档（157/627），而科室部分是**键值对**（63/76），
影像与心电图是**叙述**，最后一页是**总检结论与建议**。这一层把 `resources/narratives.json` 里的
词汇与 `profile.py` 算出来的所见，排成 `Block`（键值对 / 叙述 / 参数表 / 图像 / 总检），
挂到 `Doc.blocks` 上；渲染器按块的顺序印。

真值有三层，都写进 manifest：

* `blocks[].truth`：每个键值对条目印了什么、是否异常、对应哪条所见——mirobody 的抽取契约里
  这是 `detection_method = Physiological / Imaging` 的行；
* `findings`：具名所见的编码真值（诊断表面 → ICPC-3 D 轴期望码，或期望弃权）；
* `summary`：总检结论逐条对应到所见或异常指标，建议逐条对应到建议模板——这是
  `additional_info.assessment / recommendations` 的真值。

叙述里的数值（"结节大小约 7mm"）刻意**不**进印刷真值：它们是描述，不是指标。
"""

from __future__ import annotations

import io
import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np
from PIL import Image, ImageDraw

from . import spec, synthid
from .document import Cells, Doc, DocReading, PrintedRow, Table, build_doc, format_date, print_dates, print_value, subject_fields
from .layout import Family
from .model import Encounter, Finding, Person


@dataclass
class Block:
    kind: str                       # kv / narrative / params / image / summary / tables / general_table / cover
    title: str = ""
    rows: list[tuple[str, str]] = field(default_factory=list)
    columns: int = 2                # kv：每行几个"项目/结果"对（2 或 4）
    image: bytes | None = None
    image_size: tuple[int, int] = (0, 0)
    truth: list[dict] = field(default_factory=list)
    section_id: str = ""


def _n() -> dict:
    return spec.narratives()


def _lang(f: Family) -> str:
    return f.lang_group


def _sticky(f: Family, what: str) -> random.Random:
    return random.Random(f"book:{f.family_id}:{what}")


def _wording(f: Family, path: str, original: str) -> str:
    """One institution prints one wording per template (its LIS has one dictionary): the choice among
    the original and its paraphrases is sticky per family and draws nothing from the document stream."""
    pool = spec.phrasings(path, original)
    return pool[0] if len(pool) == 1 else _sticky(f, "phr:" + path).choice(pool)


def _pick(rng: random.Random, f: Family, path_prefix: str, pool: list[str]) -> str:
    """rng.choice over a template list, then the sticky wording of the chosen item."""
    i = rng.randrange(len(pool))
    return _wording(f, f"{path_prefix}.{i}", pool[i])


def _fill(template: str, params: dict) -> str:
    try:
        return template.format(**params)
    except (KeyError, IndexError):
        return template


# ── 科室键值对 ─────────────────────────────────────────────────
def _dialect(f: Family) -> str:
    lang = _lang(f)
    return _sticky(f, "dialect").choice(_n()["normal_dialects"][lang])


def _acuity_pair(rng: random.Random, low: bool, f: Family) -> tuple[str, str, str]:
    """(印出来的文字, 左, 右)。两种记法各半；一格印两只眼是真实体检的固定写法。"""
    ac = _n()["acuity"]
    style = "five_point" if _sticky(f, "acuity").random() < 0.5 else "decimal"
    scale = ac[style]
    pool = ac["low_index"] if low else ac["normal_index"]
    left, right = scale[rng.choice(pool)], scale[rng.choice(pool)]
    b = _n()["book"][_lang(f)]
    sep = rng.choice(["/", " / ", "  "])
    style_text = rng.choice([f"{b['left']}{left}{sep}{b['right']}{right}", f"{left}{sep}{right}",
                             f"{b['left_eye']} {left}  {b['right_eye']} {right}"])
    return style_text, left, right


def section_blocks(rng: random.Random, doc: Doc, person: Person, enc: Encounter, f: Family) -> list[Block]:
    n = _n()
    lang = _lang(f)
    pkg = n["packages"][enc.package or "standard"]
    normal_word = _dialect(f)
    columns = 4 if _sticky(f, "kvcols").random() < 0.4 else 2
    by_slot: dict[tuple[str, str | None], Finding] = {(x.where, x.item): x for x in enc.findings}
    readings = {r.key: r for r in enc.readings}
    blocks: list[Block] = []
    history_label = n["summary"][lang]["history_labels"].get(person.archetype)
    for sid in pkg["sections"]:
        sec = n["sections"][sid]
        if sec.get("sex") and sec["sex"] != person.sex:
            continue
        rows: list[tuple[str, str]] = []
        truth: list[dict] = []
        for item in sec["items"]:
            if item.get("sex") and item["sex"] != person.sex:
                continue
            if item.get("optional") and rng.random() > item["optional"]:
                continue
            label = item[lang]
            finding = by_slot.get((sid, item["id"]))
            abnormal = False
            fid = None
            if item["id"] == "history":
                scripted = [e for e in person.events if e.note == "原型剧本"]
                has_history = history_label and scripted and scripted[0].start <= enc.exam_date
                value = history_label if has_history else rng.choice(item["normal"][lang])
                abnormal = bool(has_history)
            elif item.get("from_reading"):
                r = readings.get(item["from_reading"])
                value = f"{r.value}{r.unit}" if r else normal_word
            elif item.get("kind") == "acuity":
                low = finding is not None and finding.id in ("myopia",)
                if item["id"] == "va_corrected":
                    low = False
                value, left, right = _acuity_pair(rng, low, f)
                abnormal = low
                fid = finding.id if low else None
                if finding is not None and finding.id == "presbyopia_note":
                    value = f"{value}（{_fill(n['findings']['presbyopia_note']['text'][lang], {})}）" if lang == "zh" \
                        else f"{value} ({n['findings']['presbyopia_note']['text'][lang]})"
                    abnormal, fid = True, finding.id
            elif item.get("kind") == "iop":
                value = f"{rng.randint(11, 20)}/{rng.randint(11, 20)} mmHg"
            elif finding is not None:
                fdef = n["findings"][finding.id]
                value = _fill(_wording(f, f"narratives.findings.{finding.id}.text.{lang}", fdef["text"][lang]), finding.params) \
                    if "text" in fdef else fdef["summary"][lang]
                abnormal, fid = True, finding.id
            else:
                pool = item.get("normal", {}).get(lang)
                # own_normal 的条目（中医体质）没有"未见异常"这种写法，只能印自己的正常值
                value = rng.choice(pool) if pool and (item.get("own_normal") or rng.random() < 0.7) else normal_word
            rows.append((label, value))
            truth.append({"label": label, "value": value, "abnormal": abnormal, "finding": fid,
                          "detection_method": "Physiological"})
        blocks.append(Block(kind="kv", title=sec[lang], rows=rows, columns=columns, truth=truth, section_id=sid))
    return blocks


# ── 辅助检查叙述 ───────────────────────────────────────────────
def _ecg_strip(rng: random.Random, pulse: float, pr_ms: float, qrs_ms: float, brady: bool) -> tuple[bytes, tuple[int, int]]:
    """一段Ⅱ导联条图：粉色网格 + P-QRS-T 波形，节律按心率。只是视觉构件，不编码任何真值。"""
    w, h = 720, 130
    img = Image.new("RGB", (w, h), (255, 245, 245))
    d = ImageDraw.Draw(img)
    for x in range(0, w, 5):
        d.line((x, 0, x, h), fill=(255, 200, 200) if x % 25 else (255, 150, 150), width=1)
    for y in range(0, h, 5):
        d.line((0, y, w, y), fill=(255, 200, 200) if y % 25 else (255, 150, 150), width=1)
    px_per_s = 125.0                                             # 25 mm/s，5 px/mm
    rr = 60.0 / max(40.0, pulse) * px_per_s
    base = h * 0.62
    pts = []
    x = 0.0
    phase = rng.uniform(0, rr)
    while x < w:
        u = ((x + phase) % rr) / rr                             # 0..1 在一个心动周期里
        y = 0.0
        # P 波
        y += 6 * math.exp(-((u - 0.12) / 0.03) ** 2)
        # QRS
        y -= 4 * math.exp(-((u - 0.235) / 0.006) ** 2)
        y += 48 * math.exp(-((u - 0.25) / 0.008) ** 2)
        y -= 10 * math.exp(-((u - 0.268) / 0.006) ** 2)
        # T 波
        y += 12 * math.exp(-((u - 0.45) / 0.05) ** 2)
        wander = 3 * math.sin(x / 90.0) + rng.gauss(0, 0.6)
        pts.append((x, base - y + wander))
        x += 1.0
    d.line(pts, fill=(20, 20, 20), width=2)
    d.rectangle((6, base - 50, 16, base), outline=(20, 20, 20), width=2)   # 定标脉冲
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue(), (w, h)


def _us_image(rng: random.Random, seed: int) -> tuple[bytes, tuple[int, int]]:
    """一幅像超声的扇形斑点图：只是视觉构件。"""
    w, h = 480, 360
    rs = np.random.RandomState(seed)
    Y, X = np.mgrid[0:h, 0:w].astype(np.float32)
    cx, cy = w / 2, -40
    r = np.sqrt((X - cx) ** 2 + (Y - cy) ** 2)
    ang = np.arctan2(X - cx, Y - cy)
    fan = (np.abs(ang) < 0.62) & (r > 70) & (r < h + 30)
    speckle = rs.rand(h, w).astype(np.float32)
    small = Image.fromarray((speckle * 255).astype(np.uint8), "L").resize((w // 3, h // 3)).resize((w, h), Image.BILINEAR)
    tex = np.asarray(small, dtype=np.float32) / 255
    band = np.exp(-((r - 150 - 40 * np.sin(ang * 3)) / 25) ** 2) * 0.5
    dark = np.exp(-((X - w * 0.62) ** 2 + (Y - h * 0.55) ** 2) / (2 * 28 ** 2)) * 0.8
    intensity = np.clip((0.25 + 0.5 * tex + band - dark) * (1 - r / (h + 60)) * 255, 0, 255)
    img = np.where(fan, intensity, 0).astype(np.uint8)
    out = Image.fromarray(img, "L").convert("RGB")
    d = ImageDraw.Draw(out)
    for i in range(1, 8):                                        # 右侧深度刻度
        d.line((w - 12, i * 45, w - 6, i * 45), fill=(200, 200, 200), width=1)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue(), (w, h)


def aux_blocks(rng: random.Random, doc: Doc, person: Person, enc: Encounter, f: Family,
               only: list[str] | None = None) -> list[Block]:
    n = _n()
    lang = _lang(f)
    pkg = n["packages"][enc.package or "premium"]
    age = person.age_at(enc.exam_date)
    readings = {r.key: r for r in enc.readings}
    by_where: dict[str, list[Finding]] = {}
    for x in enc.findings:
        by_where.setdefault(x.where, []).append(x)
    blocks: list[Block] = []
    for aid in (only or pkg["aux"]):
        aux = n["aux"][aid]
        if aux.get("sex") and aux["sex"] != person.sex:
            continue
        if aux.get("min_age", 0) > age:
            continue
        labels = aux["labels"][lang]
        found = by_where.get(aid, [])
        truth: list[dict] = []
        rows: list[tuple[str, str]] = []
        image, size = None, (0, 0)
        if aux["kind"] == "ecg":
            pulse = readings.get("pulse")
            pr, qrs = readings.get("pr_interval"), readings.get("qrs_duration")
            brady = any(x.id == "sinus_brady" for x in found)
            image, size = _ecg_strip(rng, float(pulse.value) if pulse else 72, float(pr.value) if pr else 150,
                                     float(qrs.value) if qrs else 90, brady)
            if found:
                impression = "；".join(_fill(n["findings"][x.id]["impression"][lang], x.params) for x in found) \
                    if lang == "zh" else "; ".join(_fill(n["findings"][x.id]["impression"][lang], x.params) for x in found)
            else:
                impression = rng.choice(aux["impression_normal"][lang])
            rows.append((labels["impression"], impression))
            truth += [{"label": labels["impression"], "value": impression, "abnormal": bool(found),
                       "finding": x.id, "detection_method": "Physiological"} for x in found] or \
                     [{"label": labels["impression"], "value": impression, "abnormal": False, "finding": None,
                       "detection_method": "Physiological"}]
        elif aux["kind"] in ("imaging", "ultrasound"):
            paras: list[str] = []
            if aux["kind"] == "ultrasound":
                for organ in aux["organs"]:
                    hits = [x for x in found if x.item == organ["id"]]
                    if hits:
                        x = hits[0]
                        fdef = n["findings"][x.id]
                        text = _wording(f, f"narratives.findings.{x.id}.finding.{x.severity}.{lang}", fdef["finding"][x.severity][lang]) \
                            if x.severity else _wording(f, f"narratives.findings.{x.id}.finding.{lang}", fdef["finding"][lang])
                        paras.append(f"{organ[lang]}：{_fill(text, x.params)}" if lang == "zh" else f"{organ[lang]}: {_fill(text, x.params)}")
                    else:
                        normal = _pick(rng, f, f"narratives.aux.{aid}.organs.{organ['id']}.normal.{lang}", organ["normal"][lang])
                        paras.append(f"{organ[lang]}：{normal}" if lang == "zh" else f"{organ[lang]}: {normal}")
                if rng.random() < 0.5:
                    image, size = _us_image(rng, rng.randint(0, 2 ** 31 - 1))
            else:
                if found:
                    x = found[0]
                    fdef = n["findings"][x.id]
                    paras.append(_fill(_wording(f, f"narratives.findings.{x.id}.finding.{lang}", fdef["finding"][lang]), x.params))
                else:
                    paras.append(_pick(rng, f, f"narratives.aux.{aid}.finding_normal.{lang}", aux["finding_normal"][lang]))
            impressions = []
            for x in found:
                fdef = n["findings"][x.id]
                imp = fdef["impression"][x.severity][lang] if x.severity else fdef["impression"][lang]
                impressions.append(_fill(imp, x.params))
            if not impressions:
                impressions = [rng.choice(aux["impression_normal"][lang])]
            rows.append((labels["finding"], "\n".join(paras)))
            numbered = [f"{i + 1}. {t}" for i, t in enumerate(impressions)] if len(impressions) > 1 else impressions
            rows.append((labels["impression"], "\n".join(numbered)))
            for x, imp in zip(found, impressions):
                truth.append({"label": labels["impression"], "value": imp, "abnormal": True, "finding": x.id,
                              "detection_method": "Imaging"})
            if not found:
                truth.append({"label": labels["impression"], "value": impressions[0], "abnormal": False,
                              "finding": None, "detection_method": "Imaging"})
        elif aux["kind"] == "params":
            b = n["book"][lang]
            catalogue = spec.indicators()
            for key in aux["parameters"]:
                r = readings.get(key)
                if r is None:
                    continue
                item = catalogue[key]
                name = item["en"] if lang == "en" else item["zh"]
                ref = spec.reference_text(key, person.sex)
                flag = "" if r.status == "normal" else ("↑" if r.status == "high" else "↓")
                text = f"{print_value(r.value, f)}{(' ' + r.unit) if r.unit else ''}{flag}"
                if ref:
                    text += f"（{b['reference']} {ref}）" if lang == "zh" else f" ({b['reference']} {ref})"
                rows.append((name, text))
                p_index = len(doc.printed)
                doc.printed.append(PrintedRow(item_name=name, item_value=print_value(r.value, f), item_unit=r.unit, item_range=ref,
                                              is_abnormal="1" if r.status != "normal" else "0"))
                doc.printed[p_index].readings.append(len(doc.readings))
                doc.readings.append(DocReading(key=key, loinc=r.loinc, canonical_value=r.canonical_value, value_text=r.value,
                                               unit_ucum=r.unit_ucum, value_kind=r.value_kind, status=r.status,
                                               observed=enc.exam_date.isoformat(), expect_resolvable=r.expect_resolvable,
                                               printed_row=p_index))
            if aux.get("finding_normal"):
                rows.append((labels["finding"], _pick(rng, f, f"narratives.aux.{aid}.finding_normal.{lang}", aux["finding_normal"][lang])))
            impressions = [_fill(n["findings"][x.id]["impression"][lang], x.params) for x in found] \
                or [rng.choice(aux["impression_normal"][lang])]
            numbered = [f"{i + 1}. {t}" for i, t in enumerate(impressions)] if len(impressions) > 1 else impressions
            rows.append((labels["impression"], "\n".join(numbered)))
            method = "Imaging" if aid == "echo" else "Physiological"
            truth += [{"label": labels["impression"], "value": imp, "abnormal": True, "finding": x.id,
                       "detection_method": method} for x, imp in zip(found, impressions)] or \
                     [{"label": labels["impression"], "value": impressions[0], "abnormal": False, "finding": None,
                       "detection_method": method}]
        elif aux["kind"] == "bmd":
            b = n["book"][lang]
            x = found[0] if found else None
            t = x.params.get("t") if x else round(rng.uniform(-0.9, 1.2), 1)
            rows.append((labels["finding"], f"L1–L4 {b['t_score']} {t:+.1f}"))
            imp = _fill(n["findings"][x.id]["impression"][lang], x.params) if x else rng.choice(aux["impression_normal"][lang])
            rows.append((labels["impression"], imp))
            truth.append({"label": labels["impression"], "value": imp, "abnormal": bool(x), "finding": x.id if x else None,
                          "detection_method": "Imaging"})
        elif aux["kind"] == "breath":
            b = n["book"][lang]
            x = found[0] if found else None
            dob = round(rng.uniform(4.5, 30.0), 1) if x else round(rng.uniform(0.1, 3.5), 1)
            rows.append((labels["finding"], f"{b['dob']} {dob}‰（{b['reference']} <4.0‰）" if lang == "zh"
                         else f"{b['dob']} {dob}‰ ({b['reference']} <4.0‰)"))
            imp = n["findings"][x.id]["impression"][lang] if x else rng.choice(aux["impression_normal"][lang])
            rows.append((labels["impression"], imp))
            truth.append({"label": labels["impression"], "value": imp, "abnormal": bool(x), "finding": x.id if x else None,
                          "detection_method": "laboratory"})
        blocks.append(Block(kind="narrative", title=aux[lang], rows=rows, truth=truth, image=image,
                            image_size=size, section_id=aid))
    return blocks


# ── 总检 ────────────────────────────────────────────────────────
def summary_block(rng: random.Random, doc: Doc, person: Person, enc: Encounter, f: Family) -> Block:
    n = _n()
    lang = _lang(f)
    s = n["summary"][lang]
    catalogue = spec.indicators()
    groups: dict[str, list[str]] = {}
    for r in enc.readings:
        if r.status == "normal" or r.key in n["lab_ignore"]:
            continue
        group = n["lab_groups"].get(f"{r.key}:{r.status}") or n["lab_groups"].get(r.key, "lab_other")
        item = catalogue[r.key]
        name = item["en"] if lang == "en" else item["zh"]
        arrow = ("↑" if r.status == "high" else "↓") if r.value_kind == "quantitative" else ""
        groups.setdefault(group, []).append(f"{name} {print_value(r.value, f)}{(' ' + r.unit) if r.unit else ''}{arrow}")
    conclusions: list[tuple[str, dict]] = []
    advice: list[tuple[str, dict]] = []
    for x in enc.findings:
        fdef = n["findings"][x.id]
        text = _fill(fdef["summary"][lang], x.params)
        if x.severity:
            text = _fill(fdef["impression"][x.severity][lang], x.params)
        conclusions.append((text, {"source": f"finding:{x.id}", "icpc3": fdef.get("icpc3"), "surface": fdef["surface"]}))
    used_advice: set[str] = set()
    for x in enc.findings:
        aid = n["findings"][x.id].get("advice")
        if aid and aid not in used_advice:
            used_advice.add(aid)
            advice.append((_wording(f, f"narratives.advice.{aid}.{lang}", n["advice"][aid][lang]),
                           {"source": f"finding:{x.id}", "template": aid}))
    for group, items in groups.items():
        text = "、".join(items) if lang == "zh" else ", ".join(items)
        conclusions.append((text, {"source": "lab:" + group, "keys": [i.split(" ")[0] for i in items]}))
        akey = group if group in n["advice"] else "lab_other"
        tmpl = _wording(f, f"narratives.advice.{akey}.{lang}", n["advice"][akey][lang])
        advice.append((_fill(tmpl, {"items": text}), {"source": "lab:" + group, "template": group}))
    if not conclusions:
        conclusions.append((_wording(f, f"narratives.advice.normal.{lang}", n["advice"]["normal"][lang]), {"source": "normal"}))
    rows: list[tuple[str, str]] = []
    truth: list[dict] = []
    lead = s["abnormal_lead"] if len(conclusions) > 1 or conclusions[0][1]["source"] != "normal" else ""
    numbered = [f"{i + 1}. {t}" for i, (t, _) in enumerate(conclusions)]
    rows.append((s["conclusion"], (lead + "\n" if lead else "") + "\n".join(numbered)))
    for i, (t, meta) in enumerate(conclusions):
        truth.append({"kind": "conclusion", "index": i + 1, "text": t, **meta})
    if advice:
        rows.append((s["advice"], "\n".join(f"{i + 1}. {t}" for i, (t, _) in enumerate(advice))))
        for i, (t, meta) in enumerate(advice):
            truth.append({"kind": "advice", "index": i + 1, "text": t, **meta})
    # 重要异常结果（《健康体检重要异常结果管理专家共识》A 类）：印一条通知，真值另记
    critical = critical_results(enc, person.sex)
    if critical:
        j = n["judgement"][lang]
        items = "、".join(critical) if lang == "zh" else ", ".join(critical)
        rows.append((j["critical_label"], _fill(j["critical_text"], {"items": items})))
        truth.append({"kind": "critical", "index": 1, "text": items, "source": "critical",
                      "keys": [c.split(" ")[0] for c in critical]})
    # 分级判定（A–E）：一部分机构印，按机构粘住
    if _sticky(f, "grades").random() < n["judgement"]["rate"].get(lang, 0.0):
        grades = judgement_grades(enc, person.sex)
        j = n["judgement"][lang]
        lines = [f"{j['areas'][area]}：{g}（{j['grade_labels'][g]}）" if lang == "zh"
                 else f"{j['areas'][area]}: {g} ({j['grade_labels'][g]})" for area, g in grades]
        rows.append((j["label"], "\n".join(lines)))
        for i, ((area, g), line) in enumerate(zip(grades, lines)):
            truth.append({"kind": "grade", "index": i + 1, "text": line, "source": "judgement", "area": area, "grade": g})
    closing = rng.choice(n["advice"]["closing"][lang])
    rows.append(("", closing))
    title = _sticky(f, "summary_title").choice(s["titles"])
    return Block(kind="summary", title=title, rows=rows, truth=truth, section_id="summary")


def _band_grade(bands: list, value: float) -> str:
    """bands = [[grade, lo, hi], ...]，lo/hi 可为 null；落在哪段就是哪级。都不落时按 A。"""
    for grade, lo, hi in bands:
        if (lo is None or value >= lo) and (hi is None or value < hi):
            return grade
    return "A"


def judgement_grades(enc: Encounter, sex: str) -> list[tuple[str, str]]:
    """每个判定区域（血压、血脂、肝功能……与各辅助检查、科室）一个字母：A 无异常 … E 治疗中。
    检验按数值分段，所见按具名所见的级别；一个区域取最重的。"""
    j = _n()["judgement"]
    order = "ABCDE"
    worst: dict[str, str] = {}

    def bump(area: str, grade: str) -> None:
        if order.index(grade) > order.index(worst.get(area, "A")):
            worst[area] = grade

    for r in enc.readings:
        area = j["area_of"].get(r.key)
        if not area:
            continue
        worst.setdefault(area, "A")
        if r.value_kind == "quantitative" and r.key in j["bands"]:
            bands = j["bands"][r.key]
            bands = bands[sex] if isinstance(bands, dict) else bands
            bump(area, _band_grade(bands, float(r.canonical_value)))
        elif r.value_kind == "qualitative" and r.status != "normal":
            bump(area, j["qualitative_grade"].get(r.value, "C"))
        elif r.status != "normal":
            bump(area, "B")
    for x in enc.findings:
        area = x.where
        worst.setdefault(area, "A")
        g = j["finding_grades"].get(x.id, "B")
        if isinstance(g, dict):
            g = g.get(x.severity or "", "B")
        bump(area, g)
    return [(area, worst[area]) for area in j["area_order"] if area in worst]


def critical_results(enc: Encounter, sex: str) -> list[str]:
    """超过 A 类重要异常结果阈值的读数，印成"指标 值 单位"。"""
    j = _n()["judgement"]["critical"]
    catalogue = spec.indicators()
    out: list[str] = []
    for r in enc.readings:
        rule = j.get(r.key)
        if not rule or r.value_kind != "quantitative":
            continue
        v = float(r.canonical_value)
        lo, hi = rule
        if (lo is not None and v <= lo) or (hi is not None and v >= hi):
            out.append(f"{catalogue[r.key]['zh']} {r.value} {r.unit}".strip())
    return out


# ── 体检报告书 ───────────────────────────────────────────────────
def build_book(rng: random.Random, doc_id: str, person: Person, enc: Encounter, groups: list[str],
               family: Family, previous: dict, banner: bool = True) -> Doc:
    """完整的体检报告书：封面 → 阅读说明 → 总检（先或后）→ 一般检查 → 科室 → 辅助检查 → 检验。"""
    n = _n()
    param_groups = {aid for aid, a in n["aux"].items() if a["kind"] == "params"}
    lab_groups = [g for g in groups if g not in param_groups] or groups
    doc = build_doc(rng, doc_id, person, enc, lab_groups, family, previous, banner=banner, book=True)
    doc.kind = "checkup_book"
    lang = _lang(family)
    b = n["book"][lang]
    summary_first = _sticky(family, "summary_pos").random() < 0.6
    summary = summary_block(rng, doc, person, enc, family)
    blocks: list[Block] = []
    if _sticky(family, "guide").random() < 0.5:
        blocks.append(Block(kind="narrative", title=b["guide"], rows=[("", "\n".join(b["guide_text"]))], section_id="guide"))
    if summary_first:
        blocks.append(summary)
    blocks.append(Block(kind="general_table", title=b["general"]))
    blocks += section_blocks(rng, doc, person, enc, family)
    aux = aux_blocks(rng, doc, person, enc, family)
    if aux:
        blocks.append(Block(kind="heading", title=b["aux"]))
        blocks += aux
    blocks.append(Block(kind="heading", title=b["lab"]))
    blocks.append(Block(kind="tables"))
    if not summary_first:
        blocks.append(summary)
    doc.blocks = blocks
    checkup_no = synthid.make(rng, 10)
    doc.cover = {"title": _sticky(family, "cover").choice(b["cover_titles"]),
                 "fields": [(b["checkup_no"][0], checkup_no),
                            (b["checkup_date"], enc.exam_date.isoformat()),
                            (b["package"], n["packages"][enc.package or "standard"][lang])]}
    doc.findings_truth = [_finding_truth(x, lang) for x in enc.findings]
    doc.summary_truth = summary.truth
    return doc


def _finding_truth(x: Finding, lang: str) -> dict:
    fdef = _n()["findings"][x.id]
    return {"id": x.id, "where": x.where, "item": x.item, "surface": x.surface, "icpc3": x.icpc3,
            "expect": "coded" if x.icpc3 else "no-match", "severity": x.severity,
            "summary": _fill(fdef["summary"][lang], x.params), "since": x.since.isoformat() if x.since else None}


# ── 门诊病历 ─────────────────────────────────────────────────────
def build_outpatient(rng: random.Random, doc_id: str, person: Person, enc: Encounter, family: Family,
                     banner: bool = True) -> Doc:
    """门诊病历：主诉 / 现病史 / 既往史 / 体格检查 / 辅助检查 / 诊断 / 处理。
    生命体征印成一行（T P R BP 体重），是印刷真值；主诉与诊断进各自的真值。"""
    n = _n()
    lang = _lang(family)
    o = n["outpatient"][lang]
    t = spec.templates()
    readings = {r.key: r for r in enc.readings}
    doc = Doc(doc_id=doc_id, person_id=person.person_id, family=family,
              title=_sticky(family, "op_title").choice(o["titles"]),
              subject=subject_fields(rng, person, family, enc.exam_date, rng.choice(t["departments"][family.lang_group]), "")[:4],
              dates=print_dates(rng, family, enc.exam_date)[:1], tables=[], banner=banner)
    doc.kind = "outpatient_record"
    # 主诉
    dx_key = person.archetype
    uri = any(e.name == "急性上呼吸道感染" and abs((enc.exam_date - e.start).days) <= 10 for e in person.events)
    if uri:
        dx_key = "uri"
    dx_surface = o["dx_surfaces"].get(dx_key)
    scripted = [e for e in person.events if e.note == "原型剧本"]
    on_med = bool(scripted) and scripted[0].start <= enc.exam_date and person.archetype in o["drugs"]
    if enc.complaints:
        c1 = enc.complaints[0]
        c2 = enc.complaints[1] if len(enc.complaints) > 1 else None
        ph = spec.complaints()["phrasing"][lang]
        template = rng.choice([x for x in ph["cc"] if ("{s2}" in x) == (c2 is not None)])
        template = _wording(family, f"complaints.phrasing.{lang}.cc.{ph['cc'].index(template)}", template)
        cc = template.format(s=c1.text, s2=c2.text if c2 else "", dur=c1.duration or rng.choice(ph["durations"]))
        s2_clause = o["s2_clause"].format(s2=c2.text) if c2 else ""
        history_clause = o["history_clause"]["medication" if on_med else "none"].format(drug=o["drugs"].get(person.archetype, ""))
        hpi = rng.choice(o["hpi_templates"]).format(
            dur=c1.duration or rng.choice(ph["durations"]), s1=c1.text, s1_cap=c1.text[:1].upper() + c1.text[1:],
            s2_clause=s2_clause, course=rng.choice(o["course_words"]), history_clause=history_clause)
    else:
        cc = rng.choice(o["followup_cc"]).format(dx=dx_surface or ("复查" if lang == "zh" else "review"))
        hpi = o["history_clause"]["medication" if on_med else "none"].format(drug=o["drugs"].get(person.archetype, ""))
    years = max(1, (enc.exam_date - (scripted[0].start if scripted else enc.exam_date)).days // 365 + 1)
    if person.archetype != "healthy" and not uri:
        pmh = o["pmh_templates"][0].format(dx=o["dx_surfaces"][person.archetype], years=years,
                                           drug_clause=o["drug_clause"]["medication" if on_med else "none"].format(
                                               drug=o["drugs"].get(person.archetype, "")))
    else:
        pmh = o["pmh_templates"][1]
    v = {k: (readings[k].value if k in readings else "-") for k in ("temp", "pulse", "resp", "sbp", "dbp", "weight")}
    vitals_line = o["vitals_line"].format(**v)
    other = [r for r in enc.readings if r.key not in ("temp", "pulse", "resp", "sbp", "dbp", "weight")]
    catalogue = spec.indicators()
    aux_text = ""
    if other:
        parts = []
        for r in other[:8]:
            item = catalogue[r.key]
            name = item.get("abbr") or (item["en"] if lang == "en" else item["zh"])
            parts.append(f"{name} {r.value}{r.unit}{'↑' if r.status == 'high' else '↓' if r.status == 'low' else ''}")
        aux_text = ("；".join(parts) if lang == "zh" else "; ".join(parts)) + ("（详见检验报告单）" if lang == "zh" else " (see laboratory report)")
    dx_lines = [dx_surface] if dx_surface else []
    plan = rng.choice(o["plan_templates"]).format(
        labs=o["monitor"].get(person.archetype, o["monitor"]["healthy"]), interval="3个月" if lang == "zh" else "3 months",
        monitor=o["monitor"].get(person.archetype, o["monitor"]["healthy"]))
    rows = [(o["cc"], cc), (o["hpi"], hpi), (o["pmh"], pmh), (o["pe"], vitals_line)]
    if aux_text:
        rows.append((o["aux"], aux_text))
    rows += [(o["dx"], "\n".join(f"{i + 1}. {d}" for i, d in enumerate(dx_lines)) if len(dx_lines) > 1 else (dx_lines[0] if dx_lines else "")),
             (o["plan"], plan)]
    doc.blocks = [Block(kind="narrative", title="", rows=rows, section_id="outpatient")]
    # 印刷真值：生命体征那一行
    for key, printed_name, unit in (("temp", "T", "℃" if lang == "zh" else "°C"), ("pulse", "P", "次/分" if lang == "zh" else "/min"),
                                    ("resp", "R", "次/分" if lang == "zh" else "/min"), ("weight", "体重" if lang == "zh" else "Weight", "kg")):
        r = readings.get(key)
        if not r:
            continue
        p_index = len(doc.printed)
        doc.printed.append(PrintedRow(item_name=printed_name, item_value=r.value, item_unit=unit, item_range="", is_abnormal=""))
        doc.printed[p_index].readings.append(len(doc.readings))
        doc.readings.append(DocReading(key=key, loinc=r.loinc, canonical_value=r.canonical_value, value_text=r.value,
                                       unit_ucum=r.unit_ucum, value_kind=r.value_kind, status=r.status,
                                       observed=enc.exam_date.isoformat(), expect_resolvable=r.expect_resolvable, printed_row=p_index))
    if "sbp" in readings and "dbp" in readings:
        p_index = len(doc.printed)
        doc.printed.append(PrintedRow(item_name="BP", item_value=f"{readings['sbp'].value}/{readings['dbp'].value}",
                                      item_unit="mmHg", item_range="", is_abnormal=""))
        for key in ("sbp", "dbp"):
            r = readings[key]
            doc.printed[p_index].readings.append(len(doc.readings))
            doc.readings.append(DocReading(key=key, loinc=r.loinc, canonical_value=r.canonical_value, value_text=r.value,
                                           unit_ucum=r.unit_ucum, value_kind=r.value_kind, status=r.status,
                                           observed=enc.exam_date.isoformat(), expect_resolvable=r.expect_resolvable, printed_row=p_index))
    doc.complaints_truth = [{"text": c.text, "symptom_id": c.symptom_id, "icpc3": c.icpc3, "expect": c.expect,
                             "duration": c.duration, "kind": "symptom"} for c in enc.complaints]
    icpc = {"hypertension": "KD73", "prediabetes_to_t2dm": "TD72", "dyslipidemia_statin": "TD75",
            "iron_deficiency_anemia": "BD66", "thyroid_disorder": "TD69", "ckd_progression": "UD66",
            "fatty_liver": "DD81", "uri": "RD02"}
    doc.diagnoses_truth = [{"text": d, "kind": "condition", "icpc3": icpc.get(dx_key), "expect": "coded" if icpc.get(dx_key) else "no-match"}
                           for d in dx_lines]
    doc.footer = [(o["doctor"], rng.choice(spec.fiction()["person_names_en" if lang == "en" else "person_names_zh"]))]
    return doc


# ── 心电图报告、超声报告 ────────────────────────────────────────
def build_ecg_report(rng: random.Random, doc_id: str, person: Person, enc: Encounter, family: Family,
                     previous: dict, banner: bool = True) -> Doc:
    doc = build_doc(rng, doc_id, person, enc, ["ecg"], family, previous, banner=banner)
    doc.kind = "ecg_report"
    lang = _lang(family)
    e = _n()["ecg_report"][lang]
    doc.title = _sticky(family, "ecg_title").choice(e["titles"])
    blocks = aux_blocks(rng, doc, person, enc, family, only=["ecg"])
    for b in blocks:
        b.title = ""
        b.rows.insert(0, (e["rhythm"], e["sinus"]))
    doc.blocks = [Block(kind="tables")] + blocks
    doc.findings_truth = [_finding_truth(x, lang) for x in enc.findings if x.where == "ecg"]
    return doc


def build_ultrasound_report(rng: random.Random, doc_id: str, person: Person, enc: Encounter, family: Family,
                            banner: bool = True) -> Doc:
    n = _n()
    lang = _lang(family)
    t = spec.templates()
    aux = n["aux"]["abd_us"]
    doc = Doc(doc_id=doc_id, person_id=person.person_id, family=family, title=aux[lang],
              subject=subject_fields(rng, person, family, enc.exam_date, rng.choice(t["departments"][family.lang_group]), "")[:5],
              dates=print_dates(rng, family, enc.exam_date)[:2], tables=[], banner=banner)
    doc.kind = "ultrasound_report"
    doc.blocks = aux_blocks(rng, doc, person, enc, family, only=["abd_us"])
    for b in doc.blocks:
        b.title = ""
    doc.findings_truth = [_finding_truth(x, lang) for x in enc.findings if x.where == "abd_us"]
    doc.footer = [(role, rng.choice(spec.fiction()["person_names_en" if lang == "en" else "person_names_zh"]))
                  for role in (family.signatures or (("检查者",) if lang == "zh" else ("Examiner",)))]
    return doc


# ── 居家记录表（血压日记 / 晨起体重）────────────────────────────
def build_home_log(rng: random.Random, doc_id: str, person: Person, window: list[dict], family: Family,
                   log_kind: str, banner: bool = True) -> Doc:
    """一张表里若干天的血压或体重：每一行有自己的日期。这是 mirobody 已知的失败模式
    （`resolve_report_date` 给整份文件一个日期，demo 实测"7 行进、1 行出"）的规模化素材。
    没有参考范围、没有标记；`is_abnormal` 一律无法判定；`observed` 逐行不同。"""
    n = _n()
    lang = _lang(family)
    hl = n["home_log"][lang]
    title = _sticky(family, "log_title").choice(hl["bp_titles"] if log_kind == "bp" else hl["weight_titles"])
    name_only = rng.random() < 0.5
    subject = subject_fields(rng, person, family, date.fromisoformat(window[0]["day"]), "", "")[:1] if name_only else []
    doc = Doc(doc_id=doc_id, person_id=person.person_id, family=family, title=title, subject=subject,
              dates=[], tables=[], banner=banner)
    doc.kind = "home_log"
    if log_kind == "bp":
        roles = ["log_date", "log_time", "log_sbp", "log_dbp", "log_pulse", "log_note"]
        header = [hl["date"], hl["time"], hl["sbp"], hl["dbp"], hl["pulse"], hl["note"]]
    else:
        roles = ["log_date", "log_time", "log_weight", "log_note"]
        header = [hl["date"], hl["time"], hl["weight"], hl["note"]]
    unit_in_header = rng.random() < 0.5
    if unit_in_header:
        units = {"log_sbp": "mmHg", "log_dbp": "mmHg", "log_pulse": hl["unit_row"][4], "log_weight": "kg"}
        header = [f"{h}({units[r]})" if r in units else h for h, r in zip(header, roles)]
    headers = [header]
    rows: list[Cells] = []
    date_fmt = rng.choice(["YYYY-MM-DD", "YYYY/MM/DD", "MM-DD", "M月D日"])
    for d in window:
        entries = d["records"] if log_kind == "bp" else [r for r in d["records"] if r["_metric"] == "weight"]
        if log_kind == "bp":
            by_time: dict[str, dict[str, dict]] = {}
            for r in d["records"]:
                by_time.setdefault(r["time"][11:16], {})[r["_metric"]] = r
            entries = [dict(time=tm, **vals) for tm, vals in sorted(by_time.items())]
        for e in entries:
            when = date.fromisoformat(d["day"])
            printed_date = format_date(datetime(when.year, when.month, when.day), date_fmt) if date_fmt != "MM-DD" \
                else f"{when.month:02d}-{when.day:02d}"
            raw = {"log_date": printed_date, "log_time": e["time"] if log_kind == "bp" else e["time"][11:16],
                   "log_note": rng.choice(hl["notes"])}
            readings = []
            if log_kind == "bp":
                for metric, role in (("sbp", "log_sbp"), ("dbp", "log_dbp"), ("hr", "log_pulse")):
                    r = e.get(metric)
                    raw[role] = str(r["value"]) if r else ""
                    if r:
                        readings.append((metric if metric != "hr" else "pulse", role, str(r["value"]),
                                         "mmHg" if metric != "hr" else hl["unit_row"][4]))
            else:
                raw["log_weight"] = str(e["value"])
                readings.append(("weight", "log_weight", str(e["value"]), "kg"))
            for key, role, value, unit in readings:
                item = spec.indicators()[key]
                p_index = len(doc.printed)
                doc.printed.append(PrintedRow(item_name=header[roles.index(role)], item_value=value,
                                              item_unit=unit if unit_in_header else "", item_range="", is_abnormal=""))
                doc.printed[p_index].readings.append(len(doc.readings))
                doc.readings.append(DocReading(key=key, loinc=item.get("loinc"), canonical_value=float(value),
                                               value_text=value, unit_ucum=item["unit"], value_kind="quantitative",
                                               status="", observed=when.isoformat(), role="export",
                                               expect_resolvable=item.get("expect_resolvable", False), printed_row=p_index))
            rows.append(Cells(name="", value="", unit="", range="", flag="", raw=raw))
    doc.tables.append(Table(columns=roles, headers=headers, rows=rows))
    return doc


# ── 影像报告单（胸片 / 胸部 CT）────────────────────────────────
def build_imaging_report(rng: random.Random, doc_id: str, person: Person, enc: Encounter, family: Family,
                         banner: bool = True) -> Doc:
    """放射科的报告单：检查所见 + 影像诊断。真实语料里 CT 报告 11 份、放射诊断报告 8 份。"""
    n = _n()
    lang = _lang(family)
    t = spec.templates()
    which = "chest_ct" if (person.age_at(enc.exam_date) >= 40 and rng.random() < 0.6) else "chest_xray"
    aux = n["aux"][which]
    doc = Doc(doc_id=doc_id, person_id=person.person_id, family=family, title=aux[lang],
              subject=subject_fields(rng, person, family, enc.exam_date, rng.choice(t["departments"][family.lang_group]), "")[:5],
              dates=print_dates(rng, family, enc.exam_date)[:2], tables=[], banner=banner)
    doc.kind = "imaging_report"
    doc.blocks = aux_blocks(rng, doc, person, enc, family, only=[which])
    for b in doc.blocks:
        b.title = ""
    doc.findings_truth = [_finding_truth(x, lang) for x in enc.findings if x.where == which]
    doc.footer = [(role, rng.choice(spec.fiction()["person_names_en" if lang == "en" else "person_names_zh"]))
                  for role in (family.signatures or (("报告医师",) if lang == "zh" else ("Radiologist",)))]
    return doc
