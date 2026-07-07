from pathlib import Path
import pandas as pd

# ======================
# 路徑設定
# ======================
BASE_DIR = Path(__file__).resolve().parents[1]

PROCESSED_DIR = BASE_DIR / "data" / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

LOAD_1MIN_PATH = PROCESSED_DIR / "load" / "daily_appliance_power_1min_kw.csv"
LOAD_15MIN_PATH = PROCESSED_DIR / "load" / "daily_appliance_power_15min_kw.csv"

PV_1MIN_PATH = PROCESSED_DIR / "pv" / "pv_curve_1min.csv"
PV_15MIN_PATH = PROCESSED_DIR / "pv" / "pv_curve_15min.csv"

OUTPUT_1MIN_PATH = PROCESSED_DIR / "Unoptimized_Battery_Data_1min.csv"
OUTPUT_15MIN_PATH = PROCESSED_DIR / "Unoptimized_Battery_Data_15min.csv"


# ======================
# 欄位名稱
# ======================
TIME_COL = "Time"
LOAD_COL = "total_power_kw"
PV_COL = "pv_kw"


# ======================
# 電池參數
# ======================
BATTERY_CAPACITY_KWH = 10.0

SOC_INIT = 50.0
SOC_MIN = 20.0
SOC_MAX = 90.0

MAX_CHARGE_KW = 3.0
MAX_DISCHARGE_KW = 3.0


# ======================
# 時間格式整理
# ======================
def fix_time_format(df, time_col):
    df[time_col] = pd.to_datetime(df[time_col].astype(str)).dt.strftime("%H:%M")
    return df


# ======================
# 讀取負載 + 太陽能資料
# ======================
def load_and_merge_data(load_path, pv_path):
    load_df = pd.read_csv(load_path)
    pv_df = pd.read_csv(pv_path)

    # 如果太陽能時間欄位叫 time，就改成 Time
    if "time" in pv_df.columns:
        pv_df = pv_df.rename(columns={"time": "Time"})

    # 時間格式統一成 HH:MM
    load_df = fix_time_format(load_df, "Time")
    pv_df = fix_time_format(pv_df, "Time")

    # 合併負載與太陽能
    df = pd.merge(
        load_df,
        pv_df[["Time", PV_COL]],
        on="Time",
        how="left"
    )

    # 沒有對到的太陽能資料補 0
    df[PV_COL] = df[PV_COL].fillna(0)

    return df


# ======================
# 電池模擬
# ======================
def simulate_battery(df, interval_min):
    soc = SOC_INIT
    dt = interval_min / 60

    result = []

    for i in range(len(df)):
        time = df.loc[i, TIME_COL]
        load_kw = df.loc[i, LOAD_COL]
        pv_kw = df.loc[i, PV_COL]

        # 第一部分：目前功率 = 負載功率 - 太陽能功率
        net_kw = load_kw - pv_kw

        battery_kw = 0.0
        grid_kw = 0.0
        mode = "Idle"

        # 第三部分：電池小於 20%，停止放電，從電網充電
        if soc < SOC_MIN:
            can_charge_kwh = BATTERY_CAPACITY_KWH * (SOC_MAX - soc) / 100
            charge_kw = min(MAX_CHARGE_KW, can_charge_kwh / dt)

            battery_kw = -charge_kw
            grid_kw = max(net_kw, 0) + charge_kw
            mode = "Grid Charge"

            soc = soc + charge_kw * dt / BATTERY_CAPACITY_KWH * 100

        else:
            # 第二部分：net_kw < 0，太陽能多，電池充電
            if net_kw < 0:
                surplus_kw = -net_kw

                can_charge_kwh = BATTERY_CAPACITY_KWH * (SOC_MAX - soc) / 100
                charge_kw = min(surplus_kw, MAX_CHARGE_KW, can_charge_kwh / dt)

                battery_kw = -charge_kw
                grid_kw = net_kw + charge_kw
                mode = "PV Charge"

                soc = soc + charge_kw * dt / BATTERY_CAPACITY_KWH * 100

            # 第二部分：net_kw > 0，負載不夠，電池放電
            elif net_kw > 0:
                can_discharge_kwh = BATTERY_CAPACITY_KWH * (soc - SOC_MIN) / 100
                discharge_kw = min(net_kw, MAX_DISCHARGE_KW, can_discharge_kwh / dt)

                battery_kw = discharge_kw
                grid_kw = net_kw - discharge_kw
                mode = "Discharge"

                soc = soc - discharge_kw * dt / BATTERY_CAPACITY_KWH * 100

        # 限制 SOC 範圍
        if soc > SOC_MAX:
            soc = SOC_MAX

        if soc < 0:
            soc = 0

        result.append({
            "Time": time,
            "Load_kW": load_kw,
            "PV_kW": pv_kw,
            "Net_kW": net_kw,
            "Battery_kW": battery_kw,
            "Grid_kW": grid_kw,
            "Grid_Import_kW": max(grid_kw, 0),
            "Grid_Export_kW": abs(min(grid_kw, 0)),
            "SOC_%": soc,
            "Mode": mode
        })

    return pd.DataFrame(result)


# ======================
# 主程式
# ======================
df_1min = load_and_merge_data(LOAD_1MIN_PATH, PV_1MIN_PATH)
result_1min = simulate_battery(df_1min, interval_min=1)
result_1min.to_csv(OUTPUT_1MIN_PATH, index=False, encoding="utf-8-sig")


df_15min = load_and_merge_data(LOAD_15MIN_PATH, PV_15MIN_PATH)
result_15min = simulate_battery(df_15min, interval_min=15)
result_15min.to_csv(OUTPUT_15MIN_PATH, index=False, encoding="utf-8-sig")


print("完成 1min 電池資料：", OUTPUT_1MIN_PATH)
print("完成 15min 電池資料：", OUTPUT_15MIN_PATH)