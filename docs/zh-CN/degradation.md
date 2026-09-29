# 图像劣化层（T2–T6）：怎么造出像真的一样的扫描件、手机照片、复印件与截图

写于 2026-09-29。对应代码 `generator/render/degrade.py`（算子与场景）、`generator/files.py`（交付形态的抽样）、
`audit/readability.py --ocr`（图像层的复核）、`scripts/measure_images.py`（参考集图像的聚合形态）。

> 一句话：**场景是算子链，不是单个滤镜；每个场景对应一条真实的采集链路；所有视图共用同一份真值；
> 参数范围对着真实语料的聚合统计调，而不是拍脑袋。** 这四条全部来自 PureDocBench，第四条是我们加的。

---

## 1. 为什么照着 PureDocBench 做

PureDocBench（arXiv:2605.07492，WeVisDoc 论文 arXiv:2609.20423 用它做主评测）是一个**源可追溯**的文档解析基准：
1,475 页 HTML/CSS 源页（10 个领域 66 个子类，其中 `06_medical` 有 10 个子类：medical_report、prescription、
clinical_record、medical_bill、health_record、medical_certificate、imaging_report、surgical_record、
discharge_summary、drug_instruction），每一页渲染成三个**对齐视图**：

| 视图 | 怎么来的 | 我们的对应 |
|---|---|---|
| Clean | 浏览器渲染 HTML | T0 文本层 PDF（`generator/render/pdf.py`，HTML → PyMuPDF Story） |
| Digital Degraded | 十条数字劣化场景（OpenCV/NumPy 算子链） | T2 / T4 / 部分 T3、T6 |
| Real Degraded | 四条真实采集链路：手机拍纸、手机拍复印件、手机拍屏、截图+社交软件转发压缩 | T3 / T4 / T6 |

三个视图**共用一份标注**——"劣化只改像素，不改内容"。评分是 `Overall = [100(1−TextEdit) + FormulaCDM + TableTEDS] / 3`，
三视图的均值 `Avg₃`。它的榜单上 Clean → Real 平均掉 8–14 分（通用 VLM 掉得少、流水线式专用模型掉得多），
这个差就是"采集链路的代价"。

我们的语料本来就是 HTML 渲染的（`document.py` 把真值排成 HTML，`pdf.py` 渲染），与它的"语义 HTML 作为可执行中间表示、
一次编译出图像、一次遍历出标注"（WeVisDoc §3.2.3）是同一个结构。所以图像层只需要补两件事：算子链与采集链路。

**从它那里照抄的**（读的是 `scripts/degradation_v2/apply_degradation_v2.py` 与 `manifests/sample_manifest_66.csv`）：

* 十条数字场景的算子顺序：`aged_archive`（纸纹→泛黄→褪色→霉斑）、`book_binding`（页曲→装订阴影→透印）、
  `multi_gen_copy`（对比拉高→多代损失→滚筒条纹）、`fax_thermal`、`low_quality_print`（碳粉条带）、`ink_bleed`、
  `heavy_compression`（降采样→JPEG）、`uneven_lighting`（渐变光→暗角）、`geometric_distort`（透视→桶形→旋转）、
  `noise_blur_combo`（噪声→运动模糊→色温）；
* 强度统一由 severity 缩放：mild 0.3 / moderate 0.6 / severe 0.9；
* 真实采集链路的子条件：纸张 平整 / 折痕褶皱 / 弯折不压平 / 隔塑料套或玻璃 / 复印件；光照 室内正常 / 室内暗光 / 闪光灯 /
  屏幕背光；角度 俯拍 / 斜拍 30–45° / 手持；以及"截图 + 转发压缩"。

**没抄的**：OpenCV 依赖（本仓只用 PIL + numpy，不为劣化层引入一个重依赖）；桶形畸变（PIL 没有便捷的重映射，
影响也小）；它的 GT 只有 Markdown/HTML，没有"每一行印刷真值"这一层——我们保留自己的两层真值。

---

## 2. 场景表（24 个）

每个场景 = 分层 + 算子链 + 对应的真实链路。参数写成 `(mild 值 → severe 值)`，按 severity 线性插值；`S` 表示直接用 severity。

### T2 扫描类（白底、不歪、常灰度）

| 场景 | 算子链 | 对应真实链路 |
|---|---|---|
| `flatbed_scan` | paper_texture(0.2→0.5) → **bleed_through**(0.3→0.9) → rotation(±0.4°→±1.5°) → copy_contrast(0.1→0.3) → jpeg(95→80) | 平板扫描仪 |
| `app_enhanced` | perspective(0.05→0.15) → hand_shadow(0.4→0.9) → **app_enhance**(S) → rotation(±0.2°→±0.8°) → downscale(1700) → jpeg(94→82) | 扫描全能王 / 微信"扫一扫·文档"的增强模式 |
| `scan_grayscale` | grayscale → fold_lines(1 条) → rotation → copy_contrast → jpeg(85→65) | 对折过的单子灰度扫描 |
| `scan_lowres` | paper_texture → rotation(±0.5°→±2°) → resolution_downup(0.75→0.5) → jpeg(75→45) | 150 dpi 级别的旧扫描 |
| `colored_slip` | **paper_color**(粉/黄/蓝/米) → paper_texture → rotation → uneven_lighting → jpeg(94→80) | 有色复写纸、热敏纸上的化验单 |

### T3 手机拍纸（深底、透视、光照不均）

| 场景 | 算子链 | 对应真实链路 |
|---|---|---|
| `clean_photo` | perspective(0.1→0.4) → uneven_lighting(0.2→0.5) → sensor_noise(0.1→0.3) → jpeg(96→90) | 手机原图，未经转发 |
| `phone_flat_top` | perspective(0.3→0.8) → uneven_lighting(S) → vignette → color_temp(暖/冷) → defocus(0.1→0.5) → sensor_noise → downscale(2000) → jpeg(93→80) | 平整纸 · 室内正常光 · 俯拍 |
| `wechat_photo` | perspective → uneven_lighting → hand_shadow → sensor_noise → **wechat_scale(1280)** → jpeg(82→68) | 俯拍后经微信转发 |
| `phone_creased` | fold_lines(0.5→1.0) → **finger_occluder**(只压页边留白) → perspective → uneven_lighting → hand_shadow → sensor_noise → downscale → jpeg | 折痕/褶皱 · 手指压纸 |
| `phone_bent` | page_curl(0.5→1.0) → perspective → uneven_lighting → defocus → sensor_noise → … | 弯折不压平 |
| `phone_oblique` | perspective(斜拍, 0.5→1.0) → rotation(±1°→±4°) → uneven_lighting → vignette → **focus_gradient**(远端失焦) → sensor_noise → … | 斜拍 30–45° |
| `phone_lowlight` | perspective → low_light(S) → motion_blur(0.3→0.8) → vignette → color_temp(暖) → … → jpeg(80→60) | 室内暗光 · 手抖 |
| `phone_flash` | perspective → flash_hotspot(S) → sensor_noise → … | 闪光灯过曝 |
| `phone_through_cover` | perspective → cover_glare(S) → uneven_lighting → defocus → sensor_noise → … | 隔塑料套 / 玻璃 |
| `phone_landscape` | perspective → uneven_lighting → **rotate_quarter**(90°) → sensor_noise → … | 整页横放拍（真实上传里的"横向.jpg"） |

### T4 劣化件（复印、传真、旧档案、转发多次）

| 场景 | 算子链 | 对应真实链路 |
|---|---|---|
| `photocopy` | grayscale → copy_contrast(0.3→0.8) → generation_loss(0.2→0.6) → **halftone**(0.1→0.4) → dirty_drum(0.3→0.8) → perspective → uneven_lighting → … | 手机拍复印件 |
| `multi_gen_copy` | grayscale → copy_contrast → generation_loss(0.5→1.0) → dirty_drum → rotation(±0.5°→±2.5°) → jpeg(75→55) | 复印件的复印件 |
| `fax_thermal` | thermal(0.4→0.9，近二值+横向条纹+泛黄) → halftone(0.2→0.6) → rotation → resolution_downup(0.8→0.55) → jpeg | 传真 / 热敏打印 |
| `aged_archive` | paper_texture → yellowing → fade_ink → foxing_spots → stains → rotation → jpeg | 泛黄旧档案 |
| `low_quality_print` | paper_texture → toner_banding(S) → ink_bleed(0.2→0.6) → perspective → uneven_lighting → … | 缺粉打印机的输出再被拍 |
| `heavy_compression` | wechat_scale(1280) → jpeg(70→35) → resolution_downup(0.9→0.6) → jpeg(72→40) | 被转发了很多次 |

### T6 屏幕（截图、拍屏）

| 场景 | 算子链 | 对应真实链路 |
|---|---|---|
| `screenshot` | **phone_frame**（状态栏/标题栏/导航条色块，页面缩到 1080 宽居中） → wechat_scale(1280) → jpeg(78→50) | 医院 App / 小程序长截图，转发 |
| `desktop_screenshot` | **desktop_frame**（浏览器工具栏、宽屏灰底、页面带阴影居中） → jpeg(95→80) | 电脑上打开门户/阅读器截图 |
| `screen_photo` | screen_pixels(像素栅格+摩尔纹+背光不均，偏冷) → perspective(0.2→0.6) → vignette → sensor_noise → … | 手机拍显示器 |

加粗的算子是本仓在 PureDocBench 之外补的（§4）。

---

## 3. 与真实语料对账：参数从哪里来

`scripts/measure_images.py` 对参考集的 315 张图像只输出**分位数与计数**（`mirobody_gen/resources/numbers_images.json`，
与 `docs/numbers.json` 同一口径：聚合量可以出闸，实例不行）。它改写了我最初的直觉：

| 量 | 实测 | 对生成器的含义 |
|---|---|---|
| 长边 | p10=779 · p50=1262 · p90=3072；≤1000 占 23%，1001–1279 占 27%，>2500 占 26% | 三条来路各占约三分之一：小图/裁切（截图、转发）、微信尺度（1280 上下）、相机原图（3000+）。所以 `downscale`/`wechat_scale` 的目标宽度不是一个值，是三档 |
| 恰好 1280 | 0.3% | 微信"长边压到 1280"的规则在这批语料里几乎不出现——多数图不是从聊天窗口另存的，是原图或 App 导出。`wechat_scale` 只在 `wechat_photo` / `screenshot` / `heavy_compression` 三个场景里用 |
| JPEG 质量估计（201 张） | p10=75 · p50=93 · p90=96；<75 只占 4% | **真实图像大多不糊也不烂**。第一版所有场景的 JPEG 质量在 45–85，系统性偏低，现已上调：扫描/原图 90–96，转发 68–82，只有 `heavy_compression`/`scan_lowres` 低于 70 |
| 灰度 | 42% | 扫描件、复印件、灰度截图很多。`scan_grayscale`、`photocopy`、`multi_gen_copy`、`fax_thermal` 加起来要有相应的份额 |
| 四角亮度 | p10=61 · p50=242 · >235 占 51% | 一半图像是**白底**（扫描/App 增强/截图），深底手机照片约四分之一。所以 T2+T6 的份额要大于 T3，第一版把 T3 当主力是错的 |
| EXIF 相机字段 | 4.4% | 绝大多数图像的相机 EXIF 被剥掉了（微信、App 导出都会剥）。我们的 JPEG 只写 `ImageDescription`/`Software` 两个合成标记，不写相机字段——与真实一致，也不伪造设备信息 |
| 拉普拉斯方差（清晰度） | p10=246 · p50=1301 · p90=10249 | 最糊的 10% 也还在可读范围，超过 p10 的模糊参数要进 severe 档 |
| 倾斜 | p50=0° · p90=0°；>1° 只占 3.5% | **绝大多数图像是正的**（扫描仪、App 自动拉正、截图）。只有手机直拍场景带旋转，且 mild 档 ≤1.5° |
| 横版 | 45% | 一半图像是横的：A5 横版化验单的照片、电脑截图（`desktop_screenshot`）、横放拍摄（`phone_landscape`） |

据此定的交付形态权重（`files.TIER_WEIGHTS`）：T0 文本层 PDF 28% · T2 扫描类 22% · T3 手机照片 24% · T4 劣化件 6% ·
T6 屏幕 20%；xlsx/csv 由机构版式决定（约 26% 的文件）。每个人再叠一层对数正态扰动（`upload_habit`）：
有人总是拍照，有人只传 PDF，有人全是截图——这与真实上传"同一个人风格固定"一致。
严重档 severe 占 15%，进 `stress` 分层不进主榜单（PLAN §3.6.2 第 3 条）。

**没对上的、下一轮要量的**：每个场景的份额（真实语料里"复印件"占多少、"隔塑料套"占多少）没有实测，只有 PureDocBench
的样本清单给了一个量级（66 页里手机拍平整纸 21、折痕 9、拍屏 8、拍复印件 6、截图 5、弯折 5、隔套 5、暗光 3、斜拍 3、闪光灯 1）。
`SCENE_WEIGHTS` 目前按这个量级配。

---

## 4. 调研之后补的算子（PureDocBench 没有、真实上传里常见）

| 算子 | 来源 | 为什么要它 |
|---|---|---|
| `bleed_through` 双面透印 | Augraphy `BleedThrough`、Genalog `bleed_through` | 体检报告书是双面印的，扫描件上下一页镜像后淡淡透出来几乎必有。实现：取同一份文件的**下一页**左右镜像、取暗部作掩膜、轻微减暗 |
| `halftone` 网点 | Augraphy `Dithering`/`DotMatrix`、PureDocBench 的"halftone patterns" | 复印与传真的网点感。Bayer 4×4 有序抖动，按强度与原图混合，不做纯二值 |
| `focus_gradient` 景深 | Augraphy `DepthSimulatedBlur` | 斜拍时远端失焦，近端清晰——整页均匀模糊是不真实的 |
| `paper_color` 有色纸 | Augraphy `ColorPaper` | 化验单常印在粉/黄复写纸或热敏纸上 |
| `finger_occluder` 手指 | PLAN §3.4 T3 定义里就有"手指遮挡"；Augraphy `PageBorder` 一类 | **只画在页边留白里**：先从 PDF 取每页文字内容框（`content_margins`），手指只能压在框外——所以真值不受影响。这是 PureDocBench "内容看不见的视图不保留原真值"规则的构造性实现：与其事后判定，不如让遮挡无法碰到内容 |
| `app_enhance` App 增强 | 扫描全能王/微信文档模式的"增强/黑白"滤镜；调研见 §5 | 国内真实上传里极常见的一条来路：自动裁切拉正 + 背景拉白 + 文字加黑锐化，阴影处留下灰斑和光晕。实现：大核模糊估计背景 → 归一化 → unsharp 锐化 → 对比拉伸 → 阴影残留光晕 |
| `wechat_scale` 微信尺寸规则 | 微信开放社区/多篇实测：宽高都 ≤1280 不动；否则长边压到 1280（宽高比 ≤2），极端长图按短边 | 转发链路的第一步不是 JPEG，是这条尺寸规则 |
| `desktop_frame` / `phone_frame` 界面框 | 真实语料 45% 横版、51% 白底 | 截图不是"页面本身"，是页面**嵌在一个 App 或浏览器里**。只画色块与几何形（电池、信号、返回箭头、地址栏占位块），**不写任何文字**——避免造出机构名、时间这类可能被当真的信息 |
| `annotate` 笔圈 | Augraphy `Markup`/`Scribbles`；DocDjinn 的手写元素 | 真实报告上被圈出来的常是异常项。在 PDF 阶段用 `page.search_for(值)` 找到异常值的框，画一个略大的椭圆（红/蓝，随机线宽）**绕着字不压字**；圈了什么写进 manifest 的 `annotations` |
| `rotate_quarter` 横放 | 真实语料 | 整页转 90°；mirobody 的 e2e 用例里就有一张"横向.jpg" |

阴影的两处细节按文献改了：软边（大核高斯）而不是硬边；`hand_shadow` 用从某条边伸进来的多边形（手/手机的形状），
`flash_hotspot` 中心过曝加四周落光。SynDocDS / SSD-DIS 那类物理光照模型（Blender 路径追踪、真实阴影垫合成）没有做，
见 §6。

---

## 5. 调研：还有哪些方案，各自能给我们什么

| 方案 | 是什么 | 能拿来的 | 为什么没直接用 |
|---|---|---|---|
| **Augraphy**（arXiv:2208.14558；44 个像素级 + 9 个空间级增强） | 文档图像增强库，ink / paper / post 三阶段流水线 | 算子目录最全：BadPhotoCopy、Faxify、DirtyDrum、DirtyRollers、LowInkPeriodicLines、BleedThrough、ShadowCast、LensFlare、ReflectedLight、LCDScreenPattern、Moire、Folding、BookBinding、Markup、Scribbles、PageBorder、DepthSimulatedBlur、ColorPaper…… §4 补的算子多数对着它的名字实现 | 重依赖（OpenCV 等）；PLAN §2 早就定了"可选依赖，缺失时回退到自写 PIL"。这一版直接写了 PIL 版；日后可以做一个 `--augraphy` 后端 |
| **PureDocBench**（arXiv:2605.07492） | 源可追溯基准，三视图对齐 | 场景表、严重度、采集链路、评分公式、"同一份真值"原则 | 它的 GT 是页面级 Markdown，没有逐行印刷真值 |
| **WeVisDoc**（arXiv:2609.20423） | 两阶段数据中心的文档解析训练框架 | "结构保持的外观合成"：从语义 HTML 双编译出图像与标注；劣化按来源分家族（纸张/复印/传输、物理采集、屏幕重采）；**看不见内容的视图要么拒绝要么改成可见内容裁剪标注**；用留出探针 + 聚类残差找失败簇、定向补数据 | 后一半（残差诊断→定向构造）是训练侧的方法，我们是评测侧，但"按场景报告 OCR 复核率、哪个场景掉得多就补哪个"是同一个思路，`audit/readability.py --ocr` 就是那个探针 |
| **Doc3D**（DewarpNet, ICCV 2019）/ **Inv3D** / **SyntheticDoc**（arXiv:2609.15503，100 万张，物理模拟纸张几何 + 路径追踪渲染） | 文档去扭曲数据集：真实纸张的 3D 网格 + Blender 渲染 | 真实的褶皱/弯折几何（UV 图、反向映射）与光照。把它们的反向映射直接施加到我们的干净页上，能得到远比 `page_curl`（正弦位移）真实的弯折 | 需要下载数据集（Doc3D 100k 张、非商业许可）；Blender 路径追踪太重。列为可选升级：`--warp-maps <dir>` |
| **Genalog**（Microsoft） | HTML 模板 → WeasyPrint 渲染 → 劣化（blur、bleed_through、salt/pepper、形态学开闭膨胀腐蚀） → OCR 对齐标签 | 与我们同构（HTML 渲染 + 劣化 + 标签传播）；形态学腐蚀/膨胀模拟缺墨/糊墨 | 劣化种类少；WeasyPrint 与本机字体耦合，破坏"同 seed 逐字节一致" |
| **SynDocDS**（PMC11154307）/ **SSD-DIS**（Sci Data 2026） | 文档阴影去除的合成/半合成数据集：物理光照模型下的多样阴影、真实阴影垫合成 | 阴影该有软边、有色偏、有形状（遮挡物） | 需要 3D 渲染或真实阴影垫；我们用多边形+大核模糊近似 |
| **屏幕重采检测**文献（SRDID162、DLC-2021、MoireDet） | 拍屏图像的摩尔纹、边界色纹 | 摩尔纹 = 屏幕子像素栅格 × 相机拜耳采样；边界处色纹是判别特征 | `screen_pixels` 用两组正弦光栅近似摩尔纹，够 OCR 层用 |
| **DocDjinn**（arXiv:2602.21824） | VLM 生成版式 + 扩散模型手写 + 语义-视觉解耦，140k 样本 | 手写批注（医生在报告上写的字）是我们没有的一类真实现象 | 需要扩散模型；本仓不引入模型依赖。手写留作外部资产（有手写字体/手写图时可作 `Markup` 叠加） |
| **扫描 App**（扫描全能王 / 微信扫一扫文档 / iOS 备忘录扫描） | 用户侧的预处理：自动裁边、透视矫正、"增强/黑白/灰度"滤镜、去阴影 | 这是**国内真实上传里最常见的白底图像来路之一**，但没有任何公开基准单独建模它 | 已实现 `app_enhance`（§4）；其"黑白"档接近二值，属 severe |
| **微信图片压缩规则** | 尺寸：≤1280 不动，否则长边压到 1280（宽高比 ≤2）；质量：重新编码 | 转发链路 | 已实现 `wechat_scale`；质量档取 68–82（多篇实测的量级） |

---

## 6. 明确没做的、以及做的话怎么做

1. **多页文件一页一张照片**：手机拍报告书是十几张照片。这会把"一份文件"拆成多个图像文件，真值随之要按页拆。
   这一版多页文件只走文本层 PDF 或整体扫描 PDF（`choose_delivery`）。做的话：`record()` 按页切 `printed_rows`
   （PDF 文字块的页号已知），每页一条记录、共用 `doc_id`。
2. **物理几何**：`page_curl` 是一维正弦；真实弯折是二维网格。用 Doc3D / SyntheticDoc 的反向映射做 `warp_map` 算子最省事。
3. **手写**：批注、签名、手写的家庭血压记录（真实里很多血压日记是手写的）。需要手写字体或手写图资产。
4. **印章的真实感**：现在是圆圈 + 一行小字；真实印章有星、有弧形文字、有印泥的不均匀。可做成 PNG 资产叠加。
5. **图像层的真值判定**：现在所有场景共用真值（PureDocBench 做法），严重档进 stress。`readability --ocr` 用 tesseract
   报每个场景的值识别率，只报告不判定——因为 OCR 认不出不等于人读不出。要真正验证"人能读"，需要一轮人工抽检（`harness/review/`）。
6. **场景份额的实测**：真实语料里各采集链路各占多少没有量过（只有白底/深底、灰度、尺寸、质量这些代理量）。
   要量的话，得给 315 张图人工标一遍"来路"。

---

## 7. 每张图在 manifest 里留下什么

`files.jsonl` 每条记录（图像层的字段）：

```
tier         T2 / T3 / T4 / T6（T0 文本层 PDF，T1 xlsx/csv）
scene        场景名（§2）
severity     mild / moderate / severe        —— severe ⇒ split = "stress"
ops          [{op, 参数...}, ...]            —— 每个算子实际抽到的参数，可复现
annotations  ["4.23", "129"]                 —— 被笔圈出的值
dpi          栅格化分辨率（T2 200 / T3 260 / T4 200 / T6 150）
image_size   [宽, 高]
format       jpg / png / pdf（扫描件 PDF 没有文本层）
source_format 机构版式原本的格式（pdf）
```

印刷真值、语义真值、块真值与 T0 完全相同——**这就是对齐视图**。`--pairs` 里每个 base 另出六个 `view:<场景>` 变体
（flatbed_scan、app_enhanced、phone_flat_top、phone_creased、photocopy、screenshot），与其它单陷阱变体一样按语义层评分：
两份文件的分差就是这条采集链路的代价。

确定性：每张图的随机数来自 `RandomState(sha1(doc_id + 场景))`，同 seed 逐字节一致（`tests/test_render.py` 的字节一致测试覆盖图像）。
合成标记：JPEG 写 EXIF `ImageDescription` 与 `Software`，PNG 写文本块，扫描件 PDF 写 `/Subject SYNTHETIC`；
页面上的横幅（`SYNTHETIC SAMPLE …`）在图像里照样可见。
