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
    "carelink_g4_sample": "https://github.com/zinojeng/CGM/blob/HEAD/140692Ho.csv",
    "carelink_780g_sample": "https://github.com/madleina/aidR/blob/HEAD/inst/extdata/carelink_example.csv",
    "carelink_2018_sample": "https://github.com/danny98m/GluGo2.0/blob/HEAD/csvData/csvInData/Boylan_Medtronic_1.csv",
    "carelink_parser": "https://github.com/odiak/carelink-csv-visualizer/blob/HEAD/src/parse.ts",
    "carelink_nsloader": "https://github.com/gh-davidr/NightscoutLoader/blob/HEAD/src/main/java/entity/DBResultMedtronicNew.java",
    "carelink_schema": "https://github.com/etauker-projects/home-automation/blob/HEAD/scripts/health-data/src/model/schema.ts",
    "guardian4_ada": "https://consumerguide.diabetes.org/node/1976",
    "ns_entries": "https://github.com/nightscout/cgm-remote-monitor/blob/master/lib/server/entries.js",
    "ns_api": "https://github.com/nightscout/cgm-remote-monitor/blob/master/lib/api/entries/index.js",
    "ns_share2": "https://github.com/nightscout/share2nightscout-bridge/blob/master/index.js",
    "ns_llu": "https://github.com/timoschlueter/nightscout-librelink-up/blob/main/src/nightscout/apiv1.ts",
    "ns_llu_config": "https://github.com/timoschlueter/nightscout-librelink-up/blob/main/src/config.ts",
    "xdrip_uploader": "https://github.com/NightscoutFoundation/xDrip/blob/master/app/src/main/java/com/eveningoutpost/dexdrip/utilitymodels/NightscoutUploader.java",
    "xdrip_csv": "https://github.com/NightscoutFoundation/xDrip/blob/master/app/src/main/java/com/eveningoutpost/dexdrip/utils/DatabaseUtil.java",
    "pydexcom": "https://github.com/gagebenne/pydexcom",
    "llu_fixture": "https://github.com/timoschlueter/nightscout-librelink-up/blob/main/tests/data/graph.json",
    "pylibrelinkup": "https://github.com/robberwick/pylibrelinkup",
    "yuwell_uk": "https://www.yuwellanytime.co.uk/product/yuwell-anytime-sensor/",
    "yuwell_glucodroid": "https://github.com/GlucoDroid/app/blob/HEAD/Common/src/main/java/tk/glucodata/drivers/anytime/AnytimeConstants.kt",
    "yuwell_trial": "https://pmc.ncbi.nlm.nih.gov/articles/PMC13366774/",
    "aidex_cn": "https://www.microtechmd.com/cn/products/cgms",
    "aidex_cloud": "https://github.com/yichi2077/CGM-Agent/blob/HEAD/src/hermes_cgm_agent/services/aidex/mapper.py",
    "tidepool_export": "https://github.com/tidepool-org/export",
    "tidepool_data_tools": "https://github.com/tidepool-org/node-data-tools",
    "tidepool_blip_dialog": "https://github.com/tidepool-org/blip/blob/develop/app/components/ExportDialog.js",
    "tidepool_platform_glucose": "https://github.com/tidepool-org/platform/blob/master/data/blood/glucose/glucose.go",
    "tidepool_sample_xlsx": "https://github.com/madleina/aidR/blob/HEAD/inst/extdata/tidepool_example.xlsx",
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
        "llu": {"pt": 3, "dtid": 40067, "app_version": "2.10.1"},
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
        "llu": {"pt": 4, "dtid": 40068, "app_version": "3.4.1.7374"},
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
        "report": {"styles": {"zh": "sibionics_cn", "en": "agp_v5"}, "clinic": "cgm_sheet_2017"},
        "stores": {"apple": {"since": "2024-07-01", "unit": "mmol/L", "source_name": "硅基动感", "delay_min": [1, 180]}},
        "confidence": "verbatim (5-min interval, 14 days, 1-hour warm-up, 40-450 mg/dL with readings above 450 shown "
                      "at 450, Apple Health sync from app v02.10.00.00 on 2024-07-01, LT serial pattern); inferred "
                      "(MARD 8.83%, a clinic export being this brand's); unsure (backfill, the low end of the "
                      "clamp, the app's HealthKit source name and delay)",
        "sources": [SRC[k] for k in ("sibionics_guide", "sibionics_zhihu", "sibionics_clinic_sample",
                                     "sibionics_serials", "sibionics_apple_health")],
    },
    "guardian_4": {
        "family": "medtronic", "maker": "Medtronic", "model": "Guardian 4", "interval_min": 5, "wear_days": 7,
        "warmup_min": 120, "range_mgdl": {"default": [40, 400]}, "mard_pct": 10.6, "backfill_h": 0,
        "time_jitter_s": 5, "dropouts_per_day": 0.3, "dropout_min": [10, 600], "calibrations_per_day": 0.05,
        "exports": ["carelink_csv"], "stores": {}, "carelink": {"system": "Guardian™ 4 system"},
        "confidence": "verbatim (5-min readings, CareLink layout of a Guardian 4 system export); secondary "
                      "(7 days, up to 2 h warm-up, MARD 10.6% adults); inferred (40-400 mg/dL); unsure (backfill, "
                      "how a reading past the range is written: we leave it out)",
        "sources": [SRC[k] for k in ("carelink_g4_sample", "guardian4_ada", "carelink_parser")],
    },
    "ican_i3": {
        "family": "none", "maker": "Sinocare", "model": "iCan i3", "interval_min": 3, "wear_days": 15,
        "warmup_min": 120, "range_mgdl": {"default": [36, 450]}, "mard_pct": 8.71, "backfill_h": 2,
        "time_jitter_s": 0, "dropouts_per_day": 0.3, "dropout_min": [10, 600], "exports": [], "stores": {},
        "no_export": "AGP and history reports (PDF, shared by e-mail) and screenshots; no readings file",
        "report": {"styles": {"zh": "agp_cn2023", "en": "agp_v5"}},
        "confidence": "verbatim (every 3 min, 15 days, 2-hour warm-up, 2.0-25.0 mmol/L, MARD 8.71%; the Chinese app "
                      "reads from Apple Health and Huawei Health rather than writing to them); inferred (no raw "
                      "export: reviewers ask for one)",
        "sources": [SRC[k] for k in ("ican_i3", "ican_manual", "ican_cn_app")],
    },
    "yuwell_ct3": {
        "family": "none", "maker": "Yuwell", "model": "Anytime CT3", "interval_min": 3, "wear_days": 14,
        "warmup_min": 60, "range_mgdl": {"default": [31, 500]}, "mard_pct": 9.1, "backfill_h": 2,
        "time_jitter_s": 0, "dropouts_per_day": 0.3, "dropout_min": [10, 600], "exports": [], "stores": {},
        "no_export": "daily, weekly and monthly reports in the app and shared through its follower app; no readings file",
        "report": {"styles": {"zh": "agp_cn2023", "en": "agp_v5"}},
        "confidence": "verbatim (every 3 min, 14 days, MARD 9.1%); inferred (60-minute warm-up, reports only); unsure "
                      "(1.7-27.8 mmol/L, from a search snippet of the CE manual)",
        "sources": [SRC[k] for k in ("yuwell_uk", "yuwell_glucodroid", "yuwell_trial")],
    },
    "aidex": {
        "family": "none", "maker": "MicroTech", "model": "AiDEX", "interval_min": 5, "wear_days": 14,
        "warmup_min": 60, "range_mgdl": {"default": [36, 450]}, "mard_pct": 9.08, "backfill_h": 2,
        "time_jitter_s": 0, "dropouts_per_day": 0.3, "dropout_min": [10, 600], "exports": [], "stores": {},
        "no_export": "reports in the app and a WeChat mini-program; reviewers ask for a CSV export",
        "report": {"styles": {"zh": "agp_cn2023", "en": "agp_v5"}},
        "confidence": "verbatim (every 5 min, 14 days, 2.0-25.0 mmol/L); inferred (60-minute warm-up, MARD 9.08%, "
                      "reports only)",
        "sources": [SRC[k] for k in ("aidex_cn", "aidex_cloud")],
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

#: Tidepool's export, written by ExcelJS 4.4.0 through @tidepool/data-tools 2.5.0. The sheet columns are the
#: library's config; the column widths, page set-up and style sheets are copied from the library's own output
#: on placeholder data (MIT-licensed boilerplate), so a generated workbook differs from a real one only in
#: data, dates and the omitted Office theme part.
TIDEPOOL_SHEETS = {
 "CGM": [
  {
   "header": "Zulu Time",
   "kind": "date"
  },
  {
   "header": "Local Time",
   "kind": "date"
  },
  {
   "header": "Device Time",
   "kind": "date"
  },
  {
   "header": "Tidepool Data Type",
   "kind": "text",
   "field": "type"
  },
  {
   "header": "Value",
   "kind": "value",
   "field": "value"
  },
  {
   "header": "Units",
   "kind": "text",
   "field": "units"
  },
  {
   "header": "Timezone Offset",
   "kind": "text",
   "field": "timezoneOffset"
  },
  {
   "header": "Conversion Offset",
   "kind": "text",
   "field": "conversionOffset"
  },
  {
   "header": "Clock Drift Offset",
   "kind": "text",
   "field": "clockDriftOffset"
  },
  {
   "header": "Device Id",
   "kind": "text",
   "field": "deviceId"
  },
  {
   "header": "Id",
   "kind": "text",
   "field": "id"
  },
  {
   "header": "Upload Id",
   "kind": "text",
   "field": "uploadId"
  },
  {
   "header": "Payload",
   "kind": "text",
   "field": "payload"
  },
  {
   "header": "Annotations",
   "kind": "text",
   "field": "annotations"
  }
 ],
 "Upload": [
  {
   "header": "Zulu Time",
   "kind": "date"
  },
  {
   "header": "Local Time",
   "kind": "date"
  },
  {
   "header": "Device Time",
   "kind": "date"
  },
  {
   "header": "Computer Time",
   "kind": "date"
  },
  {
   "header": "Timezone",
   "kind": "text",
   "field": "timezone"
  },
  {
   "header": "Time Processing",
   "kind": "text",
   "field": "timeProcessing"
  },
  {
   "header": "Tidepool Data Type",
   "kind": "text",
   "field": "type"
  },
  {
   "header": "Version",
   "kind": "text",
   "field": "version"
  },
  {
   "header": "Device Serial Number",
   "kind": "text",
   "field": "deviceSerialNumber"
  },
  {
   "header": "Device Model",
   "kind": "text",
   "field": "deviceModel"
  },
  {
   "header": "Device Manufacturers",
   "kind": "text",
   "field": "deviceManufacturers"
  },
  {
   "header": "Device Tags",
   "kind": "text",
   "field": "deviceTags"
  },
  {
   "header": "Timezone Offset",
   "kind": "text",
   "field": "timezoneOffset"
  },
  {
   "header": "Conversion Offset",
   "kind": "text",
   "field": "conversionOffset"
  },
  {
   "header": "Device Id",
   "kind": "text",
   "field": "deviceId"
  },
  {
   "header": "Id",
   "kind": "text",
   "field": "id"
  },
  {
   "header": "By User",
   "kind": "text",
   "field": "byUser"
  },
  {
   "header": "Upload Id",
   "kind": "text",
   "field": "uploadId"
  },
  {
   "header": "Payload",
   "kind": "text",
   "field": "payload"
  },
  {
   "header": "Annotations",
   "kind": "text",
   "field": "annotations"
  }
 ]
}
TIDEPOOL_COLS_XML = {
 "CGM": "<cols><col min=\"1\" max=\"3\" width=\"18\" style=\"1\" customWidth=\"1\"/><col min=\"4\" max=\"4\" width=\"15\" customWidth=\"1\"/><col min=\"5\" max=\"5\" width=\"10\" style=\"2\" customWidth=\"1\"/><col min=\"6\" max=\"6\" width=\"10\" customWidth=\"1\"/><col min=\"7\" max=\"9\" width=\"14\" customWidth=\"1\"/><col min=\"10\" max=\"14\" width=\"22\" customWidth=\"1\"/></cols>",
 "Upload": "<cols><col min=\"1\" max=\"4\" width=\"18\" style=\"1\" customWidth=\"1\"/><col min=\"5\" max=\"6\" width=\"22\" customWidth=\"1\"/><col min=\"7\" max=\"7\" width=\"15\" customWidth=\"1\"/><col min=\"8\" max=\"12\" width=\"22\" customWidth=\"1\"/><col min=\"13\" max=\"14\" width=\"14\" customWidth=\"1\"/><col min=\"15\" max=\"20\" width=\"22\" customWidth=\"1\"/></cols>"
}
TIDEPOOL_PAGE_XML = '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/><pageSetup orientation="portrait" horizontalDpi="4294967295" verticalDpi="4294967295" scale="100" fitToWidth="1" fitToHeight="1"/>'
TIDEPOOL_STYLES = {
 "mgdl": "<styleSheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" xmlns:mc=\"http://schemas.openxmlformats.org/markup-compatibility/2006\" mc:Ignorable=\"x14ac x16r2\" xmlns:x14ac=\"http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac\" xmlns:x16r2=\"http://schemas.microsoft.com/office/spreadsheetml/2015/02/main\"><numFmts count=\"1\"><numFmt numFmtId=\"164\" formatCode=\"yyyy-mm-dd hh:mm:ss\"/></numFmts><fonts count=\"2\" x14ac:knownFonts=\"1\"><font><color theme=\"1\"/><family val=\"2\"/><scheme val=\"minor\"/><sz val=\"11\"/><name val=\"Calibri\"/></font><font><b/></font></fonts><fills count=\"2\"><fill><patternFill patternType=\"none\"/></fill><fill><patternFill patternType=\"gray125\"/></fill></fills><borders count=\"1\"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count=\"1\"><xf numFmtId=\"0\" fontId=\"0\" fillId=\"0\" borderId=\"0\"/></cellStyleXfs><cellXfs count=\"6\"><xf numFmtId=\"0\" fontId=\"0\" fillId=\"0\" borderId=\"0\" xfId=\"0\"/><xf numFmtId=\"164\" fontId=\"0\" fillId=\"0\" borderId=\"0\" xfId=\"0\" applyNumberFormat=\"1\"/><xf numFmtId=\"1\" fontId=\"0\" fillId=\"0\" borderId=\"0\" xfId=\"0\" applyNumberFormat=\"1\"/><xf numFmtId=\"0\" fontId=\"1\" fillId=\"0\" borderId=\"0\" xfId=\"0\" applyFont=\"1\"/><xf numFmtId=\"164\" fontId=\"1\" fillId=\"0\" borderId=\"0\" xfId=\"0\" applyNumberFormat=\"1\" applyFont=\"1\"/><xf numFmtId=\"1\" fontId=\"1\" fillId=\"0\" borderId=\"0\" xfId=\"0\" applyNumberFormat=\"1\" applyFont=\"1\"/></cellXfs><cellStyles count=\"1\"><cellStyle name=\"Normal\" xfId=\"0\" builtinId=\"0\"/></cellStyles><dxfs count=\"0\"/><tableStyles count=\"0\" defaultTableStyle=\"TableStyleMedium2\" defaultPivotStyle=\"PivotStyleLight16\"/><extLst><ext uri=\"{EB79DEF2-80B8-43e5-95BD-54CBDDF9020C}\" xmlns:x14=\"http://schemas.microsoft.com/office/spreadsheetml/2009/9/main\"><x14:slicerStyles defaultSlicerStyle=\"SlicerStyleLight1\"/></ext><ext uri=\"{9260A510-F301-46a8-8635-F512D64BE5F5}\" xmlns:x15=\"http://schemas.microsoft.com/office/spreadsheetml/2010/11/main\"><x15:timelineStyles defaultTimelineStyle=\"TimeSlicerStyleLight1\"/></ext></extLst></styleSheet>",
 "mmol": "<styleSheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" xmlns:mc=\"http://schemas.openxmlformats.org/markup-compatibility/2006\" mc:Ignorable=\"x14ac x16r2\" xmlns:x14ac=\"http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac\" xmlns:x16r2=\"http://schemas.microsoft.com/office/spreadsheetml/2015/02/main\"><numFmts count=\"2\"><numFmt numFmtId=\"164\" formatCode=\"yyyy-mm-dd hh:mm:ss\"/><numFmt numFmtId=\"165\" formatCode=\"0.0\"/></numFmts><fonts count=\"2\" x14ac:knownFonts=\"1\"><font><color theme=\"1\"/><family val=\"2\"/><scheme val=\"minor\"/><sz val=\"11\"/><name val=\"Calibri\"/></font><font><b/></font></fonts><fills count=\"2\"><fill><patternFill patternType=\"none\"/></fill><fill><patternFill patternType=\"gray125\"/></fill></fills><borders count=\"1\"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count=\"1\"><xf numFmtId=\"0\" fontId=\"0\" fillId=\"0\" borderId=\"0\"/></cellStyleXfs><cellXfs count=\"6\"><xf numFmtId=\"0\" fontId=\"0\" fillId=\"0\" borderId=\"0\" xfId=\"0\"/><xf numFmtId=\"164\" fontId=\"0\" fillId=\"0\" borderId=\"0\" xfId=\"0\" applyNumberFormat=\"1\"/><xf numFmtId=\"165\" fontId=\"0\" fillId=\"0\" borderId=\"0\" xfId=\"0\" applyNumberFormat=\"1\"/><xf numFmtId=\"0\" fontId=\"1\" fillId=\"0\" borderId=\"0\" xfId=\"0\" applyFont=\"1\"/><xf numFmtId=\"164\" fontId=\"1\" fillId=\"0\" borderId=\"0\" xfId=\"0\" applyNumberFormat=\"1\" applyFont=\"1\"/><xf numFmtId=\"165\" fontId=\"1\" fillId=\"0\" borderId=\"0\" xfId=\"0\" applyNumberFormat=\"1\" applyFont=\"1\"/></cellXfs><cellStyles count=\"1\"><cellStyle name=\"Normal\" xfId=\"0\" builtinId=\"0\"/></cellStyles><dxfs count=\"0\"/><tableStyles count=\"0\" defaultTableStyle=\"TableStyleMedium2\" defaultPivotStyle=\"PivotStyleLight16\"/><extLst><ext uri=\"{EB79DEF2-80B8-43e5-95BD-54CBDDF9020C}\" xmlns:x14=\"http://schemas.microsoft.com/office/spreadsheetml/2009/9/main\"><x14:slicerStyles defaultSlicerStyle=\"SlicerStyleLight1\"/></ext><ext uri=\"{9260A510-F301-46a8-8635-F512D64BE5F5}\" xmlns:x15=\"http://schemas.microsoft.com/office/spreadsheetml/2010/11/main\"><x15:timelineStyles defaultTimelineStyle=\"TimeSlicerStyleLight1\"/></ext></extLst></styleSheet>"
}

# ── Reports (PDF) ────────────────────────────────────────────────────────────
#: Report styles: the words, the metrics, their order and the goals a report prints. Sources and confidence
#: per style in `_sources`/`_confidence`; `docs/DEVICE_FORMATS.md` ("Reports") has the detail.
_WEEK_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_WEEK_ZH = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
_V5_PALETTE = {  # 2025 IDC TITR report PDF (extracted) and the v5.0 sample image (sampled); see the ledger
    "title": "#5A4999", "target_line": "#4B9E53", "daily_line": "#238E44",
    "bands": {"very_high": "#F68C4A", "high": "#FEC00E", "target": "#54B861", "low": "#EF3D2D", "very_low": "#982B2F"},
    "outer": {"very_high": "#FEDFC8", "high": "#FFECC5", "target": "#D4E9D2", "low": "#F9C4B8", "very_low": "#E8B7B3"},
    "inner": {"very_high": "#F9B197", "high": "#FFD67D", "target": "#9BD09B", "low": "#F37455", "very_low": "#C2575C"},
    "median": {"very_high": "#E0662C", "high": "#E5A50A", "target": "#238E44", "low": "#D7261E", "very_low": "#7E262F"}}
_BLUE = {"very_high": "#DFE3EE", "high": "#DFE3EE", "target": "#DFE3EE", "low": "#DFE3EE", "very_low": "#DFE3EE"}
_SIB_PALETTE = {  # the Chinese Sibionics report: navy median, medium-blue 25-75%, light-blue 5-95% (colours sampled)
    "title": "#1F3B6F", "target_line": "#3E9B4F", "daily_line": "#2D5DA8",
    "bands": {"very_high": "#F08A3C", "high": "#F6C343", "target": "#4CAF50", "low": "#E53935", "very_low": "#8E1B1B"},
    "outer": _BLUE, "inner": {k: "#8FA8D6" for k in _BLUE}, "median": {k: "#14306B" for k in _BLUE}}
_AXIS = {"ceiling_mgdl": 350, "ticks_mgdl": [54, 70, 180, 250, 350], "ceiling_mmol": 21.0,
         "ticks_mmol": [3.0, 3.9, 10.0, 13.9, 21.0]}
_TICKS = {"mgdl": {"very_high": "250", "high": "180", "target": "70", "low": "54"},
          "mmol": {"very_high": "13.9", "high": "10.0", "target": "3.9", "low": "3.0"}}
_ZH_X = ["00:00", "03:00", "06:00", "09:00", "12:00", "15:00", "18:00", "21:00", "00:00"]

AGP_V5 = {
    "layout": "agp", "title": "AGP Report: Continuous Glucose Monitoring",
    "header": ["{name}", "{days} Days: {start} - {end}", "Time CGM Active: {active}%"],
    "date_start": "{B} {d}", "date_end": "{B} {d}, {Y}",
    "labels": {"metrics_title": "Glucose Metrics", "tir_title": "Time in Ranges",
               "tir_goals": "Goals for Type 1 and Type 2 Diabetes", "agp_title": "Ambulatory Glucose Profile (AGP)",
               "agp_note": "AGP is a summary of glucose values from the report period, with median (50%) and other "
                           "percentiles shown as if they occurred in a single day.",
               "daily_title": "Daily Glucose Profiles",
               "daily_note": "Each daily profile represents a midnight-to-midnight period.", "target_range": "Target Range"},
    "metrics": [{"key": "mean", "label": "Average Glucose", "goal": "Goal: <8.6 mmol/L", "goal_mgdl": "Goal: <154 mg/dL"},
                {"key": "gmi", "label": "Glucose Management Indicator (GMI)", "goal": "Goal: <7%", "goal_mgdl": "Goal: <7%"},
                {"key": "cv", "label": "Glucose Variability", "notes": ["Defined as percent coefficient of variation"],
                 "goal": "Goal: ≤36%", "goal_mgdl": "Goal: ≤36%"}],
    "bands_mgdl": {"very_high": ["Very High", "Goal: <5%"], "high": ["High", ""], "target": ["Target", "Goal: >70%"],
                   "low": ["Low", ""], "very_low": ["Very Low", "Goal: <1%"]},
    "bands_mmol": {"very_high": ["Very High", "Goal: <5%"], "high": ["High", ""], "target": ["Target", "Goal: >70%"],
                   "low": ["Low", ""], "very_low": ["Very Low", "Goal: <1%"]},
    "brackets": [{"bands": ["very_high", "high"], "label": "Very High + High", "goal": "Goal: <25%"},
                 {"bands": ["low", "very_low"], "label": "Low + Very Low", "goal": "Goal: <4%"}],
    "tir_notes": ["Each 5% increase is clinically beneficial", "Each 1% time in range = ~15 minutes"],
    "tir_ticks_mgdl": _TICKS["mgdl"], "tir_ticks_mmol": _TICKS["mmol"],
    "x_labels": ["12am", "3am", "6am", "9am", "12pm", "3pm", "6pm", "9pm", "12am"], "noon": "12pm",
    "weekdays": _WEEK_EN, "daily_number": "{d}", "palette": _V5_PALETTE, "axis": _AXIS,
    "footer": ["Synthetic report generated for testing; not a medical record."],
    "file_name": "AGP_Report_{start}-{end}.pdf",
    "_confidence": "verbatim (title, panel titles and subtitles, band names and goals, metric labels and goals, notes, "
                   "axis ticks and labels, two rows of seven daily profiles, palette of the 2025 PDF); inferred (mmol/L "
                   "goals by conversion, medians' zone colours below range); the IDC footer is not reproduced",
    "_sources": ["https://www.agpreport.org/agp/agpreports",
                 "https://www.healthpartners.com/institute/wp-content/uploads/2025/05/Determine-Where-to-Act.pdf",
                 "https://www.healthpartners.com/institute/wp-content/uploads/2025/09/AGP-Report-TITR.060125proof.pdf"],
}
AGP_CN2023 = {
    "layout": "agp", "title": "动态葡萄糖评估报告", "metrics_mode": "table",
    "header": ["基本信息", "姓名{colon}{name}", "监测时间{colon}{start} - {end}"], "date_start": "%Y/%m/%d", "date_end": "%Y/%m/%d",
    "labels": {"metrics_title": "葡萄糖指标", "metrics_cols": ["葡萄糖指标", "监测值", "参考值"],
               "tir_title": "TIR、TAR、TBR 葡萄糖目标范围内时间", "tir_goals": "",
               "agp_title": "AGP图谱", "agp_note": "AGP 是报告期间的葡萄糖值总结，显示中位数 (50%) 和其他百分位数，并假设其在同一天发生。",
               "daily_title": "每日葡萄糖曲线", "daily_note": "每个每日图谱代表一个午夜至午夜时段。", "target_range": "目标范围"},
    "metrics": [{"key": "days", "label": "CGM佩戴天数", "unit": "天"},
                {"key": "active", "label": "CGM有效记录的时间占比", "notes": ["（建议14天中70%为有效数据）"], "goal": ">70%"},
                {"key": "mean", "label": "MG 平均葡萄糖值", "goal": "<8.5 mmol/L", "goal_mgdl": "<153 mg/dL"},
                {"key": "gmi", "label": "GMI 葡萄糖管理指标", "goal": "<7.0%", "goal_mgdl": "<7.0%"},
                {"key": "cv", "label": "CV 变异系数", "goal": "<33%", "goal_mgdl": "<33%"},
                {"key": "sd", "label": "SD 葡萄糖标准差", "goal": "<1.4 mmol/L", "goal_mgdl": "<25 mg/dL"},
                {"key": "mage", "label": "MAGE 平均葡萄糖波动幅度", "goal": "<3.9 mmol/L", "goal_mgdl": "<70 mg/dL"},
                {"key": "modd", "label": "MODD 日间葡萄糖平均绝对差"},
                {"key": "lage", "label": "LAGE 最大葡萄糖波动幅度"}],
    "bands_mmol": {"very_high": ["很高（>13.9 mmol/L）", "<5%"], "high": ["高（10.1~13.9 mmol/L）", ""],
                   "target": ["目标范围（3.9~10.0 mmol/L）", ">70%"], "low": ["低（3.0~3.8 mmol/L）", ""],
                   "very_low": ["很低（<3.0 mmol/L）", "<1%"]},
    "bands_mgdl": {"very_high": ["很高（>250 mg/dL）", "<5%"], "high": ["高（181~250 mg/dL）", ""],
                   "target": ["目标范围（70~180 mg/dL）", ">70%"], "low": ["低（54~69 mg/dL）", ""],
                   "very_low": ["很低（<54 mg/dL）", "<1%"]},
    "brackets": [{"bands": ["very_high", "high"], "label": "TAR（>10.0 mmol/L）", "goal": "<25%"},
                 {"bands": ["low", "very_low"], "label": "TBR（<3.9 mmol/L）", "goal": "<4%"}],
    "tir_notes": ["每增加5%都是有益的", "图示中每1%的目标范围约等于15 min。"],
    "tir_ticks_mgdl": _TICKS["mgdl"], "tir_ticks_mmol": _TICKS["mmol"], "period_means": "分时段平均葡萄糖值",
    "legend": ["5%~95%区间", "25%~75%区间", "50%中位线", "目标范围"],
    "x_labels": _ZH_X, "noon": "12:00", "weekdays": _WEEK_ZH, "daily_number": "{d}", "palette": _V5_PALETTE,
    "axis": _AXIS, "footer": ["本报告为测试用合成数据，并非真实病历。"], "file_name": "动态葡萄糖评估报告_{start}-{end}.pdf",
    "_confidence": "verbatim (title, sections, metric names and reference values, band names and targets, notes, "
                   "legend, the period-means row: the 2023 consensus template); inferred (the vendor apps' own layouts, "
                   "which no public source shows; SD and MAGE references from the 2017 guideline; the AGP and daily "
                   "notes, taken from Abbott's Chinese LibreView report)",
    "_sources": ["https://news.qq.com/rain/a/20240305A07YB700",
                 "https://seleguide.yiigle.com/uploads/guide_html/%E4%B8%AD%E5%9B%BD%E6%8C%81%E7%BB%AD%E8%91%A1%E8%90%84%E7%B3%96%E7%9B%91%E6%B5%8B%E4%B8%B4%E5%BA%8A%E5%BA%94%E7%94%A8%E6%8C%87%E5%8D%97%EF%BC%882017%E5%B9%B4%E7%89%88%EF%BC%89.html",
                 "https://files.libreview.io/files/documents/zh-CN/FSReportTour_2026-05-06.pdf"],
}
SIBIONICS_CN = {
    "layout": "agp", "title": "血糖数据", "metrics_mode": "cards", "tir_duration": True,
    "header": ["连续{days}天数据报告汇总", "监测时间：{start} - {end}  报告生成时间：{created}"],
    "date_start": "%Y/%m/%d", "date_end": "%Y/%m/%d", "page_label": "页码{i}/{n}",
    "labels": {"metrics_title": "血糖数据", "tir_title": "TIR 葡萄糖目标范围内时间", "tir_goals": "参考值",
               "agp_title": "AGP图谱", "agp_note": "50% 中位线 / 25%-75%区间 / 5%-95%区间 / 目标范围",
               "daily_title": "每日血糖", "daily_note": "", "target_range": "目标范围"},
    "metrics": [{"key": "eag_a1c", "label": "eHbA1c 预估糖化血红蛋白"}, {"key": "mean", "label": "MG 平均葡萄糖值"},
                {"key": "sd", "label": "SD 葡萄糖标准差", "decimals": 2}, {"key": "cv", "label": "CV 变异系数"},
                {"key": "hypo_risk", "label": "低血糖风险"}],
    "hypo_levels": ["高", "中", "低", "最低"],
    "bands_mmol": {"very_high": ["很高(>13.9mmol/L)", "(<5%)"], "high": ["高(10-13.9mmol/L)", "(<25%)"],
                   "target": ["正常(3.9-10mmol/L)", "(>70%)"], "low": ["低(3-3.9mmol/L)", "(<4%)"],
                   "very_low": ["很低(<3mmol/L)", "(<1%)"]},
    "bands_mgdl": {"very_high": ["很高(>250mg/dL)", "(<5%)"], "high": ["高(180-250mg/dL)", "(<25%)"],
                   "target": ["正常(70-180mg/dL)", "(>70%)"], "low": ["低(54-70mg/dL)", "(<4%)"],
                   "very_low": ["很低(<54mg/dL)", "(<1%)"]},
    "brackets": [], "tir_notes": [], "tir_ticks_mgdl": _TICKS["mgdl"], "tir_ticks_mmol": _TICKS["mmol"],
    "period_means": "分时段平均血糖", "x_labels": _ZH_X, "noon": "12:00", "weekdays": [""] * 7,
    "daily_number": "%m/%d", "palette": _SIB_PALETTE,
    "axis": {"ceiling_mgdl": 450, "ticks_mgdl": [70, 180, 450], "ceiling_mmol": 25.0, "ticks_mmol": [3.9, 10.0, 25.0]},
    "daily_table": {"title": "每日统计", "date_label": "日期", "date_format": "%m/%d",
                    "rows": {"count": "探头值数量", "max": "上限 mmol/L", "min": "下限 mmol/L", "mean": "平均值 mmol/L",
                             "tir": "TIR 目标范围内时间", "tar": "TAR 高于目标范围时间", "tbr": "TBR 低于目标范围时间",
                             "lage": "LAGE 最大血糖波动幅度", "mage": "MAGE 平均血糖波动幅度",
                             "modd": "MODD 日间血糖平均绝对差", "sd": "SD 葡萄糖标准差", "cv": "CV 变异系数"},
                    "sections": [["", ["count", "max", "min", "mean"]], ["达标时间百分比", ["tir", "tar", "tbr"]],
                                 ["血糖波动", ["lage", "mage", "modd", "sd", "cv"]]],
                    "footnotes": ["*说明：首末日数据不足24h，无法进行计算。"]},
    "footer": ["本报告为测试用合成数据，并非真实病历。"], "file_name": "血糖报告_{start}-{end}.pdf",
    "_confidence": "verbatim (section titles, metric card labels, band labels and reference values with durations, "
                   "the daily-statistics rows and footnote, header lines, page label, 0-25 mmol/L axis, blue AGP); "
                   "inferred (eHbA1c by the ADAG relation, the low-glucose risk levels' cut-offs, colours sampled from "
                   "screenshots); left out: the vendor logo, the multi-day comparison, per-day detail and diet pages",
    "_sources": ["https://zhongce.sina.com.cn/article/view/181774", "https://news.qq.com/rain/a/20230627A08ARQ00",
                 "https://apps.apple.com/cn/app/%E7%A1%85%E5%9F%BA%E5%8A%A8%E6%84%9F/id1574026555"],
}
CGM_SHEET_2017 = {
    "layout": "sheet", "title": "持续葡萄糖监测（CGM）报告单",
    "labels": {"colon": "：", "item": "项目", "normal": "正常参考值(24 h)", "reporter": "报告者", "reviewer": "审核者",
               "report_time": "报告时间"},
    "header": [["name", "姓名"], ["sex", "性别"], ["age", "年龄"], ["date", "检查日期"], ["department", "科室"],
               ["ward", "病区"], ["bed", "床号"], ["number", "住院号/门诊号"], ["diagnosis", "临床诊断"]],
    "sex": {"male": "男", "female": "女"}, "department": "内分泌科",
    "diagnoses": {"prediabetes_to_t2dm": "2型糖尿病", "default": "糖代谢异常"},
    "rows": [{"key": "count", "label": "测定次数", "unit": ""},
             {"key": "mean", "label": "平均值(MG，mmol/L)", "normal": "<6.6", "unit": "mmol/L"},
             {"key": "sd", "label": "标准差(SD，mmol/L)", "normal": "<1.4", "unit": "mmol/L"},
             {"key": "cv", "label": "变异系数(CV，%)", "unit": "%"},
             {"key": "max", "label": "葡萄糖最高值(mmol/L)", "unit": "mmol/L"},
             {"key": "min", "label": "葡萄糖最低值(mmol/L)", "unit": "mmol/L"},
             {"key": "ge_13_9", "label": "葡萄糖≥13.9 mmol/L的百分比(%)", "unit": "%"},
             {"key": "ge_10", "label": "葡萄糖≥10.0 mmol/L的百分比(%)", "unit": "%"},
             {"key": "ge_7_8", "label": "葡萄糖≥7.8 mmol/L的百分比(%)", "normal": "17", "unit": "%"},
             {"key": "le_3_9", "label": "葡萄糖≤3.9 mmol/L的百分比(%)", "normal": "12", "unit": "%"},
             {"key": "le_2_8", "label": "葡萄糖≤2.8 mmol/L的百分比(%)", "unit": "%"},
             {"key": "in_3_9_10", "label": "3.9 mmol/L<葡萄糖<10 mmol/L的百分比(%)", "unit": "%"}],
    "summary": "CGM提示：共测定{count}个，平均值{mean} mmol/L，标准差{sd} mmol/L，变异系数{cv}%，最高值、最低值分别为"
               "{max} mmol/L、{min} mmol/L。3.9 mmol/L<葡萄糖<10 mmol/L的百分比为{in_3_9_10}%。≥7.8 mmol/L、≥10 mmol/L及"
               "≥13.9 mmol/L的百分比分别为{ge_7_8}%，{ge_10}%及{ge_13_9}%；≤3.9 mmol/L及≤2.8 mmol/L的时间及百分比分别为"
               "{le_3_9_time}({le_3_9}%)，{le_2_8_time}({le_2_8}%)",
    "summary_labels": {"count": "测定次数", "mean": "平均值", "sd": "标准差", "cv": "变异系数", "max": "最高值", "min": "最低值",
                       "in_3_9_10": "3.9 mmol/L<葡萄糖<10 mmol/L的百分比", "ge_7_8": "≥7.8 mmol/L的百分比",
                       "ge_10": "≥10 mmol/L的百分比", "ge_13_9": "≥13.9 mmol/L的百分比", "le_3_9": "≤3.9 mmol/L的百分比",
                       "le_2_8": "≤2.8 mmol/L的百分比"},
    "footer": ["本报告为测试用合成数据，并非真实病历。"], "file_name": "CGM报告单_{start}-{end}.pdf",
    "_confidence": "verbatim (title, header fields, item rows and normal values, summary sentence, signature line: "
                   "Table 5 of the 2017 guideline); inferred (department and diagnosis wording, one column per day of a "
                   "14-day sensor where the template shows four)",
    "_sources": ["https://seleguide.yiigle.com/uploads/guide_html/%E4%B8%AD%E5%9B%BD%E6%8C%81%E7%BB%AD%E8%91%A1%E8%90%84%E7%B3%96%E7%9B%91%E6%B5%8B%E4%B8%B4%E5%BA%8A%E5%BA%94%E7%94%A8%E6%8C%87%E5%8D%97%EF%BC%882017%E5%B9%B4%E7%89%88%EF%BC%89.html"],
}
REPORTS = {"agp_v5": {"en": AGP_V5}, "agp_cn2023": {"zh": AGP_CN2023}, "sibionics_cn": {"zh": SIBIONICS_CN},
           "cgm_sheet_2017": {"zh": CGM_SHEET_2017}}

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
    "carelink_csv": {
        "preamble_keys": ["Last Name", "First Name", "Patient ID", "System ID", "Start Date", "End Date", "Device"],
        "notice": "Device data shown may exceed selected date range.",
        "header_49": ["Index", "Date", "Time", "New Device Time", "BG Reading (mg/dL)", "Linked BG Meter ID",
                      "Basal Rate (U/h)", "Temp Basal Amount", "Temp Basal Type", "Temp Basal Duration (h:mm:ss)",
                      "Bolus Type", "Bolus Volume Selected (U)", "Bolus Volume Delivered (U)",
                      "Bolus Duration (h:mm:ss)", "Prime Type", "Prime Volume Delivered (U)", "Alarm", "Suspend",
                      "Rewind", "BWZ Estimate (U)", "BWZ Target High BG (mg/dL)", "BWZ Target Low BG (mg/dL)",
                      "BWZ Carb Ratio (g/U)", "BWZ Insulin Sensitivity (mg/dL/U)", "BWZ Carb Input (grams)",
                      "BWZ BG Input (mg/dL)", "BWZ Correction Estimate (U)", "BWZ Food Estimate (U)",
                      "BWZ Active Insulin (U)", "BWZ Status", "Sensor Calibration BG (mg/dL)",
                      "Sensor Glucose (mg/dL)", "ISIG Value", "Event Marker", "Bolus Number",
                      "Bolus Cancellation Reason", "BWZ Unabsorbed Insulin Total (U)", "Final Bolus Estimate",
                      "Scroll Step Size", "Insulin Action Curve Time", "Sensor Calibration Rejected Reason",
                      "Preset Bolus", "Bolus Source", "Device Update Event", "Network Device Associated Reason",
                      "Network Device Disassociated Reason", "Network Device Disconnected Reason",
                      "Sensor Exception", "Preset Temp Basal Name"],
        #: Kept in mg/dL in a mmol/L export (NightscoutLoader's mmol list keeps it; marked unsure).
        "mgdl_only": ["BWZ Insulin Sensitivity (mg/dL/U)"],
        #: Delimiter, decimal mark and the preamble's date style by language group and unit. US exports write
        #: "7/9/18 12:00:00 AM"; a day-first export "19/8/2024 00:00:00". EU exports (";" and a decimal comma)
        #: exist but the cohort has no EU group.
        "accounts": {"en": {"mg/dL": {"delimiter": ",", "decimal": ".", "preamble_date": "US"},
                            "mmol/L": {"delimiter": ",", "decimal": ".", "preamble_date": "DMY"}}},
        "confidence": "verbatim (preamble, notice line, separators with a trailing space, 49-column header, a Pump "
                      "section even for a sensor-only system, Index with five decimals running across sections, "
                      "newest first, BOM, CRLF, alarm wording); inferred (mmol/L header and one-decimal values); "
                      "unsure (file name, a reading past the range)",
        "sources": [SRC[k] for k in ("carelink_g4_sample", "carelink_780g_sample", "carelink_2018_sample",
                                     "carelink_parser", "carelink_nsloader", "carelink_schema")],
    },
    "nightscout_entries": {
        "uploaders": {"share2": {"device": "share2"}, "librelinkup": {"device": "nightscout-librelink-up"},
                      "xdrip": {"device": {"dexcom": "xDrip-DexcomG5", "libre": "xDrip-LibreReceiver"}}},
        "confidence": "verbatim (server normalisation of dateString/sysTime/utcOffset, newest-first order, the "
                      "headerless CSV with JSON-encoded cells joined by CRLF, share2 and LibreLink-Up entry fields "
                      "and device names, xDrip+ upload fields); inferred (xDrip+ collection-method names)",
        "sources": [SRC[k] for k in ("ns_entries", "ns_api", "ns_share2", "ns_llu", "ns_llu_config", "xdrip_uploader")],
    },
    "xdrip_sidiary": {
        "header": "DAY;TIME;UDT_CGMS;BG_LEVEL;CH_GR;BOLUS;REMARK",
        "confidence": "verbatim (zip and entry names, header, dd.MM.yyyy;HH:mm;, mg/dL rounded, readings above 13, "
                      "blocks in order); inferred (LF, no BOM)",
        "sources": [SRC["xdrip_csv"]],
    },
    "dexcom_share": {"confidence": "verbatim (compact array, WT/ST/DT Date(ms) forms, string trends, mg/dL); "
                                   "inferred (newest first); unsure (how a reading past the range is sent: left out)",
                     "sources": [SRC["pydexcom"], SRC["ns_share2"]]},
    "librelinkup_graph": {
        "target_mgdl": [70, 180],
        "alarm_rules": {"c": True, "h": {"th": 220, "thmm": 12.2, "d": 1440, "f": 0.1},
                        "f": {"th": 55, "thmm": 3, "d": 30, "tl": 10, "tlmm": 0.6},
                        "l": {"th": 60, "thmm": 3.3, "d": 1440, "tl": 10, "tlmm": 0.6},
                        "nd": {"i": 20, "r": 5, "l": 6}, "p": 5, "r": 5, "std": {}},
        "confidence": "verbatim (response structure, key order, timestamp form, arrows, units, graph without "
                      "arrows); unsure (MeasurementColor for red, product ids for Libre 2)",
        "sources": [SRC["llu_fixture"], SRC["pylibrelinkup"]],
    },
    "tidepool_export": {
        "sheets": TIDEPOOL_SHEETS, "cols_xml": TIDEPOOL_COLS_XML, "page_xml": TIDEPOOL_PAGE_XML,
        "uploader_version": "2.60.0",
        "styles_mgdl": TIDEPOOL_STYLES["mgdl"], "styles_mmol": TIDEPOOL_STYLES["mmol"],
        "confidence": "verbatim (file names, Excel or JSON, units by the patient setting, the JSON allow-list and "
                      "key order, compact array without a trailing newline, mmol/L storage rounded to five decimals "
                      "and multiplied back unrounded, out-of-range annotation, sheet names and order, the very-hidden "
                      "EXPORT ERROR sheet and its text, column headers, widths, number formats, Excel serial dates, "
                      "inline strings); inferred (uploads last, deviceId form for each vendor); unsure (payload "
                      "contents, which we leave out)",
        "sources": [SRC[k] for k in ("tidepool_export", "tidepool_data_tools", "tidepool_blip_dialog",
                                     "tidepool_platform_glucose", "tidepool_sample_xlsx")],
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
    "zh": {"cgm": {"sibionics_gs1": 3, "ican_i3": 1, "yuwell_ct3": 1, "aidex": 1}, "glucose_unit": {"mmol/L": 1}},
    "en": {"cgm": {"dexcom_g7": 3, "dexcom_g6": 1, "libre_2": 2, "libre_3": 2, "sibionics_gs1": 1, "guardian_4": 1},
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
    "stream.sectioned_csv": "one file holding several tables, each under its own separator and header",
    "stream.blocks_not_interleaved": "readings, calibrations and treatments in separate blocks, each in time order",
    "stream.window_24h": "a follower feed holding only the last 24 hours",
    "stream.window_12h": "a follower feed holding only the last 12 hours",
    "stream.unrounded_conversion": "mg/dL recomputed from stored mmol/L without rounding (99.9976941658)",
    "stream.report_only": "the readings reach the wearer only as a summary report",
    "stream.chart_values": "curves and bars a reader must not mistake for, or read as, individual readings",
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
        "_vocabulary_fields": ["formats", "devices", "wearables", "store_fields", "hazard_classes", "reports"],
        "devices": DEVICES,
        "wearables": WEARABLES,
        "formats": FORMATS,
        "store_fields": STORE_FIELDS,
        "reports": REPORTS,
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
