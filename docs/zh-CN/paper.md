# 论文框架（工作稿）

写于 2026-09-23。读者：合作者，含 ESL-Bench 作者组。
配套：[refs.bib](paper/refs.bib)（38 条，每条注明核实来源）·
[NUMBERS.md](NUMBERS.md)（真实语料的实测数字，唯一来源）·
合成语料的数字来自一次固定种子的构建（`seed=7`，命令见 §6 开头），换种子会有小幅波动。

本文取代 `PLAN.md` §10 的旧论文路线。定位的历次变更见 §0。

---

## 0. 定位变更记录（先读这个）

| 旧说法 | 为什么不成立 | 现在怎么写 |
|---|---|---|
| "临床一致性审计是我们的贡献，ESL-Bench 缺这个" | 读完 ESL-Bench 全文与公开数据：它有三级验证、每用户 audit 报告、硬边界 + 斜率限制 + 投影；公开数据的红细胞恒等式 6/6 精确成立 | **不作为贡献。** 我们的审计按 Kahn 框架 [kahn2016harmonized] 组织；唯一站得住的增量是"值—诊断一致性"（§6 E5） |
| "首个中文医疗报告抽取基准" | MedRepBench [shang2025medrepbench]：1,925 张真实中文医疗报告图像、同样的五个字段 | **不说首个。** 以它为基础：采用它的五字段与评分口径，产出它的官方脚本能直接读的标注（§4 C2） |
| "可控扰动 / 最小对照对是新方法" | 行为测试 CheckList [ribeiro2020beyond] 的不变性测试、对照集 [gardner2020evaluating] 早已成熟；文档侧有版面分析鲁棒性基准 RoDLA [chen2024rodla]，以及 2026-09 的预印本在 FUNSD/CORD/SROIE 上对比 OCR 劣化与金标准文本 [anvari2026pixels] | **方法谱系写明出处。** 我们的增量在**扰动什么**（从真实报告蒸馏的版式约定，不是图像劣化）与**真值放在哪一层**（语义真值不变、印刷真值可以合法地变，§4 C2） |
| "版式指纹 0.818、80% 文档版式独一无二" | 旧指纹用的是分析模型给参考值写法的**自由文本描述**，1,327 条描述里 1,100 种说法，有措辞噪声；合成侧也算不出"描述" | 换成模板化实例（`audit/fingerprint.py`，两边共用）：**0.783、74.6%**；只看列头时 0.494、40.0%。**必须连同粒度一起报** |
| "MedRepBench 的标注是逐字转写"、"其论文定义了'无法判定'" | 论文只写 OCR 辅助构建、人工核对（200 份抽检字段一致率 >97%），未写"逐字"；"无法判定"只出现在官方提示词文件里，论文正文期望模型"推断"标记 | 两处都按原文措辞写，出处写到文件 |
| E2 的真实锚点只能用我们不公开的语料 | 审稿人无法复现 | **MedRepBench 是公开的真实锚点**，私有语料只报 ρ |

---

## 1. 一句话

**健康 agent 的评测都从"数据已经被解析好"开始，而真实世界最难的一步恰恰在那之前。我们量这一步丢了多少、丢在哪，并且让"丢在哪"成为可以做因果归因的问题。**

## 2. 候选标题

1. *Lost at the Document Boundary: Measuring What Health Agents Miss When Records Arrive as Files*
2. *Seed, Value, Document: Regenerable Synthetic Health Records for Evaluating Longitudinal Health Agents*
3. *ESL-Doc: A Document-Grounded Companion to ESL-Bench*

基准名暂用 **ESL-Doc**（与 ESL-Bench 成对），待作者组定。

## 3. 摘要草稿

> 方括号为待测数字。

Health agents are increasingly evaluated on longitudinal records, but the
benchmarks begin where the hardest part ends: the data arrive already parsed.
In practice, patients hand over lab slips, checkup books and exports whose
layouts vary almost without bound — in 627 real Chinese health documents, three
in four have a layout fingerprint seen nowhere else. We present ESL-Doc, a
regenerable benchmark that renders synthetic longitudinal patients into realistic
documents, and builds on MedRepBench, the public real-image benchmark for the
same task: it adopts MedRepBench's five-field schema, and exports labels its
official scorer reads unchanged.

Every row carries **two layers of truth** — what is printed, and what it means
(analyte, LOINC code, value, unit, observation date) — so the same corpus scores
extraction and terminology mapping, which a transcription-only benchmark cannot.
Every row also carries the named extraction hazards injected into it (46 classes
distilled from the real corpus, of which only names and frequencies are released),
and **minimal pairs** that differ in exactly one hazard turn "which layout
phenomenon costs how much" into a paired, causal estimate. We further show that
MedRepBench's objective metric conflates distinct failures: its in-order
truncation penalises even an oracle transcription, and its abnormal-flag
convention caps a perfect judge at 92.3% on its own labels; we report the official
score alongside an alignment-based one.

Composed with ESL-Bench, the same queries can be answered from structured records
or from documents; the difference isolates the document-boundary cost [TBD: Δ and
its decomposition]. Model rankings on ESL-Doc agree with rankings on MedRepBench
[TBD: Spearman ρ]. The generator, specification, audits and a sealed evaluation
seed are released; the real corpus is not.

## 4. 贡献

**C1 文档边界代价（主线）。** 形式化 Δ = 结构化输入得分 − 文件输入得分，
并拆成抽取损失（行没读出来）、术语损失（读出来了没落到 LOINC）、推理损失（数据都对但答错）。
同一批 ESL-Bench 用户、同一批查询，两种到达方式。ESL-Bench 有查询与真值没有文件；
文档基准有文件但不关心下游问答。**这一条只有两边合起来才能做**（E4）。

**C2 在 MedRepBench 之上：可再生、两层真值、可做因果归因。** 逐项对照：

| | MedRepBench | ESL-Doc | 依据 |
|---|---|---|---|
| 真值层次 | 印刷内容（OCR 辅助 + 人工核对） | 印刷真值 + 语义真值（指标、LOINC、数值、UCUM、观测日期） | `generator/document.py` |
| 能考术语层吗 | 不能：没有 LOINC 真值 | 能 | 用 MedRepBench 真实指标名量过：mirobody 解析器按条目只覆盖 50.0%，我们自己目录上是循环的 91%（§9-8） |
| 失败归因 | 人工观察归纳的三类，**未逐类计量** | 每行带陷阱名，逐类计量；最小对照对给出配对差值 | 论文 v2 "No-OCR Failure Modes" 段 |
| 必须弃权 | 无 | 印了行名没印值的行，抽出值即记幻觉（SQuAD 2.0 [rajpurkar2018know] 的"答不出才对"搬到行级） | `harness/score.py` |
| 纵向 / 多文档 | 单张报告，论文明确不涉及病史整合 | 同一人多年多份；转置导出表一份文件多个日期 | `generator/export.py` |
| 规模与再生 | 固定 1,925 张 | 生成器 + 种子；换种子即得未见过的一批 | — |
| 兼容 | — | 导出它的标注格式，它的官方脚本原样可跑；我们的 V0 与官方脚本逐字段一致（`tests/test_score.py`） | `harness/medrep_view.py` |

两层真值是对照对能成立的前提：`unit.missing` 之后，纸上本来就没有单位，印刷真值里的单位
**合法地**变成空串；不变的是语义真值（这一行仍是 mmol/L 的血糖）。所以 CheckList 式的不变性测试
必须在语义层判——只有转写真值的基准做不了这件事。

**C3 评测口径的效度。** 读 MedRepBench 的公开评分脚本并在它自己的标注上量：

* **标记约定有上限**：标注里 13.2% 的条目标记为空（无法判定），而约定随值的类型变化
  （没有参考范围时，数值多标空、定性多标正常）。就算高/低判断全对，最贴合的映射也只有
  **92.3%**，朴素映射 86.8%、"没范围就空" 88.2%（`docs/handoff/medrepbench-eval-plan.md` §S4）；
* **按序截断惩罚正确答案**：oracle 转写（多交了几条参考范围被截断、因而不在 V0 真值里的行）
  在我们的检验单上只拿 0.995；规则基线的名称召回截断前后差 2.1 个点（0.919 → 0.898）；
* **名字不中整行作废**：`白细胞计数` 与 `白细胞计数(WBC)` 在官方口径下值再对也全错。

我们报官方分数以便对照，同时报**对齐口径**：匈牙利算法做一对一行对齐（表格识别里 GriTS
[smock2023grits]、TEDS [zhong2020image] 都不按顺序截断），给出行召回、行精确率，以及只在对齐行上
算的字段准确率。

**C4 真实版式与陷阱的分类学，以及一个可以公开的方式。** 从 627 份真实文档蒸馏 62 类陷阱
（覆盖 3,722 条描述的 76.9%），文本层可造 46 类，另 7 类属于通道（OCR 认错字等，留给图像层自然产生），
8 类延后并写明原因——**没有一类被静默丢弃**（`tests/test_render.py`）。
隐私论证是结构性的：真实记录不在取值路径上，区别于 Stadler 等 [stadler2022groundhog] 批评的
"在真实数据上拟合生成模型"；辅以三道独立闸门：n-gram 回放（数字不作为搬运证据，去掉数字后的残余
必须落在单个公共词条内）、姓名与机构名**白名单**（虚构池）、合成编号带盐校验尾。
按 Yao 等 [yao2025dcr] 的警告，不把距离型指标当作隐私论据。

**C5 种子/值/文档三层分解。** Synthea 与 PySynthea 的完整血常规多数违反红细胞恒等式，
且 0 条观测带参考区间（§6 E5，样本待扩）；值层按 WS/T 404/405 [wst404, wst405]、生物学变异与
恒等式派生，按 Kahn 框架审计。与 Hodges 等 [hodges2023medication] 用 MEPS 修 Synthea 用药层同一思路。

## 5. 章节结构

| § | 标题 | 要说什么 | 证据/产物 |
|---|---|---|---|
| 1 | Introduction | "已解析"假设；真实到达形态的长尾；与 MedRepBench、ESL-Bench 的关系 | NUMBERS.md |
| 2 | Related Work | §7 的七个方向 | refs.bib |
| 3 | Problem | Δ 的定义与三段分解；两层真值；为什么转写真值测不到术语层 | 形式化 |
| 4 | ESL-Doc | 4.1 三层分解；4.2 值层；4.3 文档层（版式家族、机构过程、陷阱三种来路）；4.4 两层真值与对照对；4.5 审计与隐私 | `generator/`、`audit/` |
| 5 | Corpus & Fidelity | 规模、构成、与真实分布对账（E1） | `audit/fidelity.py` |
| 6 | Measuring the Metric | MedRepBench 口径的三处问题与对齐口径（C3，E7） | `harness/score.py` |
| 7 | Experiments | E2–E6 | §6 |
| 8 | Limitations | §9 | — |
| 9 | Ethics & Data | 真实语料不公开；只发布过闸后的聚合形态（**需人定**，§11） | — |

## 6. 实验

合成侧数字的构建命令：`python3 -m generator.build --seed 7 --out out/p2 --render --pairs 12`
（60 人、601 次就诊、721 份文件、12 组对照对 338 份；38 秒）。
状态：✅ 已有数据　🟡 部分　⬜ 未开始

### E1 形态保真度 ✅（首轮）

`python3 -m audit.fidelity out/p2/files.jsonl`。**两边同一个指纹函数**。

| 量 | 真实 | 合成 | 说明 |
|---|---|---|---|
| 指纹种数比（只看列头） | 0.494 | 0.512 | 版式机制在列这一层对上了 |
| 指纹种数比（主定义） | 0.783 | 0.713 | |
| 单次指纹占文档（主定义） | 74.6% | 58.5% | 去掉"同人同机构"重复后 94.4%——真实值夹在中间，差距来自**纵向设计**（每年去同一家体检），不是版式机制 |
| 四类单位陷阱文档率 | 22.0 / 18.3 / 9.6 / 9.4% | 18.7 / 16.9 / 12.3 / 9.7% | 权重直接取这四个率（`layout.UNIT_AT`），与实测列组的单位列份额互相印证 |
| 可造陷阱各类文档率的排序相关 | — | Spearman ρ = 0.48 | 真实侧是分析模型**注意到的**，合成侧是逐条**认定的**；良性常见特征（定性结果、单侧范围、多日期）真实侧很少被记下，绝对值不可比 |
| 检验单每份行数 | p50=6（全体） | p50=6，p95=22 | 体检报告书与导出表两边不可比（真实侧分析截断了 62 份长文档） |
| 参考范围写法 JS 散度 | — | 0.366 | 合成 71 种、真实 64 种 |

机构分配是一个"两个大客户 + 中国餐馆过程长尾 + 个人粘性 + 模板逐年改版"的机制，
长尾集中度 α≈1041 由真实语料"627 份、491 种指纹"按 Ewens 公式反解——**参数来自聚合计数，
对不上时改机制，不拧系数**。第一版（50 家机构、每人 4 家固定）只得 16% 单次占比，被这张表抓到后改成现在这样。

### E2 排序一致性——效度的核心证据 🟡（工具就绪）

Gill 等 [gill2025lost] 实测过 LLM 生成的评测集**更容易**且不保持模型排序，所以既比排序也比绝对难度。

| 集合 | 性质 | 报告 |
|---|---|---|
| MedRepBench | 真实、公开 | 全部分数 |
| 私有真实语料（620 份） | 真实、不公开 | **只报 ρ** |
| ESL-Doc | 合成 | 全部分数 |

评分：官方 V0（与其论文可比）+ 对齐口径（C3）。工具：`harness/medrep_view.py`（导出）、
`harness/score.py`（评分，V0 已与官方脚本逐字段核对）、`harness/baselines.py`（oracle 与规则基线）。
mirobody 在 MedRepBench 上的接入计划见 `docs/handoff/medrepbench-eval-plan.md`。
**待做**：选 N 个系统（端到端 VLM、OCR+LLM、mirobody 产线），三个集合同口径跑一遍。

### E3 陷阱的因果效应 🟡（对照对就绪，只跑了规则基线）

`--pairs` 为同一内容、同一机构的干净版式各开一类陷阱；`harness/score.py --pairs` 报组内差值。
规则基线上的结果只用来**验证链路**（不是关于真实系统的结论）：分类列让行召回掉 0.95；
参考范围分行/按性别分层让范围准确率掉 0.42；"未做"行每组多一个幻觉值；
受检者字段印进表格每组多出 0.92 条误报。
**待做**：真实系统上跑；语义层的不变性判定需要系统输出 LOINC 与数值（`score.py` 已留接口）。
外部参照：传真式劣化使平均 F1 下降 38.8% [medrxiv2026ie]；OCR 质量对下游任务的影响 [vanstrien2020ocr]。
图像层（T2–T4）要求难度单调（T0 > T2 > T3 > T4）。

### E4 文档边界代价（C1 本体）⬜

ESL-Bench 用户 → 渲染成文件 → mirobody 产线入库 → 跑 ESL-Bench 原查询，对照结构化直接入库。
**前置**：`generator/eslbench.py` 适配器（schema 已核对，PLAN §6.1）；按医嘱拆成真实大小的报告。

### E5 值层有效性 🟡（样本太小，不可直接引用）

| 数据源 | 完整红细胞套餐 | MCV 违反 | MCHC 违反 | 带参考区间 | 值—诊断一致 |
|---|---:|---:|---:|---:|---|
| Synthea（Java，6 人） | 12 | 11 | 11 | 0 | — |
| PySynthea 1.4.0（8 人） | 8 | 6 | 7 | 0 / 2,323 | — |
| ESL-Bench（1 人 6 次检查） | 6 | 0 | 0 | 有 | 20 人中 2/7 次达标而无诊断 |
| ESL-Doc（60 人） | 266 | 0 | 0 | 有 | 0 findings（有诊断回流） |

**引用前**：固化成 `harness/external_value_audit.py`，各源扩到 ≥100 人重跑。
背景：Chen 等 [chen2019validity] 指出 Synthea 服务之后的结局建模有限，但没有检查化验值的内部一致性。

### E6 多模型面板的信度 ⬜（可选）

人工校样台（`harness/review/`）收人工判定，算面板与人工的一致性。动机：Kramer 等 [kramer2026leveraging]
重复评分时评审模型间差异可大过被评对象间差异。

### E9 生成器鲁棒性 ⬜（LLM 层启用后必做）

同一 sealed seed 的真值，用模型 A、B 各出一套改写/模板资源渲染两套语料；被评系统的排名 Spearman ρ ≥ 0.8、主指标 |Δ| ≤ 0.05
才算语料不偏向某模型（Synthetic Hospital 附录 C.2 的设计）。

### E10 / E11 表面多样性的配对代价 ⬜

对照对 `base` vs `view:paraphrase` / `view:template:<family>`，语义真值不变；报抽取与编码的配对差。
Liu 等 2026 的结果给出预期：整篇改写会丢细节，模板级 + 锁定词应把编码损失压到接近零——这正是要量的。

### E7 评测口径的效度 ✅（C3 的证据）

MedRepBench 标注上的标记约定上限（92.3 / 88.2 / 86.8%）；按序截断对 oracle 与规则基线的影响；
名字级联。全部可在 `tests/test_score.py` 与评测计划 §S4 复现。

## 7. 相关工作（每条都已核实，引用键见 refs.bib）

### 7.1 合成患者与合成 EHR

| 工作 | 做了什么 | 与我们的关系 |
|---|---|---|
| Synthea [walonoski2018synthea] | 状态机病程与诊疗路径 | 种子层来源 |
| Chen 等 2019 [chen2019validity] | 临床质量指标验证 Synthea | 结局建模薄弱；未查化验值一致性 |
| Hodges 等 2023 [hodges2023medication] | 用 MEPS 修 Synthea 用药分布 | 外挂修正某一层的先例 |
| Kramer 等 2026 [kramer2026leveraging] | LLM 从带出处的"疾病档案"生成 Synthea 模块，两级验证 + 渐进式修正 | 借"档案 → 确定性引擎、逐条溯源、程序验证反馈给模型"的框架（L0）；编码幻觉需外部校验；它做不了的人群级验证我们能做（生成秒级） |
| Lin 等 2025 [lin2025commercial] | 商用 LLM 直接生成结构化病历 | 维度一上去分布与相关性就失真——LLM 不写值的依据 |
| Patient-Zero [lai2025patientzero] | 从指南分层置换出虚拟患者，医生盲判分不出 | 同样"知识而非真实记录"的来源；它由 LLM 渲染全部内容，我们只让它改表面 |
| Poulett 等 2026 [poulett2026longitudinal] | 结构化患者 → 病程 → LLM 写病历，LLM 校验与增补 | 叙事层邻居；无文件/图像层 |
| Liu 等 2026 [liu2026rephrased] | 百万级 LLM 改写病历的系统评测 | 整篇改写丢细节（ICD 编码掉分）、按块改写少丢——支持模板级改写与锁定词契约（L2、E10） |
| PySynthea [cruz2026pysynthea] | Synthea 的 Python 重写 | 继承了观测层问题（E5） |
| ESL-Bench [li2026eslbench] | 事件驱动纵向合成，三级验证 | **姊妹工作**：我们补文档层 |
| HALO [theodorou2023halo] | 在真实 EHR 上训练生成模型 | 数据驱动路线；我们刻意不走 |
| Coogee [zhou2026coogee] | 轨迹生成 + LLM 一致性审计 | 他们以 LLM 打分为主，我们以确定性规则为主 |
| Gonzales 等 2023 [gonzales2023synthetic] | 综述 | 背景 |

### 7.2 合成数据的隐私

Stadler 等 [stadler2022groundhog]；Yao 等 [yao2025dcr]。**立场**：不拟合、取值路径上无真实记录；
距离检查只当接线错误的冒烟测试。机构分配的 α 与大客户份额来自聚合计数，不来自记录。

### 7.3 数据质量与检验医学

Kahn 等 [kahn2016harmonized]；EuBIVAS [aarsand2018eubivas]、BIVAC [aarsand2018bivac]（§9-3）；LOINC [mcdonald2003loinc]。

### 7.4 健康 agent 基准

MedAgentBench [jiang2025medagentbench]、PhysicianBench [liu2026physicianbench]、
HealthAgentBench [liu2026healthagentbench]、ESL-Bench。**共同点**：数据以结构化记录或 API 到达，没有一个把"文件"作为输入。

### 7.5 医疗报告抽取与文档理解

| 工作 | 数据 | 与我们的关系 |
|---|---|---|
| MedRepBench [shang2025medrepbench] | 1,925 张真实中文报告，五字段 | **基础**：采用其口径，产出其格式；它没有术语层真值、没有逐类归因、单张报告 |
| ChatSchema [wang2024chatschema] / Ma 等 [ma2023extracting] | 单机构、私有、百份量级 | — |
| MedStruct-S [li2026medstructs] | OCR 临床报告，开放键 | 任务互补 |
| Yu 等 [medrxiv2026ie] | 1,000 份合成基因检测报告，7 个模板 + 传真劣化 | 合成 + 劣化路线的先例；我们的版式空间来自 627 份真实文档 |
| DocILE [simsa2023docile] / DTBench [guo2026dtbench] | 通用文档抽取；难度轴可控 | — |
| Donut/SynthDoG [kim2022donut] / Augraphy [maini2023augraphy] | 渲染与劣化工程 | 图像层 |
| olmOCR 2 [poznanski2025olmocr2] | 合成 HTML + 从源码抽二元单元测试当训练奖励 | "源编译 + 单元测试"的同构；我们的可读性审计就是这类测试，L1 模板要附带它 |
| OmniDocLayout [kang2025omnidoclayout] / RIDGE [jiang2025ridge] | LLM 生成多样版式 / 关系丰富的文档 | L1 的可行性依据；它们生成内容+版式，我们只要容器 |
| Bevin 等 2025 [bevin2025invoices] | 真实票据保版式换内容（OCR + LLM + 修补） | 与我们相反的方向：真实件不能进我们的链路；对齐视图是它在合成侧的对应物 |

### 7.6 合成评测的效度

Gill 等 [gill2025lost]：LLM 生成的评测集更容易、不保持排序——E2 必须同时比排序与绝对难度。

### 7.7 行为测试、鲁棒性与评分口径

| 工作 | 做了什么 | 我们借了什么 / 不同在哪 |
|---|---|---|
| CheckList [ribeiro2020beyond] | MFT / INV / DIR 三类行为测试 | 对照对就是文档上的 INV；不同在于印刷真值可合法改变，判定放在语义层 |
| 对照集 [gardner2020evaluating] | 人工小改动构造局部决策边界 | 我们的"小改动"由生成器按陷阱类精确施加 |
| RoDLA [chen2024rodla] | 版面分析的扰动鲁棒性（12 类扰动 × 3 档） | 它扰动图像、考版面检测；我们扰动版式约定、考字段抽取 |
| From Pixels to Pairs [anvari2026pixels]（预印本） | FUNSD/CORD/SROIE 上 OCR 劣化 vs 金标准文本 | 最接近的"受控扰动 + 键值抽取"；它的扰动在 OCR 通道，我们的在版式层（通道类留给图像层） |
| SQuAD 2.0 [rajpurkar2018know] | 不可答问题 | 行级"必须弃权" |
| GriTS [smock2023grits] / TEDS [zhong2020image] | 表格识别的对齐式评分 | 对齐口径不按顺序截断 |

## 8. 审计与 Kahn 框架的映射

| Kahn 类别 | 我们的检查 | 位置 |
|---|---|---|
| Value Conformance | 单位、LOINC 可解析、参考区间出处可引用 | `audit/privacy.py`、`harness/resolve_coverage.py` |
| Relational Conformance | manifest 字段契约、派生链无环、每类陷阱都有登记 | `tests/` |
| **Computational Conformance** | 12 条恒等式 | `audit/clinical.py` |
| Completeness | 硬边界或写明豁免；必须弃权行不进召回分母 | `tests/test_spec.py`、`printed_rows[].readable` |
| Atemporal Plausibility | 生理硬边界、性别专属、值—诊断一致 | `audit/clinical.py` |
| Temporal Plausibility | RCV 超出率 | `audit/clinical.py` |
| Verification（内部） | 以上全部 + **印刷真值确实印在纸上**、标记与印出的范围一致 | `audit/readability.py` |
| Validation（外部） | 形态对账（E1）、与 MedRepBench 排序一致（E2） | `audit/fidelity.py` |

## 9. 效度威胁

1. **单一来源**：真实语料来自一个应用的用户上传。MedRepBench 同样是单一来源。
2. **陷阱分类覆盖率 76.9%**：其余 23% 是长尾个例。
3. **生物学变异来源偏旧**：Westgard 表；BIVAC 的 ALT 为 15.4% 而我们用 19.4%。需要敏感性分析或整体换成 EFLM 数据库值。
4. **种子层是美国人口**：PySynthea 的 locale pack 换不了患病率。
5. **生成器与被测系统的耦合**：陷阱是我们设计的，mirobody 团队看得到。对策：封存种子与 E2。
6. **多模型面板不是真值**：它只排序；能拦东西的是确定性审计。
7. **E5 样本太小**。
8. **词表覆盖率的循环性**（2026-09-23 实测）。合成语料上 LOINC 解析率 91%，是对着解析器建目录之后量的。
   用 MedRepBench 818 份检验单里 3,356 个真实指标名去量（mirobody db7c5054）：按不同名字 36.1%，
   按条目 54.0%，按生产口径（带值与单位的 `resolve_reading`）**50.0%**（3,054/6,109；另 247 条被单位轴拒答）。
   缺失清单里有大量常见项（RDW-CV/SD、大血小板比率、抗 TPO/Tg 抗体、尿比重/pH/尿糖、CEA）。
   **合成语料的指标名分布必须向真实世界靠**；MedRepBench 只作外部验证，它的名字不进 spec（否则就是对着考卷出题）。
9. **保真度的量法不对称**（E1）：真实侧的陷阱是分析模型"注意到的"，合成侧是逐条认定的；
   单次指纹占比的差距主要来自纵向设计；机构层参数成团出现，单一种子下个别类的文档率会偏离真实值
   （如受检者字段印进表格：本次 10.3%，真实 18.8%）。这些要和数字写在一起。
10. **规则基线不是证据**：E3 的规则基线只验证链路。关于陷阱效应的结论必须来自真实系统。
11. **字体**：`×10⁹/L`（上标 9）在内置字体里没有字形，已从版式空间里去掉；图像层换字体后再加回。
    渲染器对缺字形直接报错，不静默出片。
12. **模型偏向**（若启用 LLM 层）：改写与模板所用的模型可能让语料偏向它。对策：E9 换模型重渲染、排名 Spearman ρ ≥ 0.8；
    改写只在模板级、真值程序化；模型产出以不可信文本过隐私闸门。评审模型的方差先测后用（Kramer 等表 1）。
13. **内部效度先于外部效度**（Degli Esposti 2026 [degliesposti2026calibrating]）：生成器对"已知方向的刺激"是否作出有序、可复现的响应
    （他汀效应加倍 → LDL 分布相应下移；档案里写 ALT ×2 → 生成队列里 ALT 中位数 ×2）。这类刺激—响应检验应先于任何"像真的"主张。

## 10. 与 ESL-Bench 的关系

姊妹篇，不是竞争。ESL-Bench 回答"agent 能不能在纵向结构化数据上推理"，ESL-Doc 回答"数据以文件形式到达时还剩多少"。
建议 ESL-Bench 组在两件事上给意见：(a) 值—诊断回流（E5 那 2/7）是有意为之还是未建模；(b) 斜率限制是否直接沿用你们的 Δ_k。

## 11. 投稿前必须由人决定的事

1. **真实语料的授权基础与伦理审查口径**。GB/T 39725 的去标识化要求如何满足，需法务/合规确认。
2. **作者序列与篇目关系**（与 ESL-Bench 是姊妹篇还是扩展）。
3. **场地**。候选：NeurIPS Datasets & Benchmarks、CHIL、ML4H、JAMIA/JAMIA Open、ICDAR。本文不写截止日期。
4. **是否向 PySynthea 提 issue**（草稿在 `docs/issues/`）。
5. **MedRepBench 的非商业许可**是否覆盖本项目的用法（评测计划 §1 已列为开工前提）。

## 12. 进度

| 块 | 状态 | 位置 |
|---|---|---|
| 形态 spec（版式、陷阱、指标、队列、虚构池、印刷模板） | ✅ | `spec/` |
| 值层 + 临床审计 + 隐私闸门（含白名单与合成编号） | ✅ | `generator/`、`audit/` |
| 文档渲染器 T0/T1（PDF 文本层、XLSX、CSV、体检报告书、转置导出表） | ✅ | `generator/document.py`、`generator/render/` |
| 两层真值 + 逐行陷阱归因 + 最小对照对 | ✅ | `generator/hazards.py`、`generator/files.py` |
| 可读性审计、保真度对账 | ✅ | `audit/readability.py`、`audit/fidelity.py` |
| MedRepBench 兼容导出 + 评分器（V0 已与官方一致）+ 基线 | ✅ | `harness/` |
| 多模型面板、人工校样台 | ✅（面板未真调用） | `audit/llm_panel.py`、`harness/review/` |
| 图像层 T2–T4 | ⬜ P3 | — |
| ESL-Bench / PySynthea 适配器 | ⬜ | — |
| 真实系统跑 E2 / E3 | ⬜ | 需 mirobody 侧执行（评测计划） |
| E5 固化与扩样 | 🟡 | — |
