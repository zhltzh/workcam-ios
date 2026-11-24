# B02-W016 LiDAR Measurement Analysis Report

**DATA ORIGIN: DESKTOP_SIMULATION** - synthetic data generated to validate the analyzer. **NOT real iPhone measurement results.** Real-device status: **DEVICE_PENDING**.

## Row accounting

- rows received: **134** (valid 133 + invalid 1)
- valid samples: 112 | failures: 21 | outliers reported (kept in stats): 3
- modes present: raycast, scene_depth, smoothed_depth
- parser warnings: 2

## Matrix verification (declared vs actual)

- checklist declared total: **132** | actual recorded rows: **133** | match: **NO**
- completion by test_id: 132 / 132 (100.0 %) | missing: 0 | unexpected ids: 0

| mode | declared | actual | match |
|---|---|---|---|
| raycast | 68 | 69 | MISMATCH |
| scene_depth | 32 | 32 | OK |
| smoothed_depth | 32 | 32 | OK |

**1 cell-level differences:**

| cell | declared | actual | diff |
|---|---|---|---|
| raycast/normal/1m | 4 | 5 | +1 |

## Per-mode statistics (modes strictly separated)

No cross-mode error statistics exist in this report by design: raycast / scene_depth / smoothed_depth are never merged.

| group | attempts | valid | failures | success | mean signed | MAE | RMSE | median | P95 | max |
|---|---|---|---|---|---|---|---|---|---|---|
| raycast | 69 | 54 | 15 | 78.3% | 0.1132 m | 0.1381 m | 0.8195 m | 0.0135 m | 0.0904 m | 6.0000 m |
| scene_depth | 32 | 29 | 3 | 90.6% | 0.0598 m | 0.0633 m | 0.2447 m | 0.0110 m | 0.0734 m | 1.3100 m |
| smoothed_depth | 32 | 29 | 3 | 90.6% | 0.0097 m | 0.0118 m | 0.0170 m | 0.0060 m | 0.0384 m | 0.0410 m |

### Grouped by condition

| group | attempts | valid | failures | success | mean signed | MAE | RMSE | median | P95 | max |
|---|---|---|---|---|---|---|---|---|---|---|
| raycast/glass | 8 | 1 | 7 | 12.5% | n/a | n/a | n/a | n/a | n/a | n/a |
| raycast/low_light | 16 | 16 | 0 | 100.0% | 0.3894 m | 0.3976 m | 1.5003 m | 0.0185 m | 1.5472 m | 6.0000 m |
| raycast/normal | 21 | 20 | 1 | 95.2% | -0.0285 m | 0.0308 m | 0.1055 m | 0.0060 m | 0.0397 m | 0.4700 m |
| raycast/oblique | 8 | 8 | 0 | 100.0% | 0.0261 m | 0.0261 m | 0.0344 m | 0.0205 m | 0.0641 m | 0.0760 m |
| raycast/reflective | 8 | 1 | 7 | 12.5% | n/a | n/a | n/a | n/a | n/a | n/a |
| raycast/textureless | 8 | 8 | 0 | 100.0% | 0.0171 m | 0.0179 m | 0.0243 m | 0.0170 m | 0.0433 m | 0.0510 m |
| scene_depth/glass | 8 | 5 | 3 | 62.5% | 0.0476 m | 0.0476 m | 0.0544 m | 0.0510 m | 0.0772 m | 0.0810 m |
| scene_depth/low_light | 8 | 8 | 0 | 100.0% | 0.0176 m | 0.0206 m | 0.0238 m | 0.0180 m | 0.0382 m | 0.0420 m |
| scene_depth/normal | 8 | 8 | 0 | 100.0% | 0.1609 m | 0.1699 m | 0.4632 m | 0.0080 m | 0.8550 m | 1.3100 m |
| scene_depth/textureless | 8 | 8 | 0 | 100.0% | 0.0084 m | 0.0094 m | 0.0114 m | 0.0065 m | 0.0195 m | 0.0220 m |
| smoothed_depth/glass | 8 | 5 | 3 | 62.5% | 0.0274 m | 0.0274 m | 0.0315 m | 0.0360 m | 0.0408 m | 0.0410 m |
| smoothed_depth/low_light | 8 | 8 | 0 | 100.0% | 0.0080 m | 0.0117 m | 0.0148 m | 0.0100 m | 0.0263 m | 0.0270 m |
| smoothed_depth/normal | 8 | 8 | 0 | 100.0% | 0.0018 m | 0.0037 m | 0.0043 m | 0.0040 m | 0.0067 m | 0.0070 m |
| smoothed_depth/textureless | 8 | 8 | 0 | 100.0% | 0.0084 m | 0.0101 m | 0.0137 m | 0.0070 m | 0.0255 m | 0.0290 m |

### Grouped by material

| group | attempts | valid | failures | success | mean signed | MAE | RMSE | median | P95 | max |
|---|---|---|---|---|---|---|---|---|---|---|
| raycast/glass | 8 | 1 | 7 | 12.5% | n/a | n/a | n/a | n/a | n/a | n/a |
| raycast/matte_white | 45 | 44 | 1 | 97.8% | 0.1334 m | 0.1634 m | 0.9076 m | 0.0135 m | 0.0741 m | 6.0000 m |
| raycast/mirror | 8 | 1 | 7 | 12.5% | n/a | n/a | n/a | n/a | n/a | n/a |
| raycast/textureless | 8 | 8 | 0 | 100.0% | 0.0171 m | 0.0179 m | 0.0243 m | 0.0170 m | 0.0433 m | 0.0510 m |
| scene_depth/glass | 8 | 5 | 3 | 62.5% | 0.0476 m | 0.0476 m | 0.0544 m | 0.0510 m | 0.0772 m | 0.0810 m |
| scene_depth/matte_white | 16 | 16 | 0 | 100.0% | 0.0892 m | 0.0953 m | 0.3280 m | 0.0105 m | 0.3590 m | 1.3100 m |
| scene_depth/textureless | 8 | 8 | 0 | 100.0% | 0.0084 m | 0.0094 m | 0.0114 m | 0.0065 m | 0.0195 m | 0.0220 m |
| smoothed_depth/glass | 8 | 5 | 3 | 62.5% | 0.0274 m | 0.0274 m | 0.0315 m | 0.0360 m | 0.0408 m | 0.0410 m |
| smoothed_depth/matte_white | 16 | 16 | 0 | 100.0% | 0.0049 m | 0.0077 m | 0.0109 m | 0.0050 m | 0.0255 m | 0.0270 m |
| smoothed_depth/textureless | 8 | 8 | 0 | 100.0% | 0.0084 m | 0.0101 m | 0.0137 m | 0.0070 m | 0.0255 m | 0.0290 m |

### Grouped by lighting

| group | attempts | valid | failures | success | mean signed | MAE | RMSE | median | P95 | max |
|---|---|---|---|---|---|---|---|---|---|---|
| raycast/low_light | 16 | 16 | 0 | 100.0% | 0.3894 m | 0.3976 m | 1.5003 m | 0.0185 m | 1.5472 m | 6.0000 m |
| raycast/normal | 53 | 38 | 15 | 71.7% | -0.0031 m | 0.0288 m | 0.0812 m | 0.0100 m | 0.0822 m | 0.4700 m |
| scene_depth/low_light | 8 | 8 | 0 | 100.0% | 0.0176 m | 0.0206 m | 0.0238 m | 0.0180 m | 0.0382 m | 0.0420 m |
| scene_depth/normal | 24 | 21 | 3 | 87.5% | 0.0758 m | 0.0796 m | 0.2872 m | 0.0090 m | 0.0810 m | 1.3100 m |
| smoothed_depth/low_light | 8 | 8 | 0 | 100.0% | 0.0080 m | 0.0117 m | 0.0148 m | 0.0100 m | 0.0263 m | 0.0270 m |
| smoothed_depth/normal | 24 | 21 | 3 | 87.5% | 0.0104 m | 0.0118 m | 0.0178 m | 0.0060 m | 0.0400 m | 0.0410 m |

### Grouped by baseline distance

| group | attempts | valid | failures | success | mean signed | MAE | RMSE | median | P95 | max |
|---|---|---|---|---|---|---|---|---|---|---|
| raycast/0.5m | 4 | 4 | 0 | 100.0% | -0.1185 m | 0.1205 m | 0.2350 m | 0.0040 m | 0.4001 m | 0.4700 m |
| raycast/1m | 25 | 17 | 8 | 68.0% | 0.0153 m | 0.0174 m | 0.0246 m | 0.0130 m | 0.0392 m | 0.0760 m |
| raycast/2m | 8 | 8 | 0 | 100.0% | 0.0004 m | 0.0176 m | 0.0232 m | 0.0130 m | 0.0439 m | 0.0530 m |
| raycast/3m | 24 | 18 | 6 | 75.0% | 0.0146 m | 0.0221 m | 0.0347 m | 0.0135 m | 0.0609 m | 0.1170 m |
| raycast/5m | 8 | 7 | 1 | 87.5% | 0.8659 m | 0.8773 m | 2.2680 m | 0.0150 m | 4.2189 m | 6.0000 m |
| scene_depth/1m | 16 | 14 | 2 | 87.5% | 0.0117 m | 0.0166 m | 0.0264 m | 0.0100 m | 0.0556 m | 0.0810 m |
| scene_depth/3m | 16 | 15 | 1 | 93.8% | 0.1046 m | 0.1070 m | 0.3393 m | 0.0170 m | 0.4364 m | 1.3100 m |
| smoothed_depth/1m | 16 | 15 | 1 | 93.8% | 0.0077 m | 0.0111 m | 0.0157 m | 0.0060 m | 0.0312 m | 0.0410 m |
| smoothed_depth/3m | 16 | 14 | 2 | 87.5% | 0.0119 m | 0.0125 m | 0.0183 m | 0.0055 m | 0.0374 m | 0.0400 m |

### Grouped by pose

| group | attempts | valid | failures | success | mean signed | MAE | RMSE | median | P95 | max |
|---|---|---|---|---|---|---|---|---|---|---|
| raycast/oblique_30 | 8 | 8 | 0 | 100.0% | 0.0261 m | 0.0261 m | 0.0344 m | 0.0205 m | 0.0641 m | 0.0760 m |
| raycast/perpendicular | 61 | 46 | 15 | 75.4% | 0.1283 m | 0.1576 m | 0.8878 m | 0.0125 m | 0.1035 m | 6.0000 m |
| scene_depth/perpendicular | 32 | 29 | 3 | 90.6% | 0.0598 m | 0.0633 m | 0.2447 m | 0.0110 m | 0.0734 m | 1.3100 m |
| smoothed_depth/perpendicular | 32 | 29 | 3 | 90.6% | 0.0097 m | 0.0118 m | 0.0170 m | 0.0060 m | 0.0384 m | 0.0410 m |

## Repeatability (cell level only)

Repeatability = sample stddev of SIGNED errors of repeats at the **same baseline distance and condition** (cell = mode + condition + distance). It is null at every aggregation level above; the stddev of absolute errors is never used as repeatability.

| cell | attempts | valid | repeatability stddev |
|---|---|---|---|
| raycast/glass/1m | 4 | 0 | n/a |
| raycast/glass/3m | 4 | 1 | n/a |
| raycast/low_light/1m | 4 | 4 | 0.0070 m |
| raycast/low_light/2m | 4 | 4 | 0.0353 m |
| raycast/low_light/3m | 4 | 4 | 0.0201 m |
| raycast/low_light/5m | 4 | 4 | 2.9853 m |
| raycast/normal/0.5m | 4 | 4 | 0.2344 m |
| raycast/normal/1m | 5 | 5 | 0.0059 m |
| raycast/normal/2m | 4 | 4 | 0.0101 m |
| raycast/normal/3m | 4 | 4 | 0.0068 m |
| raycast/normal/5m | 4 | 3 | 0.0098 m |
| raycast/oblique/1m | 4 | 4 | 0.0315 m |
| raycast/oblique/3m | 4 | 4 | 0.0165 m |
| raycast/reflective/1m | 4 | 0 | n/a |
| raycast/reflective/3m | 4 | 1 | n/a |
| raycast/textureless/1m | 4 | 4 | 0.0133 m |
| raycast/textureless/3m | 4 | 4 | 0.0247 m |
| scene_depth/glass/1m | 4 | 2 | n/a |
| scene_depth/glass/3m | 4 | 3 | 0.0100 m |
| scene_depth/low_light/1m | 4 | 4 | 0.0176 m |
| scene_depth/low_light/3m | 4 | 4 | 0.0189 m |
| scene_depth/normal/1m | 4 | 4 | 0.0059 m |
| scene_depth/normal/3m | 4 | 4 | 0.6544 m |
| scene_depth/textureless/1m | 4 | 4 | 0.0088 m |
| scene_depth/textureless/3m | 4 | 4 | 0.0088 m |
| smoothed_depth/glass/1m | 4 | 3 | 0.0205 m |
| smoothed_depth/glass/3m | 4 | 2 | n/a |
| smoothed_depth/low_light/1m | 4 | 4 | 0.0166 m |
| smoothed_depth/low_light/3m | 4 | 4 | 0.0119 m |
| smoothed_depth/normal/1m | 4 | 4 | 0.0050 m |
| smoothed_depth/normal/3m | 4 | 4 | 0.0038 m |
| smoothed_depth/textureless/1m | 4 | 4 | 0.0110 m |
| smoothed_depth/textureless/3m | 4 | 4 | 0.0128 m |

## Preserved records (never silently dropped)

- failure rows: **21**
  - B02-W016-raycast-normal-5m-R2 (raycast/normal@5m): measurement_missing
  - B02-W016-raycast-glass-1m-R1 (raycast/glass@1m): depth_missing
  - B02-W016-raycast-glass-1m-R2 (raycast/glass@1m): depth_missing
  - B02-W016-raycast-glass-1m-R3 (raycast/glass@1m): depth_missing
  - B02-W016-raycast-glass-1m-R4 (raycast/glass@1m): depth_missing
  - B02-W016-raycast-glass-3m-R1 (raycast/glass@3m): depth_missing
  - B02-W016-raycast-glass-3m-R3 (raycast/glass@3m): depth_missing
  - B02-W016-raycast-glass-3m-R4 (raycast/glass@3m): depth_missing
  - B02-W016-raycast-reflective-1m-R1 (raycast/reflective@1m): no_return
  - B02-W016-raycast-reflective-1m-R2 (raycast/reflective@1m): no_return
  - ... and 11 more (see JSON)
- outliers (kept in statistics, reported for investigation): **3**
  - B02-W016-raycast-normal-0.5m-R4: measured <= 0.05 m (measured 0.03 m)
  - B02-W016-raycast-low_light-5m-R1: measured >= 10.0 m, |error| > 1.0 m (measured 11.0 m)
  - B02-W016-scene_depth-normal-3m-R2: |error| > 1.0 m (measured 4.31 m)
- invalid rows (missing/invalid distance_true_m): **1**
  - line 135: missing_or_invalid_distance_true_m

## Parser warnings

- line 134: DUPLICATE test_id 'B02-W016-raycast-normal-1m-R1' (first seen on line 6)
- line 135: missing_or_invalid_distance_true_m - row kept in invalid_rows

## Verdict

INCOMPLETE / NEEDS ATTENTION: declared matrix does NOT match actual records (132 declared vs 133 actual); duplicate test_ids present - affected shots must be re-measured; 1 invalid rows need re-export from Mac

_Reminder: this report describes SYNTHETIC validation data. It validates the analyzer only and must never be cited as iPhone LiDAR performance. Device data remains DEVICE_PENDING._
