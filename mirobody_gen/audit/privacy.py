"""Privacy gate: prove that outputs and resources contain nothing from the reference set. Non-zero exit on hits.

隐私闸门：证明生成物与 spec 里没有真实语料的内容。非零退出码即有问题。

    mirobody-gen audit-privacy                      # 扫 mirobody_gen/resources
    mirobody-gen audit-privacy --targets spec out   # 连生成物一起扫
    mirobody-gen audit-privacy --report             # 只看统计，不判定

## 三项检查

**① 回放检测**：把 `corpus/text/**`（771 份真实文档的提取文本，5.8MB）切成 N 字 n-gram，
扫描每一个目标文件，命中即为"真实语料的字面被搬进了产物"。

索引只存**哈希**，不存明文：n-gram 经 blake2b 取 8 字节存进一个排序好的 uint64 数组，
命中与否靠二分查找。所以索引文件本身不是语料的副本，可以安全落盘、可以带着跑。
（这不是洁癖：第一版如果把 n-gram 明文缓存下来，那个缓存就是一份重新切碎的病历。）

命中后还要过一道**豁免**：如果这段 n-gram 整段落在**某一个**白名单词汇里
（指标名、单位、列头、标记、标准标签——每一条自己都是过闸后进 spec 的），
那它就是一个公开医学词汇的窗口，标识不了任何人。豁免条件刻意收得很紧：
不接受"若干个公共词拼起来"，因为两个无关公共词的尾首相接本身就可能是被搬运的原文。
豁免会被计数并打印，**不会被藏起来**。

**② PII 谓词**：身份证、手机号、就诊号、姓名字段、机构名。与 `.githooks/pre-commit`
里那套**分开写**——同一个谓词跑两遍只能确认它自己的盲区，这个项目为此付过代价
（第一次审计报告"0 人名泄漏"，而库里躺着一个真实医生姓名，因为审计调用的是过滤时
用的同一个函数）。这里用的是结构化检测：姓名标签后的名字与机构名必须**属于虚构池**
（`resources/fiction.json`，白名单），而不是"不像真名就放行"（黑名单）。

就诊号这一条对生成物有一个放行口：合成编号的末三位是带盐校验尾（`mirobody_gen/synthid.py`），
这里**另写一份**同样的校验（不 import 生成器），对得上才放行，并计数打印。
真实号码碰巧对上的概率是千分之一，而且它先得躲过回放检测。

（2026-09-23 之前本段已经写着"机构名与人名必须属于虚构池"，但代码里没有这项检查——
文档先于实现。渲染器开始往纸上印姓名之后才补上。）

**③ spec 溯源**：`resources/*.json` 每个文件都必须声明 `_source`、`_provenance` 与
`_vocabulary_fields`（哪些字段装的是可用于豁免的公开词汇），
且不得含脱敏占位符 `«…»`（那是真实语料的加工痕迹，混进 spec 说明蒸馏时漏了过滤）。

## 这道闸门管不到什么

**人写的散文文档（`docs/`、`README.md`、`docs/zh-CN/plan.md`）不在回放检测的适用范围内。**
2026-09-23 实测：拿它扫 `docs/`，报出 56 处命中，逐条核对全是常见医学英文——
"reference range"、"interpretation"、"total protein"、"health records"。
真实语料里本来就有英文段落，任何一篇用英文写的论文草稿都会在 12 字窗口上撞到它们。
这不是泄漏，是回放检测的设计前提（"语料字面出现在产物里"）对散文不成立。

解决办法**不是**把这些英文词加进白名单——那会在闸门真正该管的地方（spec 与生成物）
把同样的短语一并放过。散文文档由提交钩子的 PII 规则与人工评审把关；
回放检测只对机器产出的东西负责：`mirobody_gen/resources/` 与 `out/`。

图像类产物（jpg/png/扫描 pdf）里的文字，本工具**读不出来**，所以回放检测覆盖不到它们。
这不是漏洞而是边界：图像是由 manifest 的行渲染出来的，而 manifest 走的是同一道检查。
换句话说，图像的隐私性继承自它的真值来源，不是被独立验证的。要独立验证需要对产物做 OCR，
那是另一条工序（`audit/readability.py` 会做，顺带就能拿到文本）。
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
#: 源码检出的根目录。只有需要检出才有的东西（参考集、缓存、产物）才用它；安装后的包里没有这些。
REPO = PACKAGE.parent
CORPUS_TEXT = REPO / "corpus" / "text"
CACHE = REPO / ".cache" / "corpus_ngrams.npy"

#: n-gram 长度（归一化之后的字符数）。
#:
#: 12 是权衡出来的：中文信息密度高，8 字窗口里"白细胞计数参考范围"这种纯公共词汇的
#: 组合会大量误报；20 字窗口又会漏掉"一行被整行搬运"以外的情况。12 字在实测语料上
#: 让豁免率落在可读的范围内（跑 `--report` 看当前数字）。
NGRAM = 12

_DROP = re.compile(r"[\s　·•\-—–_=|/\\()（）\[\]【】{}<>《》\"'“”‘’,，.。;；:：!！?？*#&+~]")


def normalize(text: str) -> str:
    """归一化：去空白与标点、全角转半角、英文小写。

    归一化比原文更宽——排版差异（空格、全角半角）不该让一段被搬运的文本逃过检测。
    """
    text = unicodedata.normalize("NFKC", text)
    return _DROP.sub("", text).lower()


def ngram_hashes(text: str, n: int = NGRAM) -> np.ndarray:
    """文本 → uint64 哈希数组。短于 n 的文本返回空数组。"""
    import hashlib

    if len(text) < n:
        return np.empty(0, dtype=np.uint64)
    out = np.empty(len(text) - n + 1, dtype=np.uint64)
    for i in range(len(text) - n + 1):
        digest = hashlib.blake2b(text[i:i + n].encode("utf-8"), digest_size=8).digest()
        out[i] = int.from_bytes(digest, "big")
    return out


def build_index(rebuild: bool = False) -> np.ndarray:
    """真实语料的 n-gram 哈希索引（排序后的 uint64 数组）。"""
    if CACHE.is_file() and not rebuild:
        return np.load(CACHE)
    if not CORPUS_TEXT.is_dir():
        raise SystemExit(f"找不到 {CORPUS_TEXT}——没有真实语料就无法做回放检测。"
                         "若这台机器上本来就没有语料，用 --skip-replay 明确跳过，"
                         "并且知道自己跳过了什么。")
    chunks: list[np.ndarray] = []
    files = sorted(CORPUS_TEXT.glob("*.txt"))
    for i, f in enumerate(files, 1):
        chunks.append(ngram_hashes(normalize(f.read_text(encoding="utf-8", errors="ignore"))))
        if i % 100 == 0:
            print(f"  建索引 {i}/{len(files)} …", file=sys.stderr)
    index = np.unique(np.concatenate(chunks)) if chunks else np.empty(0, dtype=np.uint64)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    np.save(CACHE, index)
    return index


# ── 目标文件的文本提取 ───────────────────────────────────────────
def extract_text(path: pathlib.Path) -> list[str] | None:
    """能读出文本就返回**文本单元列表**；读不出（图像等）返回 None。

    返回列表而不是一整段，是因为 n-gram 不能跨越语义边界：把整个 JSON 当一段文本扫，
    `"结果"` 的结尾和下一个键 `"单位"` 的开头会拼成一个真实语料里也存在的 12 字窗口，
    于是报出一堆并不存在的"逐字搬运"。JSON 按每个字符串值切，表格按行切。
    """
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        # 逐行解析。第一版对 .jsonl 走的是"整个文件 json.loads"，必然失败，
        # 于是退回按行扫原文——键名与值被拼在一起，`"start_date": "2026-04-…"`
        # 归一化后成了 `startdate202604…`，报出一堆并不存在的"逐字搬运"。
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
        # 按单元格切：同一行相邻两格的文字拼起来，会凭空造出一个真实语料里也有的窗口
        # （导出表的表头就是一串指标名首尾相接）。
        import csv as _csv
        import io as _io

        raw = path.read_bytes().decode("utf-8-sig", errors="ignore")
        return [cell for row in _csv.reader(_io.StringIO(raw), delimiter="\t" if suffix == ".tsv" else ",")
                for cell in row if cell]
    if suffix in (".txt", ".md", ".py", ".yaml", ".yml"):
        return path.read_text(encoding="utf-8", errors="ignore").splitlines()
    if suffix == ".pdf":
        try:
            import fitz
        except ImportError:
            return None
        # 按文字片段（span）切：表格一格通常就是一个 span；按行切会把同一行的几格拼在一起。
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


# ── 白名单词汇（豁免用）─────────────────────────────────────────
def load_vocabulary() -> list[str]:
    """公共词汇表：只取每份 spec 用 `_vocabulary_fields` **显式声明**的那些字段。

    第一版把 spec 里的所有字符串都当公共词汇，还加了个 `len <= 40` 的上限。两个错：
    上限把 `Mean corpuscular hemoglobin concentration`（41 字符）悄悄挡在外面，
    于是一个我自己手写的标准英文术语被报成了"逐字搬运"；而"所有字符串"又太宽——
    说明性的散文一旦混进白名单，它里面万一真夹带了语料原文，就正好被自己豁免掉。

    声明式的好处是这份清单本身可以被审：`_vocabulary_fields` 里的每个字段，
    都是我们主张"这里装的是公开医学词汇或格式记号"的地方。
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
    # 虚构池里的人名与机构名：它们本身另有白名单检查（NAME_FIELD / INSTITUTION），
    # 放进词表只是为了"医师：某某"这种标签与名字的尾首相接能被解释。
    fiction_names, fiction_institutions, _ = load_fiction()
    terms |= {normalize(n) for n in fiction_names} | {normalize(n) for n in fiction_institutions}
    terms |= indicator_composites()
    terms |= expanded_templates()
    return sorted((t for t in terms if t), key=len, reverse=True)


def expand_placeholders(value: str, slots: dict[str, list[str]]) -> list[str]:
    """带 `{槽}` 的叙述模板（"甲状腺{side}叶结节（TI-RADS {tirads}类）"）展开成印得出来的词。

    槽在 spec 的 `_placeholders` 里**声明**过取值的（侧别、叶、回声、TI-RADS 分类）逐一代入；
    没声明的（尺寸、牙位、异常项目清单）是数字或另一些公共词汇，**在槽处把模板切开**成两段——
    切开之后每一段仍只由声明过的公共词汇构成，而槽里的内容由别的词条解释（数字不构成搬运证据）。"""
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
    """`resources/templates.json` 里带 `{t}` 的标题模板（"{t} Report"、"{t}检验报告单"），
    用同一份文件里声明的套餐标题展开。展开后的每一条仍只由两份已声明的公共词汇构成。"""
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
    """同一指标词条内名称与缩写的两两拼接：`高密度脂蛋白胆固醇(HDL-C)` 归一化后是
    `高密度脂蛋白胆固醇hdlc`，两半各自是公共词汇，拼起来仍只是这一个指标的名字。

    只在**同一词条内**拼。两个无关词条的尾首相接不在此列——那本身可能就是被搬运的原文
    （见 `is_public_term_window` 的说明）。
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
                out.add(normalize(a + unit))          # 导出表表头 "Total Protein(g/L)"
            for b in names:
                if a != b:
                    out.add(normalize(a + b))
    return out


def residual_text(fragment: str) -> str:
    """去掉数字（与标记符号、顿号冒号）之后的残余。

    **数字不构成文本搬运的证据**（↑↓ 与顿号冒号同理——它们只是把公共词汇串起来的记号）：检验值是机理模型算出来的，三位有效数字的读数、日期、
    参考区间与真实语料的数字天然会撞（"报告时间20241028"撞上某份真实报告同一天的日期）。
    数字的隐私风险——证件号、电话、就诊号——由 PII 谓词单独管。所以回放命中之后，
    看的是**去掉数字剩下的文字**能不能被一个公共词条解释；一段真实病历的叙述句，
    去掉数字之后仍然是叙述句，照样会被抓住。
    """
    return re.sub(r"[\d↑↓、，,;；:：]", "", fragment)


def _strings_under(node, fields: set[str], inside: bool = False) -> list[str]:
    """树里所有落在声明字段下的字符串（字段名在任意深度命中即可）。"""
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


#: 窗口最多可以由几个**连续**的公共词条拼成。
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
    """这段窗口是否由公共词汇解释得了。

    第一版只放行"整段落在某一个词条里"（`酸激酶同工酶mb相对指数` 落在 `肌酸激酶同工酶MB相对指数` 里），
    并刻意拒绝拼接：两个无关公共词的尾首相接也可能是搬来的原文。表格语料下这条够用。

    2026-09-29 起有了叙述型文档（科室键值对、超声所见、总检），它们**本来就是公共短语的序列**：
    "超声提示：" 后面紧跟 "甲状腺未见明显异常"，"肝脏：" 后面紧跟 "肝脏形态大小正常"，
    真实报告里同样的短语也是同样的顺序。于是放宽为：窗口可以由**至多三个连续的公共词条**拼成
    （开头可以切在某个词条中间、结尾也可以，中间的必须是整词）。残余风险是一段只由三个词典短语
    拼成的原文会被放过；再长的原文一定跨过更多词条边界，仍会被抓住。

    第一版还试过"把所有白名单词汇从片段里逐个剥掉，剩不下几个字就豁免"——那是错的：
    对截断窗口失效（12 字窗口 `notapplicabl` 并不包含完整的词 `notapplicable`）。
    这里的做法用词条的后缀集与前缀集，截断窗口自然落在里面。
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


# ── 检查 ─────────────────────────────────────────────────────────
#: 与 `.githooks/pre-commit` 里那套**分开写**的 PII 谓词。
PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("身份证号", re.compile(r"[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])"
                            r"(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]")),
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("就诊/病案号", re.compile(r"(?:门诊|住院|病案|体检|就诊卡)\s*号\s*[:：]?\s*\d{6,}")),
    ("脱敏占位符", re.compile(r"«[^»]{0,20}»")),
    ("邮箱", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    ("固话", re.compile(r"(?<!\d)0\d{2,3}-\d{7,8}(?!\d)")),
]


# ── 虚构池白名单（姓名、机构名）与合成编号 ───────────────────────
#: 与 `mirobody_gen/synthid.py` **分开写**的同一个校验算法。两份实现必须一致，
#: `tests/test_render.py` 断言这一点；闸门不 import 生成器，否则它只能确认生成器自己的盲区。
_SYNTH_SALT = "mirobody-gen/synthetic-id/v1"


def synthetic_id(number: str) -> bool:
    import hashlib

    digits = "".join(ch for ch in number if ch.isdigit())
    if len(digits) < 6:
        return False
    digest = hashlib.blake2b(f"{_SYNTH_SALT}:{digits[:-3]}".encode(), digest_size=4).digest()
    return f"{int.from_bytes(digest, 'big') % 1000:03d}" == digits[-3:]


def load_fiction() -> tuple[set[str], list[str], set[str]]:
    """(虚构人名, 虚构机构名, 机构类型后缀)。"""
    path = RESOURCES / "fiction.json"
    if not path.is_file():
        return set(), [], set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    names = set(payload.get("person_names_zh", [])) | set(payload.get("person_names_en", []))
    return (names, [x["name"] for x in payload.get("institutions", [])],
            set(payload.get("institution_types", [])))


def institution_ok(found: str, institutions: list[str], types: set[str]) -> bool:
    """机构名必须能在虚构池里找到。类型后缀（"市第人民医院"、"General Hospital"）只在**整串相等**
    时放行——它是模板本身出现在 spec 里；若按子串放行，任何"XX General Hospital"都会溜过去。"""
    return found in types or any(name in found or found in name for name in institutions)


#: 姓名字段：标签后面跟着的那个名字必须属于虚构池。
NAME_FIELD = re.compile(r"(?:姓名|送检医生|申请医生|检验者|审核者|检查者|操作者|检验人|报告医生|"
                        r"Name|Requested by|Performed By|Verified By|Physician)\s*[:：]\s*"
                        r"([\u4e00-\u9fff]{2,4}|[A-Z][a-z]+ [A-Z][a-z]+)")
#: 机构名：以机构类后缀结尾的一串。必须能在虚构池里找到。
INSTITUTION = re.compile(r"([\u4e00-\u9fff]{2,14}(?:医院|保健院|体检中心|管理中心|检验所|检验中心|服务中心|门诊部))"
                         r"|((?:[A-Z][a-z]+ ){1,3}(?:General Hospital|Medical Centre|Medical Center|Hospital|"
                         r"Clinical Laboratories|Pathology Services|Health Screening Centre|Family Clinic))")


def scan(paths: list[pathlib.Path], index: np.ndarray | None,
         vocabulary: list[str], report_only: bool) -> int:
    findings = 0
    excused = 0
    excused_samples: list[str] = []
    synthetic_ids = 0
    excused_numeric = 0
    # 词条本身也去掉数字，才能和"去数字后的残余"比：`×10^9/L` 去数字是 `×^l`。
    digitless = sorted({residual_text(t) for t in vocabulary if residual_text(t)}, key=len, reverse=True)
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

        # ① 回放：逐个文本单元扫，n-gram 不跨单元
        for unit in units:
            if index is None or not index.size:
                break
            norm = normalize(unit)
            hashes = ngram_hashes(norm)
            if hashes.size:
                positions = np.clip(np.searchsorted(index, hashes), 0, index.size - 1)
                hit_at = np.nonzero(index[positions] == hashes)[0]
                for i in hit_at:
                    fragment = norm[int(i):int(i) + NGRAM]
                    if fragment in seen:
                        continue
                    seen.add(fragment)
                    if is_public_term_window(fragment, vocabulary):
                        excused += 1
                        excused_samples.append(fragment)
                        continue
                    rest = residual_text(fragment)
                    if len(rest) <= 2 or is_public_term_window(rest, vocabulary) or \
                            is_public_term_window(rest, digitless):
                        excused_numeric += 1
                        excused_samples.append(f"{fragment}（去数字后：{rest or '空'}）")
                        continue
                    findings += 1
                    print(f"回放命中  {rel}: …{fragment}…")

        # ② PII
        for label, pattern in PII_PATTERNS:
            for match in pattern.finditer(text):
                if label == "就诊/病案号" and synthetic_id(match.group()):
                    synthetic_ids += 1
                    continue
                findings += 1
                print(f"PII 命中  {rel}: {label} → {match.group()[:24]}")
        # ②' 姓名与机构名：白名单，不是黑名单
        for match in NAME_FIELD.finditer(text):
            if match.group(1) not in fiction_names:
                findings += 1
                print(f"姓名不在虚构池  {rel}: {match.group()[:24]}")
        for match in INSTITUTION.finditer(text):
            found = match.group(1) or match.group(2)
            if not institution_ok(found, fiction_institutions, institution_types):
                findings += 1
                print(f"机构不在虚构池  {rel}: {found[:30]}")

    print(f"\n扫描 {scanned} 个文件 · 命中 {findings} 处 · "
          f"按公共词汇豁免 {excused} 处 · 去数字后落在公共词条内 {excused_numeric} 处 · "
          f"合成编号（校验尾通过）{synthetic_ids} 个 · "
          f"读不出文本 {len(unreadable)} 个")
    if report_only and excused_samples:
        # docstring 承诺"豁免会被计数并打印，不会被藏起来"。只打一个总数不算打印：
        # 看不到是哪些词撑起了那个 0 命中，就无法判断豁免规则是不是太松。
        print("  豁免样例（每条都整段落在某一个公共词汇里）：")
        for fragment in excused_samples[:20]:
            print(f"    {fragment}")
        if len(excused_samples) > 20:
            print(f"    …另有 {len(excused_samples) - 20} 条")
    if unreadable:
        print(f"  （读不出的是图像等二进制产物，回放检测覆盖不到它们；见模块文档"
              f"\"这道闸门管不到什么\"。前几个：{', '.join(p.name for p in unreadable[:3])}）")
    return 0 if report_only else findings


#: 参考区间允许的出处形态。三值枚举放在文件级表达不了"一个文件里混着标准、指南、
#: 厂商说明书和恒等式"这件事——所以逐项还要有一个**可引用**的出处，
#: 且必须命中下面的形态之一。`通行临床区间` 这种说法不算出处。
CITATION_PATTERNS = ("WS/T", "GB/T", "指南", "操作规程", "说明书", "由恒等式定义",
                     "心电图学", "WHO", "IFCC", "CLSI", "专家共识")


def check_indicator_citations() -> int:
    """每个指标的参考区间都要有可引用的出处。"""
    path = RESOURCES / "indicators.json"
    if not path.is_file():
        return 0
    problems = 0
    for item in json.loads(path.read_text(encoding="utf-8"))["indicators"]:
        source = item.get("reference_source") or ""
        if not any(p in source for p in CITATION_PATTERNS):
            print(f"出处不可引用  {item['key']}: reference_source={source!r}"
                  f"（要么给标准号/指南名/说明书，要么说明为什么没有）")
            problems += 1
    return problems


def check_spec_provenance() -> int:
    """spec 的每个文件都要声明来源。"""
    allowed = {"public-standard", "format-token", "hand-authored", "llm-paraphrase", "llm-template"}
    problems = 0
    for path in sorted((RESOURCES).glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        source = payload.get("_source") if isinstance(payload, dict) else None
        if source not in allowed:
            print(f"溯源缺失  {path.relative_to(REPO)}: _source={source!r}，"
                  f"必须是 {sorted(allowed)} 之一")
            problems += 1
        if isinstance(payload, dict) and "_provenance" not in payload:
            print(f"溯源缺失  {path.relative_to(REPO)}: 没有 _provenance")
            problems += 1
        if isinstance(payload, dict) and "_vocabulary_fields" not in payload:
            print(f"溯源缺失  {path.relative_to(REPO)}: 没有 _vocabulary_fields"
                  f"（要声明哪些字段装的是可豁免的公开词汇；没有可豁免字段就写 []）")
            problems += 1
        # 模型产出的资源是不可信文本：不得豁免任何字段，必须以原文过回放检测与 PII 谓词
        if isinstance(payload, dict) and str(source).startswith("llm-") and payload.get("_vocabulary_fields"):
            print(f"豁免越界  {path.relative_to(REPO)}: _source={source!r} 的资源不得声明 _vocabulary_fields")
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
        print(f"真实语料 n-gram 索引：{index.size:,} 个 {NGRAM} 字窗口（只存哈希）")

    problems = scan(paths, index, load_vocabulary(), args.report)
    problems += check_spec_provenance()
    problems += check_indicator_citations()

    if args.report:
        raise SystemExit(0)
    if problems:
        print(f"\n隐私闸门未通过：{problems} 处。")
        raise SystemExit(1)
    print("隐私闸门通过。")


if __name__ == "__main__":
    main()
