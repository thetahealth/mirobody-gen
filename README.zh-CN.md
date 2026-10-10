# mirobody-gen

**可重放的高质量个人健康数据规模化生成器——每份报告、每条可穿戴推送、每句日记、每个基因位点都可追溯到产出它的规则，全部带逐行真值。**

[English](README.md) · [架构](docs/ARCHITECTURE.md) · [隐私模型](docs/PRIVACY.md) · [输出 schema](docs/SCHEMA.md) · [变更日志](CHANGELOG.md)

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
| **文档** | 化验单、体检报告书、门诊病历、心电/超声/影像报告、家庭记录、App 导出表——以文本层 PDF、XLSX、CSV，以及 24 种扫描/拍照/复印/截屏场景（T0–T6 档）交付；可选手写的记录本、医生手写病历与手填表格（H1–H3 档） | 文件上传管线 |
| **手机健康库** | `devices/` 批次，每批 ≤500 条，字段名照抄 Apple / 华为 / 小米 / Health Connect 的 crosswalk 表，可直接 POST | `POST /api/data` |
| **厂商云** | `vendor_signals/`：字节级对标的 HealthKit JSON、Garmin Health API（dailies/sleeps/bodyComps/activities/pulseOx）、Oura v2（活动/睡眠/心率/血氧/压力）、WHOOP v2（周期/训练/恢复） | `kernel/decoders/{apple,garmin,oura,whoop}.py`——输出形状以 [`mirobody/kernel/decoders/samples/`](https://github.com/thetahealth/mirobody/tree/main/mirobody/kernel/decoders/samples) 的验收记录为锚 |
| **基因** | WeGene、23andMe、AncestryDNA、MyHeritage 与 VCF 导出；41 个 PGx 位点 + catalog 子集 + 表外位点，等位基因按祖源频率抽取，1.5% no-call | 遗传学处理器 |

同一个人**纵向地横跨四条通道**：2024 年体检报告书上的血红蛋白、那一周 Garmin dailies 里的静息心率、
消费级基因文件里的 CYP2C19 双倍型，属于同一个人、同一条事件时间线。正是这种同一性，让"你有没有把这个人的
化验单和这个人的可穿戴流合并成一个人"成为一个可检验的问题。
厂商云 payload 就是手机健康库那条序列换成各家 API 的形状：健康库是 Apple Health 的人都有 HealthKit JSON；
Garmin / Oura / WHOOP 只给戴着设备、且健康库是这些设备写入的 Apple Health 或 Health Connect 的人——
所以同一天的步数、静息心率在 `devices/` 与那天的 Garmin daily 里是同一个数。

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

渲染使用 PyMuPDF 自带字体，同种子输出跨机器字节一致。手写页（见下）使用 `mirobody_gen/render/fonts/` 里随包的手写字体与 Pillow 的 FreeType：同一 Pillow 构建下跨机器字节一致，真值在任何机器上都一致。第三方有个同名占位包 `fitz` 会抢占 PyMuPDF 的导入名：
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
# rules 只读文字：图像档和扫描件给空预测，因此它的分数是下限
mirobody-gen baselines rules out/p3/files.jsonl --out out/p3/pred_rules.jsonl
mirobody-gen score     out/p3/files.jsonl out/p3/pred_rules.jsonl
mirobody-gen score     out/p3/pairs.jsonl out/p3/pred_rules_pairs.jsonl --pairs
```

队列语言构成是构建参数而不是资源改动：`--lang-mix 'zh:0.45,en:0.4,ja:0.15'` 重新配比各语言组人数
（设备时区、品牌份额、基因的祖源频率列随组走；非中文组的叙事措辞回落到英文字典——日语医学文书模板是
资源层的独立工程）。

冒烟构建：`--people 8`。`python -m mirobody_gen <command>` 与 `mirobody-gen <command>` 等价。

## 手写

```bash
mirobody-gen build --seed 7 --out out/p3 --render --handwriting
```

`--handwriting` 在渲染构建里加入手写文件，中英文都有，语言跟随这个人与这家机构已有的语言：一页记录本上的
家庭血压（一格 `128/82`，两个读数）、血糖（空腹与餐后）或晨起体重，数值取自本人的设备序列或生理模型；
门诊病历本上医生的手写记录，生命体征写在行文里（`T 36.8°C P 72次/分 BP 130/85mmHg`、
`Temp 36.8°C HR 72 RR 16 BP 124/80`）；以及机构自己的打印表格、结果栏由手填写。每份文件记下书写档位——
**H1** 工整（楷书、印刷体式英文）、**H2** 行书/连笔、**H3** 难认的草书，且一律是手机照片——以及它带的陷阱：
划掉的数值旁边写了更正（`hand.correction`）、日期栏的同上符号（`hand.ditto`）、单位只在表头写一次、
生命体征写在行文里。手写页走与印刷件相同的扫描与手机拍照场景；只有每个手写数值在交付图像上仍过得了
可读性下限（数字高度、墨迹对比度）的那次拍摄才被保留；没有一种拍摄能保住可读性的页面不交付。

这个选项默认关闭：不加它的构建与没有这项功能的生成器逐字节一致，已发布语料的哈希不变；加上它，
每份印刷文件与记录都不变，手写记录排在 `files.jsonl` 的后面。字体是 Google Fonts 开放许可手写字体的子集
（马善政、志莽行书、刘建毛草、龙藏；Caveat、Homemade Apple、Nanum Pen Script、Indie Flower），
按 sha256 固定、许可文本随附，见 [mirobody_gen/render/fonts/README.md](mirobody_gen/render/fonts/README.md)。
即使逐字抖动，字体也比人手规整：这些页面是手写的近似，不能替代真实手写。

## 连续数据流

```bash
mirobody-gen build --seed 7 --out out/p3 --continuous
```

`--continuous` 加上佩戴者手里真正有的曲线：

- **CGM 传感器周期。** Dexcom G7/G6、FreeStyle Libre 2/3、硅基动感 GS1、三诺爱看 i3，每 1–15 分钟
  一个读数，连续 10–15 天。
- **日内心率。** 来自本人的 Apple Watch、华为或安卓手表、小米手环或 Oura 戒指。

两者都来自同一个人、同一个模型。血糖曲线的日均值等于本人当天预期 HbA1c 对应的 eAG，所以传感器的 GMI
跟着化验单上的 HbA1c 走，二甲双胍会同时改变两者。心率围绕手机健康库自己的静息心率与睡眠记录。
两条数据流读同一份「一天的安排」（三餐、一次跑步、加起来等于健康库步数的步行片段），所以傍晚的一次
跑步在同一分钟既是心率峰也是血糖谷。

传感器层再加上：

- 组织间液滞后；
- 校准漂移；
- 按厂商公布 MARD 的噪声；
- 夜间压迫性低值；
- 预热期；
- 可回填与回填不了的断连；
- Libre 2 超过 8 小时未扫描而丢失的历史。

每种设备写出它自己的导出格式，依据是公开的原始导出文件与厂商文档：

- Dexcom Clarity CSV（BOM、全字段加引号、行长不齐、`Low`/`High`）与 Web API v3（`39`/`401`、UTC 与
  本地时间并存）。
- LibreView CSV（按记录类型分组而非按时间、账户日期格式、超量程写成量程边界）。
- 硅基动感医院端 CSV（中文表头、最新在前）与 App 导出工作簿（`.xls` 文件名、OOXML 内容）。
- Medtronic CareLink CSV（`-------` 分节、最新在前）。
- 用户自建工具链带来的文件：Nightscout 服务端存下的 entries、xDrip+ 的 SiDiary 导出、Tidepool 的 Excel 或
  JSON 导出、亲友关注端看到的 Dexcom Share 或 LibreLinkUp 快照。
- Apple Health `export.xml`、Oura API、Zepp Life CSV，以及 mirobody 的 `/api/data` 批次。

App 根本不交出读数的设备（三诺、鱼跃、微泰、硅基动感国内版），佩戴者手里是一份报告，语料里也是。报告是带文字层的
PDF，用各厂商自己的措辞：三诺、鱼跃、微泰、硅基国内版各自的报告，以及 IDC AGP 报告 v5、2017 指南的医院 CGM 报告单。指标包括
TIR/TAR/TBR、GMI、CV，以及中国指南里的 SD、MAGE、MODD、LAGE。每个印出来的数值都是真值，所以读报告的提取器
可以像读化验单一样打分。

真值（`continuous.jsonl`）为每个读数保留其背后的真实血糖或心率，记录缺口及原因、共识 CGM 指标和命名的
`stream.*` 陷阱。每条设备事实与格式细节在 `resources/streams.json` 里都带来源。
[docs/DEVICE_FORMATS.md](docs/DEVICE_FORMATS.md) 逐条列出网址、各自证实了什么、把握有多大，以及尚未
建模的格式：Medtronic 780G 泵的导出、Nightscout API v3 及 Loop/Trio/AAPS 上传器、Garmin 真实的心率映射结构、
Fitbit API 的继任者。

默认关闭；不加它的构建与之前逐字节一致。

## 一次构建产出什么

| 路径 | 内容 | 真值 |
| --- | --- | --- |
| `manifest.jsonl` | 每次就诊一条：按 mirobody 提取字段名的读数、诊断、自上次就诊以来的事件、套餐、主诉、发现 | 一切真值的根 |
| `people.jsonl` | 每人一条：原型、疾病、带幅度/起效/半衰期的事件时间线 | 归因答案 |
| `files/` + `files.jsonl` | 每份文件一条：`printed_rows[]`（MedRepBench 五字段）、`readings[]`（LOINC、UCUM、观测日期）、版式摘要、带行归因的 `hazards[]`、档位/场景/算子参数、`distractors[]`；手写文件另有 `handwriting`（档位、笔迹、更正、同上符号、转写、可读性） | 提取 + 标准化 |
| `pairs/` + `pairs.jsonl` | 每次就诊一份干净基线 + 每类陷阱一个变体，共享基线真值的扫描/拍照/复印/截屏对齐视图 | 单个陷阱的因果效应 |
| `devices/` + `devices.jsonl` | 手机健康库批次 | 每条记录的 LOINC |
| `vendor_signals/` + `vendor_signals.jsonl` | 厂商云推送（见上） | 每条记录预期落到的 catalog 指标 |
| `journal.jsonl` | 以本人口吻写的一句话日记 | 一句话应切分出的条目，带 ICPC-3 码 |
| `genomics/` + `genomics.jsonl` | 消费级基因导出 | 逐位点基因型、call 状态、catalog 归属 |
| `continuous/` + `continuous.jsonl`（`--continuous`） | CGM 导出、Apple Health `export.xml`、可穿戴心率文件与批次 | 每个读数背后的真实值、缺口、CGM 指标、`stream.*` 陷阱 |

逐字段定义见 [docs/SCHEMA.md](docs/SCHEMA.md)。

## 确定性与审计

语料是 生成器 + 种子（+ `--lang-mix`、+ `--paraphrase`、+ `--handwriting`）的函数：同参数两次构建字节一致，`out/` 可丢弃，
生成器加种子**就是**交付物。三道审计门（临床、可读性、隐私）卡每次构建，保真度对参考集聚合值出报告；测试套件
在小构建上跑临床+可读性审计，CI 以 `--skip-replay` 跑隐私门（回放索引需要参考集，按
[docs/PRIVACY.md](docs/PRIVACY.md) 在发布机器上跑）。

## 语言模型

数据路径上没有模型：数值、发现、版式与图像全部来自资源、代码与种子。可选层
（`mirobody-gen build --paraphrase`）只让模型对**叙事模板换措辞**；候选必须保住每个槽位、数字与锁定词、
留在同一种语言、不含任何人名或标识符，被接受的改写会成为一份*资源*，被隐私门当作不可信文本扫描。
默认关闭；`mirobody-gen compare` 展示它改了什么。设计记录与契约在中文设计目录（`docs/zh-CN/`，
不进公开树——需要可索取）。

## 现状与 mirobody 的关系

- 本仓库交付生成器、审计与评分 harness。mirobody 1.5.4 的两套评测用它生成语料，并通过产品自己的接口做端到端打分：
  [`benchmarks/local_models`](https://github.com/thetahealth/mirobody/tree/main/benchmarks/local_models)
  （选哪个回答模型：种子 7、6 个人，问答、文档抽取和日记句子）和
  [`benchmarks/local_ocr`](https://github.com/thetahealth/mirobody/tree/main/benchmarks/local_ocr)
  （选哪个文档 OCR 模型：种子 7 的印刷页，以及种子 42 的 `--handwriting` 构建）。除此之外 mirobody 不直接读取构建目录，
  `vendor_signals/` 的推送仍是手工喂给它的解码器测试套件。
- 目录覆盖 172 个指标（153 定量、17 定性、2 分类）、52 个医嘱组、5 档套餐、7 个科室、17 项辅助检查与
  55 个具名发现；每条参考区间都注明了公开标准或指南出处。
- 基准定位——暂定名 **ESL-Doc**，与 ESL-Bench 组合——在不进公开树的中文工作稿中展开。无论基准最终叫什么，
  生成器都保留本仓库名。

## 贡献、安全、引用

见 [CONTRIBUTING.md](CONTRIBUTING.md)、[SECURITY.md](SECURITY.md)（含"如果你认为仓库中的某个文件不是
合成的该怎么办"）与 [CITATION.cff](CITATION.cff)。Apache 2.0。
