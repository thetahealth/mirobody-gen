# Device formats

What the continuous streams (`mirobody-gen build --continuous`) write, what each byte-level choice rests on,
and what is deliberately left out. This is the ledger behind `mirobody_gen/resources/streams.json`; the
URLs below are the same ones that file's entries carry in `sources`.

Research date: 2026-10-09. Vendors change exports without notice; re-check the sources before relying on
a detail, and update this file together with `scripts/build_streams.py`.

Confidence, used throughout:

- **verbatim**: read in vendor documentation, or measured byte for byte in public raw exports that had
  not been re-saved by a spreadsheet;
- **inferred**: consistent across third-party parsers, emulators or samples, but not documented;
- **unsure**: a reasoned choice where the evidence ran out. Each one is listed as an open question below.

Every name, serial, transmitter id and date in the examples below is a placeholder.

## What is written

| Stream | Device | Files | Channel | Who gets it |
| --- | --- | --- | --- | --- |
| CGM | Dexcom G7, G6 | Clarity CSV export; Web API v3 `egvs` JSON; Apple Health records; `/api/data` batch | file upload; vendor API; phone store | English-speaking wearers |
| CGM | Abbott FreeStyle Libre 2, Libre 3 | LibreView CSV (one per person, all sessions) | file upload | English- and Japanese-speaking wearers |
| CGM | Sibionics GS1 (硅基动感) | a sensor placed in clinic: the hospital-system CSV and the hospital's CGM report sheet; the Chinese app's report; the international app's Excel export and AGP report; Apple Health records from 2024-07-01 | file upload; phone store | mostly Chinese-speaking wearers |
| CGM | Sinocare iCan i3 (三诺爱看), Yuwell Anytime CT3 (鱼跃), MicroTech AiDEX (微泰) | the app's report as a PDF (no readings file exists) | file upload | Chinese-speaking wearers |
| CGM | Medtronic Guardian 4 | CareLink Personal CSV | file upload | English-speaking wearers |
| CGM | Dexcom, Libre (a wearer's own set-up) | Nightscout `entries` JSON and CSV (share2, LibreLink-Up or xDrip+ uploader); xDrip+ SiDiary CSV zip; Tidepool export (Excel or JSON); a follower's Dexcom Share or LibreLinkUp snapshot | vendor API; file upload | English-speaking wearers |
| Heart rate | Apple Watch | `apple_health_export/export.xml`; `/api/data` batch | file upload; phone store | Apple Health stores |
| Heart rate | Huawei watch, Android watch | `/api/data` batch | phone store | Huawei Health and Health Connect stores |
| Heart rate | Xiaomi band | Zepp Life `HEARTRATE_AUTO` CSV; `/api/data` batch | file upload; phone store | Xiaomi stores |
| Heart rate | Oura ring | Oura API v2 `heartrate` JSON | vendor API | Oura adopters (`vendor_signals.adopted_vendors`) |

The truth for all of it is `continuous.jsonl` (`docs/SCHEMA.md`). The physiology the curves come from is
described in `mirobody_gen/continuous.py`.

## CGM

### Dexcom G7 and G6

| Fact | G7 | G6 | Confidence | Source |
| --- | --- | --- | --- | --- |
| Interval | 5 min | 5 min | verbatim | [UMich CGM exports][umich], [Dexcom API][dex-api] |
| Wear | 10 days + 12 h grace | 10 days | verbatim (G7), inferred (G6) | [Dexcom training][dex-train] |
| Warm-up | 30 min | 2 h | verbatim | [Dexcom training][dex-train] |
| Range | 40–400 mg/dL | 40–400 mg/dL | verbatim | [Dexcom API][dex-api], [FDA K213919][g7-fda] |
| MARD | 8.2% (arm, adults) | 9.0% | verbatim | [Garg 2022][g7-mard], [Shah 2018][g6-mard] |
| Backfill | up to 24 h | up to 3 h | verbatim | [Dexcom troubleshooting][dex-backfill] |
| Transmitter id | 12 digits, one per sensor; tick counter restarts | `8` + 5 alphanumerics, outlives sensors; counter runs on | verbatim (samples) | [Clarity sample G7][s-g7], [Clarity sample G6][s-g6] |

**Clarity CSV** (`Clarity_Export_<Last>_<First>_<YYYY-MM-DD>_<hhmmss>.csv`). Dexcom publishes no
specification; the shape was measured in about twelve public exports (2016–2025, G4 to G7 and Stelo,
mg/dL and mmol/L) and checked against the [UMich page][umich] and the [Glyapp sensor list][glyapp].

- Header, 14 columns, verbatim:
  `Index,Timestamp (YYYY-MM-DDThh:mm:ss),Event Type,Event Subtype,Patient Info,Device Info,Source Device ID,Glucose Value (mg/dL),Insulin Value (u),Carb Value (grams),Duration (hh:mm:ss),Glucose Rate of Change (mg/dL/min),Transmitter Time (Long Integer),Transmitter ID`.
  The mmol/L variant changes only `Glucose Value (mmol/L)` and `Glucose Rate of Change (mmol/L/min)`.
  Verbatim.
- UTF-8 with a BOM, every field double-quoted (exports since 2023), CRLF line endings. The BOM and quoting
  are verbatim; CRLF is inferred, because two 2025 copies were LF but had been through git normalisation.
- Untimed preamble: `FirstName`, `LastName`, optional `DateOfBirth`, then per display device a `Device`
  row and seven `Alert` rows in the order Fall, High, Low, Signal Loss, Rise, Urgent Low, Urgent Low
  Soon. Alert values in mg/dL: High 200, Low 70, Urgent Low and Urgent Low Soon 55, Rise and Fall 3
  (in the rate column), Signal Loss `00:20:00` (in the duration column). In mmol/L: 12.0, 5.0, 3.1, 3.1,
  0.2. Verbatim.
- Ragged rows: EGV and Calibration rows have 14 fields, every other row 13, with Transmitter ID missing.
  A file padded to 14 everywhere has been re-saved. Verbatim.
- `Index` runs 1..n over all rows. The timed rows follow the preamble sorted by timestamp. Verbatim.
- Timestamp: local wall-clock time, no offset, no milliseconds. EGVs are 300 ± 1–2 s apart.
  Verbatim (shown in one export by the repeated hour at a daylight-saving fall-back).
- Values: integer mg/dL; mmol/L always with one decimal. Out of range, the EGV row's subtype and value are
  `Low` / `High`. Verbatim for mg/dL; the mmol/L words are inferred from parsers.
- Device strings: `Dexcom G7 Mobile App` and `Dexcom G6 Mobile App`, verbatim. Source device ids
  `iOS G7`, `iOS G6`, `android G6` (lower-case a since 2023), `iOS Watch`: verbatim. `android G7` is
  inferred by analogy.
- Our additions from the day plan: an occasional `Exercise` row with subtype Light, Medium or Heavy and
  its duration, an occasional `Carbs` row, and an occasional fingerstick `Calibration` row (14 fields,
  Transmitter Time empty). The row shapes are verbatim; the rates are hand-set.

**Web API v3 `GET /v3/users/self/egvs`** ([reference][dex-api], [overview][dex-api-overview]). Verbatim
from the documented sample:

- the body is `{recordType: "egv", recordVersion: "3.0", userId, records}`;
- each record carries `recordId` (UUID), `systemTime` (UTC with `Z`) and `displayTime` (local with
  offset), `transmitterId` (64 lower-case hex, hashed), `transmitterTicks` (seconds since the transmitter
  started), `value`, `status`, `trend`, `trendRate`, `unit`, `rateUnit`, `displayDevice`,
  `transmitterGeneration`, `transmitterGenerationVariant` and `displayApp`;
- values are always mg/dL, with 39 and 401 standing for below and above range (`status` `low` / `high`);
- the trend enum and its rate bands are those in the reference.

Record order is unsure: we write newest first, as the v2 sample and Share do. Real-data quirks are
documented by [Tidepool's Dexcom client][tidepool-platform].

**Apple Health and Health Connect.** Dexcom writes G7 data to Apple Health "after a three-hour delay"
([Dexcom FAQ][dex-apple]) and to Health Connect with the same delay. The HealthKit source name
`Dexcom G6` is verbatim from a parsed export; `Dexcom G7` is inferred. The unit is unsure: we write
mg/dL, which is Dexcom's internal unit.

### Abbott FreeStyle Libre 2 and Libre 3

| Fact | Libre 2 | Libre 3 | Confidence | Source |
| --- | --- | --- | --- | --- |
| Stored interval | 15 min | 5 min, ±1 min jitter | verbatim | [UMich][umich], [Libre 3 manual][l3-manual], samples |
| Wear / warm-up | 14 days / 60 min | 14 days / 60 min | verbatim | [ADA guide][l-ada], [Libre 3 manual][l3-manual] |
| Memory | 8 h: history older than 8 h at the next scan is lost | whole wear period | verbatim | [Abbott 8-hour page][l-8h], [Libre 3 manual][l3-manual] |
| Range | 40–500 mg/dL; 40–400 in the US and Canada | 2.2–27.8 mmol/L; US 40–400 (unsure) | verbatim / unsure | [Libre 3 manual][l3-manual], LibreLinkUp help |
| MARD | 9.2% | 7.9% | verbatim | [Libre 2][l2-mard], [Libre 3][l3-mard] |
| Phone stores | none: no Abbott app writes to Apple Health or Health Connect | none | inferred | [Loop docs][l-nohk] |

**LibreView CSV** (`<FirstLast>_glucose_<D-M-YYYY>.csv`; one file per account, holding every session,
because the export ignores the selected date range ([UMich][umich])). Measured in about fifteen public
raw exports; Abbott publishes no specification.

- Title line, exactly five fields, verbatim:
  `Glucose Data,Generated on,<date time> UTC,Generated by,<Full Name>`.
- Header, 19 columns, verbatim:
  `Device,Serial Number,Device Timestamp,Record Type,Historic Glucose mg/dL,Scan Glucose mg/dL,Non-numeric Rapid-Acting Insulin,Rapid-Acting Insulin (units),Non-numeric Food,Carbohydrates (grams),Carbohydrates (servings),Non-numeric Long-Acting Insulin,Long-Acting Insulin (units),Notes,Strip Glucose mg/dL,Ketone mmol/L,Meal Insulin (units),Correction Insulin (units),User Change Insulin (units)`.
  The unit is part of the column names. A UK account writes `Long-Acting Insulin Value (units)` as the
  13th column (verbatim; independent samples and NightscoutLoader agree).
- UTF-8 with no BOM, CRLF, comma-delimited, minimal quoting. Every row has 19 fields. Verbatim.
- Record types: 0 historic, 1 scan (Libre 3 app views are exported as scans), 5 food, 6 events. We write
  two to eight empty type-6 rows at sensor start. Verbatim for the types; the meaning of the empty type-6
  rows is inferred.
- Rows are grouped by serial and then by record type, ascending in time within each group, so they are
  not chronological overall. Verbatim, in every raw sample.
- `Device Timestamp` is local time with no zone and no seconds, in the account's settings:
  `MM-DD-YYYY hh:mm AM` by default, `DD-MM-YYYY HH:mm` for a day-first, 24-hour account. Verbatim.
- Values: integer mg/dL; mmol/L always one decimal (`5.0`). Verbatim. A reading below range is written as
  the floor (40 or 2.2); this is inferred from readings piling up at exactly 2.2. A reading above range is
  written as the ceiling; this is unsure, since no sample contained one.
- Device and serial: `FreeStyle LibreLink` or `FreeStyle Libre 3` with a UUID (upper-case on iOS,
  lower-case on Android, inferred), and `FreeStyle Libre 2` with a reader serial of the shape
  `AAAA999-A9999`. Verbatim.
- Sources: [settings and decimal separator][lv-settings], [Tidepool uploader][lv-tidepool],
  [Juggluco's LibreView emulator][lv-juggluco], samples [Libre 3][s-l3], [US Libre app][s-lus],
  [mmol/L][s-lmmol], [UK][s-luk], [reader][s-lreader], [NightscoutLoader][nsloader].

### Sibionics GS1 (硅基动感)

| Fact | Value | Confidence | Source |
| --- | --- | --- | --- |
| Interval, wear, warm-up | 5 min, 14 days, 1 h | verbatim | [GS1 user guide][sib-guide] |
| Range | 40–450 mg/dL; readings above 450 are shown at 450 | verbatim | [GS1 user guide][sib-guide] |
| MARD | 8.83% | inferred | [知乎][sib-zhihu] |
| Serial | `LT` + year and month + 4 characters | verbatim (code) | [Juggluco][sib-serials] |
| Apple Health | written since app v02.10.00.00 (2024-07-01) | verbatim (release note) | [App Store CN][sib-apple] |

**Hospital-system CSV** (sensor placed in clinic; the sensor before metformin in this cohort). From an
anonymised public sample whose serial pattern is Sibionics':

`血糖值(mmol/L),采集时间,机构名称：<机构>,姓名：<姓名>,设备名称：<serial>`, then rows such as `6.4,2026-10-08 14:05:28,,,`. <!-- privacy-gate:ok -->

- The metadata is written into header cells, each after a full-width colon. Verbatim.
- Values in mmol/L with one decimal; timestamps `YYYY-MM-DD HH:MM:SS` with constant seconds; newest row
  first. Verbatim.
- BOM and line endings are unsure; we write a BOM and CRLF.
- The institution and patient name come from the fiction pool, the name being the one the person's own
  documents print. [Sample][sib-clinic].

**International app export** (`SiSensingCGM-01.20.00.00.xls`).

- The file is named `.xls`, but its bytes are an OOXML workbook. Verbatim.
- One sheet, `Sensor Glucose`, with every cell a string. Header `Time`, `Sensor Reading(mg/dL)`. Rows
  oldest first, timestamps `DD-MM-YYYY HH:MM GMT+8`. Verbatim, from a real sample.
- Inferred: the mmol/L header. Unsure: the suffix at offset zero (we write `GMT+0`).
- Sources: [sample][sib-xlsx], [converter][sib-xlsx-conv].

The Chinese app documents no raw export (an importer written for it has to guess among header aliases,
[GlucoControl][glucontrol]); a Chinese home wearer holds its report instead (`sibionics_cn`, below), and an
iPhone user also the Apple Health records.

### Sinocare iCan i3 (三诺爱看), Yuwell Anytime CT3 (鱼跃), MicroTech AiDEX (微泰)

| Fact | iCan i3 | Anytime CT3 | AiDEX | Source |
| --- | --- | --- | --- | --- |
| Interval | 3 min | 3 min | 5 min | [iCan][ican], [Yuwell][yuwell], [AiDEX][aidex] (verbatim) |
| Wear | 15 days | 14 days | 14 days | same (verbatim) |
| Warm-up | 2 h | 60 min (inferred) | 60 min (inferred) | [manual][ican-manual]; [GlucoDroid constants][yuwell-glucodroid] |
| Range | 2.0–25.0 mmol/L | 1.7–27.8 mmol/L (unsure: a search snippet of the CE manual) | 2.0–25.0 mmol/L | same |
| MARD | 8.71% | 9.1% | 9.08% (inferred) | same; [Yuwell trial][yuwell-trial] |

None of the three apps hands over a readings file: they show the curve, generate a report at the end of a
wear period, and share it as a PDF or picture. Reviewers ask for CSV exports; the Chinese iCan app reads
from Apple Health and Huawei Health instead of writing to them ([App Store CN][ican-cn]). So these sessions
deliver the report (below) and nothing else.

### Reports (PDF)

A wearer whose app hands over no readings still holds a report. Four styles, chosen by device, language
and whether the sensor was placed in clinic (`streams.json` `reports`, `devices.<id>.report`); every printed
value is recorded in the file's `printed_rows`, rounded once, when printed (an early version rounded twice
and printed a 99.47% time in range as 100%).

| Style | Who | Layout and wording | Confidence |
| --- | --- | --- | --- |
| `agp_v5` | English-language apps (iCan, Yuwell, AiDEX abroad; the international Sibionics app, which exports an AGP report) | The International Diabetes Center's AGP Report v5 on one page: "Time in Ranges / Goals for Type 1 and Type 2 Diabetes" with each band's goal and the bracketed sums (<25%, <4%), the patient, "14 Days: …" and "Time CGM Active", "Glucose Metrics" (Average Glucose, GMI, Glucose Variability with "Defined as percent coefficient of variation" and "Goal: ≤36%"), the AGP with its notes and 12am…12am axis, two rows of seven daily profiles | verbatim ([IDC samples][agp-idc], [v5 report in a 2024 guide][agp-v5-guide], [2025 TITR report][agp-titr]); the IDC footer and logo are not reproduced |
| `agp_cn2023` | the Chinese iCan, Yuwell and AiDEX apps | The 2023 Chinese AGP consensus template "动态葡萄糖评估报告": 基本信息, 葡萄糖指标 as 指标 / 监测值 / 参考值 (CGM佩戴天数, CGM有效记录的时间占比, MG <8.5 mmol/L, GMI <7.0%, CV <33%), "TIR、TAR、TBR 葡萄糖目标范围内时间" with 很高 / 高 / 目标范围 / 低 / 很低, "每增加5%都是有益的", the 分时段平均葡萄糖值 row, the legend, 每日葡萄糖曲线 | verbatim for the template ([consensus][agp-cn2023]); inferred that these apps follow it (no public sample of their reports); SD and MAGE references from the 2017 guideline ([guideline][cgm-cn2017]); the AGP note from Abbott's Chinese LibreView reports ([报告集概述][libreview-zh]) |
| `sibionics_cn` | Sibionics' Chinese app, a sensor the wearer bought | The 2023 Sibionics report's core pages: 血糖数据 cards (eHbA1c 预估糖化血红蛋白, MG 平均葡萄糖值, SD, CV, 低血糖风险), "TIR 葡萄糖目标范围内时间" with 很高(>13.9mmol/L) / 高(10-13.9mmol/L) / 正常(3.9-10mmol/L) / 低 / 很低, each "% (XhXXmin)" with its reference, the blue AGP on a 0–25 mmol/L axis, 每日血糖 tiles labelled MM/DD, and the 每日统计 table (探头值数量, 上限, 下限, 平均值; 达标时间百分比; 血糖波动) with `--*` for the first and last day and its footnote; "页码i/n", "监测时间… 报告生成时间…" | verbatim for the labels, rows and footnote ([review with report pages][sib-report-2023], [2022 professional layout][sib-report-2022]); inferred: eHbA1c by the ADAG relation, the risk levels' cut-offs, colours; left out: the logo, the multi-day comparison, per-day detail and diet pages (the real PDF has six pages, ours two) |
| `cgm_sheet_2017` | a sensor placed in clinic (Sibionics, with the hospital CSV) | The 2017 Chinese CGM guideline's 持续葡萄糖监测（CGM）报告单: institution, 姓名 / 性别 / 年龄 / 检查日期 / 科室 / 病区 / 床号 / 住院号/门诊号 / 临床诊断, one column per day against 正常参考值(24 h) (<6.6, <1.4, 17, 12), the "CGM提示：共测定…" sentence, 报告者 / 审核者 / 报告时间 | verbatim (Table 5 of the [guideline][cgm-cn2017]); inferred: department and diagnosis wording, fourteen day columns where the template shows four |

**Metrics** (`mirobody_gen/cgm_metrics.py`):

- ranges, mean, SD, CV and GMI by the consensus ([Battelino 2019](https://doi.org/10.2337/dci19-0028),
  [Bergenstal 2018](https://doi.org/10.2337/dc18-1581));
- MAGE as the 2017 guideline and Service define it: swings larger than one SD of the same 24-hour period, in
  the direction of the first, averaged over complete days, automated with an SD hysteresis after a 3-point
  moving average as Baghurst does ([Service 1970](https://doi.org/10.2337/diab.19.9.644),
  [Baghurst 2011](https://hdl.handle.net/2440/66223)). A clean sine wave of amplitude A gives 2A;
- MODD as the mean absolute difference of readings 24 h apart
  ([Molnar 1972](https://doi.org/10.1007/BF01218495));
- LAGE as the highest minus the lowest reading;
- the AGP as the 5/25/50/75/95th percentiles per 15-minute bin, smoothed over ±1 bin; the report period is
  the last 14 days of the sensor.

### Medtronic Guardian 4: CareLink Personal CSV

Medtronic publishes no specification. The shape comes from a real export of a sensor-only Guardian 4 system
([sample][ct-g4]), checked against a 780G export ([aidR][ct-aidr]), a 2018 export ([GluGo][ct-2018]) and
parsers and notes ([odiak][ct-odiak], [NightscoutLoader][ct-nsl], [etauker schema][ct-etauker],
[GlycemicGPT][ct-gg], [GlucoseDAO][ct-dao]).

- Preamble, verbatim: `Last Name,First Name,Patient ID,System ID,Start Date,End Date,Device,Guardian™ 4 system`
  (one device column per app installation), then the quoted names and dates, `"Serial Number"` and the
  installation ids, a blank line, `Device data shown may exceed selected date range.`, a blank line.
- Sections: `-------,Guardian™ 4 system,Pump,<id>,------- ` (a trailing space) and a `Sensor` section, each
  followed by the 49-column header. A sensor-only system still has a Pump section: the app's alarms
  (`SENSOR CONNECTED`, `LOST SENSOR SIGNAL`, `LOW SG`, `URGENT LOW SENSOR GLUCOSE`, `MOBILE DEVICE BATTERY
  LOW`), logbook entries in `Event Marker` (`Meal: 45.00grams`) and fingersticks in `BG Reading`. A second
  installation gets an empty Pump section. Verbatim.
- Sensor rows fill only `Index`, `Date` (`YYYY/MM/DD`), `Time` (`HH:MM:SS`, 300 s apart with a few seconds of
  jitter) and `Sensor Glucose`. Rows newest first. `Index` has five decimals and counts from 0 straight across
  sections. UTF-8 BOM, CRLF, a blank line after each section. Verbatim.
- The preamble's dates follow the locale: `7/9/18 12:00:00 AM` (US) or `19/8/2024 00:00:00`. Verbatim. EU
  exports use `;` and a decimal comma (`94,00000`); the cohort has no EU group, so they are not written.
- mmol/L exports rename the glucose columns `(mmol/L)` and print one decimal: inferred. How a reading past
  40–400 mg/dL is written was not seen: it is left out. The file name is unsure.
- Device facts (secondary sources): 7 days, warm-up up to 2 h, MARD 10.6%, no calibration required
  ([ADA guide][g4-ada]).

### Nightscout `/api/v1/entries`

Read from the server source ([entries.js][ns-entries], [entries API][ns-api], [swagger][ns-swagger]) and the uploaders
([share2nightscout-bridge][ns-share2], [nightscout-librelink-up][ns-llu], [xDrip+ uploader][xdrip-up]).

- On insert the server parses `dateString`, stores `utcOffset` (minutes) and `sysTime` (UTC with
  milliseconds and `Z`) and overwrites `dateString` with `sysTime`; `_id` is an ObjectId. Verbatim.
- The API returns entries newest first (`count` defaults to 10; a client after a whole sensor asks with
  `find[date][$gte]`, which bypasses the two-day cache, so there is no `mills`). JSON is compact. Verbatim,
  except compactness (inferred).
- `.csv`: no header; `dateString, date, sgv, direction, device`, each JSON-encoded (strings quoted, a missing
  value empty), rows joined by CRLF with no newline at the end. Verbatim.
- share2 entries: `sgv, date, dateString, trend, direction, device: "share2", type` (the bridge sends UTC, so
  `utcOffset` is 0). Since 15.0.8 the built-in bridge writes `device: "nightscout-connect"` unless the legacy
  bridge is chosen; we write the legacy bridge's. Verbatim.
- LibreLink-Up entries: `type, sgv, direction` (only on the reading current at a poll), `device:
  "nightscout-librelink-up", date, dateString`. Verbatim.
- xDrip+ entries: `device: "xDrip-<collector>"`, millisecond `date`, local `dateString` and `sysTime` (rewritten to
  UTC; `utcOffset` appended after them), `delta` to three decimals, `direction` (`NotComputable`, not `NOT
  COMPUTABLE`), `filtered`/`unfiltered` raw values × 1000, `rssi: 100`, `noise`. Verbatim; the collector names
  `DexcomG5` and `LibreReceiver` are inferred.

### xDrip+ "Export CSV (SiDiary format)"

`exportCSV<yyyyMMdd-HHmmss>.zip` holding `export<yyyyMMdd-HHmmss>.csv`; header
`DAY;TIME;UDT_CGMS;BG_LEVEL;CH_GR;BOLUS;REMARK`; rows `dd.MM.yyyy;HH:mm;<mg/dL>;;;;` for readings above 13,
then calibrations, then treatments, each block in time order. Verbatim from the source ([DatabaseUtil][xdrip]);
LF and no BOM are inferred from `PrintStream.println`.

### Tidepool export

Read from the export service and its library, and checked by running `@tidepool/data-tools@2.5.0` on
placeholder data and against Tidepool's demo export ([export][tp-export], [node-data-tools][tp-tools],
[blip dialog][tp-blip], [platform glucose][tp-glucose], [data model][tp-cbg], [demo export][tp-sample]).

- The web app offers Excel (default) or JSON, units by the patient's setting, at most 90 days. Files are
  `TidepoolExport.xlsx` and `TidepoolExport.json`. Verbatim.
- Values are stored in mmol/L, mg/dL divided by 18.01559 and rounded to five decimals half away from zero;
  an mg/dL export multiplies back without rounding (`99.9976941658`). A reading past the range is stored at
  the threshold with an `annotations` entry `bg/out-of-range`. Verbatim.
- JSON: one compact array, no trailing newline; only the library's allow-listed keys, in alphabetical order;
  `payload` and `annotations` as JSON strings; cbg records, then the upload record. Verbatim (uploads last:
  inferred).
- Excel (ExcelJS): a very-hidden `EXPORT ERROR` sheet first (its A1 text is the export's error message),
  then `CGM` and `Upload` in order of first appearance; bold, frozen headers; `Zulu Time`, `Local Time`,
  `Device Time` as Excel serial dates formatted `yyyy-mm-dd hh:mm:ss`; the value unrounded with display
  format `0` (mg/dL) or `0.0` (mmol/L); inline strings. The column widths, page set-up and style sheets are
  copied from the library's own output. Verbatim. We leave out the Office theme part ExcelJS adds.
- The `deviceId` form for each vendor and the `payload` contents are unsure: `deviceId` is
  `<Maker><Model>_<id>` and `payload` is left out.

### Follower snapshots: Dexcom Share and LibreLinkUp

- Dexcom Share `ReadPublisherLatestGlucoseValues?minutes=1440&maxCount=288`: a compact array, newest first,
  `{"WT":"Date(ms)","ST":"Date(ms)","DT":"Date(ms±hhmm)","Value":<mg/dL>,"Trend":"Flat"}`. Verbatim
  ([pydexcom][pydexcom]). The last 24 hours only.
- LibreLinkUp `/llu/connections/{id}/graph`: the connection (names, targets, units, sensor, alarm rules,
  current reading with `TrendArrow`), about 12 hours of `graphData` ascending without arrows, `FactoryTimestamp`
  (UTC) beside `Timestamp` (local) as `M/D/YYYY h:mm:ss AM`, and a ticket. Verbatim ([fixture][llu-fixture],
  [pylibrelinkup][pylibrelinkup]). The token is a JWT-shaped string whose payload says it is synthetic.

### CGM formats not modelled yet

- **Medtronic 780G pump exports** (insulin, Aggregated Auto Insulin Data, `Sensor Exception` rows, 53/54
  columns, EU `;` variant): the cohort has no pump users. Sources as above.
- **Medtrum TouchCare Nano** (2 min), **Glunovo** (3 min, Excel export with undocumented columns), **AiDEX X /
  LinX** (1 min), **Eversense** (no public export) ([Medtrum sheet][medtrum], [Glunovo sheet][glunovo]).
- **Sisensing and AiDEX cloud JSON**: unofficial ([Sisensing uploader][sisensing], [AiDEX mapper][aidex-json]).
- **Nightscout API v3**, **nightscout-connect**, and the Loop, Trio and AAPS uploaders (fractional `date`,
  no `dateString`, `NotComputable`/`RateOutOfRange` spellings).
- **Newer sensors**: Libre 2 Plus and Libre 3 Plus (15 days), Dexcom G7 15 Day, Dexcom Stelo.

## Heart rate

### Apple Watch and the Apple Health export

The export is `apple_health_export/export.xml`, written unzipped. mirobody's reader accepts the
directory form.

- XML prolog and the `HealthData` DTD header, `HealthKit Export Version: 14`. Verbatim. We write only the
  DTD declarations for the elements the file uses; a real export carries the whole DTD.
- Records in the attribute order of real exports: type, sourceName, sourceVersion, device, unit,
  creationDate, startDate, endDate, value. One-space indent; metadata one level deeper. Dates
  `YYYY-MM-DD HH:MM:SS +0800`. Verbatim.
- Device string, XML-escaped: `<<HKDevice: 0x…>, name:Apple Watch, manufacturer:Apple Inc., model:Watch, hardware:…, software:…>`,
  with a hex address that changes from record to record. Verbatim. The hardware ids and the OS version
  per year are inferred.
- Heart rate is `count/min`, a point sample with `creationDate` a few seconds after it. It carries
  `HKMetadataKeyHeartRateMotionContext`: 1 (sedentary) at rest and asleep, 2 (active) walking, 0 (not
  set) in workouts, as real workout samples show. Names verbatim ([Apple][hk-motion]); integers inferred.
- Cadence: background readings every few minutes, only while still; about every 5 s in a workout.
  Inferred. Series 12 watches sample as often as every 5 s all day ([Apple][apple-hr]); not modelled.
- The rest of the export is the daily records the phone store already holds:
  - resting heart rate, one record a day;
  - sleep as `HKCategoryValueSleepAnalysisAsleepUnspecified`;
  - weight, and cuff readings as a `HKCorrelationTypeIdentifierBloodPressure` with its two records both
    inside the correlation and again at top level (the DTD's note);
  - step counts, split into the day plan's walking bouts so they add up to the store's daily total.
- Blood glucose from Dexcom: mg/dL, no device attribute, `creationDate` three hours after the reading.
- Blood glucose from the Sibionics app: HealthKit's own mmol unit, `mmol<180.1558800000541>/L`. Verbatim
  in real exports.
- Sources: [real export 1][hk-sample], [real export 2][hk-sample2].

### Oura ring: API v2 `heartrate`

`{"data": [{timestamp, timestamp_unix, bpm, source}], "next_token": null}`. All four fields are required
in the OpenAPI 1.41 spec, and `source` is one of `awake`, `workout`, `rest`, `sleep`, `live`, `session`.
Verbatim ([OpenAPI][oura]). The `+00:00` timestamp form is inferred. Cadence is about 300 s, with bursts
of about 5 s in workouts (inferred).

### Xiaomi band: Zepp Life data export

`<uid>_<ms>/HEARTRATE_AUTO/HEARTRATE_AUTO_<ms>.csv`, header `date,time,heartRate`, device-local
`YYYY-MM-DD` and `HH:MM`, no offset. Inferred from four independent parsers ([one of them][zepp]). The
automatic interval is a user setting of 1, 5 or 10 minutes.

### Huawei and Android watches

These are written only as mirobody's `/api/data` batches. The field names come from mirobody's crosswalks
(`com.huawei.instantaneous.heart_rate:bpm`, `HeartRateRecord`). Huawei samples about once a minute
(inferred, [Huawei Health Kit][huawei-hr]); the Android cadence is unsure.

### Heart-rate formats not modelled yet

- **Garmin Health API dailies.** The real `timeOffsetHeartRateSamples` is a map
  `{"15": 75, "30": 76}` of 15-second offsets. mirobody's sample and decoder use a list of objects, and
  fed the real shape, its decoder produces no heart-rate facts. Not written, because the vendor-cloud
  Garmin dailies (`vendor_signals.py`) already exist in the list shape; the next change there should
  decide which shape is pinned.
- **Fitbit Web API intraday.** Retired: support ends 2026-09-30 and it is turned off on 2026-10-30. Its
  successor is the Google Health API `dataPoints` ([Fitbit][fitbit], [Google Health][google-health]).
- **WHOOP**: no intraday heart rate in the public API ([OpenAPI][whoop]).
- **Huawei Health Kit REST** (sample points with nanosecond times, CGM type `com.huawei.cgm_blood_glucose`;
  [Huawei glucose][huawei-glucose]).
- **Mi Fitness CSV** (`Uid,Sid,Key,Time,Value,UpdateTime`; the JSON keys inside `Value` are unsure).
- **Health Connect**: it has no wire format, only Kotlin objects ([androidx][health-connect]); its
  backup is an undocumented SQLite file.

## Modelling choices that are not device facts

These are hand-set and marked so in `streams.json`:

- outage rate and length (log-uniform), and fingerstick calibration rate;
- compression lows: 12% of nights, 25–50% deep, 20–60 min;
- scan spacing for Libre 2: every 1.5–6 waking hours;
- which phone is iOS (the Apple Health store) and which is Android;
- device and unit shares by language group;
- the share of hospital-placed Sibionics sensors;
- optical heart-rate noise, charging habits, and a night without a store sleep record being a night the
  watch was on its charger.

The physiology is cited in `continuous.py`:

- ADAG, eAG = 1.59 × HbA1c − 2.59 mmol/L ([Nathan 2008](https://doi.org/10.2337/dc08-0545));
- GMI = 3.31 + 0.02392 × mean mg/dL ([Bergenstal 2018](https://doi.org/10.2337/dc18-1581));
- time-in-range thresholds ([Battelino 2019](https://doi.org/10.2337/dci19-0028));
- interstitial lag ([Basu 2013](https://doi.org/10.2337/db13-1132));
- dawn phenomenon ([Monnier 2013](https://doi.org/10.2337/dc12-2127));
- HRmax = 208 − 0.7 × age ([Tanaka 2001](https://doi.org/10.1016/S0735-1097(00)01054-8)).

## Open questions

- Dexcom: whether the G7 app writes mmol/L to HealthKit for mmol users; v3 record order; whether
  Clarity writes CRLF natively.
- LibreView: how HI is written; the header language of Japanese and Chinese accounts (we write English);
  whether a single-digit day is zero-padded in the file name.
- Sibionics: the hospital CSV's BOM and line endings; the app's HealthKit source name and write delay;
  the low end of its clamp.
- Heart-rate background cadence for Huawei and Android watches.
- Reports: the layouts of the iCan, Yuwell and AiDEX apps' own reports, and the header fields and file names of
  every vendor's report PDF; the international Sibionics app's AGP export beyond "IDC-licensed".
- CareLink: how a reading past 40–400 mg/dL is written, the export's file name, and the mmol/L variant's values.
- Tidepool: `deviceId` per vendor and `payload` contents.

## What this shows about mirobody (checked against a local checkout, 2026-10-09)

The generated Apple export and Oura documents decode cleanly through mirobody's own
`apple_export.iter_items` + `apple.decode` and `oura.decode`. Every record yields a fact.

Three gaps the real formats would expose, all found by the format research rather than by these files:

1. Apple's mmol unit string `mmol<180.1558800000541>/L` is not converted, so the reading is dropped. A
   Sibionics wearer's Apple Health records exercise this.
2. A real Garmin `timeOffsetHeartRateSamples` map decodes to no heart-rate facts.
3. In older checkouts, an Oura timestamp at exactly `T00:00:00+00:00` is re-read as local midnight.
   Upstream has fixed this.

[umich]: https://teamdynamix.umich.edu/TDClient/210/DepressionCenter/Questions/Details/100073
[glyapp]: https://perso.univ-rennes1.fr/joris.heyman/glyapp_doc/html/sensors.html
[dex-api]: https://developer.dexcom.com/docs/swaggerv3/other/getestimatedglucosevaluesv3
[dex-api-overview]: https://developer.dexcom.com/docs/dexcomv3/endpoint-overview/
[dex-train]: https://www.dexcom.com/en-ca/training
[dex-backfill]: https://www.dexcom.com/en-GB/blog/troubleshoot-common-dexcom-issues-and-alerts
[dex-apple]: https://www.dexcom.com/en-us/m/faqs/how-do-i-share-my-glucose-data-with-the-apple-health-app
[g7-fda]: https://www.accessdata.fda.gov/cdrh_docs/pdf21/K213919.pdf
[g7-mard]: https://pmc.ncbi.nlm.nih.gov/articles/PMC9208857/
[g6-mard]: https://pmc.ncbi.nlm.nih.gov/articles/PMC6422005/
[s-g7]: https://github.com/GlucoTrack-Cooperatives/Diabetes_Management_Frontend
[s-g6]: https://github.com/namebrandon/blood_glucose_with_tft
[tidepool-platform]: https://github.com/tidepool-org/platform
[l3-manual]: https://freestyleserver.com/Payloads/IFU/2022/q2/ART46090-003_rev-A.pdf
[l-8h]: https://www.freestyle.abbott/en-ph/support/what-happens-if-you-dont-scan-the-sensor-within-8-hours.html
[l-ada]: https://consumerguide.diabetes.org/products/freestyle-libre-14-day-system
[l2-mard]: https://hcplive.com/view/fda-approves-14day-freestyle-libre-glucose-monitoring-system
[l3-mard]: https://www.fiercebiotech.com/medtech/ada-abbotts-freestyle-libre-3-scores-highest-accuracy-14-day-cgm-clinical-trial
[l-nohk]: https://loopkit.github.io/loopdocs/faqs/apple-health-faqs/
[lv-settings]: https://github.com/shrugalic/LibreView_to_AppleHealth_converter
[lv-tidepool]: https://github.com/tidepool-org/uploader
[lv-juggluco]: https://github.com/j-kaltes/Juggluco/blob/primary/Common/src/main/cpp/export/libreviewexport.cpp
[s-l3]: https://github.com/rainbowpuffpuff/agentic_health
[s-lus]: https://github.com/jeffreyruoss/cgm-food-repsonse-assessment
[s-lmmol]: https://github.com/mrvisser/qh-charts
[s-luk]: https://github.com/Warren8824/cgm-data-processor
[s-lreader]: https://github.com/MartynK/Martysweight
[nsloader]: https://github.com/jonmorrissey/NightscoutLoader
[sib-guide]: https://www.diabettech.com/wp-content/uploads/2024/01/GS1_A0_CGM_App_User_Guide_English_mg-dL.pdf
[sib-zhihu]: https://zhuanlan.zhihu.com/p/1962188745005564665
[sib-serials]: https://github.com/j-kaltes/Juggluco/blob/HEAD/Common/src/mobileSi/java/tk/glucodata/PhotoScan.java
[sib-apple]: https://apps.apple.com/cn/app/id1574026555
[sib-clinic]: https://github.com/2233158/ABPM_CGM_calculator/blob/HEAD/_build/builtin_glu_raw.csv
[sib-xlsx]: https://github.com/daedalus/agp_tool/tree/master/examples
[sib-xlsx-conv]: https://github.com/suhajdab/Sibionics-to-HealthCsvImport
[glucontrol]: https://github.com/Kihaku-zhou/GlucoControl/blob/HEAD/lib/data/health/importers/sibionics_csv_importer.dart
[ican]: https://www.icancgm.com/products/ican-i3/
[ican-manual]: https://www.manualslib.com/manual/3429867/Sinocare-Ican-I3-Cgm.html
[ican-cn]: https://apps.apple.com/cn/app/%E7%88%B1%E7%9C%8B%E5%81%A5%E5%BA%B7/id1547909127
[ct-aidr]: https://github.com/madleina/aidR/blob/HEAD/inst/extdata/carelink_example.csv
[ct-gg]: https://github.com/lumose-health/GlycemicGPT/blob/HEAD/apps/api/tests/test_carelink_csv.py
[ct-odiak]: https://github.com/odiak/carelink-csv-visualizer/blob/HEAD/src/parse.ts
[ct-dao]: https://github.com/GlucoseDAO/glucose_data_processing/blob/HEAD/formats/medtronic/MEDTRONIC.md
[yuwell]: https://www.yuwellanytime.co.uk/product/yuwell-anytime-sensor/
[aidex]: https://www.microtechmd.com/cn/products/cgms
[medtrum]: https://www.dvn.nl/wp-content/uploads/2025/08/TouchCare-Nano-CGM-versie-11-7-2025.pdf
[glunovo]: https://www.dvn.nl/wp-content/uploads/2025/08/Glunovo-versie-11-7-2025.pdf
[sisensing]: https://github.com/ImLovinIt/nightscout-sisensingcgm-uploader
[aidex-json]: https://github.com/yichi2077/CGM-Agent/blob/HEAD/src/hermes_cgm_agent/services/aidex/mapper.py
[ns-swagger]: https://github.com/nightscout/cgm-remote-monitor/blob/master/lib/server/swagger.yaml
[ns-entries]: https://github.com/nightscout/cgm-remote-monitor/blob/master/lib/server/entries.js
[tp-cbg]: https://github.com/tidepool-org/TidepoolApi/tree/master/docs/device-data
[xdrip]: https://github.com/NightscoutFoundation/xDrip/blob/master/app/src/main/java/com/eveningoutpost/dexdrip/utils/DatabaseUtil.java
[pydexcom]: https://github.com/gagebenne/pydexcom
[llu-fixture]: https://github.com/timoschlueter/nightscout-librelink-up
[hk-motion]: https://developer.apple.com/documentation/healthkit/hkheartratemotioncontext
[apple-hr]: https://support.apple.com/en-us/120277
[hk-sample]: https://github.com/grll/apple-health-mcp
[hk-sample2]: https://github.com/AbhikChowdhury6/dataImport
[oura]: https://cloud.ouraring.com/v2/static/json/openapi-1.41.json
[zepp]: https://github.com/zoilomora/xiaomi-mi-fit-data-export
[huawei-hr]: https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/heart-rate-0000001131423780
[huawei-glucose]: https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/blood-glucose-0000001177423531
[fitbit]: https://dev.fitbit.com/build/reference/web-api/intraday/get-heartrate-intraday-by-date/
[google-health]: https://developers.google.com/health/reference/rest/v4/users.dataTypes.dataPoints
[whoop]: https://api.prod.whoop.com/developer/doc/openapi.json
[health-connect]: https://github.com/androidx/androidx/tree/androidx-main/health/connect/connect-client
[yuwell-glucodroid]: https://github.com/GlucoDroid/app/blob/HEAD/Common/src/main/java/tk/glucodata/drivers/anytime/AnytimeConstants.kt
[yuwell-trial]: https://pmc.ncbi.nlm.nih.gov/articles/PMC13366774/
[ct-g4]: https://github.com/zinojeng/CGM/blob/HEAD/140692Ho.csv
[ct-2018]: https://github.com/danny98m/GluGo2.0/blob/HEAD/csvData/csvInData/Boylan_Medtronic_1.csv
[ct-nsl]: https://github.com/gh-davidr/NightscoutLoader/blob/HEAD/src/main/java/entity/DBResultMedtronicNew.java
[ct-etauker]: https://github.com/etauker-projects/home-automation/blob/HEAD/scripts/health-data/src/model/schema.ts
[g4-ada]: https://consumerguide.diabetes.org/node/1976
[ns-api]: https://github.com/nightscout/cgm-remote-monitor/blob/master/lib/api/entries/index.js
[ns-share2]: https://github.com/nightscout/share2nightscout-bridge/blob/master/index.js
[ns-llu]: https://github.com/timoschlueter/nightscout-librelink-up/blob/main/src/nightscout/apiv1.ts
[xdrip-up]: https://github.com/NightscoutFoundation/xDrip/blob/master/app/src/main/java/com/eveningoutpost/dexdrip/utilitymodels/NightscoutUploader.java
[tp-export]: https://github.com/tidepool-org/export
[tp-tools]: https://github.com/tidepool-org/node-data-tools
[tp-blip]: https://github.com/tidepool-org/blip/blob/develop/app/components/ExportDialog.js
[tp-glucose]: https://github.com/tidepool-org/platform/blob/master/data/blood/glucose/glucose.go
[tp-sample]: https://github.com/madleina/aidR/blob/HEAD/inst/extdata/tidepool_example.xlsx
[pylibrelinkup]: https://github.com/robberwick/pylibrelinkup
[agp-idc]: https://www.agpreport.org/agp/agpreports
[agp-v5-guide]: https://www.healthpartners.com/institute/wp-content/uploads/2025/05/Determine-Where-to-Act.pdf
[agp-titr]: https://www.healthpartners.com/institute/wp-content/uploads/2025/09/AGP-Report-TITR.060125proof.pdf
[agp-cn2023]: https://news.qq.com/rain/a/20240305A07YB700
[cgm-cn2017]: https://seleguide.yiigle.com/uploads/guide_html/%E4%B8%AD%E5%9B%BD%E6%8C%81%E7%BB%AD%E8%91%A1%E8%90%84%E7%B3%96%E7%9B%91%E6%B5%8B%E4%B8%B4%E5%BA%8A%E5%BA%94%E7%94%A8%E6%8C%87%E5%8D%97%EF%BC%882017%E5%B9%B4%E7%89%88%EF%BC%89.html
[libreview-zh]: https://files.libreview.io/files/documents/zh-CN/FSReportTour_2026-05-06.pdf
[sib-report-2023]: https://zhongce.sina.com.cn/article/view/181774
[sib-report-2022]: https://news.qq.com/rain/a/20230627A08ARQ00
