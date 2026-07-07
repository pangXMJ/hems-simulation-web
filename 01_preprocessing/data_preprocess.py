# ============================================================
# data_preprocess.py
# 將 1 分鐘設備用電資料整理成：
# 1. 各設備功率 kW + 總功率 kW 的 1 分鐘資料
# 2. 各設備功率 kW + 總功率 kW 的 15 分鐘資料
#
# 輸入：
# data/raw/daily_appliance_data_1min.csv
#
# 輸出：
# data/processed/daily_appliance_power_1min_kw.csv
# data/processed/daily_appliance_power_15min_kw.csv
# ============================================================

from pathlib import Path
import pandas as pd


# ============================================================
# 1. 路徑設定
# ============================================================

BASE_DIR = Path(__file__).resolve().parents[1]

RAW_DATA_PATH = BASE_DIR / "data" / "raw" / "daily_appliance_data_1min.csv"

PROCESSED_DIR = BASE_DIR / "data" / "processed"

OUTPUT_1MIN_PATH = PROCESSED_DIR / "daily_appliance_power_1min_kw.csv"
OUTPUT_15MIN_PATH = PROCESSED_DIR / "daily_appliance_power_15min_kw.csv"

BASE_DATE = "2026-01-01"
W_TO_KW = 1000.0


# ============================================================
# 2. 讀取並整理 1 分鐘資料
# ============================================================

def load_and_prepare_1min_data(raw_path: Path):
    if not raw_path.exists():
        raise FileNotFoundError(f"找不到原始資料檔案：{raw_path}")

    df = pd.read_csv(raw_path)

    required_columns = ["Time", "Hour", "Minute"]

    for col in required_columns:
        if col not in df.columns:
            raise ValueError(f"原始資料缺少必要欄位：{col}")

    time_columns = ["Time", "Hour", "Minute"]

    ignore_columns = [
        "timestamp",
        "step",
        "minute_of_day",
        "total_power",
        "total_power_w",
        "total_power_kw",
        "energy_kwh",
    ]

    device_columns = [
        col for col in df.columns
        if col not in time_columns
        and col not in ignore_columns
    ]

    if len(device_columns) == 0:
        raise ValueError("找不到任何設備功率欄位")

    df["Hour"] = pd.to_numeric(df["Hour"], errors="coerce").fillna(0).astype(int)
    df["Minute"] = pd.to_numeric(df["Minute"], errors="coerce").fillna(0).astype(int)

    df["minute_of_day"] = df["Hour"] * 60 + df["Minute"]

    df["timestamp"] = (
        pd.to_datetime(BASE_DATE)
        + pd.to_timedelta(df["minute_of_day"], unit="m")
    )

    df["step"] = range(len(df))

    df["Time"] = (
        df["Hour"].astype(str).str.zfill(2)
        + ":"
        + df["Minute"].astype(str).str.zfill(2)
    )

    device_kw_columns = []

    for col in device_columns:
        new_col = f"{col}_kw"

        df[new_col] = (
            pd.to_numeric(df[col], errors="coerce")
            .fillna(0)
            / W_TO_KW
        )

        device_kw_columns.append(new_col)

    df["total_power_kw"] = df[device_kw_columns].sum(axis=1)

    output_columns = (
        ["step", "Time", "Hour", "Minute", "minute_of_day"]
        + device_kw_columns
        + ["total_power_kw"]
    )

    df_1min = df[output_columns + ["timestamp"]].copy()

    return df_1min, device_kw_columns


# ============================================================
# 3. 轉成 15 分鐘資料
# ============================================================

def resample_to_15min(df_1min: pd.DataFrame, device_kw_columns: list):
    df = df_1min.copy()
    df = df.set_index("timestamp")

    df_15min = df[device_kw_columns].resample(
        "15min",
        label="left",
        closed="left"
    ).mean()

    df_15min = df_15min.reset_index()

    df_15min["Hour"] = df_15min["timestamp"].dt.hour
    df_15min["Minute"] = df_15min["timestamp"].dt.minute

    df_15min["Time"] = (
        df_15min["Hour"].astype(str).str.zfill(2)
        + ":"
        + df_15min["Minute"].astype(str).str.zfill(2)
    )

    df_15min["step"] = range(len(df_15min))

    df_15min["minute_of_day"] = (
        df_15min["Hour"] * 60
        + df_15min["Minute"]
    )

    df_15min["total_power_kw"] = df_15min[device_kw_columns].sum(axis=1)

    df_15min["energy_kwh"] = df_15min["total_power_kw"] * 0.25

    output_columns = (
        ["step", "Time", "Hour", "Minute", "minute_of_day"]
        + device_kw_columns
        + ["total_power_kw", "energy_kwh"]
    )

    df_15min = df_15min[output_columns].copy()

    return df_15min


# ============================================================
# 4. 安全輸出 CSV
# ============================================================

def save_csv_safely(df: pd.DataFrame, output_path: Path):
    try:
        df.to_csv(output_path, index=False, encoding="utf-8-sig")
    except PermissionError:
        print(f"輸出失敗：{output_path}")
        print("請確認這個 CSV 沒有被 Excel、VS Code 預覽或其他程式開啟。")
        raise


# ============================================================
# 5. 主程式
# ============================================================

def main():
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    print("開始讀取 1 分鐘原始設備資料...")
    df_1min, device_kw_columns = load_and_prepare_1min_data(RAW_DATA_PATH)

    print("開始轉換成 15 分鐘資料...")
    df_15min = resample_to_15min(df_1min, device_kw_columns)

    df_1min_output = df_1min.drop(columns=["timestamp"])

    save_csv_safely(df_1min_output, OUTPUT_1MIN_PATH)
    save_csv_safely(df_15min, OUTPUT_15MIN_PATH)

    print("轉換完成")
    print(f"1 分鐘 kW 資料輸出：{OUTPUT_1MIN_PATH}")
    print(f"15 分鐘 kW 資料輸出：{OUTPUT_15MIN_PATH}")
    print(f"1 分鐘資料筆數：{len(df_1min_output)}")
    print(f"15 分鐘資料筆數：{len(df_15min)}")
    print("設備功率欄位，單位皆為 kW：")

    for col in device_kw_columns:
        print(f"  - {col}")


if __name__ == "__main__":
    main()