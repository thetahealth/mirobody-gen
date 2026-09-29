# 实测数字（自动生成，不要手改）

本文件由 `python3 scripts/build_numbers.py --write` 生成。
**所有文档引用数字时引用这里，不要在正文里复述数值**——
同一个量在四处出现，改一处忘三处是必然，不是粗心。

| 量 | 值 | 定义 | 来源 |
| --- | --- | --- | --- |
| 真实文档（带版式分析的） | 627 | 参考集中带版式分析的文档数 | `scripts/build_numbers.py` |
| 真实文档提取文本 | 771 | 参考集提取文本的文件数，回放检测的索引建在它上面 | `scripts/build_numbers.py` |
| 版式指纹种数 | 491 / 627 = **0.783** | 定义见 `audit/fingerprint.py`：首表列头 + 前两种参考值模板 + 前两种标记 + 页数 + 语言。2026-09-23 前用的是分析器的描述文本，得 0.818，其中三四个点是措辞噪声 | `audit/fingerprint.py` |
| 只出现一次的指纹 | 占指纹 95.3%，占文档 **74.6%** | **两个分母都要写**：用错分母会把验收阈值定歪 | `audit/fingerprint.py` |
| 最大的三个版式家族 | 60 份、37 份、8 份 | 同一指纹重复出现的文档数，对应真实世界里「同一家机构的模板」 | `audit/fingerprint.py` |
| 指纹粒度敏感性 | columns: 0.494 / 单次占文档 40.0% · columns+pages+languages: 0.590 / 单次占文档 49.6% · primary: 0.783 / 单次占文档 74.6% | 同一批文档、三种粒度。**这个数对定义很敏感**，引用时必须连同定义一起给 | `audit/fingerprint.py` |
| 完全没有表格的文档 | 142（22.6%） | `result_tables` 为空的文档数——纯叙述型报告。**不是**「列头为空的表格数」（那是另一个量，曾被我写成 46） | `scripts/build_numbers.py` |
| 不同列组（未过闸） | 444 | 所有表格的列头元组去重，含 OCR 坏字与指标名列 | `scripts/build_numbers.py` |
| 每文档表格行数 | p50=6 p75=15 p95=36 max=86 零行=22.5% | `analysis[].indicator_rows` 的条数：**分析器看到的表格行** | `scripts/build_numbers.py` |
| 每文档 readings | p50=9 p75=22 p95=103 max=251（n=511） | **与上一行不是同一个量**：这是产线提取管线的输出条数，一份文档的多次检查会各出一批 readings，所以比表格行数大得多 | `library/synth_spec.json（旧管线产物）` |
| 语言（多标签，已归一） | zh-Hans 568 / en 386 / zh-Hant 28 / ja 2 | 一份文档可带多个语言标签，所以合计大于文档数。归一前有 9 种写法，其中 4 种都是繁体 | `scripts/distill_layout.py` |
| 单页文档占比 | 509/611 = 83.3% | 已剔除分析器的 0 页伪值（xlsx 没有页的概念） | `scripts/distill_layout.py` |
| 陷阱描述条数 | 3722（命中 2862，76.9%） | `analysis[].extraction_hazards` 的条数。**不是** `library/grammar.json` 里那个 2725——那是旧管线按另一种口径聚合的，两者没有换算关系 | `scripts/distill_hazards.py` |
| 陷阱分类 | 62 类（可复现 61 类） | 确定性关键词组合聚类；不可复现的那类是本语料的脱敏痕迹 | `scripts/distill_hazards.py` |
| 每文档陷阱密度（生成器用这个） | p50=4 p75=6 p95=8 max=14 零=9.2% | **剔除 `artifact.redaction_placeholder` 之后**的条数。含脱敏痕迹的原始密度是 p50=6/p95=10，用它会让注入密度系统性偏高 | `scripts/distill_hazards.py` |
| 指标目录 | 98 项 · 恒等式派生 16 项 · 带 CVI 61 项 | 手写，参考区间来自公开标准与指南 | `scripts/build_indicators.py` |
| LOINC 可解析率 | 89/98 = 91% | 构建时向 `mirobody.engine.resolve` 查询。解析不出的 9 项是**刻意保留**的弃权素材。**注意：这是我们自己目录的覆盖率，有循环性**——目录是对着这个解析器建的，不代表 mirobody 在真实世界名字上的覆盖率。无偏估计见 docs/PAPER.md §9 第 8 条（MedRepBench 真实名字，约 50%） | `scripts/build_indicators.py` |
| 参考区间出处构成 | 全国临床检验操作规程 21 / WS/T 404 19 / WS/T 405—2012 18 / 厂商试剂说明书区间 18 / 中国成人血脂异常防治指南 7 / 临床心电图学通用正常值 5 / 由恒等式定义 4 / 中国成人超重和肥胖症预防控制指南 2 / 中国高血压防治指南 2 / 中国2型糖尿病防治指南 2 | 每一项都必须是可引用的出处，`audit/privacy.py` 会检查 | `scripts/build_indicators.py` |
| 单位印在哪里 | separate_column 243 / none 135 / in_reference 86 / in_value_cell 64 / in_header 7 | 已从分析器的 28 种自由文本收敛成枚举 | `scripts/distill_layout.py` |

## 脚注

- `readings` 与 `rows` 是两个量。验收标准（PLAN §5）里的分布检验要写明用哪个；生成器目前按 `rows` 对齐，因为它对应「一份文件里印了多少行」。
- LOINC 可解析率是**对某一天的 mirobody 解析器**测出来的。mirobody 的词表变了，`expect_resolvable` 就会悄悄过时——重跑 `build_indicators.py --write` 是发版前的固定动作。
