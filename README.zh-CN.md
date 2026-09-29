# mirobody-gen

合成健康档案语料的生成器。它产出人们真正会上传的那些文件——化验单、多页体检报告书、门诊病历、心电图与超声报告、
家庭血压记录——以文本层 PDF、表格、扫描件、手机照片、复印件与 App 截图的形态交付，另附同一批虚拟人的可穿戴设备批次、
症状日记与消费级基因导出文件。每份文件都带逐行真值。

[English](README.md) · [架构](docs/ARCHITECTURE.md) · [隐私模型](docs/PRIVACY.md) · [输出 schema](docs/SCHEMA.md) ·
[设计记录（中文）](docs/zh-CN/) · [变更日志](CHANGELOG.md)

## 用途

[mirobody](https://github.com/thetahealth/mirobody) 这类健康数据引擎要从一张有折痕的手机照片上读出化验单，
判断 `血紅素` 是血红蛋白而不是糖化血红蛋白，看出印成一格的 `120/80` 是两个读数，把一张表里七个早晨的体重合并成七天。
测试这条管线需要的语料有三个真实病历给不了的性质：

- **每个印刷格都有真值**（名称、值、单位、参考范围、标记），每个格背后的语义读数也有真值（指标键、LOINC、UCUM 单位、观测日期），
  于是提取与标准化可以评分，而不是靠眼看；
- **具名的难度**：每份文件记录它带了哪些版式与内容陷阱（单位粘在值上、性别分层的参考范围印在一格、中英双表头、跨页丢表头……）、
  经过了哪条采集链路（平板扫描、微信转发的照片、传真），召回率的下降因此可以归因；
- **没有隐私暴露**：语料里没有任何东西来自真实的人。

## 数值怎么来

生成器是知识驱动的，不对数据拟合：

- 参考区间来自公开标准（WS/T 404、WS/T 405 系列、临床指南、全国临床检验操作规程）；
- 个体内/个体间生物学变异来自公开的 Westgard / EFLM 数据库；
- 60 个虚拟人各有疾病原型与事件时间线（开始他汀、一次上呼吸道感染、加班一个月），事件带起效延迟与幅度，数值由机理模型算出；
  派生量（BMI、LDL、MCH/MCHC、eGFR、球蛋白、分类绝对值）一律由恒等式计算，不采样；
- 文档的**形态**——列组、参考值方言、单位写法、标记、陷阱类、页面构件——蒸馏自一个不随仓库分发的去标识参考集，
  进入仓库的只有聚合统计与格式记号，每条带 `source` 标签。威胁模型与闸门见 [docs/PRIVACY.md](docs/PRIVACY.md)。

## 安装

```bash
pip install -e ".[render]"          # numpy + PyMuPDF、openpyxl、Pillow
pip install -e ".[dev]"             # 另加 pytest、ruff
```

需要 Python 3.11+。渲染用 PyMuPDF 自带的字体，同一 seed 在任何机器上逐字节一致。图像层只用 Pillow 与 numpy；
OCR 复核用本机的 `tesseract`（若有），只报告不判定。

## 快速开始

```bash
# 60 人，约 700 份文件，12 组最小对照对。含图像层约 10 分钟。
mirobody-gen build --seed 7 --out out/p3 --render --pairs 12

# 四道审计是这批语料能不能用的判据，不是可选的收尾。
mirobody-gen audit-clinical    out/p3/manifest.jsonl                 # 恒等式、生理边界、标记、RCV、诊断互证
mirobody-gen audit-readability out/p3/files.jsonl out/p3/pairs.jsonl   # 每条印刷真值都在纸上
mirobody-gen audit-readability out/p3/files.jsonl --ocr              # 图像层：按场景报告 OCR 认出比例
mirobody-gen audit-privacy     --targets mirobody_gen/resources out/p3 # PII 谓词、白名单、回放索引
mirobody-gen audit-fidelity    out/p3/files.jsonl                     # 形态统计对参考集聚合量

# 给抽取器评分（预测文件与 MedRepBench 同格式）
mirobody-gen baselines rules out/p3/files.jsonl --out out/p3/pred_rules.jsonl
mirobody-gen score out/p3/files.jsonl out/p3/pred_rules.jsonl
mirobody-gen score out/p3/pairs.jsonl out/p3/pred_rules_pairs.jsonl --pairs
```

`python -m mirobody_gen <command>` 与 `mirobody-gen <command>` 等价；每个命令也能按模块运行
（`python -m mirobody_gen.audit.clinical …`）。冒烟测试用 `--people 8`。

## 一次构建产出什么

| 路径 | 内容 | 真值 |
| --- | --- | --- |
| `manifest.jsonl` | 每次就诊一条：读数（字段名与 mirobody 抽取契约同名）、诊断、上次就诊以来的事件、套餐、主诉、具名所见 | 真值根 |
| `people.jsonl` | 每人一条：原型、诊断、事件时间线（幅度 / 起效 / 半衰期） | 归因问题的答案 |
| `files/` + `files.jsonl` | 每份文件一条：印刷行（MedRepBench 字段）、语义读数、版式摘要、逐行归因的陷阱、交付层 / 场景 / 算子参数、块（科室键值对、叙述、总检）、所见、主诉、诊断 | 抽取 + 标准化 |
| `pairs/` + `pairs.jsonl` | 最小对照对：一份干净的 base，每类陷阱一个变体，另有共用同一真值的扫描 / 照片 / 复印 / 截图对齐视图 | 单类陷阱的因果效应 |
| `devices/` + `devices.jsonl` | 手机健康库批次（Apple / 华为 / 小米 / Health Connect 字段名，每批 ≤500 条，可直接 POST） | 每条的 LOINC |
| `journal.jsonl` | 本人口吻的一句话日记 | 这句话该拆成哪几条，各自的 ICPC-3 码 |
| `genomics/` + `genomics.jsonl` | 消费级基因导出文件（WeGene、23andMe、AncestryDNA、MyHeritage、VCF） | 逐位点的基因型、调用状态、是否在目录内 |

逐字段定义见 [docs/SCHEMA.md](docs/SCHEMA.md)。

## 文档种类与交付层

种类：化验单、体检报告书（封面、总检结论与建议、一般检查、科室键值对、心电图条图、超声/放射叙述、检验表格）、
门诊病历（主诉、病史、体格检查行、诊断、处理）、心电图报告、超声报告、影像报告、家庭血压/体重记录、App 导出表。

交付层：T0 文本层 PDF · T1 XLSX / CSV · T2 扫描与 App 增强件 · T3 手机照片 · T4 复印、传真、旧档案、多次转发 ·
T6 截图与拍屏。24 个场景是按 PureDocBench 的劣化谱与真实采集链路设计的算子链；一份文档的所有视图共用一份真值。
细节与校准数字见 [docs/zh-CN/degradation.md](docs/zh-CN/degradation.md)。

## 确定性与审计

语料是"生成器 + seed"的函数：同一 seed 两次构建逐字节一致（文件、图像、表格、元数据），所以 `out/` 随时可删。
四道审计把关（`tests/` 在 CI 里对一个小构建跑同样的检查）：

- **临床**——面板内的恒等式、生理硬边界、性别专属项目、标记与区间一致、跨次的参考变化值筛查、诊断与数值互证；
- **可读性**——每条印刷真值都在纸上（文本层），核对前剔掉页脚构件；图像层用 OCR 报告；
- **隐私**——PII 谓词、姓名与机构白名单、资源溯源，以及在持有参考集的机器上跑的 n-gram 回放索引；
- **保真**——版式指纹多样性、陷阱密度、行数与写法分布对参考集聚合量（只报告）。

## 状态

- 与 mirobody 测试套件的接线（一个指向构建目录的环境变量）已设计、尚未接上，见 [docs/zh-CN/plan.md](docs/zh-CN/plan.md) §6。
- 目录覆盖 172 项指标（定量 153、定性 17、分类 2）、52 个医嘱组、5 档套餐、7 个科室、17 项辅助检查、55 条具名所见；每个参考区间都注明公开标准、指南或专家共识出处（见 [docs/zh-CN/research-2026-09-29.md §8](docs/zh-CN/research-2026-09-29.md)）。扩充是 spec 的改动（`scripts/build_*.py`），不是代码改动。

## 贡献、安全、引用

见 [CONTRIBUTING.md](CONTRIBUTING.md)、[SECURITY.md](SECURITY.md)（含"如果你认为某份文件不是合成的"该怎么办）
与 [CITATION.cff](CITATION.cff)。许可证：Apache License 2.0。
