"""Handwriting vocabulary and font subsets → `resources/handwriting.json` and `mirobody_gen/render/fonts/`.

    python3 scripts/build_handwriting.py                       # print the inventory and font coverage
    python3 scripts/build_handwriting.py --write               # write resources/handwriting.json
    python3 scripts/build_handwriting.py --fonts DIR --write   # also rebuild the font subsets from upstream files

## What a hand may write

Everything a handwritten page can carry is a closed inventory: the strings below, digits, ASCII and a
handful of symbols. Notebook logs, a doctor's note and a filled-in form need a few hundred Chinese
characters, not the tens of thousands a CJK font carries, so the fonts are subset to exactly the
characters the generator can emit (`inventory()`), which keeps each face under the repository's 1 MB
file limit. A string added here with a character outside the subsets fails
`tests/test_handwriting.py::test_fonts_cover_every_character_a_hand_can_write` until the subsets are
rebuilt with `--fonts`.

All wording is hand-authored, generic clinical shorthand and diary phrasing; none of it comes from a
real record. Numbers never live here: values come from the physiology model, dates from the timeline.

## Fonts

Upstream files are the OFL / Apache-2.0 handwriting families from the google/fonts repository, pinned
by the commit that last changed each file and by sha256 (`FONTS`). `--fonts DIR` expects those files in
DIR laid out as in the upstream repository (`DIR/ofl/mashanzheng/MaShanZheng-Regular.ttf`,
`DIR/ofl/mashanzheng/OFL.txt`, …: a sparse checkout works), refuses any whose sha256 differs, subsets them with fontTools (layout
tables, hinting and unused glyphs dropped; Caveat instanced at one weight), renames every subset to a
neutral family name (Nanum Pen Script declares Reserved Font Names, and a subset is a Modified Version
under the OFL), keeps the copyright and licence name records, and writes the licence texts next to the
subsets. Output is deterministic: the head timestamp is not recalculated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
RESOURCES = REPO / "mirobody_gen" / "resources"
FONT_DIR = REPO / "mirobody_gen" / "render" / "fonts"
OUT = RESOURCES / "handwriting.json"

# ── Fonts: upstream identity ─────────────────────────────────────────
#: id → upstream identity. `commit` is the google/fonts commit that last changed the file, so
#: https://raw.githubusercontent.com/google/fonts/<commit>/<path> is the exact file whose sha256 is given
#: (`git_blob` is its git object id, what the GitHub API lists). `stroke` is the pen weights a writer of
#: this face may draw with: brush faces (Ma Shan Zheng, Zhi Mang Xing) only get finer, thin pen faces
#: (Long Cang, Nanum Pen) only heavier — a thinned hairline fades to grey after a phone photo.
FONTS: dict[str, dict] = {
    "zh-kai": {"family": "Ma Shan Zheng", "script": "zh", "stroke": [-1, -1, 0], "style": "regular script (kaishu)",
               "path": "ofl/mashanzheng/MaShanZheng-Regular.ttf", "license_path": "ofl/mashanzheng/OFL.txt",
               "commit": "406197b91ff39a93061c2c2eeaee67ddf2ae1f0d", "license": "OFL-1.1",
               "sha256": "6d2546bb189c732a8ca29af9e22457b152387d158aa459e4ac2ce1e51788b7fb",
               "git_blob": "11bbfb9867d70612158229d037e7a5f622bd0e38", "bytes": 5857936},
    "zh-xing": {"family": "Zhi Mang Xing", "script": "zh", "stroke": [-1, 0], "style": "running script (xingshu), brush",
                "path": "ofl/zhimangxing/ZhiMangXing-Regular.ttf", "license_path": "ofl/zhimangxing/OFL.txt",
                "commit": "b12c22f97f4769802373d8de6a0f4115eabb9a24", "license": "OFL-1.1",
                "sha256": "644e0cae9b40f0b10ab729a01bd32032e3973bac22be3dccae01bf6ae7fde969",
                "git_blob": "d037d1f00395175de01de33e62957c30a008a1b1", "bytes": 4063532},
    "zh-cao": {"family": "Liu Jian Mao Cao", "script": "zh", "stroke": [0, 1], "style": "cursive script (caoshu)",
               "path": "ofl/liujianmaocao/LiuJianMaoCao-Regular.ttf", "license_path": "ofl/liujianmaocao/OFL.txt",
               "commit": "0bf38a8a393773912e43d57dfb8b45e7fb868044", "license": "OFL-1.1",
               "sha256": "cab396b91a5b7c0b4005a35891180d06e6751f5ac261fe680aec65c1ae209033",
               "git_blob": "f380deef04a5ee6d5af2c6fba60d1613d831c1aa", "bytes": 4951804},
    "zh-pen": {"family": "Long Cang", "script": "zh", "stroke": [0, 1, 1], "style": "running hand, hard pen",
               "path": "ofl/longcang/LongCang-Regular.ttf", "license_path": "ofl/longcang/OFL.txt",
               "commit": "20da2e7644bac5325abbc9f30848ae3162bfc974", "license": "OFL-1.1",
               "sha256": "e5bf2c3f24ef2327c6f136d8f73e2f9dfdf44896fdbeb35a9515f44777bb91bc",
               "git_blob": "363bb08696fe4d3827bd36d3d2fbc028d1205161", "bytes": 5162508},
    "en-casual": {"family": "Caveat", "script": "en", "stroke": [0, 0, 1], "style": "casual joined hand",
                  "path": "ofl/caveat/Caveat[wght].ttf", "license_path": "ofl/caveat/OFL.txt",
                  "commit": "a85fc09e44c70c7159761adfdc9d5dd007792c15", "license": "OFL-1.1",
                  "sha256": "0bdb6b660482d31531b3945849fba5916b3ef8695da7024a9e6b9ee3c4157988",
                  "git_blob": "f84acf2ce2d1b3329b194749530dbaec047e6df6", "bytes": 403648, "instance": {"wght": 450}},
    "en-cursive": {"family": "Homemade Apple", "script": "en", "stroke": [0, 1], "style": "looped cursive",
                   "path": "apache/homemadeapple/HomemadeApple-Regular.ttf",
                   "license_path": "apache/homemadeapple/LICENSE.txt",
                   "commit": "8e94f9c1272836a25d2e65bd7d729e63ac9edb66", "license": "Apache-2.0",
                   "sha256": "dd1baaca3cde1b1f8415aed3b0aea6808655c9d9ca5a99c7282d9accc16c1a58",
                   "git_blob": "bea3c3094de8340f5287e40ef84fe7f670e06f2e", "bytes": 110004},
    "en-pen": {"family": "Nanum Pen Script", "script": "en", "stroke": [0, 1, 1], "style": "narrow felt-pen print",
               "path": "ofl/nanumpenscript/NanumPenScript-Regular.ttf", "license_path": "ofl/nanumpenscript/OFL.txt",
               "commit": "16680f8688ffcd467d2eb2146a9ce0343404581d", "license": "OFL-1.1",
               "sha256": "6f0d1ab29c7894010dc88831fb7a0a51edb79136e450344183de5b1a8b52bd43",
               "git_blob": "565747d0573126bb057d0675151c894bf019d7a5", "bytes": 3201664},
    "en-print": {"family": "Indie Flower", "script": "en", "stroke": [0, 1], "style": "rounded print hand",
                 "path": "ofl/indieflower/IndieFlower-Regular.ttf", "license_path": "ofl/indieflower/OFL.txt",
                 "commit": "48e30c13625133283f79042e86693a5d04c6bfa0", "license": "OFL-1.1",  # privacy-gate:ok (a commit id)
                 "sha256": "ccc94b22b156e9c5dfe50fd051f01b097600b252c24473e624bb43a143140a94",
                 "git_blob": "547ca58030e8a7de5aca859a11fc00f14511a7ea", "bytes": 108196},
}

# ── Tiers ────────────────────────────────────────────────────────────
#: H1 neat, H2 running, H3 hard cursive. A tier fixes which faces a writer may use and how far the pen
#: wanders from the font; capture (scan or phone photo) is drawn per page from the tier's own weights,
#: and H3 is always a phone photo. Jitter amplitudes were set by eye on rendered pages: past these values
#: digits stop being readable to a careful reader, which is the line this corpus may not cross.
TIERS = {
    "H1": {
        "description": "neat: regular-script Chinese, print-like English letters",
        "weight": 0.38,
        "faces": {"zh": {"zh-kai": 1}, "en": {"en-print": 2, "en-pen": 1}},
        "jitter": {"rotate_deg": 1.5, "baseline": 0.03, "size": 0.035, "spacing": 0.05, "slant_deg": [-2, 5],
                   "drift_deg": 0.45, "warp": 0.012},
        "capture": {"T2": 0.6, "T3": 0.4},
        "scenes": {"T2": {"flatbed_scan": 55, "app_enhanced": 45}, "T3": {"clean_photo": 70, "phone_flat_top": 30}},
        "severity": {"mild": 0.7, "moderate": 0.3},
    },
    "H2": {
        "description": "running hand: semi-cursive Chinese (xingshu), casual joined English",
        "weight": 0.42,
        "faces": {"zh": {"zh-xing": 1, "zh-pen": 2}, "en": {"en-casual": 2, "en-pen": 1}},
        "jitter": {"rotate_deg": 2.6, "baseline": 0.045, "size": 0.05, "spacing": 0.08, "slant_deg": [0, 9],
                   "drift_deg": 0.8, "warp": 0.02},
        "capture": {"T2": 0.4, "T3": 0.6},
        "scenes": {"T2": {"flatbed_scan": 50, "app_enhanced": 50},
                   "T3": {"clean_photo": 40, "phone_flat_top": 35, "wechat_photo": 25}},
        "severity": {"mild": 0.55, "moderate": 0.45},
    },
    "H3": {
        "description": "hard cursive (caoshu, looped joined script), always a phone photo",
        "weight": 0.20,
        "faces": {"zh": {"zh-cao": 1, "zh-xing": 1}, "en": {"en-cursive": 1}},
        "jitter": {"rotate_deg": 3.2, "baseline": 0.055, "size": 0.06, "spacing": 0.1, "slant_deg": [3, 12],
                   "drift_deg": 1.1, "warp": 0.026},
        "capture": {"T3": 1.0},
        "scenes": {"T3": {"phone_flat_top": 35, "wechat_photo": 30, "phone_oblique": 15, "phone_lowlight": 20}},
        "severity": {"mild": 0.5, "moderate": 0.5},
    },
}

#: Ballpoint and gel inks as scanned (sRGB), with how often each is the pen in hand.
INKS = {
    "blue": {"rgb": [28, 48, 150], "weight": 0.5},
    "blue_black": {"rgb": [30, 38, 86], "weight": 0.2},
    "black": {"rgb": [30, 30, 36], "weight": 0.3},
}

#: How often each kind of handwritten file appears, per eligible occasion.
RATES = {
    "bp_log": 0.6,          # a person with a home cuff keeps a notebook page of readings
    "glucose_log": 0.75,    # a person on the prediabetes → type 2 diabetes course self-monitors
    "weight_log": 0.35,     # a person who weighs at least every other day
    "clinic_note": 0.45,    # a clinic visit with vitals leaves a handwritten note
    "form": 0.18,           # a check-up's general examination sheet is filled in by hand
}

#: The messes a real page carries, per page. Rates are hand-set: a correction on roughly every other
#: notebook page, a ditto mark whenever a day has a second reading.
MESSES = {
    "correction_log": 0.45,
    "correction_note": 0.2,
    "correction_form": 0.25,
    "second_reading": 0.45,       # a BP day with an evening reading under the morning one
    "unit_in_header": 0.5,        # the unit written once with the column label
    "unit_nowhere": 0.3,          # of the rest: no unit at all (otherwise after every value)
    "ruled_columns": 0.5,         # columns ruled by hand
    "page_year": 0.7,             # a month-and-year line under the title
}

# ── What a hand writes ───────────────────────────────────────────────
LOGS = {
    "zh": {
        "bp": {
            "titles": ["血压记录", "家庭血压记录", "量血压记录", "血压监测"],
            "columns": [
                {"date": "日期", "time": "时间", "bp": "血压", "pulse": "心率", "note": "备注"},
                {"date": "日期", "time": "时间", "bp": "血压", "pulse": "脉搏", "note": "备注"},
                {"date": "日期", "time": "时间", "sbp": "高压", "dbp": "低压", "pulse": "心率", "note": "备注"},
                {"date": "日期", "time": "时间", "sbp": "收缩压", "dbp": "舒张压", "pulse": "脉搏", "note": "备注"},
            ],
            "units": {"bp": "mmHg", "sbp": "mmHg", "dbp": "mmHg", "pulse": "次/分"},
            "time_words": {"morning": "早", "evening": "晚"},
            "notes": ["服药后", "服药前", "起床后", "早饭前", "运动后", "有点头晕", "睡得不好", "左臂", "右臂",
                      "复测", "喝了咖啡", "忘记吃药", "感觉还好"],
        },
        "glucose": {
            "titles": ["血糖记录", "血糖监测", "测血糖记录"],
            "columns": [
                {"date": "日期", "fasting": "空腹", "breakfast": "早餐后", "lunch": "午餐后", "dinner": "晚餐后",
                 "note": "备注"},
                {"date": "日期", "fasting": "空腹", "post": "餐后2小时", "note": "备注"},
            ],
            "units": {"glucose": "mmol/L"},
            "notes": ["吃了面条", "聚餐", "没吃早饭", "运动后", "吃了水果", "加餐", "感冒了", "按时服药"],
        },
        "weight": {
            "titles": ["体重记录", "每天称体重", "体重打卡"],
            "columns": [{"date": "日期", "weight": "体重", "note": "备注"}],
            "units": {"weight": "kg"},
            "notes": ["早上空腹", "晚饭吃多了", "运动后", "便后", "穿睡衣", "出差"],
        },
        "date_formats": ["{m}/{d}", "{m}.{d}", "{m}月{d}日", "{m}-{d}"],
        "month_line": "{y}年{m}月",
    },
    "en": {
        "bp": {
            "titles": ["Blood pressure log", "BP record", "Home BP readings", "Blood pressure diary"],
            "columns": [
                {"date": "Date", "time": "Time", "bp": "BP", "pulse": "Pulse", "note": "Notes"},
                {"date": "Date", "time": "Time", "bp": "Blood pressure", "pulse": "HR", "note": "Comments"},
                {"date": "Date", "time": "Time", "sbp": "Sys", "dbp": "Dia", "pulse": "Pulse", "note": "Notes"},
            ],
            "units": {"bp": "mmHg", "sbp": "mmHg", "dbp": "mmHg", "pulse": "bpm"},
            "time_words": {"morning": "AM", "evening": "PM"},
            "notes": ["after meds", "before meds", "on waking", "before breakfast", "after walk", "bit dizzy",
                      "poor sleep", "left arm", "right arm", "retaken", "coffee", "missed dose", "felt fine"],
        },
        "glucose": {
            "titles": ["Blood sugar log", "Glucose readings", "Blood glucose diary"],
            "columns": [
                {"date": "Date", "fasting": "Fasting", "breakfast": "After breakfast", "lunch": "After lunch",
                 "dinner": "After dinner", "note": "Notes"},
                {"date": "Date", "fasting": "Fasting", "post": "2h after meal", "note": "Notes"},
            ],
            "units": {"glucose": "mmol/L"},
            "notes": ["pasta", "ate out", "skipped breakfast", "after walk", "fruit", "snack", "had a cold",
                      "meds on time"],
        },
        "weight": {
            "titles": ["Weight log", "Morning weight", "Weigh-ins"],
            "columns": [{"date": "Date", "weight": "Weight", "note": "Notes"}],
            "units": {"weight": "kg"},
            "notes": ["before breakfast", "big dinner", "after run", "in pyjamas", "travelling"],
        },
        "date_formats": ["{d} {mon}", "{mon} {d}", "{d}/{m}", "{d}.{m}"],
        "month_line": "{month} {y}",
        "months": ["January", "February", "March", "April", "May", "June", "July", "August", "September",
                   "October", "November", "December"],
        "months_short": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    },
}

#: A doctor's note in a clinic booklet. `vitals` gives each reading's label and the units a doctor may
#: write after it ("" = no unit); `dx` follows the person's archetype, `dx` "uri" the acute visit.
NOTE = {
    "zh": {
        "header": ["门诊病历", "门诊记录"],
        "fields": {"department": "科别", "date": "日期"},
        "departments": ["内科", "心内科", "内分泌科", "全科"],
        "visit": {"followup": ["复诊", "{dx}复诊"], "first": ["初诊"]},
        "status": {
            "general": ["自诉无明显不适", "一般情况可", "饮食睡眠可", "二便正常", "近期无特殊不适"],
            "hypertension": ["偶有头晕", "血压控制可", "家中自测血压偏高"],
            "prediabetes_to_t2dm": ["口干多饮不明显", "血糖控制尚可", "空腹血糖偏高"],
            "uri": ["咽痛，咳嗽", "鼻塞流涕", "咳嗽，少痰"],
        },
        "vitals": {
            # "℃" is one glyph none of the faces has; a hand writes it as "°C" here, as many do.
            "temp": {"labels": ["T", "T:"], "units": ["°C", ""]},
            "pulse": {"labels": ["P", "P:"], "units": ["次/分", ""]},
            "resp": {"labels": ["R", "R:"], "units": ["次/分", ""]},
            "bp": {"labels": ["BP", "BP:"], "units": ["mmHg", ""]},
            "weight": {"labels": ["体重", "Wt"], "units": ["kg"]},
            "spo2": {"labels": ["SpO2"], "units": ["%"]},
        },
        "exam": ["心肺听诊未见异常", "双肺呼吸音清", "心律齐", "腹软无压痛", "双下肢无水肿"],
        "exam_uri": ["咽部充血", "双肺呼吸音清"],
        "dx_label": ["诊断：", "印象："],
        "dx": {"hypertension": "高血压", "prediabetes_to_t2dm": "2型糖尿病", "dyslipidemia_statin": "血脂异常",
               "iron_deficiency_anemia": "缺铁性贫血", "thyroid_disorder": "甲状腺功能减退",
               "ckd_progression": "慢性肾脏病", "fatty_liver": "脂肪肝", "uri": "上呼吸道感染", "healthy": "健康查体"},
        "plan_label": ["处理：", "建议："],
        "plans": ["继续原方案治疗", "低盐低脂饮食", "监测血压", "监测血糖", "适当运动", "定期复查", "多饮水，注意休息",
                  "不适随诊"],
        "date_formats": ["{y}.{m}.{d}", "{y}-{m}-{d}", "{y}年{m}月{d}日"],
    },
    "en": {
        "header": ["Progress notes", "Clinic notes"],
        "fields": {"date": "Date"},
        "departments": ["Medicine", "Cardiology", "Endocrinology", "General practice"],
        "visit": {"followup": ["Review", "F/U {dx}"], "first": ["New patient"]},
        "status": {
            "general": ["No complaints.", "Feels well.", "Eating and sleeping well.", "No new symptoms."],
            "hypertension": ["Occasional dizziness.", "Home BP readings higher."],
            "prediabetes_to_t2dm": ["No thirst or polyuria.", "Fasting sugars a bit high."],
            "uri": ["Sore throat, cough.", "Runny nose, mild fever."],
        },
        "vitals": {
            "temp": {"labels": ["T", "Temp"], "units": ["°C", ""]},
            "pulse": {"labels": ["HR", "P", "Pulse"], "units": ["bpm", ""]},
            "resp": {"labels": ["RR"], "units": [""]},
            "bp": {"labels": ["BP"], "units": ["mmHg", ""]},
            "weight": {"labels": ["Wt", "Weight"], "units": ["kg"]},
            "spo2": {"labels": ["SpO2", "Sats"], "units": ["%"]},
        },
        "exam": ["Chest clear.", "Heart sounds normal.", "Abdomen soft, non-tender.", "No ankle oedema."],
        "exam_uri": ["Throat red.", "Chest clear."],
        "dx_label": ["Imp:", "Dx:"],
        "dx": {"hypertension": "HTN", "prediabetes_to_t2dm": "T2DM", "dyslipidemia_statin": "Dyslipidaemia",
               "iron_deficiency_anemia": "Iron deficiency anaemia", "thyroid_disorder": "Hypothyroidism",
               "ckd_progression": "CKD", "fatty_liver": "Fatty liver", "uri": "URTI", "healthy": "Health check"},
        "plan_label": ["Plan:"],
        "plans": ["continue current meds", "low salt diet", "home BP monitoring", "check sugars at home",
                  "regular exercise", "bloods next visit", "fluids and rest", "return if worse"],
        "date_formats": ["{d} {mon} {y}", "{d}/{m}/{y}", "{d}.{m}.{y}"],
    },
}

#: A printed form whose result column is filled in by hand: titles and the column headings it prints
#: when the institution's own layout has no spelling for a role.
FORM = {
    "zh": {"titles": ["健康体检表", "一般检查记录表", "体格检查表"],
           "headers": {"name": "项目", "result": "结果", "unit": "单位", "reference": "参考范围"}},
    "en": {"titles": ["Physical examination form", "Vital signs record", "General examination record"],
           "headers": {"name": "Item", "result": "Result", "unit": "Unit", "reference": "Reference range"}},
}

#: Hazard classes that only handwriting produces. The other classes a handwritten file carries
#: (`value.pair_in_one_cell`, `unit.in_header_or_reference_only`, `unit.missing`, `unit.glued_to_value`,
#: `value.multiple_per_row`, `meta.narrative_block`, `meta.date_format_dialect`) are the distilled
#: taxonomy's own (`resources/hazards.json`).
HAZARDS = [
    {"name": "hand.written", "source": "content",
     "description": "the value was written by hand (every row of a handwritten file; the tier says how hard)"},
    {"name": "hand.correction", "source": "incident",
     "description": "a value struck through with the correction written beside it; the correction is the truth"},
    {"name": "hand.ditto", "source": "incident",
     "description": "a ditto mark repeats the cell above (a second reading on the same day); the date it stands "
                    "for is in the semantic truth"},
]

#: Paper: notebook and clinic-booklet stock, and the printed furniture on a booklet page.
PAPERS = {
    "notebook": {"sizes_mm": [[148, 210], [176, 250]], "rule_mm": [7.0, 8.5], "margin_mm": [18, 26],
                 "top_mm": [20, 28], "tints": [[250, 249, 242], [247, 246, 238], [251, 251, 248]],
                 "rule_rgb": [[168, 192, 222], [176, 196, 214], [160, 178, 206]], "margin_rgb": [[226, 132, 140]]},
    "booklet": {"sizes_mm": [[140, 200], [148, 210]], "rule_mm": [7.5, 8.5], "margin_mm": [14, 18],
                "top_mm": [30, 34], "tints": [[250, 249, 244], [248, 247, 240]],
                "rule_rgb": [[182, 196, 210], [170, 186, 204]], "margin_rgb": []},
}

#: Symbols a hand writes beyond the strings above: digits and the punctuation of dates, times and values,
#: and for a Chinese hand the full-width punctuation of its sentences.
EXTRA_CHARS = {"en": "0123456789./:-+%()°~", "zh": "0123456789./:-+%()°~，、：（）"}

#: Identifiers a handwritten record carries (`handwriting.scope`, `handwriting.paper`).
IDENTIFIERS = {"scopes": ["page", "values"], "papers": ["ruled_notebook", "clinic_booklet", "printed_form"]}


def payload() -> dict:
    return {
        "_source": "hand-authored",
        "_note": ("What a hand may write on a handwritten page (notebook logs, a doctor's note, a printed form "
                  "filled in by hand), the writing tiers, inks, paper and the handwriting hazard classes. "
                  "Hand-authored generic clinical shorthand and diary phrasing; nothing comes from a real record. "
                  "Values and dates are never stored here."),
        "_provenance": {"script": "scripts/build_handwriting.py", "fonts": "mirobody_gen/render/fonts/fonts.json"},
        "_vocabulary_fields": ["logs", "note", "form", "hazard_classes", "tiers", "inks", "identifiers"],
        "tiers": TIERS, "inks": INKS, "rates": RATES, "messes": MESSES, "papers": PAPERS,
        "logs": LOGS, "note": NOTE, "form": FORM, "hazard_classes": HAZARDS, "identifiers": IDENTIFIERS,
    }


def _strings(node) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for k, v in node.items() for s in [k, *_strings(v)]]
    if isinstance(node, list):
        return [s for v in node for s in _strings(v)]
    return []


def inventory(script: str) -> str:
    """Every character a hand of this script may write: its strings, the English ones (a Chinese page
    writes `mmHg`, `BP`, `SpO2`), printable ASCII and the extra symbols. Form titles and headings are
    printed by the PDF renderer, not written, so they are not in it."""
    chars = {chr(c) for c in range(0x20, 0x7F)} | set(EXTRA_CHARS[script])
    sources = [LOGS["en"], NOTE["en"]]
    if script == "zh":
        sources += [LOGS["zh"], NOTE["zh"]]
    for node in sources:
        for s in _strings(node):
            chars |= set(s)
    return "".join(sorted(ch for ch in chars if not ch.isspace() or ch == " "))


# ── Subsetting ───────────────────────────────────────────────────────
def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def subset_fonts(src: pathlib.Path) -> dict:
    """Subset every upstream face in `src` into FONT_DIR. Returns the fonts.json payload."""
    import io

    from fontTools import subset
    from fontTools.ttLib import TTFont

    FONT_DIR.mkdir(parents=True, exist_ok=True)
    table: dict[str, dict] = {}
    licences: dict[str, str] = {}
    for font_id, meta in FONTS.items():
        upstream = src / meta["path"]
        data = upstream.read_bytes()
        if len(data) != meta["bytes"] or _sha256(data) != meta["sha256"]:
            raise SystemExit(f"{upstream}: not the pinned upstream file ({len(data)} bytes, sha256 {_sha256(data)})")
        font = TTFont(io.BytesIO(data), recalcTimestamp=False)
        if meta.get("instance"):
            from fontTools.varLib import instancer

            font = instancer.instantiateVariableFont(font, meta["instance"], updateFontNames=False)
        chars = inventory(meta["script"])
        cmap = font.getBestCmap()
        covered = "".join(ch for ch in chars if ord(ch) in cmap)
        options = subset.Options()
        options.layout_features = []
        options.drop_tables += ["GSUB", "GPOS", "GDEF", "BASE", "JSTF", "DSIG", "STAT", "MVAR", "HVAR", "VVAR"]
        options.hinting = False
        options.notdef_outline = True
        options.glyph_names = False
        options.name_IDs = [0, 1, 2, 3, 4, 5, 6, 13, 14]
        options.recalc_timestamp = False
        subsetter = subset.Subsetter(options)
        subsetter.populate(unicodes=[ord(ch) for ch in covered])
        subsetter.subset(font)
        name = f"MG Hand {font_id.upper().replace('-', ' ')}"
        postscript = f"MGHand-{font_id.replace('-', '')}"
        for record in list(font["name"].names):
            if record.nameID in (1, 3, 4, 6, 16, 17, 21, 22):
                font["name"].removeNames(nameID=record.nameID)
        font["name"].setName(name, 1, 3, 1, 0x409)
        font["name"].setName(f"{name}; subset for mirobody-gen", 3, 3, 1, 0x409)
        font["name"].setName(name, 4, 3, 1, 0x409)
        font["name"].setName(postscript, 6, 3, 1, 0x409)
        font["name"].setName(
            f"Modified by mirobody-gen: subset of {meta['family']} (google/fonts {meta['commit'][:12]}, "
            f"{meta['path']}) to the characters a handwritten page can carry, renamed.", 10, 3, 1, 0x409)
        buf = io.BytesIO()
        font.save(buf)
        out = buf.getvalue()
        file = f"hand-{font_id}.ttf"
        (FONT_DIR / file).write_bytes(out)
        licence_file = f"LICENSE-{font_id}.txt"
        licences[licence_file] = (src / meta["license_path"]).read_text(encoding="utf-8")
        table[font_id] = {
            # The commit pins stay in this script: a 40-digit hex id can look like a phone number to the
            # pre-commit gate, and a JSON line cannot carry its per-line exemption. Blob id and sha256 pin
            # the same bytes.
            **{k: meta[k] for k in ("family", "script", "style", "license", "path", "license_path", "git_blob")},
            "upstream_sha256": _sha256(data), "upstream_bytes": len(data),
            "file": file, "sha256": _sha256(out), "bytes": len(out), "license_file": licence_file,
            "renamed_to": name, "instance": meta.get("instance"), "stroke": meta["stroke"], "coverage": covered,
        }
        print(f"{font_id:<11} {meta['family']:<18} {len(data):>9} → {len(out):>7} bytes, {len(covered)} chars")
    for name, text in licences.items():
        (FONT_DIR / name).write_text(text, encoding="utf-8")
    return {"_note": "Handwriting faces: subsets of open-licensed upstream fonts. Regenerate with "
                     "scripts/build_handwriting.py --fonts DIR. Each subset is a Modified Version: renamed, "
                     "copyright and licence records kept, licence text alongside.",
            "source": "https://github.com/google/fonts", "fonts": table}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="write resources/handwriting.json")
    ap.add_argument("--fonts", help="directory holding the pinned upstream font files and their licences")
    args = ap.parse_args()
    data = payload()
    for script in ("zh", "en"):
        inv = inventory(script)
        cjk = sum(1 for ch in inv if ord(ch) > 0x2E80)
        print(f"{script}: {len(inv)} characters a hand may write ({cjk} CJK)")
    if args.fonts:
        fonts = subset_fonts(pathlib.Path(args.fonts))
        if args.write:
            (FONT_DIR / "fonts.json").write_text(json.dumps(fonts, ensure_ascii=False, indent=1) + "\n",
                                                 encoding="utf-8")
            print(f"wrote {FONT_DIR / 'fonts.json'}")
    if args.write:
        OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
