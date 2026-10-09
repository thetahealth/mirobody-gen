"""Continuous-stream devices and export shapes → `resources/streams.json`.

    python3 scripts/build_streams.py           # print the inventory
    python3 scripts/build_streams.py --write   # write resources/streams.json

Everything a CGM or wearable export can carry that is a fact about the device or the file rather than
about the person: sampling interval, wear period, warm-up, reportable range, published accuracy, backfill,
the words a CSV header prints, the identifiers' formats, which phone stores an app writes to and with what
delay. Each device and format names its sources; `docs/DEVICE_FORMATS.md` is the human-readable ledger
of the same URLs with what each one established and how sure we are.

Confidence, per entry (`confidence`): `verbatim` — read in the vendor's documentation or byte-for-byte in
public raw exports; `inferred` — consistent across third-party parsers and samples but not documented;
`unsure` — a reasoned choice where evidence ran out (the ledger says which). Numbers that are modelling
choices rather than device facts (how often a signal drops, how often a wearer calibrates) are marked
`hand-set` in the field name's comment and in the ledger.

Values never live here: glucose and heart rate come from the physiology model and the day plan
(`mirobody_gen/continuous.py`).
"""

from __future__ import annotations

import argparse
import json
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
OUT = REPO / "mirobody_gen" / "resources" / "streams.json"

SRC = {
    "dexcom_api": "https://developer.dexcom.com/docs/swaggerv3/other/getestimatedglucosevaluesv3",
    "dexcom_api_overview": "https://developer.dexcom.com/docs/dexcomv3/endpoint-overview/",
    "dexcom_training": "https://www.dexcom.com/en-ca/training",
    "dexcom_backfill": "https://www.dexcom.com/en-GB/blog/troubleshoot-common-dexcom-issues-and-alerts",
    "dexcom_one_plus": "https://www.dexcom.com/en-GB/blog/introducing-dexcom-one-plus",
    "dexcom_apple_health": "https://www.dexcom.com/en-us/m/faqs/how-do-i-share-my-glucose-data-with-the-apple-health-app",
    "g7_mard": "https://pmc.ncbi.nlm.nih.gov/articles/PMC9208857/",
    "g6_mard": "https://pmc.ncbi.nlm.nih.gov/articles/PMC6422005/",
    "g7_range": "https://www.accessdata.fda.gov/cdrh_docs/pdf21/K213919.pdf",
    "umich_cgm": "https://teamdynamix.umich.edu/TDClient/210/DepressionCenter/Questions/Details/100073",
    "glyapp": "https://perso.univ-rennes1.fr/joris.heyman/glyapp_doc/html/sensors.html",
    "clarity_sample_g7": "https://github.com/GlucoTrack-Cooperatives/Diabetes_Management_Frontend",
    "clarity_sample_g6_mmol": "https://github.com/leesadie/reachout-tir",
    "clarity_sample_g6": "https://github.com/namebrandon/blood_glucose_with_tft",
    "clarity_sample_stelo": "https://github.com/iainharlow/cgm",
    "tidepool_dexcom": "https://github.com/tidepool-org/platform",
    "hk_motion": "https://developer.apple.com/documentation/healthkit/hkheartratemotioncontext",
    "hk_export_sample": "https://github.com/grll/apple-health-mcp",
    "hk_export_sample2": "https://github.com/AbhikChowdhury6/dataImport",
    "apple_hr_cadence": "https://support.apple.com/en-us/120277",
    "health_connect": "https://github.com/androidx/androidx/tree/androidx-main/health/connect/connect-client",
    "oura_openapi": "https://cloud.ouraring.com/v2/static/json/openapi-1.41.json",
    "huawei_hr": "https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/heart-rate-0000001131423780",
    "huawei_glucose": "https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/blood-glucose-0000001177423531",
    "zepp_export": "https://github.com/zoilomora/xiaomi-mi-fit-data-export",
    "libre3_manual": "https://freestyleserver.com/Payloads/IFU/2022/q2/ART46090-003_rev-A.pdf",
    "libre_8h": "https://www.freestyle.abbott/en-ph/support/what-happens-if-you-dont-scan-the-sensor-within-8-hours.html",
    "libre_ada_guide": "https://consumerguide.diabetes.org/products/freestyle-libre-14-day-system",
    "libre2_mard": "https://hcplive.com/view/fda-approves-14day-freestyle-libre-glucose-monitoring-system",
    "libre3_mard": "https://www.fiercebiotech.com/medtech/ada-abbotts-freestyle-libre-3-scores-highest-accuracy-14-day-cgm-clinical-trial",
    "libreview_settings": "https://github.com/shrugalic/LibreView_to_AppleHealth_converter",
    "libreview_tidepool": "https://github.com/tidepool-org/uploader",
    "libreview_juggluco": "https://github.com/j-kaltes/Juggluco/blob/primary/Common/src/main/cpp/export/libreviewexport.cpp",
    "libreview_sample_l3": "https://github.com/rainbowpuffpuff/agentic_health",
    "libreview_sample_us": "https://github.com/jeffreyruoss/cgm-food-repsonse-assessment",
    "libreview_sample_mmol": "https://github.com/mrvisser/qh-charts",
    "libreview_sample_uk": "https://github.com/Warren8824/cgm-data-processor",
    "libreview_sample_reader": "https://github.com/MartynK/Martysweight",
    "nightscoutloader": "https://github.com/jonmorrissey/NightscoutLoader",
    "libre_no_healthkit": "https://loopkit.github.io/loopdocs/faqs/apple-health-faqs/",
    "sibionics_guide": "https://www.diabettech.com/wp-content/uploads/2024/01/GS1_A0_CGM_App_User_Guide_English_mg-dL.pdf",
    "sibionics_zhihu": "https://zhuanlan.zhihu.com/p/1962188745005564665",
    "sibionics_xlsx_sample": "https://github.com/daedalus/agp_tool/tree/master/examples",
    "sibionics_xlsx_converter": "https://github.com/suhajdab/Sibionics-to-HealthCsvImport",
    "sibionics_clinic_sample": "https://github.com/2233158/ABPM_CGM_calculator/blob/HEAD/_build/builtin_glu_raw.csv",
    "sibionics_serials": "https://github.com/j-kaltes/Juggluco/blob/HEAD/Common/src/mobileSi/java/tk/glucodata/PhotoScan.java",
    "sibionics_apple_health": "https://apps.apple.com/cn/app/id1574026555",
    "ican_i3": "https://www.icancgm.com/products/ican-i3/",
    "ican_manual": "https://www.manualslib.com/manual/3429867/Sinocare-Ican-I3-Cgm.html",
    "ican_cn_app": "https://apps.apple.com/cn/app/%E7%88%B1%E7%9C%8B%E5%81%A5%E5%BA%B7/id1547909127",
    "glucontrol_aliases": "https://github.com/Kihaku-zhou/GlucoControl/blob/HEAD/lib/data/health/importers/sibionics_csv_importer.dart",
    "mirobody_crosswalks": "https://github.com/thetahealth/mirobody/tree/main/mirobody/res/crosswalks",
}

# ── CGM devices ──────────────────────────────────────────────────────────────
#: `dropouts_per_day` / `dropout_min` (log-uniform outage length) and `calibrations_per_day` are hand-set
#: modelling choices; everything else is a device fact with the sources listed.
DEVICES: dict[str, dict] = {
    "dexcom_g7": {
        "family": "dexcom", "maker": "Dexcom", "model": "G7", "interval_min": 5, "wear_days": 10, "grace_hours": 12,
        "warmup_min": 30, "range_mgdl": {"default": [40, 400]}, "mard_pct": 8.2, "backfill_h": 24, "time_jitter_s": 1,
        "first_tick_s": [1680, 1715], "transmitter": {"format": "digits", "length": 12},
        "dropouts_per_day": 0.3, "dropout_min": [10, 1800], "calibrations_per_day": 0.03,
        "exports": ["clarity_csv", "dexcom_api_v3"],
        "clarity": {"device_info": "Dexcom G7 Mobile App", "source_device": {"apple": "iOS G7", "android": "android G7"},
                    "watch_source": "iOS Watch"},
        "api": {"transmitterGeneration": "g7", "transmitterGenerationVariant": "g7", "displayApp": "G7"},
        "stores": {"apple": {"delay_h": 3, "unit": "mg/dL", "source_name": "Dexcom G7"},
                   "health_connect": {"delay_h": 3, "package": "com.dexcom.g7"}},
        "confidence": "verbatim (interval, wear, warm-up, range, backfill, API, CSV); inferred (android source id, "
                      "HealthKit source name, Health Connect package); unsure (HealthKit unit for mmol users)",
        "sources": [SRC[k] for k in ("dexcom_training", "dexcom_api", "dexcom_backfill", "g7_mard", "g7_range",
                                     "umich_cgm", "clarity_sample_g7", "dexcom_apple_health")],
    },
    "dexcom_g6": {
        "family": "dexcom", "maker": "Dexcom", "model": "G6", "interval_min": 5, "wear_days": 10, "grace_hours": 0,
        "warmup_min": 120, "range_mgdl": {"default": [40, 400]}, "mard_pct": 9.0, "backfill_h": 3, "time_jitter_s": 1,
        "first_tick_s": [7573, 7800], "transmitter": {"format": "alnum", "length": 6, "prefix": "8", "lifetime_days": 80},
        "dropouts_per_day": 0.3, "dropout_min": [10, 1800], "calibrations_per_day": 0.05,
        "exports": ["clarity_csv", "dexcom_api_v3"],
        "clarity": {"device_info": "Dexcom G6 Mobile App", "source_device": {"apple": "iOS G6", "android": "android G6"}},
        "api": {"transmitterGeneration": "g6", "transmitterGenerationVariant": "g6", "displayApp": "G6"},
        "stores": {"apple": {"delay_h": 3, "unit": "mg/dL", "source_name": "Dexcom G6"}},
        "confidence": "verbatim (warm-up, range, backfill, CSV tokens incl. 'android G6' lower-case, HealthKit "
                      "source name); inferred (10-day wear)",
        "sources": [SRC[k] for k in ("dexcom_training", "dexcom_api", "dexcom_backfill", "g6_mard", "umich_cgm",
                                     "clarity_sample_g6", "clarity_sample_g6_mmol")],
    },
    "libre_2": {
        "family": "libre", "maker": "Abbott", "model": "FreeStyle Libre 2", "interval_min": 15, "wear_days": 14,
        "warmup_min": 60, "range_mgdl": {"default": [40, 500], "us": [40, 400]}, "mard_pct": 9.2, "memory_h": 8,
        "scans": True, "time_jitter_s": 30, "dropouts_per_day": 0.0, "dropout_min": [10, 60],
        "app_name": "FreeStyle LibreLink", "reader_name": "FreeStyle Libre 2", "reader_share": 0.2,
        "exports": ["libreview_csv"], "stores": {},
        "confidence": "verbatim (15-min history, 14 days, 60-min warm-up, 8-hour memory, range by region, MARD, "
                      "device strings, reader serial pattern); inferred (no Abbott app writes to Apple Health or "
                      "Health Connect)",
        "sources": [SRC[k] for k in ("libre_8h", "libre_ada_guide", "libre2_mard", "libreview_sample_mmol",
                                     "libreview_sample_reader", "libreview_tidepool", "libre_no_healthkit")],
    },
    "libre_3": {
        "family": "libre", "maker": "Abbott", "model": "FreeStyle Libre 3", "interval_min": 5, "wear_days": 14,
        "warmup_min": 60, "range_mgdl": {"default": [40, 500], "us": [40, 400]}, "mard_pct": 7.9, "memory_h": None,
        "scans": True, "time_jitter_s": 30, "dropouts_per_day": 0.0, "dropout_min": [10, 60],
        "app_name": "FreeStyle Libre 3", "exports": ["libreview_csv"], "stores": {},
        "confidence": "verbatim (5-min history, 14-day memory, 2.2-27.8 mmol/L, MARD 7.9%, app views exported as "
                      "scans, +/-1 min jitter); unsure (US range 40-400)",
        "sources": [SRC[k] for k in ("libre3_manual", "libre3_mard", "umich_cgm", "libreview_sample_l3",
                                     "libreview_tidepool")],
    },
    "sibionics_gs1": {
        "family": "sibionics", "maker": "Sibionics", "model": "GS1", "interval_min": 5, "wear_days": 14,
        "warmup_min": 60, "range_mgdl": {"default": [40, 450]}, "mard_pct": 8.83, "backfill_h": 2,
        "time_jitter_s": 0, "dropouts_per_day": 0.3, "dropout_min": [10, 600], "clinic_share": 0.7,
        "exports": ["sibionics_clinic_csv", "sibionics_app_xlsx"],
        "stores": {"apple": {"since": "2024-07-01", "unit": "mmol/L", "source_name": "硅基动感", "delay_min": [1, 180]}},
        "confidence": "verbatim (5-min interval, 14 days, 1-hour warm-up, 40-450 mg/dL with readings above 450 shown "
                      "at 450, Apple Health sync from app v02.10.00.00 on 2024-07-01, LT serial pattern); inferred "
                      "(MARD 8.83%, a clinic export being this brand's); unsure (backfill, the low end of the "
                      "clamp, the app's HealthKit source name and delay)",
        "sources": [SRC[k] for k in ("sibionics_guide", "sibionics_zhihu", "sibionics_clinic_sample",
                                     "sibionics_serials", "sibionics_apple_health")],
    },
    "ican_i3": {
        "family": "none", "maker": "Sinocare", "model": "iCan i3", "interval_min": 3, "wear_days": 15,
        "warmup_min": 120, "range_mgdl": {"default": [36, 450]}, "mard_pct": 8.71, "backfill_h": 2,
        "time_jitter_s": 0, "dropouts_per_day": 0.3, "dropout_min": [10, 600], "exports": [], "stores": {},
        "no_export": "AGP and history reports (PDF, shared by e-mail) and screenshots; no readings file",
        "confidence": "verbatim (every 3 min, 15 days, 2-hour warm-up, 2.0-25.0 mmol/L, MARD 8.71%; the Chinese app "
                      "reads from Apple Health and Huawei Health rather than writing to them); inferred (no raw "
                      "export: reviewers ask for one)",
        "sources": [SRC[k] for k in ("ican_i3", "ican_manual", "ican_cn_app")],
    },
}

# ── Wearables (intraday heart rate) ──────────────────────────────────────────
#: `background_s`: seconds between background samples, a [lo, hi] range or a list of user settings to pick
#: from; `workout_s`: cadence during a recorded workout; `still_only`: background readings only while the
#: wearer is still; `charge`: daily or about weekly; `noise_bpm`: optical reading error (hand-set).
WEARABLES: dict[str, dict] = {
    "apple_watch": {"background_s": [240, 600], "workout_s": 5, "still_only": True, "charge": "daily",
                    "noise_bpm": 1.5, "confidence": "inferred (Apple white paper as quoted; real exports show 2-7 s "
                    "workout spacing); Series 12 samples as often as every 5 s all day, not modelled",
                    "sources": [SRC["apple_hr_cadence"], SRC["hk_export_sample"], SRC["hk_motion"]]},
    "huawei_watch": {"background_s": [55, 65], "workout_s": 5, "still_only": False, "charge": "weekly",
                     "noise_bpm": 2.0, "confidence": "inferred (about one sample a minute, Terra FAQ); unsure cadence "
                     "of workouts", "sources": [SRC["huawei_hr"]]},
    "mi_band": {"background_s": [[60, 60], [300, 300], [600, 600]], "workout_s": None, "still_only": False,
                "charge": "weekly", "noise_bpm": 2.0,
                "confidence": "inferred (the band's automatic heart-rate interval is a user setting of 1/5/10 min)",
                "sources": [SRC["zepp_export"]]},
    "android_watch": {"background_s": [[60, 60], [600, 600]], "workout_s": 5, "still_only": False, "charge": "daily",
                      "noise_bpm": 2.0, "confidence": "unsure (continuous vs every-10-minutes setting)",
                      "sources": [SRC["health_connect"]]},
    "oura_ring": {"background_s": [300, 300], "workout_s": 5, "still_only": False, "charge": "weekly",
                  "noise_bpm": 1.0, "confidence": "inferred (about 300 s at rest, ~5 s bursts in workouts)",
                  "sources": [SRC["oura_openapi"]]},
}

# ── Export formats ───────────────────────────────────────────────────────────
FORMATS: dict[str, dict] = {
    "clarity_csv": {
        "header_mgdl": ["Index", "Timestamp (YYYY-MM-DDThh:mm:ss)", "Event Type", "Event Subtype", "Patient Info",
                        "Device Info", "Source Device ID", "Glucose Value (mg/dL)", "Insulin Value (u)",
                        "Carb Value (grams)", "Duration (hh:mm:ss)", "Glucose Rate of Change (mg/dL/min)",
                        "Transmitter Time (Long Integer)", "Transmitter ID"],
        "header_mmol": ["Index", "Timestamp (YYYY-MM-DDThh:mm:ss)", "Event Type", "Event Subtype", "Patient Info",
                        "Device Info", "Source Device ID", "Glucose Value (mmol/L)", "Insulin Value (u)",
                        "Carb Value (grams)", "Duration (hh:mm:ss)", "Glucose Rate of Change (mmol/L/min)",
                        "Transmitter Time (Long Integer)", "Transmitter ID"],
        "alert_order": ["Fall", "High", "Low", "Signal Loss", "Rise", "Urgent Low", "Urgent Low Soon"],
        "alerts_mgdl": {"Fall": {"rate": "3"}, "High": {"glucose": "200"}, "Low": {"glucose": "70"},
                        "Signal Loss": {"duration": "00:20:00"}, "Rise": {"rate": "3"},
                        "Urgent Low": {"glucose": "55"}, "Urgent Low Soon": {"glucose": "55"}},
        "alerts_mmol": {"Fall": {"rate": "0.2"}, "High": {"glucose": "12.0"}, "Low": {"glucose": "5.0"},
                        "Signal Loss": {"duration": "00:20:00"}, "Rise": {"rate": "0.2"},
                        "Urgent Low": {"glucose": "3.1"}, "Urgent Low Soon": {"glucose": "3.1"}},
        "confidence": "verbatim (header, preamble order, alert values, ragged rows, BOM, quoting); inferred (CRLF)",
        "sources": [SRC[k] for k in ("umich_cgm", "glyapp", "clarity_sample_g7", "clarity_sample_g6",
                                     "clarity_sample_g6_mmol", "clarity_sample_stelo")],
    },
    "libreview_csv": {
        "title": ["Glucose Data", "Generated on", "Generated by"],
        "header": {
            "en_US": ["Device", "Serial Number", "Device Timestamp", "Record Type", "Historic Glucose {unit}",
                      "Scan Glucose {unit}", "Non-numeric Rapid-Acting Insulin", "Rapid-Acting Insulin (units)",
                      "Non-numeric Food", "Carbohydrates (grams)", "Carbohydrates (servings)",
                      "Non-numeric Long-Acting Insulin", "Long-Acting Insulin (units)", "Notes",
                      "Strip Glucose {unit}", "Ketone mmol/L", "Meal Insulin (units)", "Correction Insulin (units)",
                      "User Change Insulin (units)"],
            "en_GB": ["Device", "Serial Number", "Device Timestamp", "Record Type", "Historic Glucose {unit}",
                      "Scan Glucose {unit}", "Non-numeric Rapid-Acting Insulin", "Rapid-Acting Insulin (units)",
                      "Non-numeric Food", "Carbohydrates (grams)", "Carbohydrates (servings)",
                      "Non-numeric Long-Acting Insulin", "Long-Acting Insulin Value (units)", "Notes",
                      "Strip Glucose {unit}", "Ketone mmol/L", "Meal Insulin (units)", "Correction Insulin (units)",
                      "User Change Insulin (units)"]},
        #: Account settings by language group and unit: date order, clock, header variant. Month-first and
        #: 12-hour are LibreView's defaults; a UK account shows day-first, 24-hour.
        "accounts": {"en": {"mg/dL": {"date": "MDY", "clock": "12h", "header": "en_US"},
                            "mmol/L": {"date": "DMY", "clock": "24h", "header": "en_GB"}},
                     "ja": {"mg/dL": {"date": "MDY", "clock": "12h", "header": "en_US"},
                            "mmol/L": {"date": "DMY", "clock": "24h", "header": "en_US"}},
                     "zh": {"mg/dL": {"date": "MDY", "clock": "12h", "header": "en_US"},
                            "mmol/L": {"date": "DMY", "clock": "24h", "header": "en_GB"}}},
        "confidence": "verbatim (title line, headers, record types 0/1/5/6, value formats, grouping by serial and "
                      "type, CRLF, no BOM); inferred (LO written as the floor 40 / 2.2); unsure (HI written as the "
                      "ceiling; a Japanese or Chinese account's header language)",
        "sources": [SRC[k] for k in ("umich_cgm", "libreview_settings", "libreview_tidepool", "libreview_juggluco",
                                     "libreview_sample_l3", "libreview_sample_us", "libreview_sample_mmol",
                                     "libreview_sample_uk", "nightscoutloader")],
    },
    "sibionics_clinic_csv": {
        #: The last three header cells are a label, a full-width colon and the value (institution, patient,
        #: device). Kept apart here so that no label is stored next to its colon: the repository's privacy
        #: hook rejects a name label followed by a colon anywhere in a tracked file.
        "header": ["血糖值(mmol/L)", "采集时间"], "header_labels": ["机构名称", "姓名", "设备名称"], "label_sep": "：",
        "confidence": "verbatim (header with metadata after full-width colons, one-decimal mmol/L, "
                      "YYYY-MM-DD HH:MM:SS, newest first, constant seconds); inferred (brand, from the LT serial); "
                      "unsure (BOM and line endings)",
        "sources": [SRC[k] for k in ("sibionics_clinic_sample", "sibionics_serials", "glucontrol_aliases")],
    },
    "sibionics_app_xlsx": {
        "file_name": "SiSensingCGM-01.20.00.00.xls", "sheet": "Sensor Glucose",
        "header": ["Time", "Sensor Reading({unit})"],
        "confidence": "verbatim (file name with .xls over OOXML bytes, sheet name, string cells, oldest first, "
                      "DD-MM-YYYY HH:MM GMT+H, mg/dL header); inferred (mmol/L header); unsure (GMT suffix at "
                      "offset zero)",
        "sources": [SRC[k] for k in ("sibionics_xlsx_sample", "sibionics_xlsx_converter", "sibionics_guide")],
    },
    "dexcom_api_v3": {"confidence": "verbatim (documented sample and enums); unsure (record order)",
                      "sources": [SRC["dexcom_api"], SRC["dexcom_api_overview"], SRC["tidepool_dexcom"]]},
    "apple_export": {
        "locale": {"zh": "zh_CN", "en": "en_GB", "ja": "ja_JP"},
        "watch_hardware": ["Watch6,2", "Watch6,7", "Watch7,2"],
        "phone_hardware": ["iPhone14,2", "iPhone15,3", "iPhone16,1"],
        "os_versions": {"watch": {"2021": "8.1", "2022": "9.1", "2023": "10.1", "2024": "11.1", "2025": "26.0",
                                  "2026": "26.4"},
                        "phone": {"2021": "15.1", "2022": "16.1", "2023": "17.1", "2024": "18.1", "2025": "26.0",
                                  "2026": "26.4"}},
        "confidence": "verbatim (prolog, DTD lines used, attribute order, date format, device string, metadata keys, "
                      "HealthKit mmol unit); inferred (hardware ids, OS version per year, resting-HR span)",
        "sources": [SRC[k] for k in ("hk_export_sample", "hk_export_sample2", "hk_motion", "dexcom_apple_health")],
    },
    "oura_api_v2_heartrate": {"confidence": "verbatim (fields, source enum); inferred (+00:00 timestamp form)",
                              "sources": [SRC["oura_openapi"]]},
    "zepp_life_heartrate_auto_csv": {"confidence": "inferred (four independent parsers agree; no official doc)",
                                     "sources": [SRC["zepp_export"]]},
}

#: The vendor field a phone store files a reading under (mirobody's crosswalks; Xiaomi has no glucose type,
#: so the catalogue name is sent, as `devices.FALLBACK_FIELDS` does).
STORE_FIELDS = {"glucose": {"apple": "HKQuantityTypeIdentifierBloodGlucose", "health_connect": "BloodGlucoseRecord",
                            "huawei": "com.huawei.instantaneous.blood_glucose", "xiaomi": "bloodGlucoses"}}

#: Which CGM a language group's wearers use and in what unit. Hand-set shares (no public market split at
#: this granularity); units follow national practice: mmol/L in mainland China and the UK, mg/dL in the US
#: and Japan, so the English-speaking group is split.
ADOPTION = {
    "zh": {"cgm": {"sibionics_gs1": 3, "ican_i3": 1}, "glucose_unit": {"mmol/L": 1}},
    "en": {"cgm": {"dexcom_g7": 3, "dexcom_g6": 1, "libre_2": 2, "libre_3": 2, "sibionics_gs1": 1},
           "glucose_unit": {"mg/dL": 1, "mmol/L": 1}},
    "ja": {"cgm": {"libre_2": 2, "libre_3": 1, "dexcom_g7": 1}, "glucose_unit": {"mg/dL": 1}},
}

HAZARD_CLASSES = {
    "stream.gap.warmup": "no readings while a new sensor warms up",
    "stream.gap.signal_loss": "readings missing after an outage longer than the sensor can backfill",
    "stream.gap.not_scanned": "history lost because a scan-based sensor was not scanned within its memory",
    "stream.backfilled": "an outage the receiver recovered: readings present, arrived late",
    "stream.compression_low": "a false low while the wearer lay on the sensor",
    "stream.sensor_failed": "a sensor that ended before its wear period",
    "stream.scan_duplicate": "a scan reading next to the historic reading of the same minutes",
    "stream.out_of_range_text": "a reading printed as a word (Low/High, LO/HI) instead of a number",
    "stream.out_of_range_sentinel": "a reading coded as a number outside the range (39, 401) with a status",
    "stream.out_of_range_clamped": "a reading past the range written as the range limit, indistinguishable from it",
    "stream.unsorted_rows": "rows grouped by device and record type, not in time order",
    "stream.locale_date_order": "day-first dates that parse as month-first when the day is 12 or less",
    "stream.newest_first": "rows in reverse time order",
    "stream.metadata_in_header": "institution, patient and device written into header cells after the column names",
    "stream.extension_mismatch": "a file named .xls whose bytes are an OOXML workbook",
    "stream.unit_healthkit_mmol": "HealthKit's mmol unit string with the molar mass inside it",
    "stream.no_export": "the app shows the readings but hands over only reports and screenshots",
    "stream.local_time_no_offset": "wall-clock timestamps with no UTC offset",
    "stream.utc_and_local_pair": "each record carries UTC and local time; they must not be double-shifted",
    "stream.utc_only": "UTC timestamps; the local day must be reconstructed",
    "stream.unit_mmol": "glucose in mmol/L where the catalogue unit is mg/dL",
    "stream.ragged_rows": "CSV rows of unequal length",
    "stream.bom": "a byte order mark before the first header",
    "stream.store_delay": "the app writes to the phone store hours after the reading",
    "stream.off_wrist": "a span with no samples because the device was off the body",
    "stream.workout_burst": "sampling jumps from minutes to seconds during a workout",
}


def build() -> dict:
    return {
        "_source": "public-standard",
        "_note": ("Device facts and export shapes for continuous streams, each with its sources and confidence. "
                  "Mixed provenance by design: vendor documentation and public raw exports (format tokens), "
                  "published accuracy studies, and a few hand-set modelling rates marked as such."),
        "_provenance": {"script": "scripts/build_streams.py", "ledger": "docs/DEVICE_FORMATS.md", "sources": SRC},
        "_vocabulary_fields": ["formats", "devices", "wearables", "store_fields", "hazard_classes"],
        "devices": DEVICES,
        "wearables": WEARABLES,
        "formats": FORMATS,
        "store_fields": STORE_FIELDS,
        "adoption": ADOPTION,
        "hazard_classes": HAZARD_CLASSES,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="write resources/streams.json")
    args = ap.parse_args()
    data = build()
    for key, dev in data["devices"].items():
        print(f"{key:<14} {dev['maker']} {dev['model']}: every {dev['interval_min']} min, {dev['wear_days']} d, "
              f"warm-up {dev['warmup_min']} min, MARD {dev['mard_pct']}%, exports {', '.join(dev['exports'])}")
    print(f"{len(data['wearables'])} wearables · {len(data['formats'])} formats · {len(SRC)} sources")
    if args.write:
        OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"→ {OUT.relative_to(REPO)}")


if __name__ == "__main__":
    main()
