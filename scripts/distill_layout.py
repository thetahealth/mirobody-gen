"""把真实语料的版式观察蒸馏成 `resources/layout.json`。

    python3 scripts/distill_layout.py              # 报告 + 丢弃清单，不写文件
    python3 scripts/distill_layout.py --write      # 写 resources/layout.json
    python3 scripts/distill_layout.py --dropped 40 # 看被白名单挡掉的是什么

输入是 `analysis/*.json`（627 份真实文档的版式观察）。输出是渲染器要用的**形态**：
列组与列序、参考值方言、标记方言、日期标签与字面格式、页面构件种类、叙述段标签、
以及页数/行数/语言的分布。

## 过闸（docs/zh-CN/plan.md §4.2）

1. **文档数 ≥ 3**：只出现一两次的串最可能是某台仪器、某个批号、某家机构。
2. **白名单，不是黑名单**：列头必须能映射到一个已知**角色**（name/result/unit/reference/
   flag/…），映射不上的整条列组丢弃。这条不是洁癖——实测 n≥3 的"列头"里混着
   `葡萄糖`/`肌酐`/`白细胞计数`（转置表把指标放到了列上）、`«NAME»`（脱敏占位符）、
   以及 `联系电话` 这种真实表单字段。黑名单永远追不上这些。
3. **模板化而不是实例化**：参考值方言存成 `{lo}--{hi}{unit}` 这样的模板，
   数字由我们自己的临床 spec 填；日期存成字面格式而不是某个日期。
4. **带 source 标签**：输出的每一块都标 `format-token`，审计会检查。

被挡掉的东西会打印出来（`--dropped`）。**丢了什么要看得见**，否则白名单会悄悄把
某一整类真实版式挡在门外，而这种损失在输出里只表现为"生成的报告没那么多样"。
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

# ── 列头白名单：拼写 → 角色 ──────────────────────────────────────
# 逐条人工审过。角色是渲染器真正需要的东西：它要知道这一列放什么，而不是它叫什么。
# 同一角色下的多种拼写就是"版式多样性"的一部分，全部保留。
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
# 转置导出（产线 xlsx，157 份）的列：一行是一次检查，列是元信息 + 指标名。
# 这四个列头各出现在约 90 份文档里，第一版白名单把它们整条丢掉了，等于把最大的一类
# 真实版式挡在门外。列**标签**是结构，可以留；列里的**内容**（真实机构名、真实文件名，
# 而真实文件名形如 `<姓名>_体检报告_<日期>_<体检号>.pdf`）由生成器自己合成。
_role("meta_facility", "机构名称", "医院名称", "送检机构", "Facility", "Institution")
_role("meta_filename", "文件名称", "文件名", "File", "Filename", "File Name")
_role("meta_report_time", "报告时间", "报告时间（Report Time）", "Report Time", "ReportTime")
_role("meta_checkup", "体检", "是否体检", "体检标记")
# 骨密度报告：T 值/Z 值是标准列，不是指标名。
_role("tscore", "T值", "T-score", "T Score", "T值(T-score)")
_role("zscore", "Z值", "Z-score", "Z Score")
_role("bmd", "骨密度", "BMD", "骨密度值")
_role("result", "检测值", "测量值", "本次结果", "Measured Value")
_role("reference", "REF.RANGE", "REF RANGE", "参考值(范围)", "参考值范围(Reference)")

#: 标记方言白名单。生成器要用它来渲染"异常"这件事，所以只收真的是标记的东西。
FLAG_MARKERS = {
    "↑", "↓", "⇑", "⇓", "▲", "▼", "H", "L", "h", "l", "HH", "LL", "*", "**", "+", "++",
    "+++", "-", "±", "高", "低", "偏高", "偏低", "升高", "降低", "正常", "异常", "阴性",
    "阳性", "陰性", "陽性", "↑↑", "↓↓", "High", "Low", "Normal", "Abnormal", "N", "A",
    "危急", "复查",
}

#: 页面构件分类：只记**种类**，不记字面。实测的字面里有
#: `RJ-QR-46-16`、`总院【XN-2000】血常`、`Run: 20693-20` 这类仪器/批次/院区串。
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

#: 文档类型分类：把 627 条自由文本 doc_kind 收敛成有限集合。
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

#: 语言归一化。分析器写了 9 种写法，其中 4 种是同一件事
#: （繁體中文 / 繁体中文 / Chinese (Traditional) / Traditional Chinese）。
#: 原样透传会让采样器按错的分布抽语言——这与 unit_location 是同一类错。
LANGUAGE_CANON: dict[str, str] = {
    "简体中文": "zh-Hans", "中文": "zh-Hans", "Chinese": "zh-Hans",
    "Simplified Chinese": "zh-Hans", "Chinese (Simplified)": "zh-Hans",
    "繁體中文": "zh-Hant", "繁体中文": "zh-Hant",
    "Traditional Chinese": "zh-Hant", "Chinese (Traditional)": "zh-Hant",
    "English": "en", "英文": "en",
    "日本語": "ja", "Japanese": "ja",
}

#: 单位位置：把分析器写的 28 种自由文本（"third column"、"第三列"、"单位 column"、
#: "inside '检查结果' column, after value with space"…）收敛成渲染器真正要的五种。
#: 原样透传是不行的：那既是散文也是别人对真实文档的描述，隐私闸门会（正确地）报警。
UNIT_LOCATIONS: list[tuple[str, tuple[str, ...]]] = [
    ("in_reference", ("reference", "参考", "range")),
    ("in_value_cell", ("embedded", "value cell", "in value", "result column", "检查结果",
                       "粘", "值内", "结果列")),
    ("in_header", ("header", "表头", "列头", "column header")),
    ("separate_column", ("separate", "单位列", "单位 column", "unit column", "column",
                         "第", "列", "rightmost", "last")),
    ("none", ("none", "n/a", "not applicable", "无", "没有", "empty")),
]

#: 日期字面格式白名单：必须由日期/时间占位符与常见分隔符组成。
#: 这条挡掉 `«D:####-##-##»`（脱敏占位符）与 `unknown`。
DATE_FORMAT_RE = re.compile(r"^[YMDHhms年月日时分秒/.\-: ]+$")

#: 列头本身是日期时的形状：脱敏占位符 `«D:####年##月»`，或字面日期 `2024年3月`、`2024-03`。
DATE_COLUMN_RE = re.compile(
    r"^«?D?:?[#\d]{2,4}[-/年][#\d]{1,2}([-/月][#\d]{1,2})?[日]?»?$")


def normalize_label(text: str) -> str:
    return re.sub(r"\s+", "", str(text)).strip()


#: 指标名词表。转置表把指标放在列上，所以"这个列头是不是一个指标名"必须回答得了。
#: 词表从 `indicator_rows[].name` 收集（医学词汇，不是个人信息），出现在 ≥3 份文档才收。
#: 这样"葡萄糖""肌酐"作为列头是合法的 analyte 角色，而"张三"不会是。
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
    # 大小写不敏感地再试一次（Unit / UNIT / unit）
    for spelling, role in COLUMN_ROLES.items():
        if spelling.lower() == lab.lower():
            return role
    if lab in ANALYTE_NAMES:
        return "analyte"
    # 列头本身就是一个日期（转置导出里"一列 = 一次检查"）。实测里它是脱敏后的
    # `«D:####年##月»`，原件是 `2024年3月` 这样的月份。
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
    """`3.5--9.5x10^9/L` → `{n}--{n}x10^9/L`：留方言，去数字。"""
    if not example or len(example) > 40:
        return None
    # 含脱敏占位符的例子整条丢：`{n}.«D:####-#».{n}mIU/L` 这种模板留在 spec 里，
    # 等于把语料的加工痕迹当成了一种真实的参考值方言。
    if "«" in example or "»" in example:
        return None
    t = re.sub(r"\d+(?:\.\d+)?", "{n}", str(example).strip())
    return t if "{n}" in t or re.search(r"[≤≥<>]", t) else None


def _fold_label(label: str, role: str | None = None) -> str:
    """列头折叠：指标列与日期列都收敛成占位符，只有已知角色的列头才留字面。

    未知列头**不折叠成 {analyte} 而是原样保留**，再由上层 n≥3 的闸门决定去留——
    第一版把未知列头也折进 {analyte}，结果脱敏占位符 `«D:####-##-##»` 被当成指标名
    混进了 spec，被隐私闸门当场抓到。折叠是为了压噪声，不是为了掩盖没看懂的东西。
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
    ap.add_argument("--dropped", type=int, default=0, help="打印前 N 个被挡掉的列头")
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
            # 转置导出的上下文接纳：一张表里已经有 ≥2 个元信息列（机构名称/报告时间/
            # 文件名称/体检/目录），说明这是"一行一次检查、一列一个指标"的导出格式，
            # 此时剩下的未知列头就是指标名。不这样做的话，指标词表永远抓不到它们——
            # 转置表里指标**只**出现在列上，从不出现在 indicator_rows 里，
            # 而从列头去建指标词表是循环论证。
            meta_like = sum(1 for r in roles if r and (r.startswith("meta_") or r == "category"))
            if meta_like >= 2:
                roles = [r if r else "analyte" for r in roles]
            if any(r is None for r in roles):
                dropped_sets += 1
                for c, r in zip(cols, roles):
                    if r is None:
                        dropped_labels[c] += 1
                continue
            # 含脱敏占位符的列头一律整条丢弃：`«NAME»`、`«D:####年##月»` 是语料的加工痕迹，
            # 不是版式。留一个占位符在 spec 里，隐私闸门会（正确地）当场报警。
            if any("«" in c or "»" in c for c in cols):
                dropped_sets += 1
                for c in cols:
                    if "«" in c or "»" in c:
                        dropped_labels[c] += 1
                continue
            seen_sets.add(tuple(zip(cols, roles)))
            # 角色序列里把连续的 analyte 列折叠成一个 `analyte+`：转置表有几十个指标列，
            # 逐个记下来只会让"列组"这个统计量炸成噪声，而渲染器要的是"这里有一串指标列"。
            folded: list[str] = []
            for r in roles:
                if r == "analyte" and folded and folded[-1] == "analyte+":
                    continue
                folded.append("analyte+" if r == "analyte" else str(r))
            seen_roles.add(tuple(folded))
            if t.get("unit_location"):
                # "none" 要先匹配：一条写着 "none (separate column)" 的描述，
                # 按 separate_column 记会把"这份报告没有单位列"记反。
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
        # 0 页是分析器的伪值（xlsx 没有页的概念，图像也未必数得出页）。
        # 留在分布里，采样器会抽出"零页文档"这种不存在的东西。
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
        # 排序键要把**值本身**带上。只按计数排的话，并列项的先后取决于
        # Counter 的插入顺序，而插入顺序又来自 set 的迭代顺序——Python 的字符串哈希
        # 每个进程都不一样，于是同一份输入两次跑出不同的 spec。
        # 这个 bug 是 `test_spec_is_regenerable_without_drift` 抓到的。
        items = sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))
        return [{"value": k if isinstance(k, str) else list(k), "documents": v}
                for k, v in items if v >= min_docs]

    kept_sets = gate(col_sets)
    kept_roles = gate(col_sets_roles)
    print(f"文档 {docs} 份")
    print(f"列组：保留 {len(kept_sets)} 种拼写组合 / {len(kept_roles)} 种角色组合"
          f"（n≥{MIN_DOCS}）；整条丢弃 {dropped_sets} 次，涉及 {len(dropped_labels)} 种未知列头")
    print(f"参考值方言模板 {len(gate(ref_tpl))} 种 · 标记 {len(gate(flags, 1))} 种 · "
          f"日期标签 {len(gate(date_labels))} 种 · 日期格式 {len(gate(date_fmts))} 种")
    if langs_unknown:
        print("语言写法未归一（已丢弃）:",
              ", ".join(f"{k}:{v}" for k, v in langs_unknown.most_common(5)))
    print(f"页面构件 {len(furniture)} 类 · 文档类型 {len(doc_kinds)} 类 · "
          f"叙述段标签 {len(gate(narrative))} 种 · 签名角色 {len(gate(signature))} 种")

    print("\n角色组合（前 12）:")
    for item in kept_roles[:12]:
        print(f"  {item['documents']:4d}  {' | '.join(item['value'])}")
    print("\n参考值方言（前 12）:")
    for item in gate(ref_tpl)[:12]:
        print(f"  {item['documents']:4d}  {item['value']}")
    print("\n文档类型:", ", ".join(f"{k}:{v}" for k, v in sorted(doc_kinds.items(), key=lambda kv: (-kv[1], str(kv[0])))))
    print("页面构件:", ", ".join(f"{k}:{v}" for k, v in sorted(furniture.items(), key=lambda kv: (-kv[1], str(kv[0])))))

    if args.dropped:
        print(f"\n被白名单挡掉的列头（前 {args.dropped}，按文档数）:")
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
        print(f"\n已写出 {out}")


if __name__ == "__main__":
    main()
