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
| CGM | Sibionics GS1 (硅基动感) | hospital-system CSV (sensor placed in clinic); app Excel export (international app); Apple Health records from 2024-07-01 | file upload; phone store | mostly Chinese-speaking wearers |
| CGM | Sinocare iCan i3 (三诺爱看) | none: the app gives reports and screenshots only | — | Chinese-speaking wearers |
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

A Chinese home wearer on Android gets no file: the domestic app documents no raw export, and an importer
written for it has to guess among header aliases ([GlucoControl][glucontrol]).

### Sinocare iCan i3 (三诺爱看)

- Device facts, verbatim ([iCan i3][ican], [manual][ican-manual]): every 3 minutes, 15 days, 2 h
  warm-up, 2.0–25.0 mmol/L, MARD 8.71%.
- No raw export. The app produces AGP and history reports as PDF, and the Chinese app reads from Apple
  Health and Huawei Health rather than writing to them ([App Store CN][ican-cn]). The session exists in
  the truth with no file and the hazard `stream.no_export`.

### CGM formats not modelled yet

Each is a candidate for a follow-up; the evidence is already gathered:

- **Medtronic CareLink CSV** (Guardian 4, Simplera, 780G). A preamble, then `-------` sections and 53–54
  columns; newest first; `;` delimiter and decimal comma in EU locales. The cohort has no insulin-pump
  users. Sources: [aidR sample][ct-aidr], [GlycemicGPT fixture][ct-gg], [parser][ct-odiak],
  [GlucoseDAO notes][ct-dao].
- **Yuwell Anytime CT3/CT15**: 3 min, 14–16 days, MARD 9.1%. **MicroTech AiDEX / LinX**: 5 or 1 min,
  14–15 days. **Medtrum TouchCare Nano**: 2 min. **Glunovo**: 3 min, Excel export with undocumented
  columns. None publishes a raw export ([Yuwell][yuwell], [AiDEX][aidex], [Medtrum sheet][medtrum],
  [Glunovo sheet][glunovo]).
- **Sisensing and AiDEX cloud JSON**: unofficial, reverse-engineered ([Sisensing uploader][sisensing],
  [AiDEX mapper][aidex-json]).
- **Nightscout `entries`**: `sgv` always in mg/dL; the server rewrites `dateString` to UTC and adds
  `utcOffset` and `sysTime` ([swagger][ns-swagger], [entries.js][ns-entries]).
- **Tidepool `cbg`**: stored in mmol/L only, mg/dL divided by 18.01559 without rounding ([cbg][tp-cbg]).
- **xDrip+** SiDiary CSV and SQLite exports ([DatabaseUtil][xdrip]).
- **Dexcom Share** ([pydexcom][pydexcom]), **LibreLinkUp graph** (last 12 h only;
  [fixture][llu-fixture]), **Eversense** (no public export).
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
