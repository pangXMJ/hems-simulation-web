# 表燈（住商）電價 CSV 資料說明

本資料依使用者提供的電價截圖整理，方便後續在程式中用開關選擇不同計算方式。

## 檔案

- `electricity_tariffs_all.csv`：全部方案合併版，建議程式直接讀這份。
- `tariff_plan_metadata.csv`：方案基本資料，例如基本電費、超過 2000 度加價。
- `non_time_tier_rates.csv`：非時間電價級距表。
- `tou_simple_2stage_rates.csv`：簡易型時間電價二段式。
- `tou_simple_3stage_rates.csv`：簡易型時間電價三段式。

## 欄位重點

- `plan_code`：方案代碼，可作為前端 radio/selectbox 的值。
- `charge_type`：`energy_rate`、`basic_fee`、`surcharge`。
- `season_code`：`summer`、`non_summer`、`all`。
- `day_type_code`：`weekday`、`weekend_holiday`、`all`。
- `period_code`：`peak`、`mid_peak`、`off_peak`、`tier`。
- `start_minute` / `end_minute`：一天中的分鐘數，00:00 = 0，24:00 = 1440，方便程式判斷時段。
- `block_start_kwh` / `block_end_kwh`：非時間電價級距用。第一級 0~120，第二級 120~330，依此類推；最後一級 `block_end_kwh` 留空表示無上限。

## 方案代碼

- `non_tou_residential`：非時間電價-住宅用
- `tou_2stage_residential`：簡易型時間電價-二段式
- `tou_3stage_residential`：簡易型時間電價-三段式
