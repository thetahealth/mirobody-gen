"""Distill the real corpus's layout observations into `resources/layout.json`.

    python3 scripts/distill_layout.py              # report + drop list, doesn't write a file
    python3 scripts/distill_layout.py --write      # write resources/layout.json
    python3 scripts/distill_layout.py --dropped 40 # see what the allowlist blocked

The input is `analysis/*.json` (layout observations from 627 real documents). The output is
the **shape** the renderer needs: column groups and ordering, reference-value dialects, flag
dialects, date labels and literal formats, the kinds of page furniture, narrative-block
labels, and the distribution of page count / row count / language.

## Gating (docs/zh-CN/plan.md §4.2)

1. **Document count >= 3**: a string seen only once or twice is most likely a particular
   instrument, batch number or institution.
2. **An allowlist, not a denylist**: a column header must map to a known **role**
   (name/result/unit/reference/flag/...), and the whole column group is dropped if it
   doesn't. This isn't fastidiousness -- the "column headers" observed with n>=3 include
   things like glucose/creatinine/WBC-count (a transposed table puts indicators on columns),
   `«NAME»` (a redaction placeholder), and real form fields like a phone-number column. A
   denylist can never keep up with these.
3. **Templatize, don't instantiate**: a reference-value dialect is stored as a template like
   `{lo}--{hi}{unit}`, with numbers filled in from our own clinical spec; a date is stored as
   its literal format, not a specific date.
4. **Tagged with a source**: every block of output is marked `format-token`, which the audit checks.

What gets blocked is printed out (`--dropped`). **What's lost must be visible**, or the
allowlist can silently shut an entire class of real layouts out, and that loss would only
show up in the output as "the generated reports aren't as diverse."
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent
RESOURCES = REPO / "mirobody_gen" / "resources"
MIN_DOCS = 3

# ── Column-header allowlist: spelling -> role ───────────────────────
# Reviewed entry by entry. A role is what the renderer actually needs: what this column holds,
# not what it's called. The several spellings under one role are themselves part of "layout
# diversity" and are all kept.
COLUMN_ROLES: dict[str, str] = {}


def _role(role: str, *spellings: str) -> None:
    for s in spellings:
        COLUMN_ROLES[s] = role


_role("seq", "序号", "序 号", "编号", "No.", "NO", "No", "#", "序")
_role("name", "项目", "項目", "项目名称", "項目名稱", "检验项目", "檢驗項目", "检查项目",
      "检查项目名称", "检测项目", "检查名称", "检验项目名称", "检验名称", "测定项目",
      "項目名", "Item", "Items", "Test", "Tests", "Test Name", "Analyte", "Parameter",
      "Examination", "Test Item", "ITEM", "TEST")
_role("abbr", "简称", "缩写", "英文缩写", "指标简称", "英文名称", "英文名", "代号",
      "Abbr", "Abbreviation", "Code", "Short Name", "English Name")
_role("result", "结果", "結果", "检查结果", "檢查結果", "检验结果", "检测结果", "测定值",
      "測定值", "报告结果", "化验结果", "数值", "Result", "RESULT", "Results", "Value",
      "VALUE", "Measured", "Measurement", "Reading")
_role("unit", "单位", "單位", "计量单位", "单位(Unit)", "Unit", "UNIT", "Units", "单 位")
_role("reference", "参考值", "參考值", "参考范围", "參考範圍", "参考区间", "參考區間",
      "正常范围值", "正常参考值", "正常范围", "正常值", "参考", "参考值范围", "生物参考区间",
      "Reference", "Reference Range", "Ref Range", "Ref.", "Normal Range", "Normal Value",
      "REFERENCE", "Range")
_role("flag", "标志", "標誌", "提示", "状态", "狀態", "异常", "異常", "是否异常", "结果提示",
      "标记", "异常提示", "结果判断", "判断", "Flag", "FLAG", "Status", "Abnormal",
      "Indicator", "H/L", "提示信息")
_role("method", "方法", "检测方法", "检验方法", "Method", "Methodology")
_role("specimen", "标本", "样本", "标本类型", "样本类型", "Specimen", "Sample")
_role("examiner", "检查者", "检验者", "审核者", "检验人", "操作者", "Examiner", "Operator",
      "Verified By", "Performed By")
_role("note", "备注", "備註", "说明", "注", "医生建议", "建议", "Remark", "Remarks",
      "Note", "Notes", "Comment", "Comments")
_role("finding", "检查所见", "檢查所見", "所见", "影像表现", "影像所见", "超声所见",
      "描述", "Findings", "Observation", "Description")
_role("conclusion", "诊断意见", "印象", "结论", "诊断", "提示诊断", "Impression",
      "Conclusion", "Diagnosis")
_role("category", "分类", "类别", "科室", "科别", "组别", "Category", "Department", "Group",
      "一级目录（中文）", "一级目录（英文）", "二级目录（中文）", "二级目录（英文）",
      "一级目录", "二级目录", "三级目录")
_role("date", "日期", "检查日期", "报告日期", "采样日期", "Date", "Collected", "Reported")
# Columns from the transposed export (the pipeline's xlsx, 157 documents): one row is one
# examination, and the columns are metadata + indicator names. Each of these four headers
# appears in about 90 documents; the first-version allowlist dropped them wholesale, shutting
# out the single largest class of real layouts. The column **label** is structure and can
# stay; the **content** of the column (a real institution name, a real filename -- a real
# filename looks like `<name>_checkup-report_<date>_<checkup-id>.pdf`) is synthesized by the
# generator itself.
_role("meta_facility", "机构名称", "医院名称", "送检机构", "Facility", "Institution")
_role("meta_filename", "文件名称", "文件名", "File", "Filename", "File Name")
_role("meta_report_time", "报告时间", "报告时间（Report Time）", "Report Time", "ReportTime")
_role("meta_checkup", "体检", "是否体检", "体检标记")
# Bone-density reports: T-score/Z-score are standard columns, not indicator names.
_role("tscore", "T值", "T-score", "T Score", "T值(T-score)")
_role("zscore", "Z值", "Z-score", "Z Score")
_role("bmd", "骨密度", "BMD", "骨密度值")
_role("result", "检测值", "测量值", "本次结果", "Measured Value")
_role("reference", "REF.RANGE", "REF RANGE", "参考值(范围)", "参考值范围(Reference)")

#: Flag-dialect allowlist. The generator uses this to render "abnormal," so only things that
#: are genuinely flags belong here.
FLAG_MARKERS = {
    "↑", "↓", "⇑", "⇓", "▲", "▼", "H", "L", "h", "l", "HH", "LL", "*", "**", "+", "++",
    "+++", "-", "±", "高", "低", "偏高", "偏低", "升高", "降低", "正常", "异常", "阴性",
    "阳性", "陰性", "陽性", "↑↑", "↓↓", "High", "Low", "Normal", "Abnormal", "N", "A",
    "危急", "复查",
}

#: Page-furniture classification: records only the **kind**, never the literal text. Observed
#: literals include instrument/batch/branch strings such as an equipment code, a headquarters
#: institution tag with a bracketed model number, or a run identifier.
FURNITURE_KINDS: list[tuple[str, tuple[str, ...]]] = [
    ("page_number", ("第", "页", "共", "page", "Page", "/", "頁")),
    ("print_info", ("打印", "print", "Print", "打印次数", "打印时间")),
    ("barcode", ("条形码", "条码", "barcode", "Barcode", "BARCODE")),
    ("qr_code", ("二维码", "QR", "qr code", "QR code")),
    ("disclaimer", ("仅供", "临床参考", "仅对", "负责", "本报告", "disclaimer", "Disclaimer",
                    "声明", "责任", "valid", "reference only")),
    ("end_marker", ("END", "END---", "以下空白", "报告结束", "完")),
    ("instrument", ("仪器", "设备", "机号", "instrument", "Analyzer", "XN-", "Run")),
    ("department_stamp", ("急诊", "门诊", "住院", "体检中心", "科", "病区")),
    ("sheet_marker", ("sheet", "Sheet", "--- sheet")),
    ("url_or_contact", ("http", "www", "电话", "地址", "邮编", "tel", "Tel")),
    ("photo_slot", ("照片", "photo", "Photo", "头像")),
    ("logo", ("logo", "Logo", "LOGO", "标识")),
]

#: Document-type classification: collapses 627 free-text doc_kind values into a finite set.
DOC_KINDS: list[tuple[str, tuple[str, ...]]] = [
    ("checkup_book", ("体检报告", "健康体检", "體檢", "checkup", "check-up", "physical examination",
                      "health examination", "体检")),
    ("cbc", ("血常规", "血细胞", "complete blood count", "cbc", "blood count", "血液分析")),
    ("chemistry", ("生化", "chemistry", "metabolic panel", "cmp", "bmp", "肝功", "肾功",
                   "liver function", "renal function")),
    ("lipid", ("血脂", "lipid", "胆固醇", "cholesterol")),
    ("glucose_diabetes", ("血糖", "糖化", "糖尿病", "glucose", "hba1c", "glycated", "diabet")),
    ("thyroid", ("甲状腺", "甲功", "thyroid", "tsh")),
    ("urinalysis", ("尿液", "尿常规", "urine", "urinalysis")),
    ("coagulation", ("凝血", "coagulation", "d-dimer", "d-二聚体")),
    ("tumor_marker", ("肿瘤标志", "tumor marker", "甲胎蛋白", "癌胚抗原", "afp", "cea")),
    ("serology_immune", ("免疫", "抗体", "抗原", "serolog", "immuno", "igg", "ige", "过敏原",
                         "allergen", "rheumatoid", "类风湿")),
    ("vitamin_hormone", ("维生素", "vitamin", "激素", "hormone", "25-羟", "皮质醇")),
    ("ecg", ("心电", "ecg", "ekg", "electrocardio")),
    ("ultrasound", ("超声", "彩超", "b超", "ultrasound", "sonograph", "doppler")),
    ("ct_mri_xray", ("ct", "mri", "磁共振", "x线", "x-ray", "радио", "radiolog", "放射", "dr片",
                     "胸片", "平扫")),
    ("endoscopy_pathology", ("内镜", "胃镜", "肠镜", "病理", "活检", "endoscop", "patholog",
                             "biopsy", "cytolog")),
    ("bone_density", ("骨密度", "bone density", "dxa")),
    ("pulmonary_function", ("肺功能", "pulmonary function", "spirometry")),
    ("emg_eeg", ("肌电", "脑电", "emg", "eeg", "nerve conduction")),
    ("discharge_or_visit", ("出院", "门诊病历", "入院", "discharge", "outpatient", "medical record",
                            "clinic visit", "病历")),
    ("device_export", ("设备", "手环", "手表", "device", "wearable", "monitor", "血压计", "血糖仪")),
    ("esr", ("血沉", "红细胞沉降", "sedimentation", "esr")),
    ("blood_gas", ("血气", "blood gas", "动脉血")),
]

#: Language canonicalization. The analyzer produced 9 spellings, 4 of which are the same
#: thing: the simplified- and traditional-script labels for "Traditional Chinese" below, plus
#: its two English spellings. Passing them through unchanged would make the sampler draw
#: languages from the wrong distribution -- the same class of error as unit_location.
LANGUAGE_CANON: dict[str, str] = {
    "简体中文": "zh-Hans", "中文": "zh-Hans", "Chinese": "zh-Hans",
    "Simplified Chinese": "zh-Hans", "Chinese (Simplified)": "zh-Hans",
    "繁體中文": "zh-Hant", "繁体中文": "zh-Hant",
    "Traditional Chinese": "zh-Hant", "Chinese (Traditional)": "zh-Hant",
    "English": "en", "英文": "en",
    "日本語": "ja", "Japanese": "ja",
}

#: Unit location: collapses the analyzer's 28 free-text forms ("third column", a Chinese
#: equivalent of "column 3", "unit column" in Chinese, "inside the result column, after the
#: value with a space"...) into the five the renderer actually needs. Passing them through
#: unchanged won't work: that's prose, and someone else's description of a real document,
#: which the privacy gate would (correctly) flag.
UNIT_LOCATIONS: list[tuple[str, tuple[str, ...]]] = [
    ("in_reference", ("reference", "参考", "range")),
    ("in_value_cell", ("embedded", "value cell", "in value", "result column", "检查结果",
                       "粘", "值内", "结果列")),
    ("in_header", ("header", "表头", "列头", "column header")),
    ("separate_column", ("separate", "单位列", "单位 column", "unit column", "column",
                         "第", "列", "rightmost", "last")),
    ("none", ("none", "n/a", "not applicable", "无", "没有", "empty")),
]

#: Date literal-format allowlist: must be composed only of date/time placeholders and common
#: separators. This blocks `«D:####-##-##»` (a redaction placeholder) and `unknown`.
DATE_FORMAT_RE = re.compile(r"^[YMDHhms年月日时分秒/.\-: ]+$")

#: The shape of a column header that is itself a date: a redaction placeholder of the form
#: `«D:####» + year/month markers`, or a literal date such as "2024, month 3" or `2024-03`.
DATE_COLUMN_RE = re.compile(
    r"^«?D?:?[#\d]{2,4}[-/年][#\d]{1,2}([-/月][#\d]{1,2})?[日]?»?$")


def normalize_label(text: str) -> str:
    return re.sub(r"\s+", "", str(text)).strip()


#: Indicator-name vocabulary. A transposed table puts indicators on columns, so the question
#: "is this column header an indicator name" must be answerable. The vocabulary is collected
#: from `indicator_rows[].name` (medical terminology, not personal information), and only
#: accepted once it appears in >=3 documents. That way "glucose" or "creatinine" is a
#: legitimate analyte role as a column header, while a person's name is not.
ANALYTE_NAMES: set[str] = set()


def load_analyte_vocabulary(min_docs: int = MIN_DOCS) -> set[str]:
    counter: collections.Counter[str] = collections.Counter()
    for f in sorted(glob.glob(str(REPO / "analysis" / "*.json"))):
        a = (json.load(open(f, encoding="utf-8")) or {}).get("analysis") or {}
        names = {normalize_label(r.get("name", "")) for r in (a.get("indicator_rows") or [])
                 if isinstance(r, dict)}
        for n in names:
            if n and "«" not in n and len(n) <= 30:
                counter[n] += 1
    return {k for k, v in counter.items() if v >= min_docs}


def role_of(label: str) -> str | None:
    lab = normalize_label(label)
    if lab in COLUMN_ROLES:
        return COLUMN_ROLES[lab]
    # Retry case-insensitively (Unit / UNIT / unit)
    for spelling, role in COLUMN_ROLES.items():
        if spelling.lower() == lab.lower():
            return role
    if lab in ANALYTE_NAMES:
        return "analyte"
    # The column header is itself a date (in a transposed export, "one column = one
    # examination"). As observed it's the redacted year/month placeholder matched by
    # DATE_COLUMN_RE; the original is a plain year-and-month value.
    if DATE_COLUMN_RE.match(lab):
        return "date_column"
    return None


def classify(text: str, table: list[tuple[str, tuple[str, ...]]]) -> str | None:
    low = str(text).lower()
    for kind, keys in table:
        if any(k.lower() in low for k in keys):
            return kind
    return None


def templatize_reference(example: str) -> str | None:
    """`3.5--9.5x10^9/L` -> `{n}--{n}x10^9/L`: keep the dialect, strip the numbers."""
    if not example or len(example) > 40:
        return None
    # Drop any example containing a redaction placeholder outright: a template like
    # `{n}.«D:####-#».{n}mIU/L` surviving into the spec would treat a corpus de-identification
    # artifact as a real reference-value dialect.
    if "«" in example or "»" in example:
        return None
    t = re.sub(r"\d+(?:\.\d+)?", "{n}", str(example).strip())
    return t if "{n}" in t or re.search(r"[≤≥<>]", t) else None


def _fold_label(label: str, role: str | None = None) -> str:
    """Column-header folding: both indicator and date columns collapse to placeholders; only
    a column header with a known role keeps its literal text.

    An unknown column header is **kept as-is rather than folded into {analyte}**, leaving
    the n>=3 gate upstream to decide whether it survives -- the first version folded unknown
    headers into {analyte} too, and a redaction placeholder `«D:####-##-##»` slipped into the
    spec disguised as an indicator name, caught on the spot by the privacy gate. Folding is
    meant to suppress noise, not to hide something we don't understand.
    """
    role = role or role_of(label)
    if role == "analyte" or label in ANALYTE_NAMES:
        return "{analyte}"
    if role == "date_column":
        return "{date}"
    return label


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--dropped", type=int, default=0, help="print the first N column headers that were blocked")
    args = ap.parse_args()

    global ANALYTE_NAMES
    ANALYTE_NAMES = load_analyte_vocabulary()

    docs = 0
    col_sets: collections.Counter[tuple[str, ...]] = collections.Counter()
    col_sets_roles: collections.Counter[tuple[str, ...]] = collections.Counter()
    dropped_labels: collections.Counter[str] = collections.Counter()
    dropped_sets = 0
    ref_tpl: collections.Counter[str] = collections.Counter()
    flags: collections.Counter[str] = collections.Counter()
    date_labels: collections.Counter[str] = collections.Counter()
    date_fmts: collections.Counter[str] = collections.Counter()
    furniture: collections.Counter[str] = collections.Counter()
    doc_kinds: collections.Counter[str] = collections.Counter()
    narrative: collections.Counter[str] = collections.Counter()
    signature: collections.Counter[str] = collections.Counter()
    pages: collections.Counter[int] = collections.Counter()
    rows_per_doc: list[int] = []
    langs: collections.Counter[str] = collections.Counter()
    langs_unknown: collections.Counter[str] = collections.Counter()
    unit_loc: collections.Counter[str] = collections.Counter()
    tables_per_doc: collections.Counter[int] = collections.Counter()

    for f in sorted(glob.glob(str(REPO / "analysis" / "*.json"))):
        a = (json.load(open(f, encoding="utf-8")) or {}).get("analysis") or {}
        if not a:
            continue
        docs += 1
        seen_sets: set[tuple[str, ...]] = set()
        seen_roles: set[tuple[str, ...]] = set()
        tables = a.get("result_tables") or []
        tables_per_doc[len(tables)] += 1
        for t in tables:
            cols = [normalize_label(c) for c in (t.get("columns") or []) if str(c).strip()]
            if not cols:
                continue
            roles = [role_of(c) for c in cols]
            # Context acceptance for a transposed export: if a table already has >=2
            # metadata-like columns (institution name / report time / filename / checkup /
            # category), that means it's the "one row = one examination, one column = one
            # indicator" export format, and the remaining unknown column headers are
            # indicator names. Without this, the indicator vocabulary could never catch them --
            # in a transposed table, indicators appear **only** on columns, never in
            # indicator_rows, so building the indicator vocabulary from column headers alone
            # would be circular.
            meta_like = sum(1 for r in roles if r and (r.startswith("meta_") or r == "category"))
            if meta_like >= 2:
                roles = [r if r else "analyte" for r in roles]
            if any(r is None for r in roles):
                dropped_sets += 1
                for c, r in zip(cols, roles):
                    if r is None:
                        dropped_labels[c] += 1
                continue
            # Any column header containing a redaction placeholder is dropped outright: `«NAME»`
            # and the redacted year/month date placeholder are artifacts of how the corpus was
            # de-identified, not layout. Leaving a placeholder in the spec would (correctly) set off the privacy
            # gate on the spot.
            if any("«" in c or "»" in c for c in cols):
                dropped_sets += 1
                for c in cols:
                    if "«" in c or "»" in c:
                        dropped_labels[c] += 1
                continue
            seen_sets.add(tuple(zip(cols, roles)))
            # Fold consecutive analyte columns in a role sequence into one `analyte+`: a
            # transposed table has dozens of indicator columns, and recording each one
            # individually would just blow up the "column group" statistic into noise, when
            # what the renderer needs is just "there's a run of indicator columns here."
            folded: list[str] = []
            for r in roles:
                if r == "analyte" and folded and folded[-1] == "analyte+":
                    continue
                folded.append("analyte+" if r == "analyte" else str(r))
            seen_roles.add(tuple(folded))
            if t.get("unit_location"):
                # "none" must be matched first: a description reading "none (separate column)"
                # recorded as separate_column would record the exact opposite of "this report has
                # no unit column."
                raw = str(t["unit_location"]).strip().lower()
                where = ("none" if raw.startswith(("none", "n/a", "not applicable", "无"))
                         else classify(raw, UNIT_LOCATIONS))
                if where:
                    unit_loc[where] += 1
        for s in sorted(seen_sets):
            col_sets[tuple(_fold_label(label, role) for label, role in s)] += 1
        for s in sorted(seen_roles):
            col_sets_roles[s] += 1

        for r in a.get("reference_forms") or []:
            tpl = templatize_reference(r.get("example", "") if isinstance(r, dict) else r)
            if tpl:
                ref_tpl[tpl] += 1
        for fl in a.get("flag_forms") or []:
            ex = (fl.get("example", "") if isinstance(fl, dict) else str(fl)).strip()
            for marker in re.split(r"[、,，/\s]+", ex):
                if marker and marker in FLAG_MARKERS:
                    flags[marker] += 1
        for d in a.get("datetime_fields") or []:
            if not isinstance(d, dict):
                continue
            lab = normalize_label(d.get("label", ""))
            if lab and "«" not in lab:
                date_labels[lab] += 1
            fmt = str(d.get("literal_format", "")).strip()
            if fmt and DATE_FORMAT_RE.match(fmt):
                date_fmts[fmt] += 1
        for item in a.get("furniture") or []:
            kind = classify(item, FURNITURE_KINDS)
            if kind:
                furniture[kind] += 1
        kind = classify(f"{a.get('doc_kind', '')} {a.get('issuer_kind', '')}", DOC_KINDS)
        if kind:
            doc_kinds[kind] += 1
        for n in a.get("narrative_blocks") or []:
            lab = normalize_label(n.get("label", "") if isinstance(n, dict) else n)
            if lab and role_of(lab) in ("finding", "conclusion", "note") or lab in (
                    "主诉", "现病史", "既往史", "体格检查", "辅助检查", "提醒", "温馨提示"):
                narrative[lab] += 1
        for s in a.get("signature_roles") or []:
            lab = normalize_label(s)
            if lab and "«" not in lab and role_of(lab) == "examiner":
                signature[lab] += 1
        # A page count of 0 is an analyzer artifact (xlsx has no concept of pages, and an image
        # may not have a countable page either). Leaving it in the distribution would let the
        # sampler draw a "zero-page document," which doesn't exist.
        if isinstance(a.get("page_count"), int) and a["page_count"] >= 1:
            pages[a["page_count"]] += 1
        rows_per_doc.append(len(a.get("indicator_rows") or []))
        for lang in a.get("languages") or []:
            canon = LANGUAGE_CANON.get(str(lang).strip())
            if canon:
                langs[canon] += 1
            else:
                langs_unknown[str(lang).strip()] += 1

    def gate(counter: collections.Counter, min_docs: int = MIN_DOCS) -> list[dict]:
        # The sort key must carry the **value itself**. Sorting by count alone would let ties
        # break by Counter insertion order, which in turn comes from set iteration order --
        # Python's string hashing differs per process, so the same input would produce a
        # different spec on two separate runs. This bug was caught by
        # `test_spec_is_regenerable_without_drift`.
        items = sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))
        return [{"value": k if isinstance(k, str) else list(k), "documents": v}
                for k, v in items if v >= min_docs]

    kept_sets = gate(col_sets)
    kept_roles = gate(col_sets_roles)
    print(f"{docs} documents")
    print(f"Column groups: kept {len(kept_sets)} spelling combinations / {len(kept_roles)} role combinations"
          f" (n>={MIN_DOCS}); dropped wholesale {dropped_sets} times, covering {len(dropped_labels)} unknown column headers")
    print(f"{len(gate(ref_tpl))} reference-value dialect templates · {len(gate(flags, 1))} flag markers · "
          f"{len(gate(date_labels))} date labels · {len(gate(date_fmts))} date formats")
    if langs_unknown:
        print("Language spellings not canonicalized (dropped):",
              ", ".join(f"{k}:{v}" for k, v in langs_unknown.most_common(5)))
    print(f"{len(furniture)} page-furniture kinds · {len(doc_kinds)} document types · "
          f"{len(gate(narrative))} narrative-block labels · {len(gate(signature))} signature roles")

    print("\nRole combinations (top 12):")
    for item in kept_roles[:12]:
        print(f"  {item['documents']:4d}  {' | '.join(item['value'])}")
    print("\nReference-value dialects (top 12):")
    for item in gate(ref_tpl)[:12]:
        print(f"  {item['documents']:4d}  {item['value']}")
    print("\nDocument types:", ", ".join(f"{k}:{v}" for k, v in sorted(doc_kinds.items(), key=lambda kv: (-kv[1], str(kv[0])))))
    print("Page furniture:", ", ".join(f"{k}:{v}" for k, v in sorted(furniture.items(), key=lambda kv: (-kv[1], str(kv[0])))))

    if args.dropped:
        print(f"\nColumn headers blocked by the allowlist (top {args.dropped}, by document count):")
        for lab, n in dropped_labels.most_common(args.dropped):
            print(f"  {n:4d}  {lab[:60]}")

    if args.write:
        ordered = sorted(rows_per_doc)
        payload = {
            "_vocabulary_fields": ["column_header_vocabulary", "column_roles",
                                   "reference_dialects", "flag_markers", "date_labels",
                                   "date_formats", "narrative_labels", "signature_roles",
                                   "column_sets_by_spelling", "column_sets_by_role",
                                   "furniture_kinds", "doc_kinds", "unit_location"],
            "_source": "format-token",
            "_note": (
                "从 627 份真实文档的版式观察蒸馏。列头经角色白名单过滤，参考值只留方言模板，"
                "页面构件与文档类型只留种类，日期只留字面格式。任何仅出现在 1–2 份文档里的串一律丢弃。"
            ),
            "_provenance": {"documents": docs, "min_documents": MIN_DOCS,
                            "script": "scripts/distill_layout.py",
                            "dropped_column_sets": dropped_sets,
                            "dropped_labels": len(dropped_labels)},
            "column_sets_by_spelling": kept_sets,
            "column_sets_by_role": kept_roles,
            "column_header_vocabulary": sorted(
                {x for st in kept_sets for x in st["value"] if not x.startswith("{")}),
            "column_roles": {k: v for k, v in sorted(COLUMN_ROLES.items())},
            "reference_dialects": gate(ref_tpl),
            "flag_markers": gate(flags, 1),
            "date_labels": gate(date_labels),
            "date_formats": gate(date_fmts),
            "furniture_kinds": [{"value": k, "occurrences": v} for k, v in sorted(furniture.items(), key=lambda kv: (-kv[1], str(kv[0])))],
            "doc_kinds": [{"value": k, "documents": v} for k, v in sorted(doc_kinds.items(), key=lambda kv: (-kv[1], str(kv[0])))],
            "narrative_labels": gate(narrative),
            "signature_roles": gate(signature),
            "unit_location": [{"value": k, "occurrences": v} for k, v in sorted(unit_loc.items(), key=lambda kv: (-kv[1], str(kv[0])))],
            "distributions": {
                "page_count": {str(k): v for k, v in sorted(pages.items())},
                "tables_per_document": {str(k): v for k, v in sorted(tables_per_doc.items())},
                "languages": {k: v for k, v in sorted(langs.items(), key=lambda kv: (-kv[1], str(kv[0])))},
                "rows_per_document": {
                    "p05": ordered[int(len(ordered) * 0.05)],
                    "p25": ordered[int(len(ordered) * 0.25)],
                    "p50": ordered[len(ordered) // 2],
                    "p75": ordered[int(len(ordered) * 0.75)],
                    "p95": ordered[int(len(ordered) * 0.95)],
                    "max": max(ordered),
                    "zero_share": round(sum(1 for x in ordered if x == 0) / len(ordered), 4),
                },
            },
        }
        out = RESOURCES / "layout.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
