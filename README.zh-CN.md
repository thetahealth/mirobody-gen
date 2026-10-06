# mirobody-gen

**可重放的高质量个人健康数据规模化生成器——每份报告、每条可穿戴推送、每句日记、每个基因位点都可追溯到产出它的规则，全部带逐行真值。**

[English](README.md) · [架构](docs/ARCHITECTURE.md) · [隐私模型](docs/PRIVACY.md) · [输出 schema](docs/SCHEMA.md) · [设计记录（中文）](docs/zh-CN/) · [变更日志](CHANGELOG.md)

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3775A9)](pyproject.toml)

---

这是一个**独立的生成器项目**。它的职责是产出**广泛、拟真、完全可溯源的个人健康数据**——构成一份真实
个人档案的全部材料：化验单、体检报告书、门诊病历、可穿戴设备流、手机健康库批次、日记句子与消费级基因导出——
**高效**（60 个人、几十秒）、**准确**（数值由机理模型与公开标准算出，派生量按定义恒等式计算）、**可追溯**
（每个数值、每条版式约定、每类陷阱都声明来源，整份语料由 生成器+种子 字节级重放）。

让产出**可检验**而不只是"看起来像"的三个性质：

- **每个印刷格背后有两层真值**——纸上写了什么（名称、值、单位、参考范围、标记，即 MedRepBench 五字段）
  *以及*它意味着什么（指标键、LOINC、UCUM 单位、观测日期）；
- **具名的难度**——每份文件声明它携带的陷阱类。分类学从一次真实语料研究中蒸馏出 62 类（文本层可造 61 类；
  `unit.glued_to_value` 出现在 22% 的真实文档上）；最小对照对把每一类的因果代价单独隔离；
- **零隐私暴露**——没有任何东西来自真实的人。文档*形态*统计以格式记号与聚合计数的形式来自一份私有脱敏
  参考集，每条带 `_source` 标签，由 allow-list `.gitignore`、pre-commit 隐私钩子和 n-gram 回放门把守。

语料在人这一层是纵向的：60 个虚拟人、8 种原型、跨多年的事件时间线，同一个人纵向一致地出现在**四条交付
通道**上——文档、手机健康库批次、厂商云推送、基因导出——于是"你有没有把这个人的化验单和这个人的可穿戴流
合并成一个人"成为一个可检验的问题。

[mirobody](https://github.com/thetahealth/mirobody) 是第一个消费方与验证场：它的文件管线、厂商解码器、
`/api/data` 端点与遗传学处理器正好对应上面四条通道，它的解码器测试套件形状把我们的厂商推送锚定到字节级。
工作论文将这份语料与 [ESL-Bench](https://arxiv.org/abs/2604.02834) 组合，量化"人手里拿着的材料"与
"已结构化的记录"之间的损失——但这份生成器的用途不限于此：任何需要拟真且带真值的健康数据的地方都用得上。

## 四条交付通道

| 通道 | 产物 | mirobody 入口 |
| --- | --- | --- |
| **文档** | 化验单、体检报告书、门诊病历、心电/超声/影像报告、家庭记录、App 导出表——以文本层 PDF、XLSX、CSV，以及 24 种扫描/拍照/复印/截屏场景（T0–T6 档）交付 | 文件上传管线 |
| **手机健康库** | `devices/` 批次，每批 ≤500 条，字段名照抄 Apple / 华为 / 小米 / Health Connect 的 crosswalk 表，可直接 POST | `POST /api/data` |
| **厂商云** | `vendor_signals/`：字节级对标的 HealthKit JSON、Garmin Health API（dailies/sleeps/bodyComps/activities/pulseOx）、Oura v2（活动/睡眠/心率/血氧/压力）、WHOOP v2（周期/训练/恢复） | `kernel/decoders/{apple,garmin,oura,whoop}.py`——输出形状以 [`mirobody/kernel/decoders/samples/`](https://github.com/thetahealth/mirobody/tree/feat/1.5.4/mirobody/kernel/decoders/samples) 的验收记录为锚 |
| **基因** | WeGene、23andMe、AncestryDNA、MyHeritage 与 VCF 导出；41 个 PGx 位点 + catalog 子集 + 表外位点，等位基因按祖源频率抽取，1.5% no-call | 遗传学处理器 |

同一个人**纵向地横跨四条通道**：2024 年体检报告书上的血红蛋白、那一周 Garmin dailies 里的静息心率、
消费级基因文件里的 CYP2C19 双倍型，属于同一个人、同一条事件时间线。正是这种同一性，让"你有没有把这个人的
化验单和这个人的可穿戴流合并成一个人"成为一个可检验的问题。

## 数值怎么来

知识驱动，不对数据拟合：

- 参考区间来自 **WS/T 404（生化）** 与 **WS/T 405（血细胞分析）** 系列、临床指南与全国临床检验操作规程；
- 个体内/个体间生物学变异来自公开的 EFLM / Westgard 数据库；
- 60 个虚拟人、8 种原型（健康、糖尿病前期转 2 型糖尿病、他汀治疗血脂异常、缺铁性贫血、甲状腺疾病、CKD 进展、
  脂肪肝、高血压），每人带事件时间线（开始他汀、一次感染、加班一个月），事件效应带起效延迟、幅度与半衰期；
  派生量（BMI、LDL、MCH/MCHC、eGFR、分类绝对值）一律按恒等式计算——MCV/MCHC/MCH 三个公开 CV<sub>G</sub>
  值 4.85%/2.8%/5.2% 只有在 MCH = MCHC × MCV 成立时才互相自洽，所以我们强制它成立；
- 文档*形态*（列组、参考范围方言、单位写法、标记符号、页面家具）蒸馏自一份不分发的私有脱敏参考集；只有格式
  记号与聚合统计进入仓库，每条带 `_source` 标签，由 allow-list `.gitignore`、pre-commit 隐私钩子和发布候选
  上的 n-gram 回放门共同把守。威胁模型见 [docs/PRIVACY.md](docs/PRIVACY.md)。

## 安装

Python 3.11+，建议在虚拟环境中（PyMuPDF 与 Pillow 仅渲染时需要）：

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[render]"    # PDF、表格、图像档
pip install -e ".[dev]"       # + pytest, ruff
```

渲染使用 PyMuPDF 自带字体，同种子输出跨机器字节一致。第三方有个同名占位包 `fitz` 会抢占 PyMuPDF 的导入名：
如果 `import fitz` 解析到的不是 PyMuPDF，先 `pip uninstall fitz`——真包叫 `pymupdf`。

## 快速开始

```bash
# 60 人、约 720 份文件、12 组最小对照对（不含图像档约 38 秒；含图像档更慢）
mirobody-gen build --seed 7 --out out/p3 --render --pairs 12

# 审计是验收门，不是可选的后处理
mirobody-gen audit-clinical     out/p3/manifest.jsonl                  # 恒等式、边界、标记、RCV、诊断
mirobody-gen audit-readability  out/p3/files.jsonl out/p3/pairs.jsonl  # 每条印刷真值都在页面上
mirobody-gen audit-privacy      --targets mirobody_gen/resources out/p3 # PII 谓词、白名单、回放索引
mirobody-gen audit-fidelity     out/p3/files.jsonl                     # 形态分布对账参考集聚合值

# 用两层真值给提取器评分（预测用 MedRepBench 格式）
mirobody-gen baselines rules out/p3/files.jsonl --out out/p3/pred_rules.jsonl
mirobody-gen score     out/p3/files.jsonl out/p3/pred_rules.jsonl
mirobody-gen score     out/p3/pairs.jsonl out/p3/pred_rules_pairs.jsonl --pairs
```

队列语言构成是构建参数而不是资源改动：`--lang-mix 'zh:0.45,en:0.4,ja:0.15'` 重新配比各语言组人数
（设备时区、品牌份额、基因的祖源频率列随组走；非中文组的叙事措辞回落到英文字典——日语医学文书模板是
资源层的独立工程）。

冒烟构建：`--people 8`。`python -m mirobody_gen <command>` 与 `mirobody-gen <command>` 等价。

## 一次构建产出什么

| 路径 | 内容 | 真值 |
| --- | --- | --- |
| `manifest.jsonl` | 每次就诊一条：按 mirobody 提取字段名的读数、诊断、自上次就诊以来的事件、套餐、主诉、发现 | 一切真值的根 |
| `people.jsonl` | 每人一条：原型、疾病、带幅度/起效/半衰期的事件时间线 | 归因答案 |
| `files/` + `files.jsonl` | 每份文件一条：`printed_rows[]`（MedRepBench 五字段）、`readings[]`（LOINC、UCUM、观测日期）、版式摘要、带行归因的 `hazards[]`、档位/场景/算子参数、`distractors[]` | 提取 + 标准化 |
| `pairs/` + `pairs.jsonl` | 每次就诊一份干净基线 + 每类陷阱一个变体，共享基线真值的扫描/拍照/复印/截屏对齐视图 | 单个陷阱的因果效应 |
| `devices/` + `devices.jsonl` | 手机健康库批次 | 每条记录的 LOINC |
| `vendor_signals/` + `vendor_signals.jsonl` | 厂商云推送（见上） | 每条记录预期落到的 catalog 指标 |
| `journal.jsonl` | 以本人口吻写的一句话日记 | 一句话应切分出的条目，带 ICPC-3 码 |
| `genomics/` + `genomics.jsonl` | 消费级基因导出 | 逐位点基因型、call 状态、catalog 归属 |

逐字段定义见 [docs/SCHEMA.md](docs/SCHEMA.md)。

## 确定性与审计

语料是 生成器 + 种子（+ `--lang-mix`、+ `--paraphrase`）的函数：同参数两次构建字节一致，`out/` 可丢弃，
生成器加种子**就是**交付物。三道审计门（临床、可读性、隐私）卡每次构建，保真度对参考集聚合值出报告；测试套件
在小构建上跑临床+可读性审计，CI 以 `--skip-replay` 跑隐私门（回放索引需要参考集，按
[docs/PRIVACY.md](docs/PRIVACY.md) 在发布机器上跑）。

## 语言模型

数据路径上没有模型：数值、发现、版式与图像全部来自资源、代码与种子。可选层
（`mirobody-gen build --paraphrase`）只让模型对**叙事模板换措辞**；候选必须保住每个槽位、数字与锁定词、
留在同一种语言、不含任何人名或标识符，被接受的改写会成为一份*资源*，被隐私门当作不可信文本扫描。
默认关闭；`mirobody-gen compare` 展示它改了什么。设计与契约见
[docs/zh-CN/llm-integration-2026-09-29.md](docs/zh-CN/llm-integration-2026-09-29.md)。

## 现状与 mirobody 的关系

- 本仓库交付生成器、审计与评分 harness。mirobody 通过一个指向构建目录的环境变量接入构建；**`feat/1.5.4`
  尚未接入**，目前语料通过这里的 `score` / `baselines` CLI 消费，以及把 `vendor_signals/` 的推送手工喂给
  mirobody 解码器测试套件的形状。
- 目录覆盖 172 个指标（153 定量、17 定性、2 分类）、52 个医嘱组、5 档套餐、7 个科室、17 项辅助检查与
  55 个具名发现；每条参考区间都注明了公开标准或指南出处。
- 工作论文（中文工作稿 [docs/zh-CN/paper.md](docs/zh-CN/paper.md)）记录了基准定位——暂定名
  **ESL-Doc**，与 ESL-Bench 组合——附 47 条全部核实过的参考文献
  [docs/paper/refs.bib](docs/paper/refs.bib)。无论基准最终叫什么，生成器都保留本仓库名。

## 贡献、安全、引用

见 [CONTRIBUTING.md](CONTRIBUTING.md)、[SECURITY.md](SECURITY.md)（含"如果你认为仓库中的某个文件不是
合成的该怎么办"）与 [CITATION.cff](CITATION.cff)。Apache 2.0。
