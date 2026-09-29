"""手写的"档案层"设计 → `resources/narratives.json`、`resources/complaints.json`、`resources/genomics.json`。

    python3 scripts/build_profile.py --write

检验表格之外的部分——真实的体检报告书（参考集里最大的一类文档）
是**科室键值对 + 辅助检查叙述 + 检验表格 + 总检结论与建议**四部分，而且科室部分占了
63/76 份（旧 HANDOFF 实测）。这里手写的是那三部分的**词汇与规则**：

* `narratives.json`：套餐档次（基础/标准/深度）各含哪些科室与辅助检查；每个科室有哪些条目、
  正常时怎么印；具名的异常所见（脂肪肝、甲状腺结节、龋齿……）印什么、按什么率出现、
  对应哪个 ICPC-3 诊断码（mirobody 的 D 轴，`res/icpc3/conditions_zh.tsv` 里有的才给码，
  没有的填 null——那是考弃权的素材）、总检里怎么写结论与建议。
* `complaints.json`：主诉与日记的症状词表。**每个中文表面都逐条对照过 mirobody 的
  `symptoms_zh.tsv`**（2026-09-29 版），给了码的就是那边精确匹配得到的 S 轴码；
  `frontier` 里的表面是刻意不在词表里的（考"该弃权时弃权"）。
* `genomics.json`：药物基因组位点。41 个带基因标注的位点来自 mirobody 的
  `genotype_sites.sqlite3`（dbSNP b155 common ∩ CPIC），坐标与等位基因照抄；
  等位基因频率是**手写的近似值**（按公开人群数据的量级，东亚/欧洲两列），
  只为让生成的基因型看起来像人，mirobody 不读它们。

全部手写，不含任何来自真实语料的内容。所有印在纸上的句子都是教科书式的通用医学表述。
"""

from __future__ import annotations

import argparse
import json
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
RESOURCES = REPO / "mirobody_gen" / "resources"

# ═══════════════════════════════════════════════════════════════════
# 1. 体检报告书
# ═══════════════════════════════════════════════════════════════════

#: 套餐档次。检验部分在 cohort.json 的 `checkup_packages`（同名）；这里是科室与辅助检查。
#: 档次不是随便分的：基础套餐对应单位福利体检的常见配置，深度套餐对应自费高端体检。
PACKAGES = {
    "entry": {
        "zh": "入职体检", "en": "Pre-employment examination",
        "sections": ["internal", "surgical", "eye", "ent"],
        "aux": ["ecg", "chest_xray"],
    },
    "senior": {
        "zh": "老年人健康体检", "en": "Older-adult health examination",
        "sections": ["internal", "surgical", "eye", "ent", "dental", "tcm"],
        "aux": ["ecg", "abd_us"],
    },
    "basic": {
        "zh": "基础套餐", "en": "Basic package",
        "sections": ["internal", "surgical", "eye", "ent", "dental"],
        "aux": ["ecg", "chest_xray", "abd_us"],
    },
    "standard": {
        "zh": "标准套餐", "en": "Standard package",
        "sections": ["internal", "surgical", "eye", "ent", "dental", "gyn"],
        "aux": ["ecg", "chest_xray", "abd_us", "thyroid_us", "breast_us", "prostate_us", "hp_breath", "body_composition"],
    },
    "premium": {
        "zh": "深度套餐", "en": "Comprehensive package",
        "sections": ["internal", "surgical", "eye", "ent", "dental", "gyn", "tcm"],
        "aux": ["ecg", "chest_ct", "abd_us", "thyroid_us", "carotid_us", "breast_us", "prostate_us",
                "gyn_us", "mammography", "bmd", "hp_breath", "body_composition", "spirometry", "arterial",
                "echo", "fundus_photo"],
    },
}

#: 机构类型 → 档次权重。体检中心卖套餐，医院体检科多半是基础/标准。
PACKAGE_WEIGHTS = {
    "checkup_center": {"basic": 0.35, "standard": 0.45, "premium": 0.20},
    "hospital": {"basic": 0.55, "standard": 0.40, "premium": 0.05},
}

#: 正常条目怎么印。一家机构选一种写法，全本一致（LIS 只有一份字典）。
NORMAL_DIALECTS = {
    "zh": ["未见异常", "正常", "未见明显异常", "(-)", "未见明显异常。"],
    "en": ["Normal", "Unremarkable", "NAD", "Within normal limits"],
}
NOT_DONE = {"zh": ["未查", "弃检", "拒查"], "en": ["Not examined", "Declined"]}

#: 科室：条目顺序即印刷顺序。`normal` 是条目自己的正常写法（没有就用机构方言）。
#: `sex` 限制性别；`optional` 的条目按概率印（不是每家都查）。
SECTIONS = {
    "internal": {
        "zh": "内科", "en": "Internal Medicine",
        "items": [
            {"id": "history", "zh": "既往史", "en": "Past history",
             "normal": {"zh": ["无", "否认", "无特殊"], "en": ["None reported", "Denied"]}},
            {"id": "heart_rate", "zh": "心率", "en": "Heart rate", "from_reading": "pulse"},
            {"id": "rhythm", "zh": "心律", "en": "Rhythm", "normal": {"zh": ["齐", "整齐"], "en": ["Regular"]}},
            {"id": "heart_sound", "zh": "心音", "en": "Heart sounds",
             "normal": {"zh": ["正常", "未闻及杂音", "心音有力，未闻及病理性杂音"], "en": ["Normal, no murmur"]}},
            {"id": "lung", "zh": "肺部", "en": "Lungs",
             "normal": {"zh": ["呼吸音清", "双肺呼吸音清，未闻及干湿性啰音", "未见异常"], "en": ["Clear, no added sounds"]}},
            {"id": "liver_palp", "zh": "肝脏", "en": "Liver", "normal": {"zh": ["未触及", "肋下未触及"], "en": ["Not palpable"]}},
            {"id": "spleen", "zh": "脾脏", "en": "Spleen", "normal": {"zh": ["未触及", "肋下未触及"], "en": ["Not palpable"]}},
            {"id": "abdomen", "zh": "腹部", "en": "Abdomen",
             "normal": {"zh": ["软，无压痛", "平软，无压痛及反跳痛", "未见异常"], "en": ["Soft, non-tender"]}},
            {"id": "neuro", "zh": "神经系统", "en": "Nervous system",
             "normal": {"zh": ["生理反射存在，病理反射未引出", "未见异常"], "en": ["Reflexes normal"]}},
            {"id": "int_misc", "zh": "内科其他", "en": "Other", "optional": 0.6},
        ],
    },
    "surgical": {
        "zh": "外科", "en": "Surgery",
        "items": [
            {"id": "skin", "zh": "皮肤", "en": "Skin", "normal": {"zh": ["未见异常", "正常"], "en": ["Normal"]}},
            {"id": "lymph", "zh": "浅表淋巴结", "en": "Superficial lymph nodes",
             "normal": {"zh": ["未触及肿大", "未见异常"], "en": ["Not enlarged"]}},
            {"id": "thyroid_palp", "zh": "甲状腺", "en": "Thyroid",
             "normal": {"zh": ["未触及肿大", "未见异常", "无肿大"], "en": ["Not enlarged"]}},
            {"id": "breast", "zh": "乳腺", "en": "Breasts", "sex": "female",
             "normal": {"zh": ["未见异常", "未触及肿块"], "en": ["No mass palpable"]}},
            {"id": "spine", "zh": "脊柱", "en": "Spine",
             "normal": {"zh": ["生理弯曲存在", "未见异常", "无畸形，活动正常"], "en": ["Normal curvature"]}},
            {"id": "limbs", "zh": "四肢关节", "en": "Limbs and joints",
             "normal": {"zh": ["未见异常", "活动正常"], "en": ["Normal"]}},
            {"id": "anus", "zh": "肛门直肠", "en": "Anorectal", "optional": 0.7,
             "normal": {"zh": ["未见异常", "未查"], "en": ["Not examined"]}},
            {"id": "surg_misc", "zh": "外科其他", "en": "Other", "optional": 0.5},
        ],
    },
    "eye": {
        "zh": "眼科", "en": "Ophthalmology",
        "items": [
            {"id": "va", "zh": "视力（裸眼）", "en": "Visual acuity (unaided)", "kind": "acuity"},
            {"id": "va_corrected", "zh": "矫正视力", "en": "Corrected visual acuity", "kind": "acuity", "optional": 0.5},
            {"id": "color", "zh": "色觉", "en": "Colour vision", "normal": {"zh": ["正常", "未见异常"], "en": ["Normal"]}},
            {"id": "external_eye", "zh": "外眼", "en": "External eye", "normal": {"zh": ["未见异常", "正常"], "en": ["Normal"]}},
            {"id": "conjunctiva", "zh": "结膜", "en": "Conjunctiva", "optional": 0.6,
             "normal": {"zh": ["未见异常", "无充血"], "en": ["Normal"]}},
            {"id": "fundus", "zh": "眼底", "en": "Fundus", "optional": 0.7,
             "normal": {"zh": ["未见异常", "视盘边界清，动静脉比例正常"], "en": ["Normal"]}},
            {"id": "iop", "zh": "眼压", "en": "Intraocular pressure", "kind": "iop", "optional": 0.35},
        ],
    },
    "ent": {
        "zh": "耳鼻喉科", "en": "ENT",
        "items": [
            {"id": "ear_canal", "zh": "外耳道", "en": "External ear canal",
             "normal": {"zh": ["未见异常", "通畅"], "en": ["Clear"]}},
            {"id": "eardrum", "zh": "鼓膜", "en": "Tympanic membrane", "normal": {"zh": ["完整", "未见异常"], "en": ["Intact"]}},
            {"id": "hearing", "zh": "听力", "en": "Hearing", "normal": {"zh": ["正常", "粗测正常"], "en": ["Normal"]}},
            {"id": "nose", "zh": "鼻腔", "en": "Nasal cavity", "normal": {"zh": ["未见异常", "通畅"], "en": ["Clear"]}},
            {"id": "pharynx", "zh": "咽部", "en": "Pharynx", "normal": {"zh": ["未见异常", "无充血"], "en": ["Normal"]}},
            {"id": "tonsil", "zh": "扁桃体", "en": "Tonsils", "normal": {"zh": ["无肿大", "未见异常"], "en": ["Not enlarged"]}},
        ],
    },
    "dental": {
        "zh": "口腔科", "en": "Dental",
        "items": [
            {"id": "lips", "zh": "唇颊腭", "en": "Lips, cheeks, palate", "normal": {"zh": ["未见异常"], "en": ["Normal"]}},
            {"id": "teeth", "zh": "牙齿", "en": "Teeth", "normal": {"zh": ["未见异常", "无缺失"], "en": ["Normal"]}},
            {"id": "gums", "zh": "牙龈", "en": "Gums", "normal": {"zh": ["未见异常", "无红肿"], "en": ["Normal"]}},
            {"id": "periodontal", "zh": "牙周", "en": "Periodontium", "optional": 0.6,
             "normal": {"zh": ["未见异常"], "en": ["Normal"]}},
        ],
    },
    "tcm": {
        "zh": "中医体质辨识", "en": "TCM constitution assessment",
        "items": [
            {"id": "constitution", "zh": "体质类型", "en": "Constitution type", "own_normal": True,
             "normal": {"zh": ["平和质"], "en": ["Balanced constitution"]}},
            {"id": "tongue", "zh": "舌象", "en": "Tongue", "optional": 0.6,
             "normal": {"zh": ["舌淡红，苔薄白"], "en": ["Pale-red tongue, thin white coating"]}},
            {"id": "pulse_tcm", "zh": "脉象", "en": "Pulse quality", "optional": 0.6,
             "normal": {"zh": ["脉和缓有力", "脉平"], "en": ["Moderate and forceful"]}},
        ],
    },
    "gyn": {
        "zh": "妇科", "en": "Gynaecology", "sex": "female",
        "items": [
            {"id": "vulva", "zh": "外阴", "en": "Vulva", "normal": {"zh": ["未见异常", "已婚式"], "en": ["Normal"]}},
            {"id": "vagina", "zh": "阴道", "en": "Vagina", "normal": {"zh": ["未见异常", "通畅"], "en": ["Normal"]}},
            {"id": "cervix", "zh": "宫颈", "en": "Cervix", "normal": {"zh": ["光滑", "未见异常"], "en": ["Smooth"]}},
            {"id": "uterus", "zh": "子宫", "en": "Uterus", "normal": {"zh": ["正常大小", "未见异常"], "en": ["Normal size"]}},
            {"id": "adnexa", "zh": "附件", "en": "Adnexa", "normal": {"zh": ["未触及异常", "未见异常"], "en": ["Normal"]}},
        ],
    },
}

#: 辅助检查。`organs` 的每一项有正常所见；`impression_normal` 是无异常时的提示。
#: `parameters` 引用指标键（心电图的数值会印成一张小表）。
AUX = {
    "ecg": {
        "zh": "心电图", "en": "Electrocardiogram", "kind": "ecg",
        "parameters": ["pulse", "pr_interval", "qrs_duration", "qtc", "qrs_axis"],
        "impression_normal": {"zh": ["窦性心律，正常心电图", "窦性心律 正常心电图", "窦性心律，大致正常心电图"],
                              "en": ["Sinus rhythm. Normal ECG.", "Sinus rhythm, within normal limits."]},
        "labels": {"zh": {"finding": "心电图描述", "impression": "心电图诊断"},
                   "en": {"finding": "Description", "impression": "Interpretation"}},
    },
    "chest_xray": {
        "zh": "胸部X线摄影（正位）", "en": "Chest radiograph (PA)", "kind": "imaging",
        "finding_normal": {"zh": ["两肺纹理清晰，未见明显实质性病变。心影大小形态未见异常。双侧膈面光整，肋膈角锐利。",
                                  "两肺野清晰，肺纹理走行自然。心影不大。膈面光滑，肋膈角锐利。"],
                           "en": ["Lungs are clear. Cardiomediastinal silhouette within normal limits. Costophrenic angles sharp."]},
        "impression_normal": {"zh": ["心肺膈未见明显异常", "胸部平片未见明显异常"], "en": ["No acute cardiopulmonary abnormality."]},
        "labels": {"zh": {"finding": "影像所见", "impression": "影像诊断"}, "en": {"finding": "Findings", "impression": "Impression"}},
    },
    "chest_ct": {
        "zh": "胸部CT平扫（低剂量）", "en": "Low-dose chest CT", "kind": "imaging",
        "finding_normal": {"zh": ["两肺野透亮度正常，肺纹理走行自然，未见明显实质性病变。气管及支气管通畅。纵隔未见肿大淋巴结。心影大小正常。胸腔未见积液。"],
                           "en": ["Lungs are clear without nodule or consolidation. Airways patent. No mediastinal lymphadenopathy. No pleural effusion."]},
        "impression_normal": {"zh": ["胸部CT平扫未见明显异常"], "en": ["No significant abnormality."]},
        "labels": {"zh": {"finding": "影像所见", "impression": "影像诊断"}, "en": {"finding": "Findings", "impression": "Impression"}},
    },
    "abd_us": {
        "zh": "腹部超声（肝、胆、胰、脾、双肾）", "en": "Abdominal ultrasound (liver, gallbladder, pancreas, spleen, kidneys)",
        "kind": "ultrasound",
        "organs": [
            {"id": "liver", "zh": "肝脏", "en": "Liver",
             "normal": {"zh": ["肝脏形态大小正常，包膜光滑，实质回声均匀，肝内管道显示清晰，门静脉内径正常。"],
                        "en": ["Liver normal in size and contour, homogeneous echotexture, intrahepatic ducts not dilated."]}},
            {"id": "gallbladder", "zh": "胆囊", "en": "Gallbladder",
             "normal": {"zh": ["胆囊大小形态正常，壁不厚，腔内未见明显异常回声。"],
                        "en": ["Gallbladder normal in size, wall not thickened, no stones."]}},
            {"id": "pancreas", "zh": "胰腺", "en": "Pancreas",
             "normal": {"zh": ["胰腺形态大小正常，回声均匀，胰管无扩张。"], "en": ["Pancreas normal, duct not dilated."]}},
            {"id": "spleen", "zh": "脾脏", "en": "Spleen",
             "normal": {"zh": ["脾脏形态大小正常，回声均匀。"], "en": ["Spleen normal in size and echotexture."]}},
            {"id": "kidneys", "zh": "双肾", "en": "Kidneys",
             "normal": {"zh": ["双肾形态大小正常，皮髓质分界清，集合系统无分离，未见明显异常回声。"],
                        "en": ["Both kidneys normal in size, corticomedullary differentiation preserved, no hydronephrosis."]}},
        ],
        "impression_normal": {"zh": ["肝、胆、胰、脾、双肾未见明显异常", "肝胆胰脾双肾声像图未见明显异常"],
                              "en": ["No significant abnormality of the liver, gallbladder, pancreas, spleen or kidneys."]},
        "labels": {"zh": {"finding": "超声所见", "impression": "超声提示"}, "en": {"finding": "Findings", "impression": "Impression"}},
    },
    "thyroid_us": {
        "zh": "甲状腺超声", "en": "Thyroid ultrasound", "kind": "ultrasound",
        "organs": [{"id": "thyroid", "zh": "甲状腺", "en": "Thyroid",
                    "normal": {"zh": ["甲状腺双叶大小形态正常，包膜完整，实质回声均匀，未见明显占位性病变。CDFI：血流信号未见异常。"],
                               "en": ["Both lobes normal in size, homogeneous echotexture, no focal lesion. Normal vascularity."]}}],
        "impression_normal": {"zh": ["甲状腺未见明显异常"], "en": ["Normal thyroid ultrasound."]},
        "labels": {"zh": {"finding": "超声所见", "impression": "超声提示"}, "en": {"finding": "Findings", "impression": "Impression"}},
    },
    "carotid_us": {
        "zh": "颈动脉超声", "en": "Carotid ultrasound", "kind": "ultrasound",
        "organs": [{"id": "carotid", "zh": "双侧颈动脉", "en": "Carotid arteries",
                    "normal": {"zh": ["双侧颈总动脉、颈内动脉内径正常，内中膜光滑，未见明显斑块，血流通畅。"],
                               "en": ["Common and internal carotid arteries of normal calibre, intima-media smooth, no plaque."]}}],
        "impression_normal": {"zh": ["双侧颈动脉未见明显异常"], "en": ["Normal carotid ultrasound."]},
        "labels": {"zh": {"finding": "超声所见", "impression": "超声提示"}, "en": {"finding": "Findings", "impression": "Impression"}},
    },
    "breast_us": {
        "zh": "乳腺超声", "en": "Breast ultrasound", "kind": "ultrasound", "sex": "female",
        "organs": [{"id": "breast", "zh": "双侧乳腺", "en": "Breasts",
                    "normal": {"zh": ["双侧乳腺腺体层次清晰，回声均匀，未见明显占位性病变。双侧腋窝未见肿大淋巴结。"],
                               "en": ["Fibroglandular tissue normal, no focal lesion. No axillary lymphadenopathy."]}}],
        "impression_normal": {"zh": ["双侧乳腺未见明显异常"], "en": ["Normal breast ultrasound."]},
        "labels": {"zh": {"finding": "超声所见", "impression": "超声提示"}, "en": {"finding": "Findings", "impression": "Impression"}},
    },
    "prostate_us": {
        "zh": "前列腺超声", "en": "Prostate ultrasound", "kind": "ultrasound", "sex": "male", "min_age": 40,
        "organs": [{"id": "prostate", "zh": "前列腺", "en": "Prostate",
                    "normal": {"zh": ["前列腺大小形态正常，包膜完整，内部回声均匀。"], "en": ["Prostate normal in size and echotexture."]}}],
        "impression_normal": {"zh": ["前列腺未见明显异常"], "en": ["Normal prostate ultrasound."]},
        "labels": {"zh": {"finding": "超声所见", "impression": "超声提示"}, "en": {"finding": "Findings", "impression": "Impression"}},
    },
    "gyn_us": {
        "zh": "妇科超声（经腹）", "en": "Pelvic ultrasound", "kind": "ultrasound", "sex": "female",
        "organs": [{"id": "uterus_us", "zh": "子宫及双附件", "en": "Uterus and adnexa",
                    "normal": {"zh": ["子宫前位，大小形态正常，肌层回声均匀，内膜居中。双侧附件区未见明显异常回声。"],
                               "en": ["Anteverted uterus of normal size, homogeneous myometrium. Adnexa unremarkable."]}}],
        "impression_normal": {"zh": ["子宫及双附件未见明显异常"], "en": ["Normal pelvic ultrasound."]},
        "labels": {"zh": {"finding": "超声所见", "impression": "超声提示"}, "en": {"finding": "Findings", "impression": "Impression"}},
    },
    "bmd": {
        "zh": "骨密度（双能X线）", "en": "Bone densitometry (DXA)", "kind": "bmd", "min_age": 40,
        "labels": {"zh": {"finding": "检查结果", "impression": "结论"}, "en": {"finding": "Result", "impression": "Conclusion"}},
        "impression_normal": {"zh": ["骨量正常"], "en": ["Normal bone density."]},
    },
    # 参数型（kind=params）：一张小表 + 结论，读数计入印刷真值；报告书里不再进检验表
    "spirometry": {
        "zh": "肺功能检查", "en": "Spirometry", "kind": "params",
        "parameters": ["fvc", "fvc_pct", "fev1", "fev1_pct", "fev1_fvc"],
        "impression_normal": {"zh": ["肺通气功能正常", "通气功能未见异常"], "en": ["Normal spirometry."]},
        "labels": {"zh": {"finding": "检查结果", "impression": "结论"}, "en": {"finding": "Results", "impression": "Interpretation"}},
    },
    "arterial": {
        "zh": "动脉硬化检测", "en": "Arterial stiffness (baPWV / ABI)", "kind": "params",
        "parameters": ["bapwv_l", "bapwv_r", "abi_l", "abi_r"],
        "impression_normal": {"zh": ["动脉弹性正常，未见明显动脉硬化", "血管弹性良好"], "en": ["Normal arterial stiffness indices."]},
        "labels": {"zh": {"finding": "检查结果", "impression": "结论"}, "en": {"finding": "Results", "impression": "Interpretation"}},
    },
    "body_composition": {
        "zh": "人体成分分析", "en": "Body composition analysis", "kind": "params",
        "parameters": ["body_fat", "visceral_fat", "muscle_mass", "bmr"],
        "impression_normal": {"zh": ["体成分构成正常"], "en": ["Body composition within normal limits."]},
        "labels": {"zh": {"finding": "检查结果", "impression": "结论"}, "en": {"finding": "Results", "impression": "Interpretation"}},
    },
    "echo": {
        "zh": "超声心动图（心脏彩超）", "en": "Echocardiography", "kind": "params",
        "parameters": ["lvedd", "ivs", "la", "lvef", "ea"],
        "finding_normal": {"zh": ["各房室内径正常，室壁厚度及运动正常，各瓣膜形态、启闭正常，未见明显异常血流信号。心包腔未见积液。"],
                           "en": ["Normal chamber dimensions and wall thickness; normal valve morphology and function; no significant regurgitation; no pericardial effusion."]},
        "impression_normal": {"zh": ["心脏结构及功能未见明显异常"], "en": ["Normal echocardiogram."]},
        "labels": {"zh": {"finding": "超声所见", "impression": "超声提示"}, "en": {"finding": "Findings", "impression": "Impression"}},
    },
    "fundus_photo": {
        "zh": "眼底照相", "en": "Fundus photography", "kind": "imaging",
        "finding_normal": {"zh": ["双眼视盘边界清，色泽正常，C/D约0.3，视网膜血管走行正常，动静脉比例约2:3，黄斑中心凹反光存在，未见出血及渗出。"],
                           "en": ["Both discs sharp with C/D ratio about 0.3; retinal vessels of normal calibre (A:V about 2:3); foveal reflex present; no haemorrhage or exudate."]},
        "impression_normal": {"zh": ["双眼眼底未见明显异常"], "en": ["Normal fundi."]},
        "labels": {"zh": {"finding": "检查所见", "impression": "检查结论"}, "en": {"finding": "Findings", "impression": "Impression"}},
    },
    "mammography": {
        "zh": "乳腺X线摄影（钼靶）", "en": "Mammography", "kind": "imaging", "sex": "female", "min_age": 40,
        "finding_normal": {"zh": ["双侧乳腺呈散在纤维腺体型（b类），腺体分布对称，未见明确肿块、钙化及结构扭曲，皮肤及乳头未见异常，双侧腋窝未见肿大淋巴结。"],
                           "en": ["Scattered fibroglandular density (type b), symmetric; no mass, suspicious calcification or architectural distortion; skin and nipples normal; no axillary lymphadenopathy."]},
        "impression_normal": {"zh": ["双乳未见明显异常（BI-RADS 1类）"], "en": ["No mammographic evidence of malignancy (BI-RADS 1)."]},
        "labels": {"zh": {"finding": "影像所见", "impression": "影像诊断"}, "en": {"finding": "Findings", "impression": "Impression"}},
    },
    "hp_breath": {
        "zh": "碳13尿素呼气试验", "en": "13C urea breath test", "kind": "breath",
        "labels": {"zh": {"finding": "检测结果", "impression": "结论"}, "en": {"finding": "Result", "impression": "Conclusion"}},
        "impression_normal": {"zh": ["幽门螺杆菌阴性"], "en": ["Helicobacter pylori negative."]},
    },
}

#: 具名异常所见。
#:
#: * `where`：印在哪个科室条目或哪个辅助检查器官上；
#: * `rate`：基础率（成人），`age` 每岁的增量，`archetype`/`sex` 的倍数；
#:   这些数只求量级对（真实体检里脂肪肝、甲状腺结节、龋齿是最常见的三类异常），不是流行病学估计；
#: * `sticky`：一旦出现，之后每年都在（囊肿、结节、结石不会自己消失）；
#: * `surface`：mirobody 会拿去编码的诊断表面；`icpc3` 是它 `conditions_zh.tsv` 里的答案，
#:   没有的填 null——那正是"该弃权"的素材，与检验层的 9 个刻意解析不出的指标同一用意；
#: * `severity`：可升级的所见（脂肪肝轻→中），按年份推进；
#: * `advice`：总检建议的模板 id。
FINDINGS = {
    "fatty_liver": {
        "where": ("abd_us", "liver"), "sticky": True, "severity": ["mild", "moderate"],
        "finding": {"mild": {"zh": "肝脏形态大小正常，实质回声细密增强，远场回声轻度衰减，肝内管道显示尚清晰。",
                             "en": "Liver normal in size; diffusely increased parenchymal echogenicity with mild posterior attenuation."},
                    "moderate": {"zh": "肝脏体积增大，实质回声弥漫性增强，远场回声明显衰减，肝内管道显示欠清晰。",
                                 "en": "Liver enlarged; diffusely increased echogenicity with marked posterior attenuation, vessels poorly seen."}},
        "impression": {"mild": {"zh": "轻度脂肪肝", "en": "Mild fatty liver"},
                       "moderate": {"zh": "中度脂肪肝", "en": "Moderate fatty liver"}},
        "surface": "脂肪肝", "icpc3": "DD81",
        "summary": {"zh": "脂肪肝", "en": "Fatty liver"}, "advice": "fatty_liver",
        "rate": {"base": 0.12, "age": 0.003, "sex": {"male": 1.4, "female": 0.7},
                 "archetype": {"fatty_liver": 12.0, "prediabetes_to_t2dm": 3.0, "dyslipidemia_statin": 2.5}},
    },
    "liver_cyst": {
        "where": ("abd_us", "liver"), "sticky": True, "size_mm": [6, 25],
        "finding": {"zh": "肝{lobe}叶见一无回声区，大小约{mm}mm，壁薄，边界清，后方回声增强。",
                    "en": "A {mm} mm thin-walled anechoic lesion in the {lobe_en} lobe with posterior enhancement."},
        "impression": {"zh": "肝囊肿", "en": "Hepatic cyst"},
        "surface": "肝囊肿", "icpc3": None,
        "summary": {"zh": "肝囊肿", "en": "Hepatic cyst"}, "advice": "benign_cyst",
        "rate": {"base": 0.04, "age": 0.002},
    },
    "gb_polyp": {
        "where": ("abd_us", "gallbladder"), "sticky": True, "size_mm": [3, 8],
        "finding": {"zh": "胆囊壁上见一强回声，大小约{mm}mm，不随体位改变移动，后方无声影。",
                    "en": "A {mm} mm non-mobile echogenic focus on the gallbladder wall without acoustic shadow."},
        "impression": {"zh": "胆囊息肉样病变", "en": "Gallbladder polyp"},
        "surface": "胆囊息肉", "icpc3": None,
        "summary": {"zh": "胆囊息肉样病变", "en": "Gallbladder polyp"}, "advice": "gb_polyp",
        "rate": {"base": 0.05, "age": 0.001},
    },
    "gb_stone": {
        "where": ("abd_us", "gallbladder"), "sticky": True, "size_mm": [5, 18],
        "finding": {"zh": "胆囊腔内见一强回声团，大小约{mm}mm，后方伴声影，随体位改变移动。",
                    "en": "A {mm} mm mobile echogenic focus with posterior acoustic shadowing within the gallbladder."},
        "impression": {"zh": "胆囊结石", "en": "Cholelithiasis"},
        "surface": "胆结石", "icpc3": "DD82",
        "summary": {"zh": "胆囊结石", "en": "Gallstone"}, "advice": "gb_stone",
        "rate": {"base": 0.03, "age": 0.002, "sex": {"female": 1.4, "male": 0.9},
                 "archetype": {"fatty_liver": 1.8, "dyslipidemia_statin": 1.5}},
    },
    "kidney_stone": {
        "where": ("abd_us", "kidneys"), "sticky": True, "size_mm": [3, 9],
        "finding": {"zh": "{side}肾集合系统内见一强回声，大小约{mm}mm，后方伴声影。",
                    "en": "A {mm} mm echogenic focus with acoustic shadowing in the {side_en} renal collecting system."},
        "impression": {"zh": "{side}肾结石", "en": "{side_en} renal calculus"},
        "surface": "肾结石", "icpc3": "UD67",
        "summary": {"zh": "肾结石", "en": "Kidney stone"}, "advice": "kidney_stone",
        "rate": {"base": 0.04, "age": 0.001, "sex": {"male": 1.4, "female": 0.8}, "archetype": {"ckd_progression": 1.6}},
    },
    "kidney_cyst": {
        "where": ("abd_us", "kidneys"), "sticky": True, "size_mm": [8, 30],
        "finding": {"zh": "{side}肾见一无回声区，大小约{mm}mm，壁薄，边界清，后方回声增强。",
                    "en": "A {mm} mm simple cyst in the {side_en} kidney."},
        "impression": {"zh": "{side}肾囊肿", "en": "{side_en} renal cyst"},
        "surface": "肾囊肿", "icpc3": None,
        "summary": {"zh": "肾囊肿", "en": "Renal cyst"}, "advice": "benign_cyst",
        "rate": {"base": 0.03, "age": 0.003, "archetype": {"ckd_progression": 2.0}},
    },
    "thyroid_nodule": {
        "where": ("thyroid_us", "thyroid"), "sticky": True, "size_mm": [3, 14],
        "finding": {"zh": "甲状腺{side}叶见一{echo}回声结节，大小约{mm}mm，边界清，形态规则，内部回声均匀，CDFI：周边见少许血流信号。",
                    "en": "A {mm} mm well-defined {echo_en} nodule in the {side_en} lobe with regular margins and peripheral vascularity."},
        "impression": {"zh": "甲状腺{side}叶结节（TI-RADS {tirads}类）", "en": "{side_en} thyroid nodule, TI-RADS {tirads}"},
        "surface": "甲状腺结节", "icpc3": None,
        "summary": {"zh": "甲状腺结节", "en": "Thyroid nodule"}, "advice": "thyroid_nodule",
        "rate": {"base": 0.15, "age": 0.004, "sex": {"female": 1.5, "male": 0.8}, "archetype": {"thyroid_disorder": 2.5}},
    },
    "thyroid_diffuse": {
        "where": ("thyroid_us", "thyroid"), "sticky": True,
        "finding": {"zh": "甲状腺双叶大小正常或略增大，实质回声弥漫性减低、不均匀，呈网格状改变，CDFI：血流信号增多。",
                    "en": "Both lobes show diffusely heterogeneous, hypoechoic parenchyma with increased vascularity."},
        "impression": {"zh": "甲状腺弥漫性病变（请结合甲状腺功能）", "en": "Diffuse thyroid disease; correlate with thyroid function."},
        "surface": "桥本甲状腺炎", "icpc3": None,
        "summary": {"zh": "甲状腺弥漫性病变", "en": "Diffuse thyroid change"}, "advice": "thyroid_diffuse",
        "rate": {"base": 0.01, "archetype": {"thyroid_disorder": 40.0}},
    },
    "carotid_imt": {
        "where": ("carotid_us", "carotid"), "sticky": True, "min_age": 40,
        "finding": {"zh": "双侧颈总动脉内中膜增厚，厚约1.1–1.3mm，回声增强，未见明显斑块。",
                    "en": "Bilateral common carotid intima-media thickening (1.1–1.3 mm) without discrete plaque."},
        "impression": {"zh": "双侧颈动脉内中膜增厚", "en": "Carotid intima-media thickening"},
        "surface": "颈动脉内中膜增厚", "icpc3": None,
        "summary": {"zh": "颈动脉内中膜增厚", "en": "Carotid IMT increased"}, "advice": "vascular",
        "rate": {"base": 0.05, "age": 0.006, "archetype": {"dyslipidemia_statin": 2.5, "hypertension": 2.5, "prediabetes_to_t2dm": 1.8}},
    },
    "carotid_plaque": {
        "where": ("carotid_us", "carotid"), "sticky": True, "min_age": 45, "size_mm": [8, 20],
        "finding": {"zh": "{side}侧颈总动脉分叉处见一强回声斑块，大小约{mm}×2.1mm，管腔未见明显狭窄。",
                    "en": "A {mm} × 2.1 mm echogenic plaque at the {side_en} carotid bifurcation without significant stenosis."},
        "impression": {"zh": "{side}侧颈动脉斑块形成", "en": "{side_en} carotid plaque"},
        "surface": "颈动脉斑块", "icpc3": None,
        "summary": {"zh": "颈动脉斑块", "en": "Carotid plaque"}, "advice": "vascular",
        "rate": {"base": 0.03, "age": 0.006, "archetype": {"dyslipidemia_statin": 3.0, "hypertension": 2.5}},
    },
    "breast_hyperplasia": {
        "where": ("breast_us", "breast"), "sticky": True, "sex": "female",
        "finding": {"zh": "双侧乳腺腺体层次欠清，回声增粗、分布不均，未见明显占位性病变。",
                    "en": "Fibroglandular tissue coarsened and heterogeneous bilaterally; no focal mass."},
        "impression": {"zh": "双侧乳腺增生", "en": "Bilateral fibrocystic change"},
        "surface": "乳腺增生", "icpc3": "GD67",
        "summary": {"zh": "乳腺增生", "en": "Fibrocystic breast change"}, "advice": "breast",
        "rate": {"base": 0.30, "max_age": 55},
    },
    "breast_nodule": {
        "where": ("breast_us", "breast"), "sticky": True, "sex": "female", "size_mm": [4, 12],
        "finding": {"zh": "{side}侧乳腺{clock}点方向见一低回声结节，大小约{mm}mm，边界清，形态规则。",
                    "en": "A {mm} mm well-circumscribed hypoechoic nodule in the {side_en} breast at {clock} o'clock."},
        "impression": {"zh": "{side}侧乳腺结节（BI-RADS 3类）", "en": "{side_en} breast nodule, BI-RADS 3"},
        "surface": "乳腺结节", "icpc3": None,
        "summary": {"zh": "乳腺结节", "en": "Breast nodule"}, "advice": "breast_nodule",
        "rate": {"base": 0.08},
    },
    "prostate_hyperplasia": {
        "where": ("prostate_us", "prostate"), "sticky": True, "sex": "male", "min_age": 50,
        "finding": {"zh": "前列腺体积增大，约4.6×3.8×3.2cm，内腺增大，回声不均，可见钙化斑。",
                    "en": "Prostate enlarged (4.6 × 3.8 × 3.2 cm) with heterogeneous transition zone and calcifications."},
        "impression": {"zh": "前列腺增生伴钙化", "en": "Benign prostatic hyperplasia with calcification"},
        "surface": "前列腺增生", "icpc3": "GD70",
        "summary": {"zh": "前列腺增生", "en": "Prostatic hyperplasia"}, "advice": "prostate",
        "rate": {"base": 0.05, "age": 0.012},
    },
    "uterine_fibroid": {
        "where": ("gyn_us", "uterus_us"), "sticky": True, "sex": "female", "max_age": 55, "size_mm": [12, 40],
        "finding": {"zh": "子宫{wall}壁肌层内见一低回声结节，大小约{mm}mm，边界清，周边可见环状血流。",
                    "en": "A {mm} mm well-defined hypoechoic myometrial nodule in the {wall_en} wall with peripheral flow."},
        "impression": {"zh": "子宫肌瘤", "en": "Uterine fibroid"},
        "surface": "子宫肌瘤", "icpc3": "GD29",
        "summary": {"zh": "子宫肌瘤", "en": "Uterine fibroid"}, "advice": "fibroid",
        "rate": {"base": 0.10, "age": 0.004},
    },
    "lung_nodule": {
        "where": ("chest_ct", None), "sticky": True, "size_mm": [3, 6],
        "finding": {"zh": "{side}肺{lobe_lung}叶见一{density}结节，直径约{mm}mm，边界清。余两肺未见明显实质性病变。纵隔未见肿大淋巴结。",
                    "en": "A {mm} mm {density_en} nodule in the {side_en} {lobe_lung_en} lobe. Lungs otherwise clear. No lymphadenopathy."},
        "impression": {"zh": "{side}肺{lobe_lung}叶微小结节，建议年度复查", "en": "{side_en} {lobe_lung_en} lobe pulmonary nodule; annual follow-up suggested"},
        "surface": "肺结节", "icpc3": None,
        "summary": {"zh": "肺微小结节", "en": "Small pulmonary nodule"}, "advice": "lung_nodule",
        "rate": {"base": 0.15, "age": 0.004},
    },
    "lung_markings": {
        "where": ("chest_xray", None), "sticky": False,
        "finding": {"zh": "两肺纹理增粗、增多，未见明显实质性病变。心影大小形态未见异常。双侧膈面光整，肋膈角锐利。",
                    "en": "Bronchovascular markings mildly prominent; no consolidation. Heart size normal."},
        "impression": {"zh": "两肺纹理增粗", "en": "Prominent lung markings"},
        "surface": "肺纹理增粗", "icpc3": None,
        "summary": {"zh": "两肺纹理增粗", "en": "Prominent lung markings"}, "advice": "lung_markings",
        "rate": {"base": 0.12, "age": 0.003},
    },
    "sinus_brady": {
        "where": ("ecg", None), "sticky": False, "condition": {"pulse": "<60"},
        "impression": {"zh": "窦性心动过缓", "en": "Sinus bradycardia"},
        "surface": "窦性心动过缓", "icpc3": None,
        "summary": {"zh": "窦性心动过缓", "en": "Sinus bradycardia"}, "advice": "brady",
        "rate": {"base": 1.0},
    },
    "sinus_arrhythmia": {
        "where": ("ecg", None), "sticky": False,
        "impression": {"zh": "窦性心律不齐", "en": "Sinus arrhythmia"},
        "surface": "窦性心律不齐", "icpc3": None,
        "summary": {"zh": "窦性心律不齐", "en": "Sinus arrhythmia"}, "advice": "ecg_minor",
        "rate": {"base": 0.06, "max_age": 40},
    },
    "t_wave": {
        "where": ("ecg", None), "sticky": False, "min_age": 40,
        "impression": {"zh": "窦性心律，部分导联T波改变（Ⅱ、Ⅲ、aVF）", "en": "Sinus rhythm with nonspecific T-wave changes (II, III, aVF)"},
        "surface": "T波改变", "icpc3": None,
        "summary": {"zh": "心电图T波改变", "en": "Nonspecific T-wave changes"}, "advice": "ecg_minor",
        "rate": {"base": 0.05, "age": 0.002, "archetype": {"hypertension": 2.0}},
    },
    "pac": {
        "where": ("ecg", None), "sticky": False, "min_age": 35,
        "impression": {"zh": "窦性心律，偶发房性早搏", "en": "Sinus rhythm with occasional premature atrial complexes"},
        "surface": "房性早搏", "icpc3": None,
        "summary": {"zh": "偶发房性早搏", "en": "Occasional PACs"}, "advice": "ecg_minor",
        "rate": {"base": 0.03, "age": 0.002},
    },
    "caries": {
        "where": ("dental", "teeth"), "sticky": True,
        "text": {"zh": "龋齿（{tooth}）", "en": "Dental caries ({tooth})"},
        "surface": "龋齿", "icpc3": None,
        "summary": {"zh": "龋齿", "en": "Dental caries"}, "advice": "dental",
        "rate": {"base": 0.25},
    },
    "calculus": {
        "where": ("dental", "gums"), "sticky": True,
        "text": {"zh": "牙结石，牙龈轻度红肿", "en": "Dental calculus with mild gingivitis"},
        "surface": "牙结石", "icpc3": None,
        "summary": {"zh": "牙结石", "en": "Dental calculus"}, "advice": "dental",
        "rate": {"base": 0.30, "age": 0.002},
    },
    "missing_teeth": {
        "where": ("dental", "teeth"), "sticky": True, "min_age": 45,
        "text": {"zh": "缺牙（{tooth}）", "en": "Missing tooth ({tooth})"},
        "surface": "缺牙", "icpc3": None,
        "summary": {"zh": "缺牙", "en": "Missing teeth"}, "advice": "dental",
        "rate": {"base": 0.05, "age": 0.008},
    },
    "myopia": {
        "where": ("eye", "va"), "sticky": True, "acuity": "low",
        "surface": "近视", "icpc3": "FD69",
        "summary": {"zh": "视力下降（屈光不正）", "en": "Reduced visual acuity (refractive error)"}, "advice": "eye",
        "rate": {"base": 0.45, "max_age": 45, "after_max_age": 0.2},
    },
    "presbyopia_note": {
        "where": ("eye", "va_corrected"), "sticky": True, "min_age": 45,
        "text": {"zh": "老视", "en": "Presbyopia"},
        "surface": "老花", "icpc3": None,
        "summary": {"zh": "老视", "en": "Presbyopia"}, "advice": "eye",
        "rate": {"base": 0.10, "age": 0.01},
    },
    "fundus_arteriosclerosis": {
        "where": ("eye", "fundus"), "sticky": True, "min_age": 45,
        "text": {"zh": "视网膜动脉硬化Ⅰ级", "en": "Grade I retinal arteriosclerosis"},
        "surface": "视网膜动脉硬化", "icpc3": None,
        "summary": {"zh": "眼底动脉硬化", "en": "Retinal arteriosclerosis"}, "advice": "vascular",
        "rate": {"base": 0.02, "age": 0.004, "archetype": {"hypertension": 4.0, "prediabetes_to_t2dm": 2.0}},
    },
    "conjunctivitis": {
        "where": ("eye", "conjunctiva"), "sticky": False,
        "text": {"zh": "结膜轻度充血", "en": "Mild conjunctival injection"},
        "surface": "结膜炎", "icpc3": "FD01",
        "summary": {"zh": "结膜炎", "en": "Conjunctivitis"}, "advice": "eye",
        "rate": {"base": 0.03},
    },
    "chronic_pharyngitis": {
        "where": ("ent", "pharynx"), "sticky": True,
        "text": {"zh": "咽部黏膜慢性充血，咽后壁淋巴滤泡增生", "en": "Chronic pharyngeal congestion with lymphoid follicles"},
        "surface": "慢性咽炎", "icpc3": None,
        "summary": {"zh": "慢性咽炎", "en": "Chronic pharyngitis"}, "advice": "ent",
        "rate": {"base": 0.12},
    },
    "septal_deviation": {
        "where": ("ent", "nose"), "sticky": True,
        "text": {"zh": "鼻中隔偏曲", "en": "Deviated nasal septum"},
        "surface": "鼻中隔偏曲", "icpc3": None,
        "summary": {"zh": "鼻中隔偏曲", "en": "Deviated septum"}, "advice": "ent",
        "rate": {"base": 0.06},
    },
    "cerumen": {
        "where": ("ent", "ear_canal"), "sticky": False,
        "text": {"zh": "{side}侧外耳道耵聍", "en": "{side_en} cerumen"},
        "surface": "耵聍", "icpc3": None,
        "summary": {"zh": "外耳道耵聍", "en": "Cerumen"}, "advice": "ent",
        "rate": {"base": 0.06},
    },
    "hearing_loss": {
        "where": ("ent", "hearing"), "sticky": True, "min_age": 55,
        "text": {"zh": "双耳高频听力下降", "en": "Bilateral high-frequency hearing loss"},
        "surface": "听力下降", "icpc3": None,
        "summary": {"zh": "听力下降", "en": "Hearing loss"}, "advice": "ent",
        "rate": {"base": 0.02, "age": 0.006},
    },
    "thyroid_enlarged": {
        "where": ("surgical", "thyroid_palp"), "sticky": True,
        "text": {"zh": "甲状腺Ⅰ度肿大", "en": "Grade I thyroid enlargement"},
        "surface": "甲状腺肿大", "icpc3": None,
        "summary": {"zh": "甲状腺肿大", "en": "Goitre"}, "advice": "thyroid_nodule",
        "rate": {"base": 0.01, "archetype": {"thyroid_disorder": 15.0}},
    },
    "varicose": {
        "where": ("surgical", "limbs"), "sticky": True, "min_age": 40,
        "text": {"zh": "{side}下肢静脉曲张", "en": "{side_en} lower-limb varicose veins"},
        "surface": "静脉曲张", "icpc3": "KD79",
        "summary": {"zh": "下肢静脉曲张", "en": "Varicose veins"}, "advice": "surgical_minor",
        "rate": {"base": 0.02, "age": 0.003},
    },
    "hemorrhoids": {
        "where": ("surgical", "anus"), "sticky": True,
        "text": {"zh": "混合痔", "en": "Mixed haemorrhoids"},
        "surface": "痔疮", "icpc3": "DD84",
        "summary": {"zh": "痔", "en": "Haemorrhoids"}, "advice": "surgical_minor",
        "rate": {"base": 0.10},
    },
    "scoliosis": {
        "where": ("surgical", "spine"), "sticky": True,
        "text": {"zh": "脊柱轻度侧弯", "en": "Mild scoliosis"},
        "surface": "脊柱侧弯", "icpc3": "LD70",
        "summary": {"zh": "脊柱侧弯", "en": "Scoliosis"}, "advice": "surgical_minor",
        "rate": {"base": 0.03},
    },
    "cervical_spondylosis": {
        "where": ("surgical", "spine"), "sticky": True, "min_age": 35,
        "text": {"zh": "颈椎生理曲度变直，颈部活动轻度受限", "en": "Straightened cervical lordosis, mildly restricted neck movement"},
        "surface": "颈椎病", "icpc3": "LD65",
        "summary": {"zh": "颈椎病", "en": "Cervical spondylosis"}, "advice": "surgical_minor",
        "rate": {"base": 0.04, "age": 0.003},
    },
    "cervical_erosion": {
        "where": ("gyn", "cervix"), "sticky": True, "sex": "female", "max_age": 50,
        "text": {"zh": "宫颈轻度糜烂样改变", "en": "Mild cervical ectropion"},
        "surface": "宫颈糜烂", "icpc3": None,
        "summary": {"zh": "宫颈糜烂样改变", "en": "Cervical ectropion"}, "advice": "gyn",
        "rate": {"base": 0.15},
    },
    # ── 2026-09-29 扩充：参数型辅助检查、眼底照相、钼靶、中医体质 ──
    "airflow_limitation": {
        "where": ("spirometry", None), "sticky": False, "condition": {"fev1_fvc": "<70"},
        "impression": {"zh": "轻度阻塞性通气功能障碍", "en": "Mild obstructive ventilatory defect"},
        "surface": "阻塞性通气功能障碍", "icpc3": None,
        "summary": {"zh": "肺功能：阻塞性通气功能障碍", "en": "Obstructive ventilatory defect"}, "advice": "lung_function",
        "rate": {"base": 1.0},
    },
    "restrictive_defect": {
        "where": ("spirometry", None), "sticky": False, "condition": {"fvc_pct": "<80"},
        "impression": {"zh": "轻度限制性通气功能障碍", "en": "Mild restrictive ventilatory defect"},
        "surface": "限制性通气功能障碍", "icpc3": None,
        "summary": {"zh": "肺功能：限制性通气功能障碍", "en": "Restrictive ventilatory defect"}, "advice": "lung_function",
        "rate": {"base": 1.0},
    },
    "arterial_stiff": {
        "where": ("arterial", None), "sticky": False, "condition": {"bapwv_l": ">1400"},
        "impression": {"zh": "臂踝脉搏波传导速度增快，提示动脉弹性减退", "en": "Increased baPWV, consistent with reduced arterial compliance"},
        "surface": "动脉硬化", "icpc3": None,
        "summary": {"zh": "动脉弹性减退（baPWV增快）", "en": "Reduced arterial compliance (raised baPWV)"}, "advice": "vascular",
        "rate": {"base": 1.0},
    },
    "abi_low": {
        "where": ("arterial", None), "sticky": False, "condition": {"abi_l": "<0.9"},
        "impression": {"zh": "左侧踝臂指数减低，提示下肢动脉供血不足可能", "en": "Reduced left ABI, suggesting lower-limb arterial disease"},
        "surface": "下肢动脉硬化闭塞症", "icpc3": None,
        "summary": {"zh": "踝臂指数减低", "en": "Reduced ankle-brachial index"}, "advice": "vascular",
        "rate": {"base": 1.0},
    },
    "visceral_fat_high": {
        "where": ("body_composition", None), "sticky": False, "condition": {"visceral_fat": ">9"},
        "impression": {"zh": "内脏脂肪超标", "en": "Excess visceral fat"},
        "surface": "腹型肥胖", "icpc3": None,
        "summary": {"zh": "内脏脂肪超标", "en": "Excess visceral fat"}, "advice": "body_fat",
        "rate": {"base": 1.0},
    },
    "lv_hypertrophy": {
        "where": ("echo", None), "sticky": False, "condition": {"ivs": ">11"},
        "impression": {"zh": "室间隔增厚，左室肥厚可能", "en": "Septal thickening; possible left ventricular hypertrophy"},
        "surface": "左心室肥厚", "icpc3": None,
        "summary": {"zh": "室间隔增厚", "en": "Septal thickening"}, "advice": "echo_minor",
        "rate": {"base": 1.0},
    },
    "diastolic_dysfunction": {
        "where": ("echo", None), "sticky": False, "condition": {"ea": "<1.0"},
        "impression": {"zh": "左室舒张功能减低（E/A<1）", "en": "Impaired left ventricular relaxation (E/A < 1)"},
        "surface": "左室舒张功能减退", "icpc3": None,
        "summary": {"zh": "左室舒张功能减低", "en": "Impaired LV relaxation"}, "advice": "echo_minor",
        "rate": {"base": 1.0},
    },
    "tr_trivial": {
        "where": ("echo", None), "sticky": False,
        "impression": {"zh": "三尖瓣少量反流", "en": "Trivial tricuspid regurgitation"},
        "surface": "三尖瓣反流", "icpc3": None,
        "summary": {"zh": "三尖瓣少量反流", "en": "Trivial tricuspid regurgitation"}, "advice": "echo_minor",
        "rate": {"base": 0.12, "age": 0.003},
    },
    "mr_trivial": {
        "where": ("echo", None), "sticky": False,
        "impression": {"zh": "二尖瓣少量反流", "en": "Trivial mitral regurgitation"},
        "surface": "二尖瓣反流", "icpc3": None,
        "summary": {"zh": "二尖瓣少量反流", "en": "Trivial mitral regurgitation"}, "advice": "echo_minor",
        "rate": {"base": 0.05, "age": 0.002},
    },
    "fundus_hypertensive": {
        "where": ("fundus_photo", None), "sticky": True,
        "finding": {"zh": "双眼视网膜动脉变细，反光增强，动静脉比例约1:2，可见动静脉交叉压迫征，未见出血及渗出。",
                    "en": "Retinal arteriolar narrowing with increased light reflex, A:V about 1:2, arteriovenous nicking; no haemorrhage or exudate."},
        "impression": {"zh": "双眼视网膜动脉硬化（Ⅰ–Ⅱ级）", "en": "Hypertensive retinopathy, grade I–II"},
        "surface": "视网膜动脉硬化", "icpc3": None,
        "summary": {"zh": "视网膜动脉硬化", "en": "Retinal arteriosclerosis"}, "advice": "vascular",
        "rate": {"base": 0.02, "age": 0.002, "archetype": {"hypertension": 5.0}},
    },
    "breast_calcification": {
        "where": ("mammography", None), "sticky": True,
        "finding": {"zh": "双侧乳腺呈散在纤维腺体型（b类），{side}乳外上象限见数枚散在粗大钙化，形态规则，未见成簇细小钙化，未见肿块及结构扭曲。",
                    "en": "Scattered fibroglandular density; a few coarse, regular calcifications in the upper outer {side_en} breast; no clustered microcalcification, mass or distortion."},
        "impression": {"zh": "{side}乳良性钙化（BI-RADS 2类）", "en": "Benign calcifications, {side_en} breast (BI-RADS 2)"},
        "surface": "乳腺钙化", "icpc3": None,
        "summary": {"zh": "乳腺良性钙化", "en": "Benign breast calcification"}, "advice": "breast",
        "rate": {"base": 0.12, "age": 0.003},
    },
    "qi_deficiency": {
        "where": ("tcm", "constitution"), "sticky": True,
        "text": {"zh": "气虚质（倾向）", "en": "Qi-deficiency tendency"},
        "surface": "气虚质", "icpc3": None,
        "summary": {"zh": "中医体质：气虚质", "en": "TCM constitution: qi deficiency"}, "advice": "tcm",
        "rate": {"base": 0.10, "sex": {"female": 1.3, "male": 0.8}},
    },
    "phlegm_damp": {
        "where": ("tcm", "constitution"), "sticky": True,
        "text": {"zh": "痰湿质（倾向）", "en": "Phlegm-dampness tendency"},
        "surface": "痰湿质", "icpc3": None,
        "summary": {"zh": "中医体质：痰湿质", "en": "TCM constitution: phlegm-dampness"}, "advice": "tcm",
        "rate": {"base": 0.08, "archetype": {"fatty_liver": 3.0, "prediabetes_to_t2dm": 2.5, "dyslipidemia_statin": 2.0}},
    },
    "yang_deficiency": {
        "where": ("tcm", "constitution"), "sticky": True,
        "text": {"zh": "阳虚质（倾向）", "en": "Yang-deficiency tendency"},
        "surface": "阳虚质", "icpc3": None,
        "summary": {"zh": "中医体质：阳虚质", "en": "TCM constitution: yang deficiency"}, "advice": "tcm",
        "rate": {"base": 0.08, "sex": {"female": 1.4, "male": 0.7}},
    },
    "damp_heat": {
        "where": ("tcm", "constitution"), "sticky": True,
        "text": {"zh": "湿热质（倾向）", "en": "Damp-heat tendency"},
        "surface": "湿热质", "icpc3": None,
        "summary": {"zh": "中医体质：湿热质", "en": "TCM constitution: damp-heat"}, "advice": "tcm",
        "rate": {"base": 0.07, "sex": {"male": 1.3, "female": 0.8}},
    },
    "hp_positive": {
        "where": ("hp_breath", None), "sticky": True, "rate": {"base": 0.40},
        "impression": {"zh": "幽门螺杆菌阳性", "en": "Helicobacter pylori positive"},
        "surface": "幽门螺杆菌感染", "icpc3": None,
        "summary": {"zh": "幽门螺杆菌阳性", "en": "H. pylori positive"}, "advice": "hp",
    },
    "osteopenia": {
        "where": ("bmd", None), "sticky": True, "min_age": 45, "t_score": [-2.4, -1.1],
        "impression": {"zh": "骨量减少", "en": "Osteopenia"},
        "surface": "骨量减少", "icpc3": None,
        "summary": {"zh": "骨量减少", "en": "Osteopenia"}, "advice": "bone",
        "rate": {"base": 0.05, "age": 0.010, "sex": {"female": 1.6, "male": 0.7}},
    },
    "osteoporosis": {
        "where": ("bmd", None), "sticky": True, "min_age": 55, "t_score": [-3.4, -2.5],
        "impression": {"zh": "骨质疏松", "en": "Osteoporosis"},
        "surface": "骨质疏松", "icpc3": "LD81",
        "summary": {"zh": "骨质疏松", "en": "Osteoporosis"}, "advice": "bone",
        "rate": {"base": 0.02, "age": 0.008, "sex": {"female": 2.0, "male": 0.6}},
    },
}

#: 总检建议模板。`{items}` 会替换成异常项目的名字。
ADVICE = {
    "fatty_liver": {"zh": "脂肪肝：建议控制饮食、加强运动、控制体重，限制饮酒，定期复查肝功能及肝脏超声。",
                    "en": "Fatty liver: dietary control, regular exercise and weight management; limit alcohol; repeat liver function tests and ultrasound periodically."},
    "benign_cyst": {"zh": "囊肿：多为良性，一般无需特殊处理，建议每年复查超声观察变化。",
                    "en": "Cyst: usually benign; annual ultrasound follow-up is sufficient."},
    "gb_polyp": {"zh": "胆囊息肉样病变：建议每 6–12 个月复查超声，若增大或直径超过 10mm 请至肝胆外科就诊。",
                 "en": "Gallbladder polyp: ultrasound every 6–12 months; consult hepatobiliary surgery if it grows or exceeds 10 mm."},
    "gb_stone": {"zh": "胆囊结石：建议低脂饮食，规律进食早餐，定期复查；如出现右上腹痛请及时就诊。",
                 "en": "Gallstones: low-fat diet, regular meals, periodic review; seek care for right upper quadrant pain."},
    "kidney_stone": {"zh": "肾结石：建议多饮水，适当运动，定期复查泌尿系超声；如出现腰痛、血尿请及时就诊。",
                     "en": "Kidney stone: increase fluid intake, stay active, periodic ultrasound; seek care for flank pain or haematuria."},
    "thyroid_nodule": {"zh": "甲状腺结节：建议每 6–12 个月复查甲状腺超声及甲状腺功能，必要时内分泌科就诊。",
                       "en": "Thyroid nodule: repeat thyroid ultrasound and function tests every 6–12 months; endocrinology review if indicated."},
    "thyroid_diffuse": {"zh": "甲状腺弥漫性病变：建议内分泌科就诊，复查甲状腺功能及相关抗体。",
                        "en": "Diffuse thyroid change: endocrinology review with thyroid function and antibody tests."},
    "vascular": {"zh": "血管相关异常：建议控制血压、血脂、血糖，戒烟，低盐低脂饮食，定期复查颈动脉超声。",
                 "en": "Vascular finding: control blood pressure, lipids and glucose; stop smoking; low-salt, low-fat diet; periodic carotid ultrasound."},
    "breast": {"zh": "乳腺增生：建议保持心情舒畅，规律作息，每年复查乳腺超声。",
               "en": "Fibrocystic change: annual breast ultrasound; no specific treatment needed."},
    "breast_nodule": {"zh": "乳腺结节：建议 6 个月后复查乳腺超声，必要时乳腺外科就诊。",
                      "en": "Breast nodule: repeat ultrasound in 6 months; breast clinic review if indicated."},
    "prostate": {"zh": "前列腺增生：建议避免久坐、憋尿，泌尿外科随诊，定期复查前列腺特异性抗原。",
                 "en": "Prostatic hyperplasia: urology follow-up; periodic PSA."},
    "fibroid": {"zh": "子宫肌瘤：建议每年复查妇科超声，如月经量增多请妇科就诊。",
                "en": "Uterine fibroid: annual pelvic ultrasound; gynaecology review if menorrhagia develops."},
    "lung_nodule": {"zh": "肺微小结节：建议 12 个月后复查胸部 CT，戒烟。",
                    "en": "Small pulmonary nodule: repeat chest CT in 12 months; stop smoking."},
    "lung_markings": {"zh": "两肺纹理增粗：多见于吸烟或慢性支气管炎，建议戒烟，必要时呼吸科就诊。",
                      "en": "Prominent lung markings: often related to smoking or chronic bronchitis; stop smoking."},
    "brady": {"zh": "窦性心动过缓：如无不适一般无需处理，运动人群常见；如有头晕、乏力请心内科就诊。",
              "en": "Sinus bradycardia: common in active individuals; cardiology review if dizziness or fatigue."},
    "ecg_minor": {"zh": "心电图轻度异常：建议结合临床，必要时心内科就诊复查心电图。",
                  "en": "Minor ECG abnormality: correlate clinically; repeat ECG or cardiology review if indicated."},
    "dental": {"zh": "口腔：建议口腔科就诊处理龋齿/牙结石，早晚刷牙，定期洁牙。",
               "en": "Dental: see a dentist for caries or calculus; brush twice daily and have regular cleaning."},
    "eye": {"zh": "眼科：建议注意用眼卫生，必要时眼科验光配镜。",
            "en": "Eyes: eye hygiene; refraction and spectacles if needed."},
    "ent": {"zh": "耳鼻喉科：建议避免刺激性饮食，戒烟，必要时耳鼻喉科就诊。",
            "en": "ENT: avoid irritants, stop smoking; ENT review if symptomatic."},
    "surgical_minor": {"zh": "外科：建议注意姿势，适当锻炼，避免久坐久站，必要时相关科室就诊。",
                       "en": "Surgical: posture and exercise; avoid prolonged sitting or standing; specialist review if needed."},
    "gyn": {"zh": "妇科：建议定期妇科检查及宫颈细胞学筛查。",
            "en": "Gynaecology: regular gynaecological examination and cervical screening."},
    "hp": {"zh": "幽门螺杆菌阳性：建议消化内科就诊，评估是否需要根除治疗，注意分餐。",
           "en": "H. pylori positive: gastroenterology review for eradication therapy."},
    "bone": {"zh": "骨量减少/骨质疏松：建议补充钙剂及维生素 D，适当负重运动，定期复查骨密度。",
             "en": "Low bone density: calcium and vitamin D, weight-bearing exercise, periodic DXA."},
    "lung_function": {"zh": "肺通气功能异常：建议戒烟，避免粉尘及刺激性气体，呼吸科就诊复查肺功能（支气管舒张试验）。",
                      "en": "Abnormal spirometry: stop smoking, avoid dust and irritants; respiratory review with post-bronchodilator spirometry."},
    "echo_minor": {"zh": "心脏超声轻度异常：多为生理性或年龄相关改变，建议结合临床，控制血压，必要时心内科随诊。",
                   "en": "Minor echocardiographic finding: often physiological or age-related; correlate clinically and control blood pressure."},
    "body_fat": {"zh": "体脂率/内脏脂肪偏高：建议控制总热量，增加有氧运动与抗阻训练，减少腰围，定期复查。",
                 "en": "Raised body fat / visceral fat: reduce energy intake, add aerobic and resistance exercise, reduce waist circumference."},
    "tcm": {"zh": "中医体质偏颇：建议根据体质类型调整饮食起居，规律作息，必要时中医科就诊调理。",
            "en": "TCM constitutional imbalance: adjust diet and daily routine according to constitution type; TCM consultation if desired."},
    # 检验异常的建议，按指标组
    "lab_hepatitis": {"zh": "肝炎病毒标志物阳性（{items}）：建议感染科或肝病科就诊，复查肝功能、HBV-DNA 及肝脏超声，家人建议筛查并接种疫苗。",
                      "en": "Positive hepatitis serology ({items}): hepatology review with liver function tests, HBV DNA and ultrasound; screen and vaccinate household contacts."},
    "lab_coag": {"zh": "凝血功能异常（{items}）：建议排除采血因素后复查凝血功能，必要时血液科就诊。",
                 "en": "Abnormal coagulation ({items}): repeat after excluding sampling artefact; haematology review if persistent."},
    "lab_cardiac": {"zh": "心肌标志物异常（{items}）：建议心内科就诊，复查心电图、心肌标志物及心脏超声。",
                    "en": "Abnormal cardiac markers ({items}): cardiology review with ECG, repeat markers and echocardiography."},
    "lab_renal_early": {"zh": "尿微量白蛋白异常（{items}）：提示早期肾损伤可能，建议控制血压、血糖，3 个月内复查尿白蛋白/肌酐比值，必要时肾内科就诊。",
                        "en": "Raised urine albumin ({items}): possible early kidney damage; control blood pressure and glucose; repeat UACR within 3 months."},
    "lab_immune": {"zh": "免疫/风湿指标异常（{items}）：建议风湿免疫科就诊，结合关节症状进一步检查。",
                   "en": "Abnormal immunology ({items}): rheumatology review, correlate with joint symptoms."},
    "lab_pancreas": {"zh": "胰酶异常（{items}）：建议复查，如有上腹痛请消化内科就诊。",
                     "en": "Abnormal pancreatic enzymes ({items}): repeat; gastroenterology review if abdominal pain."},
    "lab_stool": {"zh": "便潜血阳性：建议消化内科就诊，必要时行胃肠镜检查。",
                  "en": "Positive faecal occult blood: gastroenterology review; endoscopy if indicated."},
    "lab_hp": {"zh": "幽门螺杆菌抗体阳性：抗体阳性不能区分现症感染与既往感染，建议行碳13/碳14呼气试验确认后决定是否根除。",
               "en": "H. pylori antibody positive: serology cannot distinguish current from past infection; confirm with a urea breath test."},
    "lab_cervical": {"zh": "宫颈癌筛查异常（{items}）：建议妇科就诊，必要时行阴道镜检查；高危型 HPV 阳性者 12 个月后复查。",
                     "en": "Abnormal cervical screening ({items}): gynaecology review; colposcopy if indicated; repeat HPV in 12 months."},
    "lab_lipid": {"zh": "血脂异常（{items}）：建议低脂饮食，控制体重，加强运动，3–6 个月复查血脂；必要时心内科或内分泌科就诊。",
                  "en": "Dyslipidaemia ({items}): low-fat diet, weight control, exercise; repeat lipids in 3–6 months."},
    "lab_glucose": {"zh": "血糖异常（{items}）：建议控制饮食、加强运动，复查空腹血糖及糖化血红蛋白，必要时内分泌科就诊。",
                    "en": "Abnormal glucose ({items}): diet and exercise; repeat fasting glucose and HbA1c; endocrinology review if indicated."},
    "lab_liver": {"zh": "肝功能异常（{items}）：建议戒酒，避免肝损药物，休息，2–4 周后复查肝功能。",
                  "en": "Abnormal liver function ({items}): avoid alcohol and hepatotoxic drugs; repeat in 2–4 weeks."},
    "lab_renal": {"zh": "肾功能异常（{items}）：建议多饮水，避免肾毒性药物，肾内科就诊复查。",
                  "en": "Abnormal renal function ({items}): hydration, avoid nephrotoxic drugs; nephrology review."},
    "lab_ua": {"zh": "尿酸偏高：建议低嘌呤饮食，限制饮酒，多饮水，定期复查。",
               "en": "Raised uric acid: low-purine diet, limit alcohol, hydration, periodic review."},
    "lab_cbc": {"zh": "血常规异常（{items}）：建议复查血常规，必要时血液科就诊。",
                "en": "Abnormal blood count ({items}): repeat CBC; haematology review if persistent."},
    "lab_anemia": {"zh": "贫血（{items}）：建议查找病因，补充含铁食物，血液科或消化科就诊。",
                   "en": "Anaemia ({items}): investigate cause; iron-rich diet; haematology review."},
    "lab_thyroid": {"zh": "甲状腺功能异常（{items}）：建议内分泌科就诊，复查甲状腺功能。",
                    "en": "Abnormal thyroid function ({items}): endocrinology review."},
    "lab_tumor": {"zh": "肿瘤标志物偏高（{items}）：单项轻度升高多无临床意义，建议 1–3 个月复查，必要时专科就诊。",
                  "en": "Raised tumour marker ({items}): mild isolated elevation is often non-specific; repeat in 1–3 months."},
    "lab_urine": {"zh": "尿常规异常（{items}）：建议清洁中段尿复查，必要时肾内科或泌尿科就诊。",
                  "en": "Abnormal urinalysis ({items}): repeat with a clean-catch sample."},
    "lab_bp": {"zh": "血压偏高：建议低盐饮食，规律作息，家庭自测血压并记录，如持续升高请心内科就诊。",
               "en": "Raised blood pressure: low-salt diet, home blood pressure monitoring; cardiology review if persistent."},
    "lab_bmi": {"zh": "超重/肥胖：建议控制饮食总量，增加运动，减轻体重。",
                "en": "Overweight/obesity: reduce energy intake, increase activity."},
    "lab_other": {"zh": "其他检验异常（{items}）：建议结合临床，定期复查。",
                  "en": "Other abnormal results ({items}): correlate clinically and repeat."},
    "normal": {"zh": "本次体检未见明显异常，建议保持健康的生活方式，定期体检。",
               "en": "No significant abnormality found. Maintain a healthy lifestyle and have regular check-ups."},
    "closing": {"zh": ["以上建议仅供参考，如有不适请及时就医。", "本报告不作为疾病诊断证明，具体诊疗请遵专科医师意见。"],
                "en": ["These recommendations are for guidance only; seek medical care if symptomatic."]},
}

#: 指标键 → 总检建议组。没列的走 lab_other。带方向的（`键:low` / `键:high`）优先于不带的：
#: 血红蛋白偏低是贫血，偏高不是。
LAB_GROUPS = {
    "hgb:high": "lab_cbc", "rbc:high": "lab_cbc", "hct:high": "lab_cbc", "mcv:high": "lab_cbc", "mch:high": "lab_cbc",
    "mchc:high": "lab_cbc", "ferritin:high": "lab_other", "serum_iron:high": "lab_other",
    "hdl:low": "lab_lipid", "hdl:high": "lab_other", "glu:low": "lab_other",
    "chol": "lab_lipid", "tg": "lab_lipid", "hdl": "lab_lipid", "ldl": "lab_lipid", "apoa1": "lab_lipid",
    "apob": "lab_lipid", "lpa": "lab_lipid",
    "glu": "lab_glucose", "hba1c": "lab_glucose", "ga": "lab_glucose",
    "alt": "lab_liver", "ast": "lab_liver", "ggt": "lab_liver", "alp": "lab_liver", "tbil": "lab_liver",
    "dbil": "lab_liver", "ibil": "lab_liver", "tba": "lab_liver",
    "crea": "lab_renal", "urea": "lab_renal", "cysc": "lab_renal", "egfr": "lab_renal",
    "ua": "lab_ua",
    "wbc": "lab_cbc", "plt": "lab_cbc", "neut_pct": "lab_cbc", "lymph_pct": "lab_cbc",
    "hgb": "lab_anemia", "rbc": "lab_anemia", "hct": "lab_anemia", "mcv": "lab_anemia", "mch": "lab_anemia",
    "mchc": "lab_anemia", "ferritin": "lab_anemia", "serum_iron": "lab_anemia",
    "tsh": "lab_thyroid", "ft3": "lab_thyroid", "ft4": "lab_thyroid", "tt3": "lab_thyroid", "tt4": "lab_thyroid",
    "tpoab": "lab_thyroid",
    "afp": "lab_tumor", "cea": "lab_tumor", "psa": "lab_tumor", "ca125": "lab_tumor",
    "urine_pro": "lab_urine", "urine_glu": "lab_urine", "urine_bld": "lab_urine", "urine_ket": "lab_urine",
    "urine_nit": "lab_urine", "urine_rbc": "lab_urine", "urine_wbc": "lab_urine",
    "sbp": "lab_bp", "dbp": "lab_bp", "bmi": "lab_bmi", "waist": "lab_bmi",
    # 2026-09-29 扩充
    "hbsag": "lab_hepatitis", "hbeag": "lab_hepatitis", "hbeab": "lab_hepatitis", "hbcab": "lab_hepatitis", "hcvab": "lab_hepatitis",
    "pt": "lab_coag", "inr": "lab_coag", "aptt": "lab_coag", "tt": "lab_coag", "fib": "lab_coag", "ddimer": "lab_coag",
    "ldh": "lab_cardiac", "ck": "lab_cardiac", "ckmb": "lab_cardiac", "hstni": "lab_cardiac", "ntprobnp": "lab_cardiac",
    "umalb": "lab_renal_early", "uacr": "lab_renal_early", "b2mg": "lab_renal_early",
    "insulin": "lab_glucose", "cpep": "lab_glucose", "ogtt2h": "lab_glucose",
    "tgab": "lab_thyroid", "tg_protein": "lab_thyroid",
    "rf": "lab_immune", "aso": "lab_immune", "ccp": "lab_immune", "igg": "lab_immune", "iga": "lab_immune",
    "igm": "lab_immune", "c3": "lab_immune", "c4": "lab_immune",
    "amy": "lab_pancreas", "lps": "lab_pancreas", "che": "lab_liver", "pa": "lab_liver", "nonhdl": "lab_lipid",
    "fobt": "lab_stool", "hp_ab": "lab_hp",
    "hpv16": "lab_cervical", "hpv18": "lab_cervical", "hpv_other": "lab_cervical", "tct": "lab_cervical",
    "ca199": "lab_tumor", "ca153": "lab_tumor", "ca724": "lab_tumor", "cyfra211": "lab_tumor", "nse": "lab_tumor",
    "scc": "lab_tumor", "fpsa": "lab_tumor", "fpsa_ratio": "lab_tumor", "pg1": "lab_tumor", "pg2": "lab_tumor", "pgr": "lab_tumor",
    "body_fat": "lab_bmi", "visceral_fat": "lab_bmi",
}
#: 总检里不值得单列的异常（分类计数、指数类的轻微越界），只在表格里带标记。
LAB_IGNORE = {"mono_pct", "eos_pct", "baso_pct", "neut_abs", "lymph_abs", "mono_abs", "eos_abs", "baso_abs",
              "mpv", "pdw", "pct", "rdw", "urine_sg", "urine_ph", "ag_ratio", "glb", "resp", "pulse",
              "pr_interval", "qrs_duration", "qtc", "qrs_axis", "height", "weight",
              # 参数型辅助检查的读数：异常由具名所见表达，总检里不再按指标重复
              "fvc", "fvc_pct", "fev1", "fev1_pct", "fev1_fvc", "bapwv_l", "bapwv_r", "abi_l", "abi_r",
              "lvef", "lvedd", "ivs", "la", "ea", "muscle_mass", "bmr", "spo2", "abo", "rh"}

#: 总检页的固定文字与标题。
SUMMARY = {
    "zh": {"titles": ["总检结论", "终检结论", "体检结论与建议", "综合报告单", "健康体检总结"],
           "conclusion": "主要结论", "advice": "健康建议", "abnormal_lead": "本次体检发现以下异常：",
           "lab_lead": "检验异常：", "exam_lead": "检查异常：", "signature": "总检医师",
           "history_labels": {"hypertension": "高血压病史", "prediabetes_to_t2dm": "2型糖尿病病史",
                              "dyslipidemia_statin": "高脂血症病史", "thyroid_disorder": "甲状腺功能减退病史",
                              "iron_deficiency_anemia": "缺铁性贫血病史", "ckd_progression": "慢性肾脏病病史",
                              "fatty_liver": "脂肪肝病史"}},
    "en": {"titles": ["Summary and Recommendations", "Overall Conclusion", "Physician Summary"],
           "conclusion": "Findings", "advice": "Recommendations", "abnormal_lead": "The following abnormalities were noted:",
           "lab_lead": "Laboratory:", "exam_lead": "Examination:", "signature": "Reviewing physician",
           "history_labels": {"hypertension": "Hypertension", "prediabetes_to_t2dm": "Type 2 diabetes",
                              "dyslipidemia_statin": "Hyperlipidaemia", "thyroid_disorder": "Hypothyroidism",
                              "iron_deficiency_anemia": "Iron-deficiency anaemia", "ckd_progression": "Chronic kidney disease",
                              "fatty_liver": "Fatty liver"}},
}

#: 分级判定（A–E）与重要异常结果。
#:
#: * 分段出处：日本人間ドック学会《判定区分》（2026 年 4 月 1 日改定），原表以 mg/dL 计，这里换成 SI
#:   （血糖 ×0.0555、胆固醇 ×0.0259、甘油三酯 ×0.0113、尿酸 ×59.5、肌酐 ×88.4）；血压按中国高血压指南；
#: * 所见的级别是编者按各所见的临床处置意义定的（囊肿 B、结节要随访 C、骨质疏松 D）；
#: * 重要异常结果阈值出自《健康体检重要异常结果管理专家共识》的 A 类（需立即处置）。
#: 分段格式：[级别, 下限(含), 上限(不含)]，null 表示无界；按性别分的用 {"male": [...], "female": [...]}。
JUDGEMENT = {
    "rate": {"zh": 0.3, "en": 0.2},
    "zh": {"label": "分级判定", "critical_label": "重要异常结果提示",
           "critical_text": "本次体检发现重要异常结果：{items}。已按规定通知受检者，请立即前往相应专科就诊。",
           "grade_labels": {"A": "未见异常", "B": "轻度异常，暂无需处理", "C": "需复查或改善生活方式",
                            "D": "需进一步检查或治疗", "E": "治疗中"},
           "areas": {"bp": "血压", "obesity": "体重/体脂", "lipid": "血脂", "glucose": "血糖", "liver": "肝功能",
                     "renal": "肾功能", "ua": "尿酸", "cbc": "血常规", "urine": "尿常规", "thyroid": "甲状腺功能",
                     "tumor": "肿瘤标志物", "hepatitis": "肝炎标志物", "coag": "凝血功能", "cardiac": "心肌标志物",
                     "immune": "免疫", "cervical": "宫颈癌筛查", "stool": "便潜血", "hp_serology": "幽门螺杆菌抗体",
                     "internal": "内科", "surgical": "外科", "eye": "眼科", "ent": "耳鼻喉科", "dental": "口腔科",
                     "gyn": "妇科", "tcm": "中医体质", "ecg": "心电图", "chest_xray": "胸片", "chest_ct": "胸部CT",
                     "abd_us": "腹部超声", "thyroid_us": "甲状腺超声", "carotid_us": "颈动脉超声", "breast_us": "乳腺超声",
                     "prostate_us": "前列腺超声", "gyn_us": "妇科超声", "bmd": "骨密度", "hp_breath": "呼气试验",
                     "spirometry": "肺功能", "arterial": "动脉硬化", "body_composition": "人体成分", "echo": "心脏超声",
                     "fundus_photo": "眼底", "mammography": "乳腺钼靶"}},
    "en": {"label": "Category", "critical_label": "Critical result notice",
           "critical_text": "A critical result was found at this examination: {items}. The examinee has been notified; please seek medical attention immediately.",
           "grade_labels": {"A": "No abnormality", "B": "Minor abnormality, no action needed",
                            "C": "Re-examination or lifestyle change advised", "D": "Further investigation or treatment", "E": "Under treatment"},
           "areas": {"bp": "Blood pressure", "obesity": "Weight / body fat", "lipid": "Lipids", "glucose": "Glucose", "liver": "Liver",
                     "renal": "Kidney", "ua": "Uric acid", "cbc": "Blood count", "urine": "Urinalysis", "thyroid": "Thyroid",
                     "tumor": "Tumour markers", "hepatitis": "Hepatitis serology", "coag": "Coagulation", "cardiac": "Cardiac markers",
                     "immune": "Immunology", "cervical": "Cervical screening", "stool": "Faecal occult blood", "hp_serology": "H. pylori antibody",
                     "internal": "Internal medicine", "surgical": "Surgery", "eye": "Ophthalmology", "ent": "ENT", "dental": "Dental",
                     "gyn": "Gynaecology", "tcm": "TCM constitution", "ecg": "ECG", "chest_xray": "Chest X-ray", "chest_ct": "Chest CT",
                     "abd_us": "Abdominal ultrasound", "thyroid_us": "Thyroid ultrasound", "carotid_us": "Carotid ultrasound",
                     "breast_us": "Breast ultrasound", "prostate_us": "Prostate ultrasound", "gyn_us": "Pelvic ultrasound", "bmd": "Bone density",
                     "hp_breath": "Urea breath test", "spirometry": "Spirometry", "arterial": "Arterial stiffness",
                     "body_composition": "Body composition", "echo": "Echocardiography", "fundus_photo": "Fundus", "mammography": "Mammography"}},
    "area_order": ["bp", "obesity", "lipid", "glucose", "liver", "renal", "ua", "cbc", "urine", "thyroid", "tumor", "hepatitis",
                   "coag", "cardiac", "immune", "cervical", "stool", "hp_serology", "internal", "surgical", "eye", "ent", "dental",
                   "gyn", "tcm", "ecg", "chest_xray", "chest_ct", "abd_us", "thyroid_us", "carotid_us", "breast_us", "prostate_us",
                   "gyn_us", "bmd", "hp_breath", "spirometry", "arterial", "body_composition", "echo", "fundus_photo", "mammography"],
    "area_of": {"sbp": "bp", "dbp": "bp", "bmi": "obesity", "waist": "obesity", "body_fat": "obesity", "visceral_fat": "obesity",
                "chol": "lipid", "tg": "lipid", "hdl": "lipid", "ldl": "lipid", "nonhdl": "lipid", "apoa1": "lipid", "apob": "lipid", "lpa": "lipid",
                "glu": "glucose", "hba1c": "glucose", "insulin": "glucose", "cpep": "glucose", "ogtt2h": "glucose",
                "alt": "liver", "ast": "liver", "ggt": "liver", "alp": "liver", "tbil": "liver", "dbil": "liver", "ibil": "liver",
                "tp": "liver", "alb": "liver", "tba": "liver", "che": "liver", "pa": "liver",
                "crea": "renal", "urea": "renal", "cysc": "renal", "egfr": "renal", "umalb": "renal", "uacr": "renal", "b2mg": "renal",
                "ua": "ua", "wbc": "cbc", "hgb": "cbc", "plt": "cbc", "rbc": "cbc", "hct": "cbc", "mcv": "cbc", "mch": "cbc", "mchc": "cbc",
                "neut_pct": "cbc", "lymph_pct": "cbc", "ferritin": "cbc", "serum_iron": "cbc",
                "urine_pro": "urine", "urine_glu": "urine", "urine_bld": "urine", "urine_ket": "urine", "urine_nit": "urine",
                "urine_rbc": "urine", "urine_wbc": "urine",
                "tsh": "thyroid", "ft3": "thyroid", "ft4": "thyroid", "tt3": "thyroid", "tt4": "thyroid", "tpoab": "thyroid", "tgab": "thyroid",
                "tg_protein": "thyroid",
                "afp": "tumor", "cea": "tumor", "psa": "tumor", "fpsa_ratio": "tumor", "ca125": "tumor", "ca199": "tumor", "ca153": "tumor",
                "ca724": "tumor", "cyfra211": "tumor", "nse": "tumor", "scc": "tumor", "pg1": "tumor", "pgr": "tumor",
                "hbsag": "hepatitis", "hbeag": "hepatitis", "hbcab": "hepatitis", "hcvab": "hepatitis",
                "pt": "coag", "inr": "coag", "aptt": "coag", "fib": "coag", "ddimer": "coag",
                "hstni": "cardiac", "ntprobnp": "cardiac", "ck": "cardiac", "ckmb": "cardiac",
                "rf": "immune", "aso": "immune", "ccp": "immune", "igg": "immune", "iga": "immune", "igm": "immune", "c3": "immune", "c4": "immune",
                "hpv16": "cervical", "hpv18": "cervical", "hpv_other": "cervical", "tct": "cervical", "fobt": "stool", "hp_ab": "hp_serology"},
    "bands": {
        "sbp": [["A", None, 130], ["B", 130, 140], ["C", 140, 160], ["D", 160, None]],
        "dbp": [["A", None, 85], ["B", 85, 90], ["C", 90, 100], ["D", 100, None]],
        "bmi": [["C", None, 17.0], ["B", 17.0, 18.5], ["A", 18.5, 25.0], ["B", 25.0, 30.0], ["C", 30.0, None]],
        "glu": [["A", None, 5.6], ["B", 5.6, 6.1], ["C", 6.1, 7.0], ["D", 7.0, None]],
        "hba1c": [["A", None, 5.6], ["B", 5.6, 6.0], ["C", 6.0, 6.5], ["D", 6.5, None]],
        "ldl": [["B", None, 1.55], ["A", 1.55, 3.1], ["B", 3.1, 3.6], ["C", 3.6, 4.65], ["D", 4.65, None]],
        "hdl": [["D", None, 0.9], ["C", 0.9, 1.03], ["A", 1.03, None]],
        "tg": [["A", None, 1.7], ["B", 1.7, 3.4], ["C", 3.4, 5.65], ["D", 5.65, None]],
        "chol": [["B", None, 3.6], ["A", 3.6, 5.2], ["B", 5.2, 5.7], ["C", 5.7, 6.75], ["D", 6.75, None]],
        "alt": [["A", None, 31], ["B", 31, 51], ["C", 51, 101], ["D", 101, None]],
        "ast": [["A", None, 31], ["B", 31, 36], ["C", 36, 51], ["D", 51, None]],
        "ggt": [["A", None, 51], ["B", 51, 81], ["C", 81, 101], ["D", 101, None]],
        "ua": [["C", None, 120], ["A", 120, 417], ["B", 417, 471], ["C", 471, 530], ["D", 530, None]],
        "crea": {"male": [["A", None, 89], ["B", 89, 97], ["C", 97, 115], ["D", 115, None]],
                 "female": [["A", None, 63], ["B", 63, 71], ["C", 71, 89], ["D", 89, None]]},
        "egfr": [["D", None, 45], ["C", 45, 60], ["A", 60, None]],
        "hgb": {"male": [["D", None, 110], ["C", 110, 121], ["B", 121, 131], ["A", 131, 167], ["B", 167, 181], ["D", 181, None]],
                "female": [["D", None, 100], ["C", 100, 111], ["B", 111, 121], ["A", 121, 147], ["B", 147, 161], ["D", 161, None]]},
        "wbc": [["D", None, 2.0], ["C", 2.0, 2.6], ["B", 2.6, 3.1], ["A", 3.1, 8.5], ["B", 8.5, 10.0], ["C", 10.0, 12.0], ["D", 12.0, None]],
        "plt": [["D", None, 100], ["C", 100, 123], ["B", 123, 145], ["A", 145, 330], ["B", 330, 400], ["C", 400, 500], ["D", 500, None]],
    },
    "qualitative_grade": {"弱阳性": "B", "+": "C", "++": "D", "+++": "D", "阳性": "C", "ASC-US": "C", "LSIL": "D", "ASC-H": "D"},
    "finding_grades": {"fatty_liver": {"mild": "B", "moderate": "C"}, "liver_cyst": "B", "kidney_cyst": "B", "gb_polyp": "B",
                       "gb_stone": "C", "kidney_stone": "C", "thyroid_nodule": "C", "thyroid_diffuse": "C", "carotid_imt": "B",
                       "carotid_plaque": "C", "breast_hyperplasia": "B", "breast_nodule": "C", "prostate_hyperplasia": "B",
                       "uterine_fibroid": "B", "lung_nodule": "C", "lung_markings": "B", "sinus_brady": "B", "sinus_arrhythmia": "A",
                       "t_wave": "C", "pac": "B", "caries": "B", "calculus": "B", "missing_teeth": "B", "myopia": "B",
                       "presbyopia_note": "A", "fundus_arteriosclerosis": "B", "conjunctivitis": "B", "chronic_pharyngitis": "B",
                       "septal_deviation": "B", "cerumen": "A", "hearing_loss": "C", "thyroid_enlarged": "C", "varicose": "B",
                       "hemorrhoids": "B", "scoliosis": "B", "cervical_spondylosis": "B", "cervical_erosion": "B",
                       "hp_positive": "C", "osteopenia": "B", "osteoporosis": "D", "airflow_limitation": "C",
                       "restrictive_defect": "C", "arterial_stiff": "B", "abi_low": "D", "visceral_fat_high": "B",
                       "lv_hypertrophy": "C", "diastolic_dysfunction": "B", "tr_trivial": "A", "mr_trivial": "A",
                       "fundus_hypertensive": "B", "breast_calcification": "B", "qi_deficiency": "B", "phlegm_damp": "B",
                       "yang_deficiency": "B", "damp_heat": "B"},
    # 重要异常结果（A 类）：[下限(≤ 触发), 上限(≥ 触发)]，null 无
    "critical": {"sbp": [None, 180], "dbp": [None, 110], "glu": [2.8, 16.7], "hgb": [60, None], "plt": [30, None],
                 "wbc": [1.0, 30.0], "k": [2.5, 6.5], "na": [120, 160], "crea": [None, 707], "alt": [None, 600],
                 "ast": [None, 600], "hstni": [None, 500], "tbil": [None, 171]},
}

#: 视力：两种记法各占一半（5 分记录法 / 小数记录法），这是真实体检报告里的一个方言。
ACUITY = {"five_point": ["5.2", "5.1", "5.0", "4.9", "4.8", "4.7", "4.6", "4.5", "4.3", "4.0"],
          "decimal": ["1.5", "1.2", "1.0", "0.8", "0.6", "0.5", "0.4", "0.3", "0.2", "0.1"],
          "normal_index": [0, 1, 2], "low_index": [3, 4, 5, 6, 7, 8, 9]}

#: 页面构件与标签。
BOOK = {
    "zh": {"cover_titles": ["健康体检报告", "体检报告书", "个人健康体检报告", "健康体检报告书"],
           "basic_info": "基本信息", "checkup_no": ["体检编号", "体检号", "档案号"], "package": "体检套餐",
           "checkup_date": "体检日期", "general": "一般检查", "lab": "实验室检查", "aux": "辅助检查",
           "dept_exam": "科室检查", "item": "检查项目", "result": "检查结果", "guide": "报告阅读说明",
           "guide_text": ["本报告仅反映受检者本次体检时的健康状况，不作为疾病诊断证明。",
                          "标有↑或↓的项目表示高于或低于参考范围，请结合总检建议阅读。"],
           "examiner": "检查医师", "reviewer": "审核医师", "left": "左", "right": "右",
           "left_eye": "左眼", "right_eye": "右眼", "t_score": "T值", "dob": "DOB值", "reference": "参考值"},
    "en": {"cover_titles": ["Health Check-up Report", "Annual Health Screening Report", "Medical Examination Report"],
           "basic_info": "Basic Information", "checkup_no": ["Report No.", "Check-up ID"], "package": "Package",
           "checkup_date": "Examination date", "general": "General Examination", "lab": "Laboratory Tests",
           "aux": "Investigations", "dept_exam": "Clinical Examination", "item": "Item", "result": "Result",
           "guide": "How to read this report",
           "guide_text": ["This report reflects the examinee's status on the day of examination and is not a diagnosis.",
                          "Values marked H/L are above/below the reference range; please read together with the recommendations."],
           "examiner": "Examined by", "reviewer": "Reviewed by", "left": "L", "right": "R",
           "left_eye": "Left eye", "right_eye": "Right eye", "t_score": "T-score", "dob": "DOB", "reference": "Reference"},
}

# ═══════════════════════════════════════════════════════════════════
# 2. 门诊病历、心电图报告、超声报告、居家记录
# ═══════════════════════════════════════════════════════════════════

OUTPATIENT = {
    "zh": {"titles": ["门诊病历", "门诊病历记录", "门诊复诊记录"],
           "cc": "主诉", "hpi": "现病史", "pmh": "既往史", "pe": "体格检查", "aux": "辅助检查",
           "dx": "诊断", "plan": "处理", "doctor": "医师", "dept": "科室", "visit_no": ["门诊号", "就诊号", "病历号"],
           "vitals_line": "T {temp}℃  P {pulse}次/分  R {resp}次/分  BP {sbp}/{dbp}mmHg  体重 {weight}kg",
           "hpi_templates": ["患者{dur}前无明显诱因出现{s1}{s2_clause}，{course}。{history_clause}",
                             "{dur}来{s1}{s2_clause}，{course}，今来复诊。{history_clause}"],
           "s2_clause": "，伴{s2}", "course_words": ["无加重", "程度轻，间断发作", "近日略有加重", "休息后可缓解"],
           "history_clause": {"medication": "规律服用{drug}，未自行停药。", "none": "既往体健。"},
           "followup_cc": ["{dx}复诊", "{dx}复查", "复查{dx}"],
           "plan_templates": ["1. 继续目前治疗；2. 复查{labs}；3. 低盐低脂饮食，规律作息；4. {interval}后复诊。",
                              "1. 复查{labs}；2. 继续用药，监测{monitor}；3. 不适随诊。"],
           "monitor": {"hypertension": "血压", "prediabetes_to_t2dm": "血糖", "dyslipidemia_statin": "血脂",
                       "iron_deficiency_anemia": "血常规", "thyroid_disorder": "甲功", "ckd_progression": "肾功能",
                       "fatty_liver": "肝功能", "healthy": "症状"},
           "drugs": {"hypertension": "氨氯地平", "prediabetes_to_t2dm": "二甲双胍", "dyslipidemia_statin": "阿托伐他汀",
                     "iron_deficiency_anemia": "多糖铁复合物", "thyroid_disorder": "左甲状腺素钠"},
           "dx_surfaces": {"hypertension": "高血压", "prediabetes_to_t2dm": "2型糖尿病", "dyslipidemia_statin": "高脂血症",
                           "iron_deficiency_anemia": "缺铁性贫血", "thyroid_disorder": "甲状腺功能减退",
                           "ckd_progression": "慢性肾病", "fatty_liver": "脂肪肝", "uri": "上呼吸道感染"},
           "pmh_templates": ["{dx}病史{years}年，{drug_clause}", "否认高血压、糖尿病、冠心病病史。"],
           "drug_clause": {"medication": "口服{drug}治疗。", "none": "未规律治疗。"}},
    "en": {"titles": ["Outpatient Record", "Clinic Note", "Follow-up Visit Note"],
           "cc": "Chief complaint", "hpi": "History of present illness", "pmh": "Past medical history",
           "pe": "Examination", "aux": "Investigations", "dx": "Diagnosis", "plan": "Plan", "doctor": "Physician",
           "dept": "Department", "visit_no": ["Visit No.", "Encounter ID"],
           "vitals_line": "T {temp}°C  P {pulse}/min  R {resp}/min  BP {sbp}/{dbp} mmHg  Weight {weight} kg",
           "hpi_templates": ["{s1_cap}{s2_clause} for {dur}, {course}. {history_clause}"],
           "s2_clause": " with {s2}", "course_words": ["not worsening", "mild and intermittent", "slightly worse recently", "relieved by rest"],
           "history_clause": {"medication": "Taking {drug} regularly.", "none": "Otherwise well."},
           "followup_cc": ["Follow-up for {dx}", "{dx} review"],
           "plan_templates": ["1. Continue current treatment. 2. Repeat {labs}. 3. Low-salt, low-fat diet. 4. Review in {interval}.",
                              "1. Repeat {labs}. 2. Continue medication; monitor {monitor}. 3. Return if symptoms worsen."],
           "monitor": {"hypertension": "blood pressure", "prediabetes_to_t2dm": "glucose", "dyslipidemia_statin": "lipids",
                       "iron_deficiency_anemia": "blood count", "thyroid_disorder": "thyroid function",
                       "ckd_progression": "renal function", "fatty_liver": "liver function", "healthy": "symptoms"},
           "drugs": {"hypertension": "amlodipine", "prediabetes_to_t2dm": "metformin", "dyslipidemia_statin": "atorvastatin",
                     "iron_deficiency_anemia": "iron polysaccharide", "thyroid_disorder": "levothyroxine"},
           "dx_surfaces": {"hypertension": "hypertension", "prediabetes_to_t2dm": "type 2 diabetes", "dyslipidemia_statin": "hyperlipidaemia",
                           "iron_deficiency_anemia": "iron deficiency anaemia", "thyroid_disorder": "hypothyroidism",
                           "ckd_progression": "chronic kidney disease", "fatty_liver": "fatty liver", "uri": "upper respiratory tract infection"},
           "pmh_templates": ["{dx} for {years} years, {drug_clause}", "No history of hypertension, diabetes or heart disease."],
           "drug_clause": {"medication": "on {drug}.", "none": "not on regular treatment."}},
}

ECG_REPORT = {
    "zh": {"titles": ["心电图报告", "十二导联心电图报告单", "常规心电图检查报告"], "rhythm": "心律", "sinus": "窦性心律",
           "params": {"pulse": "心率", "pr_interval": "PR间期", "qrs_duration": "QRS时限", "qtc": "QT/QTc", "qrs_axis": "电轴"},
           "finding": "心电图描述", "impression": "心电图诊断", "reporter": "报告医师", "strip": "Ⅱ导联 25mm/s 10mm/mV"},
    "en": {"titles": ["ECG Report", "12-lead Electrocardiogram Report"], "rhythm": "Rhythm", "sinus": "Sinus rhythm",
           "params": {"pulse": "Heart rate", "pr_interval": "PR interval", "qrs_duration": "QRS duration", "qtc": "QT/QTc", "qrs_axis": "Axis"},
           "finding": "Description", "impression": "Interpretation", "reporter": "Reported by", "strip": "Lead II 25 mm/s 10 mm/mV"},
}

HOME_LOG = {
    "zh": {"bp_titles": ["家庭血压记录", "血压日记", "血压监测记录表"], "weight_titles": ["体重记录", "晨起体重"],
           "date": "日期", "time": "时间", "sbp": "收缩压", "dbp": "舒张压", "pulse": "脉搏", "weight": "体重",
           "note": "备注", "notes": ["", "", "", "晨起", "服药后", "睡前", "运动后"], "unit_row": ["", "", "mmHg", "mmHg", "次/分"]},
    "en": {"bp_titles": ["Home Blood Pressure Log", "BP Diary"], "weight_titles": ["Weight Log", "Morning Weight"],
           "date": "Date", "time": "Time", "sbp": "Systolic", "dbp": "Diastolic", "pulse": "Pulse", "weight": "Weight",
           "note": "Note", "notes": ["", "", "", "morning", "after medication", "bedtime", "after exercise"],
           "unit_row": ["", "", "mmHg", "mmHg", "bpm"]},
}

# ═══════════════════════════════════════════════════════════════════
# 3. 主诉与日记
# ═══════════════════════════════════════════════════════════════════

#: 症状词表。`zh`/`en` 是**mirobody 词表里精确存在的表面**（2026-09-29 核对 `symptoms_zh.tsv`、
#: `symptoms_en.tsv`；英文另可命中 ICPC-3 的英文优选词，见 `en_preferred`）。
#: `icpc3` 是那边给出的 S 轴码。
SYMPTOMS = {
    "fatigue": {"zh": ["乏力", "疲劳", "疲倦", "没力气"], "en": ["tired all the time", "no energy", "exhausted"],
                "en_preferred": "General weakness or tiredness", "icpc3": "AS04"},
    "dizziness": {"zh": ["头晕", "头昏", "眩晕"], "en": ["dizzy"], "en_preferred": "Vertigo or dizziness", "icpc3": "NS09",
                  "zh_codes": {"头昏": "NS09.00"}},
    "headache": {"zh": ["头痛", "头疼"], "en": [], "en_preferred": "Headache", "icpc3": "NS01"},
    "palpitations": {"zh": ["心悸", "心慌", "心跳快"], "en": [], "en_preferred": "Palpitations, awareness of heart", "icpc3": "KS02"},
    "thirst": {"zh": ["口渴"], "en": [], "en_preferred": "Excessive thirst", "icpc3": "TS01"},
    "dry_mouth": {"zh": ["口干"], "en": [], "en_preferred": "Dry mouth", "icpc3": "DS20.00"},
    "weight_loss": {"zh": ["体重下降", "消瘦"], "en": [], "en_preferred": "Weight loss", "icpc3": "TS07"},
    "weight_gain": {"zh": ["体重增加"], "en": [], "en_preferred": "Weight gain", "icpc3": "TS06"},
    "cold": {"zh": ["怕冷", "畏寒"], "en": ["shivery"], "en_preferred": "Chills", "icpc3": "AS02"},
    "sweating": {"zh": ["多汗", "出汗多"], "en": [], "en_preferred": "Sweating problem", "icpc3": "AS10"},
    "edema": {"zh": ["水肿", "浮肿"], "en": [], "en_preferred": "Swelling and generalized edema", "icpc3": "AS09"},
    "leg_edema": {"zh": ["脚肿", "腿肿"], "en": ["swollen ankles", "puffy ankles"], "en_preferred": None, "icpc3": "KS04"},
    "insomnia": {"zh": ["失眠", "睡不着", "入睡困难", "多梦"], "en": [], "en_preferred": "Sleep disturbance", "icpc3": "PS06"},
    "cough": {"zh": ["咳嗽"], "en": [], "en_preferred": "Cough", "icpc3": "RS07"},
    "sputum": {"zh": ["痰多", "咳痰"], "en": [], "en_preferred": None, "icpc3": "RS15"},
    "sore_throat": {"zh": ["咽痛"], "en": ["sore throat"], "en_preferred": "Pain in throat", "icpc3": "RS12.00"},
    "fever": {"zh": ["发烧", "发热", "低烧"], "en": ["feverish", "running a temperature", "temperature"], "en_preferred": "Fever", "icpc3": "AS03"},
    "nasal": {"zh": ["鼻塞", "流鼻涕", "打喷嚏"], "en": [], "en_preferred": "Sneezing or nasal congestion", "icpc3": "RS09"},
    "bloating": {"zh": ["腹胀", "嗳气"], "en": ["bloated", "wind"], "en_preferred": "Flatulence, gas and belching", "icpc3": "DS08"},
    "epigastric": {"zh": ["胃痛", "胃胀"], "en": [], "en_preferred": "Epigastric pain", "icpc3": "DS02"},
    "abdominal_pain": {"zh": ["腹痛"], "en": ["tummy ache", "belly ache", "stomach cramps"], "en_preferred": "General abdominal pain", "icpc3": "DS01"},
    "reflux": {"zh": ["反酸", "烧心"], "en": ["acid reflux"], "en_preferred": None, "icpc3": "DS03"},
    "nausea": {"zh": ["恶心"], "en": ["feeling queasy"], "en_preferred": "Nausea", "icpc3": "DS09"},
    "constipation": {"zh": ["便秘"], "en": ["backed up"], "en_preferred": "Constipation", "icpc3": "DS12"},
    "diarrhea": {"zh": ["腹泻", "拉肚子"], "en": ["the runs", "loose stools"], "en_preferred": "Diarrhoea", "icpc3": "DS11"},
    "low_back_pain": {"zh": ["腰痛"], "en": [], "en_preferred": "Low back symptom or complaint", "icpc3": "LS03"},
    "neck_pain": {"zh": ["颈部疼痛"], "en": [], "en_preferred": "Neck symptom or complaint", "icpc3": "LS01"},
    "shoulder_pain": {"zh": ["肩膀疼", "肩痛"], "en": [], "en_preferred": None, "icpc3": "LS07"},
    "knee_pain": {"zh": ["膝盖疼"], "en": [], "en_preferred": None, "icpc3": "LS14"},
    "joint_pain": {"zh": ["关节痛"], "en": [], "en_preferred": "Other specified joint symptoms or complaints", "icpc3": "LS20"},
    "chest_tightness": {"zh": ["胸闷"], "en": [], "en_preferred": None, "icpc3": "RS99"},
    "dyspnea": {"zh": ["气短", "呼吸困难", "憋气"], "en": [], "en_preferred": "Shortness of breath", "icpc3": "RS02"},
    "chest_pain": {"zh": ["胸痛"], "en": [], "en_preferred": "Chest pain", "icpc3": "AS12"},
    "blurred_vision": {"zh": ["视物模糊", "视力下降"], "en": [], "en_preferred": "Decreased visual acuity", "icpc3": "FS05"},
    "dry_eye": {"zh": ["眼睛干", "眼睛干涩"], "en": [], "en_preferred": "Dry eye or other abnormal eye sensations", "icpc3": "FS07"},
    "numb_hands": {"zh": ["手麻"], "en": [], "en_preferred": "Tingling fingers, feet, toes", "icpc3": "NS04"},
    "tremor": {"zh": ["手抖"], "en": [], "en_preferred": None, "icpc3": "NS07"},
    "hair_loss": {"zh": ["脱发"], "en": [], "en_preferred": "Hair loss or baldness", "icpc3": "SS10"},
    "rash": {"zh": ["皮疹"], "en": ["rash"], "en_preferred": "Rash localised", "icpc3": "SS05"},
    "itch": {"zh": ["瘙痒"], "en": [], "en_preferred": "Pruritus", "icpc3": "SS02"},
    "frequency": {"zh": ["尿频", "尿急"], "en": [], "en_preferred": "Urinary frequency or urgency", "icpc3": "US02"},
    "appetite": {"zh": ["食欲不振", "没胃口"], "en": [], "en_preferred": "Loss of appetite", "icpc3": "TS03"},
    "anxiety": {"zh": ["焦虑", "紧张"], "en": [], "en_preferred": "Feeling anxious or nervous or tense", "icpc3": "PS01"},
    "low_mood": {"zh": ["情绪低落"], "en": [], "en_preferred": "Feeling sad", "icpc3": "PS03"},
    "irritable": {"zh": ["烦躁"], "en": [], "en_preferred": "Feeling or being irritable or angry", "icpc3": "PS04"},
    "memory": {"zh": ["记忆力下降", "健忘", "注意力不集中"], "en": [], "en_preferred": "Memory or attention problem", "icpc3": "PS17"},
    "tinnitus": {"zh": ["耳鸣"], "en": [], "en_preferred": "Tinnitus, ringing or buzzing ear", "icpc3": "HS03"},
    "snoring": {"zh": ["打鼾"], "en": [], "en_preferred": "Snoring", "icpc3": "RS05"},
    "night_sweats": {"zh": ["盗汗", "夜间出汗"], "en": [], "en_preferred": None, "icpc3": "AS10.01"},
    "toothache": {"zh": ["牙痛", "牙龈出血"], "en": [], "en_preferred": None, "icpc3": "DS19"},
    "stress": {"zh": ["压力大"], "en": [], "en_preferred": None, "icpc3": "PS02"},
}

#: 刻意**不在** mirobody 词表里的表面：考"该弃权就弃权"。`expect` 写明那边应给的结果。
FRONTIER = {
    "polyuria": {"zh": ["多尿", "夜尿多", "起夜多"], "en": ["peeing a lot at night"], "expect": "no-match"},
    "pale": {"zh": ["面色苍白", "脸色差"], "en": ["look pale"], "expect": "no-match"},
    "drowsy": {"zh": ["嗜睡", "白天犯困"], "en": ["sleepy in the daytime"], "expect": "no-match"},
    "pain_generic": {"zh": ["疼"], "en": [], "expect": "refused"},
    "hiccup": {"zh": ["打嗝"], "en": [], "expect": "needs-input"},
    "foam_urine": {"zh": ["泡沫尿", "尿里有泡沫"], "en": ["foamy urine"], "expect": "no-match"},
    "cramps": {"zh": ["腿抽筋", "小腿抽筋"], "en": ["leg cramps"], "expect": "no-match"},
}

#: 原型 → 干预前/后的主诉池（id 引用 SYMPTOMS 或 FRONTIER），以及事件 → 主诉池。
COMPLAINT_POOLS = {
    "archetype": {
        "prediabetes_to_t2dm": {"before": ["thirst", "dry_mouth", "polyuria", "fatigue", "weight_loss", "blurred_vision"],
                                "after": ["fatigue", "numb_hands"], "none_rate": 0.35},
        "dyslipidemia_statin": {"before": ["dizziness", "fatigue", "headache"], "after": ["joint_pain", "fatigue"], "none_rate": 0.6},
        "iron_deficiency_anemia": {"before": ["fatigue", "dizziness", "palpitations", "pale", "hair_loss", "dyspnea"],
                                   "after": ["fatigue"], "none_rate": 0.25},
        "thyroid_disorder": {"before": ["fatigue", "cold", "weight_gain", "edema", "constipation", "drowsy", "memory", "hair_loss"],
                             "after": ["fatigue", "palpitations"], "none_rate": 0.3},
        "ckd_progression": {"before": ["edema", "leg_edema", "foam_urine", "fatigue", "appetite", "polyuria"],
                            "after": ["edema", "fatigue"], "none_rate": 0.4},
        "fatty_liver": {"before": ["fatigue", "bloating", "epigastric"], "after": ["fatigue"], "none_rate": 0.55},
        "hypertension": {"before": ["headache", "dizziness", "palpitations", "insomnia", "neck_pain", "tinnitus"],
                         "after": ["dizziness", "leg_edema"], "none_rate": 0.35},
        "healthy": {"before": [], "after": [], "none_rate": 1.0},
    },
    "incident": {
        "急性上呼吸道感染": ["cough", "sore_throat", "fever", "nasal", "sputum", "fatigue"],
        "献血 400mL": ["dizziness", "fatigue"],
        "连续加班一个月": ["fatigue", "insomnia", "neck_pain", "dry_eye", "headache", "stress"],
        "春节假期饮食": ["bloating", "reflux", "weight_gain", "diarrhea"],
        "开始规律跑步": ["knee_pain", "cramps"],
    },
    #: 日常背景主诉（谁都可能记一笔），每人每月的期望条数。
    "background": {"insomnia": 0.05, "headache": 0.05, "low_back_pain": 0.05, "neck_pain": 0.04, "dry_eye": 0.03,
                   "shoulder_pain": 0.03, "bloating": 0.03, "constipation": 0.02, "toothache": 0.02, "rash": 0.02,
                   "anxiety": 0.02, "irritable": 0.01, "snoring": 0.01, "hiccup": 0.005, "pain_generic": 0.005,
                   "abdominal_pain": 0.02, "nausea": 0.01, "itch": 0.02, "tremor": 0.005, "frequency": 0.01},
}

#: 主诉的写法（门诊病历）与日记句子（口语）。`{s}` 症状表面，`{dur}` 时长，`{s2}` 第二症状。
PHRASING = {
    "zh": {"durations": ["3天", "5天", "1周", "2周", "半月", "1月余", "2月余", "3月余", "半年余", "1年"],
           "cc": ["{s}{dur}", "反复{s}{dur}", "{s}伴{s2}{dur}", "间断{s}{dur}，加重3天", "{s}{dur}，{s2}1周"],
           "journal": ["今天{s}，{s2}。", "最近{dur}总是{s}。", "{s}了，可能是{cause}。", "早上起来{s}，{s2}。",
                       "这几天{s}，{s2}，不知道要不要去看看。", "{s}，比昨天好点了。", "晚上{s}，睡得不好。",
                       "{s}{dur}了，明天去看医生。", "有点{s}。", "又{s}了。"],
           "causes": ["没睡好", "加班", "换季", "吃多了", "吹了空调", "喝了酒", "感冒了", "太累了"],
           "measure": ["量了血压{sbp}/{dbp}", "血压{sbp}/{dbp}，心率{pulse}", "早上体重{weight}公斤",
                       "体温{temp}度", "今天走了{steps}步"]},
    "en": {"durations": ["3 days", "5 days", "a week", "2 weeks", "2 weeks", "a month", "2 months", "3 months", "6 months", "a year"],
           "cc": ["{s} for {dur}", "Recurrent {s} for {dur}", "{s} with {s2} for {dur}", "Intermittent {s} for {dur}, worse for 3 days"],
           "journal": ["{s_cap} today, {s2}.", "Been {s} for {dur}.", "{s_cap}, probably {cause}.", "Woke up {s}, {s2}.",
                       "{s_cap} the last few days, {s2}.", "{s_cap}, better than yesterday.", "{s_cap} tonight, slept badly.",
                       "{s_cap} for {dur} now, will see the doctor tomorrow.", "A bit {s}."],
           "causes": ["from not sleeping", "from overtime", "the weather", "from overeating", "the aircon", "the drinks", "a cold", "just tired"],
           "measure": ["BP {sbp}/{dbp}", "BP {sbp}/{dbp}, HR {pulse}", "weight {weight} kg this morning", "temp {temp}", "{steps} steps today"]},
}

# ═══════════════════════════════════════════════════════════════════
# 4. 基因位点
# ═══════════════════════════════════════════════════════════════════

#: 41 个带基因标注的位点，坐标/等位基因来自 mirobody `genotype_sites.sqlite3`（dbSNP b155 common ∩ CPIC）。
#: `alt` 只取第一个替代等位基因（多等位位点在芯片上通常只测一个）。
#: `eas`/`eur` 是替代等位基因频率的**手写近似**（公开人群数据的量级；给生成用，mirobody 不读）。
#: `label` 是该位点常见的星号等位/临床标签，仅作注释。
PGX_SITES = [
    ("rs28399499", "19", 41518221, 41012316, "T", "C", "CYP2B6", 0.00, 0.00, "*18"),
    ("rs3211371", "19", 41522715, 41016810, "C", "A", "CYP2B6", 0.01, 0.10, "*5"),
    ("rs34223104", "19", 41497129, 40991224, "T", "C", "CYP2B6", 0.02, 0.05, "*22"),
    ("rs3745274", "19", 41512841, 41006936, "G", "T", "CYP2B6", 0.18, 0.25, "*6/*9 (516G>T)"),
    ("rs8192709", "19", 41497274, 40991369, "C", "T", "CYP2B6", 0.03, 0.04, "*2"),
    ("rs12248560", "10", 96521657, 94761900, "C", "T", "CYP2C19", 0.02, 0.22, "*17"),
    ("rs12769205", "10", 96535124, 94775367, "A", "G", "CYP2C19", 0.30, 0.15, "*2 haplotype"),
    ("rs4244285", "10", 96541616, 94781859, "G", "A", "CYP2C19", 0.31, 0.15, "*2"),
    ("rs4986893", "10", 96540410, 94780653, "G", "A", "CYP2C19", 0.05, 0.00, "*3"),
    ("rs1057910", "10", 96741053, 94981296, "A", "C", "CYP2C9", 0.03, 0.07, "*3"),
    ("rs1799853", "10", 96702047, 94942290, "C", "T", "CYP2C9", 0.00, 0.12, "*2"),
    ("rs2256871", "10", 96708974, 94949217, "A", "G", "CYP2C9", 0.01, 0.01, "*9"),
    ("rs7900194", "10", 96702066, 94942309, "G", "A", "CYP2C9", 0.00, 0.00, "*8"),
    ("rs10264272", "7", 99262835, 99665212, "C", "T", "CYP3A5", 0.00, 0.00, "*6"),
    ("rs2108622", "19", 15990431, 15879621, "C", "T", "CYP4F2", 0.22, 0.30, "*3 (V433M)"),
    ("rs3093105", "19", 16008388, 15897578, "A", "C", "CYP4F2", 0.12, 0.08, "W12G"),
    ("rs3093153", "19", 16001215, 15890405, "C", "A", "CYP4F2", 0.10, 0.12, "intronic"),
    ("rs3093200", "19", 15989589, 15878779, "G", "A", "CYP4F2", 0.05, 0.10, "intronic"),
    ("rs17376848", "1", 97915624, 97450068, "A", "G", "DPYD", 0.04, 0.05, "*9A-linked"),
    ("rs1801159", "1", 97981395, 97515839, "T", "C", "DPYD", 0.22, 0.20, "*5 (I543V)"),
    ("rs1801160", "1", 97770920, 97305364, "C", "T", "DPYD", 0.01, 0.04, "*6 (V732I)"),
    ("rs2297595", "1", 98165091, 97699535, "T", "C", "DPYD", 0.02, 0.09, "M166V"),
    ("rs61622928", "1", 98039437, 97573881, "C", "T", "DPYD", 0.01, 0.01, "intronic"),
    ("rs1208", "8", 18258316, 18400806, "G", "A", "NAT2", 0.18, 0.55, "*4/*5 (803A>G)"),
    ("rs1799930", "8", 18258103, 18400593, "G", "A", "NAT2", 0.25, 0.28, "*6"),
    ("rs1799931", "8", 18258370, 18400860, "G", "A", "NAT2", 0.12, 0.03, "*7"),
    ("rs1801279", "8", 18257704, 18400194, "G", "A", "NAT2", 0.00, 0.00, "*14"),
    ("rs1801280", "8", 18257854, 18400344, "T", "C", "NAT2", 0.05, 0.45, "*5"),
    ("rs116855232", "13", 48619855, 48045719, "C", "T", "NUDT15", 0.10, 0.00, "*3 (R139C)"),
    ("rs11045819", "12", 21329813, 21176879, "C", "A", "SLCO1B1", 0.01, 0.15, "*4 (P155T)"),
    ("rs11045852", "12", 21349885, 21196951, "A", "G", "SLCO1B1", 0.05, 0.04, "intronic"),
    ("rs2306283", "12", 21329738, 21176804, "A", "G", "SLCO1B1", 0.75, 0.40, "*1B (N130D)"),
    ("rs34671512", "12", 21391976, 21239042, "A", "C", "SLCO1B1", 0.02, 0.05, "L643F"),
    ("rs4149056", "12", 21331549, 21178615, "T", "C", "SLCO1B1", 0.12, 0.16, "*5 (V174A)"),
    ("rs59113707", "12", 21355489, 21202555, "C", "G", "SLCO1B1", 0.01, 0.01, "intronic"),
    ("rs59502379", "12", 21358933, 21205999, "G", "C", "SLCO1B1", 0.01, 0.02, "*9 (G488A)"),
    ("rs1142345", "6", 18130918, 18130687, "T", "C", "TPMT", 0.02, 0.04, "*3C"),
    ("rs1800460", "6", 18139228, 18138997, "C", "T", "TPMT", 0.00, 0.04, "*3B"),
    ("rs4148323", "2", 234669144, 233760498, "G", "A", "UGT1A1", 0.15, 0.01, "*6 (G71R)"),
    ("rs887829", "2", 234668570, 233759924, "C", "T", "UGT1A1", 0.14, 0.32, "*28 tag"),
    ("rs9923231", "16", 31107689, 31096368, "C", "T", "VKORC1", 0.90, 0.40, "-1639G>A"),
]

#: 位点表之外的几个常见"消费级基因检测"位点：mirobody 的位点表里**没有**它们，
#: 上传后应落成 `unresolved`——这是考"表外位点不乱猜"的素材。坐标按公开 dbSNP 手写，
#: 只求形状对（mirobody 不会拿它们做任何匹配）。
OFF_CATALOG_SITES = [
    ("rs671", "12", 112241766, 111803962, "G", "A", "ALDH2", 0.24, 0.00, "ALDH2*2 (酒精代谢)"),
    ("rs1801133", "1", 11856378, 11796321, "G", "A", "MTHFR", 0.35, 0.33, "C677T"),
    ("rs429358", "19", 45411941, 44908684, "T", "C", "APOE", 0.09, 0.15, "ε4"),
    ("rs7412", "19", 45412079, 44908822, "C", "T", "APOE", 0.08, 0.08, "ε2"),
    ("rs4988235", "2", 136608646, 135851076, "G", "A", "MCM6", 0.00, 0.70, "乳糖耐受"),
    ("rs9939609", "16", 53820527, 53786615, "T", "A", "FTO", 0.12, 0.42, "肥胖风险"),
    ("rs1800562", "6", 26093141, 26092913, "G", "A", "HFE", 0.00, 0.06, "C282Y"),
    ("rs12913832", "15", 28365618, 28120472, "A", "G", "HERC2", 0.00, 0.75, "眼色"),
]

GENOMICS_VENDORS = {
    "wegene": {"header": ["# Generated by WeGene", "# 本文件为原始基因型数据，build GRCh37"],
               "columns": "rsid\tchromosome\tposition\tgenotype", "build": "GRCh37", "ext": "txt", "no_call": "--",
               "sep": "\t", "genotype": "joined"},
    "23andme": {"header": ["# This data file generated by 23andMe at: {date}", "# Below is a text version of your data. Fields are TAB-separated",
                           "# Each line corresponds to a single SNP. For each SNP, we provide its identifier (an rsid or an internal id), its location on the reference human genome (build 37), and the genotype call oriented with respect to the plus strand on the human reference sequence."],
                "columns": "# rsid\tchromosome\tposition\tgenotype", "build": "GRCh37", "ext": "txt", "no_call": "--",
                "sep": "\t", "genotype": "joined"},
    "ancestry": {"header": ["#AncestryDNA raw data download", "#This file was generated by AncestryDNA at: {date}", "#Data was collected using AncestryDNA array version: V2.0",
                            "#Genotype data is provided in GRCh37 (build 37) coordinates"],
                 "columns": "rsid\tchromosome\tposition\tallele1\tallele2", "build": "GRCh37", "ext": "txt", "no_call": "0",
                 "sep": "\t", "genotype": "split"},
    "myheritage": {"header": ["##fileformat=MyHeritage", "##format=MHv1.0", "##timestamp={date}", "##reference=build37"],
                   "columns": "RSID,CHROMOSOME,POSITION,RESULT", "build": "GRCh37", "ext": "csv", "no_call": "--",
                   "sep": ",", "genotype": "joined_quoted"},
    "vcf": {"header": ["##fileformat=VCFv4.2", "##reference=GRCh38", "##source=mirobody-gen synthetic"],
            "columns": "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE", "build": "GRCh38", "ext": "vcf",
            "no_call": "./.", "sep": "\t", "genotype": "vcf"},
}


def catalog_sites() -> list[list]:
    """mirobody 位点表里**没有**基因标注的其余位点（489 − 41 = 448 个）：rsid/chrom/pos37/pos38/ref/alt。
    有那个 sqlite 时从它读（坐标是公开的 dbSNP 记录）；没有时沿用 spec 里已有的一份，避免漂移。"""
    import sqlite3

    db = REPO.parent / "mirobody" / "mirobody" / "res" / "genomics" / "genotype_sites.sqlite3"
    if db.is_file():
        con = sqlite3.connect(db)
        rows = con.execute("select rsid, chrom, pos37, pos38, ref, alt from sites "
                           "where gene is null or gene = '' order by rsid").fetchall()
        return [[r[0], str(r[1]), int(r[2]), int(r[3]), r[4], r[5].split(",")[0], None, 0.3, 0.3, ""] for r in rows]
    existing = RESOURCES / "genomics.json"
    if existing.is_file():
        return json.loads(existing.read_text(encoding="utf-8")).get("catalog_sites", [])
    return []


#: 交付形态的权重。数值的校准依据是参考集图像的聚合统计（resources/numbers_images.json）：
#: 图像占文件的一半；其中白底（扫描/App 增强/截图）占 51%，深底照片约 25%，灰度 42%，
#: 长边中位数 1262、p90 3072，JPEG 质量 p50=93。所以扫描类与屏幕类合起来要比"手机糊照"多。
DELIVERY = {
    "tier_weights": {"T0": 0.28, "T2": 0.22, "T3": 0.24, "T4": 0.06, "T6": 0.20},
    "scene_weights": {
        "T2": {"flatbed_scan": 30, "app_enhanced": 30, "scan_grayscale": 20, "scan_lowres": 10, "colored_slip": 10},
        "T3": {"clean_photo": 25, "phone_flat_top": 18, "wechat_photo": 20, "phone_creased": 10, "phone_bent": 5,
               "phone_oblique": 6, "phone_lowlight": 5, "phone_flash": 3, "phone_through_cover": 3, "phone_landscape": 5},
        "T4": {"photocopy": 30, "multi_gen_copy": 15, "fax_thermal": 15, "aged_archive": 10, "low_quality_print": 15,
               "heavy_compression": 15},
        "T6": {"screenshot": 50, "desktop_screenshot": 38, "screen_photo": 12},
    },
    "severity_weights": {"mild": 0.45, "moderate": 0.40, "severe": 0.15},
    #: 栅格化分辨率：扫描仪 200 dpi；手机相机原图约 3000 px 长边，按 A4 折合 260 dpi；屏幕 150 dpi。
    "dpi_of_tier": {"T2": 200, "T3": 260, "T4": 200, "T6": 150},
    #: 笔圈异常值的文档率（真实报告上医生/本人圈出的常是异常项）。
    "annotate_rate": 0.15,
}


def machine_vocab() -> dict:
    """生成物里出现的机器标识符（厂商字段名、场景名、算子名、文档种类、分层……）。
    它们不是从语料来的，但隐私闸门只认 spec 里声明过的公共词汇——不声明就会被当成来历不明的串。
    从生成器的常量表里收集，这样代码加一个场景、spec 就多一个词，两处不会漂移。"""
    import sys
    sys.path.insert(0, str(REPO))
    from mirobody_gen import devices, genomics, journal
    from mirobody_gen.render import degrade

    fields = sorted({f for v in devices.VENDORS.values() for f, _ in v["fields"].values()}
                    | {f for f, _ in devices.FALLBACK_FIELDS.values()}
                    | {v["source"] for v in devices.VENDORS.values()} | set(devices.VENDORS)
                    | {u for v in devices.VENDORS.values() for _, u in v["fields"].values()})
    op_values = sorted({"warm", "cool", "left", "right", "top", "bottom", "pink", "yellow", "blue", "cream"})
    return {
        "device_fields": fields,
        "device_metrics": sorted(devices.LOINC),
        "scenes": sorted(degrade.SCENES),
        "scene_descriptions": [s["description"] for s in degrade.SCENES.values()],
        "real_chains": sorted({s["real_chain"] for s in degrade.SCENES.values()}),
        "ops": sorted(degrade.OPS) + op_values,
        "tiers": ["T0", "T1", "T2", "T3", "T4", "T5", "T6"],
        "severities": sorted(degrade.SEVERITY),
        "containers": ["pdf", "scanpdf", "jpg", "png", "xlsx", "csv"],
        "kinds": ["lab_slip", "checkup_book", "outpatient_record", "ecg_report", "ultrasound_report",
                  "imaging_report", "home_log", "export"],
        "exam_types": ["routine", "follow-up", "clinic", "specialty", "home_log", "export"],
        "roles": ["current", "previous", "export"],
        "splits": ["main", "stress"],
        "journal_kinds": ["measurement", "symptom", "condition", "medication", "other"],
        "journal_metrics": sorted(journal.LOINC),
        "genomics_vendors": sorted(genomics.VENDOR_WEIGHTS["zh"] | genomics.VENDOR_WEIGHTS["en"]),
        "genomics_terms": ["GRCh37", "GRCh38", "called", "no_call", "unresolved", "homozygous", "heterozygous",
                           "rsid", "chromosome", "position", "genotype", "allele1", "allele2", "RSID", "CHROMOSOME",
                           "POSITION", "RESULT", "CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO", "FORMAT",
                           "SAMPLE", "PASS", "GT", "synthetic", "mirobody-gen"],
        "synthetic_marks": [degrade.SYNTHETIC_MARK, "SYNTHETIC SAMPLE", "GENERATED DATA, NOT A REAL PATIENT RECORD"],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    narratives = {
        "_source": "hand-authored",
        "_note": ("体检报告书的科室条目、具名异常所见、辅助检查叙述、总检结论与建议；门诊病历、心电图报告、"
                  "居家记录的固定文字。全部手写，是教科书式的通用医学表述，不含任何来自真实语料的内容。"
                  "异常所见的发生率只求量级对，不是流行病学估计。"),
        "_provenance": {"script": "scripts/build_profile.py", "sections": len(SECTIONS), "findings": len(FINDINGS),
                        "aux": len(AUX), "packages": len(PACKAGES)},
        # 印在纸上的每一句都要能被审、都过隐私闸门：这些键下的字符串是公共词汇。
        "_vocabulary_fields": ["zh", "en", "normal", "finding", "impression", "text", "summary", "surface",
                               "titles", "cover_titles", "checkup_no", "guide_text", "closing", "cc", "journal",
                               "causes", "measure", "durations", "hpi_templates", "followup_cc", "plan_templates",
                               "pmh_templates", "notes", "unit_row", "labels", "history_labels", "monitor", "drugs",
                               "dx_surfaces", "course_words", "history_clause", "drug_clause", "s2_clause",
                               "vitals_line", "strip", "params", "sinus", "rhythm", "mild", "moderate",
                               "five_point", "decimal", "bp_titles", "weight_titles", "label", "critical_label",
                               "critical_text", "grade_labels", "areas"],
        # 叙述模板的槽位可以填什么（隐私闸门据此把模板展开成公共词汇；没列的槽视为空）
        "_placeholders": {"side": ["左", "右"], "side_en": ["left", "right", "Left", "Right"], "lobe": ["左", "右"],
                          "lobe_en": ["left", "right"], "lobe_lung": ["上", "中", "下"], "lobe_lung_en": ["upper", "middle", "lower"],
                          "echo": ["低", "等", "囊实性混合"], "echo_en": ["hypoechoic", "isoechoic", "mixed cystic-solid"],
                          "wall": ["前", "后"], "wall_en": ["anterior", "posterior"], "density": ["磨玻璃", "实性"],
                          "density_en": ["ground-glass", "solid"], "tirads": ["2", "3", "4A"]},
        "packages": PACKAGES, "package_weights": PACKAGE_WEIGHTS, "normal_dialects": NORMAL_DIALECTS,
        "not_done": NOT_DONE, "sections": SECTIONS, "aux": AUX,
        "findings": {k: {**v, "where": list(v["where"])} for k, v in FINDINGS.items()},
        "advice": ADVICE, "lab_groups": LAB_GROUPS, "lab_ignore": sorted(LAB_IGNORE), "summary": SUMMARY,
        "acuity": ACUITY, "book": BOOK, "outpatient": OUTPATIENT, "ecg_report": ECG_REPORT, "home_log": HOME_LOG,
        "judgement": JUDGEMENT,
    }
    complaints = {
        "_source": "hand-authored",
        "_note": ("主诉与日记的症状词表。中文表面逐条对照 mirobody res/icpc3/symptoms_zh.tsv（2026-09-29），"
                  "icpc3 是那边精确匹配给出的 S 轴码；frontier 里的表面刻意不在词表里，考弃权。"),
        "_provenance": {"script": "scripts/build_profile.py", "symptoms": len(SYMPTOMS), "frontier": len(FRONTIER),
                        "mirobody_symptoms_zh_checked": "2026-09-29"},
        "_vocabulary_fields": ["zh", "en", "en_preferred", "durations", "cc", "journal", "causes", "measure"],
        "symptoms": SYMPTOMS, "frontier": FRONTIER, "pools": COMPLAINT_POOLS, "phrasing": PHRASING,
    }
    genomics = {
        "_source": "hand-authored",
        "_note": ("药物基因组位点：41 个带基因标注的位点坐标与等位基因照抄 mirobody genotype_sites.sqlite3"
                  "（dbSNP b155 common ∩ CPIC v1.60）；等位基因频率是手写近似（东亚/欧洲），mirobody 不读它们。"
                  "off_catalog 是位点表之外的常见消费级位点，上传后应为 unresolved。"),
        "_provenance": {"script": "scripts/build_profile.py", "pgx_sites": len(PGX_SITES),
                        "off_catalog": len(OFF_CATALOG_SITES), "site_table": "dbsnp-b155-common-pgx-candidate",
                        "catalog_sites_from": "mirobody genotype_sites.sqlite3 (dbSNP b155 common), gene-unlabelled rows"},
        "_vocabulary_fields": ["header", "columns", "label", "gene"],
        "columns": ["rsid", "chrom", "pos37", "pos38", "ref", "alt", "gene", "alt_freq_eas", "alt_freq_eur", "label"],
        "pgx_sites": [list(s) for s in PGX_SITES],
        "catalog_sites": catalog_sites(),
        "off_catalog": [list(s) for s in OFF_CATALOG_SITES],
        "vendors": GENOMICS_VENDORS,
    }
    print(f"科室 {len(SECTIONS)} · 辅助检查 {len(AUX)} · 具名所见 {len(FINDINGS)} · 建议 {len(ADVICE)} · "
          f"症状 {len(SYMPTOMS)}+{len(FRONTIER)} · 位点 {len(PGX_SITES)}+{len(OFF_CATALOG_SITES)}")
    if args.write:
        vocab_payload = {
            "_source": "hand-authored",
            "_note": "生成物里出现的机器标识符：厂商字段名、场景与算子名、文档种类、分层、格式枚举。由生成器的常量表收集。",
            "_provenance": {"script": "scripts/build_profile.py", "collected_from": "mirobody_gen.devices / render.degrade / journal / genomics"},
            "_vocabulary_fields": ["device_fields", "device_metrics", "scenes", "scene_descriptions", "real_chains", "ops",
                                   "tiers", "severities", "containers", "kinds", "exam_types", "roles", "splits",
                                   "journal_kinds", "journal_metrics", "genomics_vendors", "genomics_terms", "synthetic_marks"],
            **machine_vocab(),
        }
        delivery_payload = {
            "_source": "hand-authored",
            "_note": "交付形态（文本层 / 扫描 / 手机照片 / 劣化件 / 屏幕）的分层与场景权重、严重度分布、栅格化分辨率、笔圈率。"
                     "校准依据是参考集图像的聚合统计（numbers_images.json）。",
            "_provenance": {"script": "scripts/build_profile.py", "calibrated_against": "numbers_images.json"},
            "_vocabulary_fields": [],
            **DELIVERY,
        }
        for name, payload in (("narratives.json", narratives), ("complaints.json", complaints), ("genomics.json", genomics),
                              ("vocab.json", vocab_payload), ("delivery.json", delivery_payload)):
            out = RESOURCES / name
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            print(f"写入 {out}")


if __name__ == "__main__":
    main()
