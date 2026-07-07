from pathlib import Path
import pandas as pd

# ============================================================
# convert_all_1min_to_15min_kw.py
# 功能：
# 1. 讀取三份 1 分鐘 CSV
# 2. 判斷 Time / Hour / Minute，建立內部 timestamp
# 3. 將功率欄位由 W 轉成 kW
# 4. 每 15 分鐘取平均，輸出 15 分鐘 kW 版本
#
# 注意：
# - 15 分鐘資料是「功率平均值」，不是 15 分鐘能量加總。
# - 日期只在程式內部用來 resample，輸出仍保留 HH:MM。
# ============================================================

# 專案根目錄：HEMS-SIMULATION-WEB
BASE_DIR = Path(__file__).resolve().parents[1]

# 原始資料資料夾
RAW_LOAD_DIR = BASE_DIR / "data" / "01_raw" / "load"
RAW_PV_DIR = BASE_DIR / "data" / "01_raw" / "pv"

# 輸出資料夾
PROCESSED_DIR = BASE_DIR / "data" / "02_processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

# 三個輸入檔案
PV_INPUT_PATH = RAW_PV_DIR / "pv_curve_1min.csv"
DAILY_LOAD_INPUT_PATH = RAW_LOAD_DIR / "daily_appliance_data_1min.csv"
LOADSHAPES_INPUT_PATH = RAW_LOAD_DIR / "LoadShapes_All_Nodes_1min.csv"

# 六個輸出檔案
PV_OUTPUT_1MIN_PATH = PROCESSED_DIR / "pv_curve_1min_kw.csv"
PV_OUTPUT_15MIN_PATH = PROCESSED_DIR / "pv_curve_15min_kw.csv"

DAILY_LOAD_OUTPUT_1MIN_PATH = PROCESSED_DIR / "daily_appliance_data_1min_kw.csv"
DAILY_LOAD_OUTPUT_15MIN_PATH = PROCESSED_DIR / "daily_appliance_data_15min_kw.csv"

LOADSHAPES_OUTPUT_1MIN_PATH = PROCESSED_DIR / "LoadShapes_All_Nodes_1min_kw.csv"
LOADSHAPES_OUTPUT_15MIN_PATH = PROCESSED_DIR / "LoadShapes_All_Nodes_15min_kw.csv"

FILES = [
    {
        "input": PV_INPUT_PATH,
        "output_1min": PV_OUTPUT_1MIN_PATH,
        "output_15min": PV_OUTPUT_15MIN_PATH,
    },
    {
        "input": DAILY_LOAD_INPUT_PATH,
        "output_1min": DAILY_LOAD_OUTPUT_1MIN_PATH,
        "output_15min": DAILY_LOAD_OUTPUT_15MIN_PATH,
    },
    {
        "input": LOADSHAPES_INPUT_PATH,
        "output_1min": LOADSHAPES_OUTPUT_1MIN_PATH,
        "output_15min": LOADSHAPES_OUTPUT_15MIN_PATH,
    },
]

BASE_DATE = "2026-01-01"
W_TO_KW = 1000.0
DT_1MIN_HOURS = 1 / 60
DT_15MIN_HOURS = 15 / 60


def make_kw_column_name(col: str) -> str:
    """
    將原本欄位名稱改成 kW 欄位名稱。
    例如：
    - pv_w -> pv_kw
    - fridge -> fridge_kw
    """
    lower_col = col.lower()

    if lower_col.endswith("_w"):
        return col[:-2] + "_kw"

    if lower_col.endswith("w") and lower_col not in ["row"]:
        # 避免少數欄位像 powerW 這類名稱沒有底線
        return col[:-1] + "kw"

    if lower_col.endswith("_kw"):
        return col

    return col + "_kw"


def prepare_time_column(df: pd.DataFrame) -> pd.DataFrame:
    """
    產生 timestamp、Time、Hour、Minute、minute_of_day。
    輸出 CSV 不會保留日期，只會保留 HH:MM。
    """
    df = df.copy()

    if "Time" in df.columns:
        time_text = df["Time"].astype(str).str.strip()

        # 將 0:01 也整理成 00:01
        parsed_time = pd.to_datetime(
            BASE_DATE + " " + time_text,
            errors="coerce"
        )

        if parsed_time.isna().any():
            raise ValueError("Time 欄位有無法解析的時間格式，請確認是否為 HH:MM")

        df["timestamp"] = parsed_time
        df["Hour"] = df["timestamp"].dt.hour
        df["Minute"] = df["timestamp"].dt.minute

    elif "Hour" in df.columns and "Minute" in df.columns:
        df["Hour"] = pd.to_numeric(df["Hour"], errors="coerce").fillna(0).astype(int)
        df["Minute"] = pd.to_numeric(df["Minute"], errors="coerce").fillna(0).astype(int)

        df["timestamp"] = (
            pd.to_datetime(BASE_DATE)
            + pd.to_timedelta(df["Hour"] * 60 + df["Minute"], unit="m")
        )

    else:
        raise ValueError("資料需要有 Time 欄位，或 Hour + Minute 欄位")

    df["Time"] = (
        df["Hour"].astype(str).str.zfill(2)
        + ":"
        + df["Minute"].astype(str).str.zfill(2)
    )

    df["minute_of_day"] = df["Hour"] * 60 + df["Minute"]

    return df


def get_power_columns(df: pd.DataFrame) -> list:
    """
    找出功率欄位。
    原則：排除時間欄位，其餘數值欄位都當作功率 W。
    """
    time_columns = {
        "Time",
        "Hour",
        "Minute",
        "timestamp",
        "step",
        "minute_of_day",
    }

    power_columns = []

    for col in df.columns:
        if col in time_columns:
            continue

        numeric_col = pd.to_numeric(df[col], errors="coerce")

        # 至少大部分資料能轉成數字，才視為功率欄位
        if numeric_col.notna().mean() >= 0.95:
            power_columns.append(col)

    if len(power_columns) == 0:
        raise ValueError("找不到可轉換的功率欄位")

    return power_columns


def convert_one_file(input_path: Path):
    """將單一 1 分鐘 CSV 轉成 1 分鐘 kW + 15 分鐘 kW CSV。"""
    df = pd.read_csv(input_path)
    df = prepare_time_column(df)

    power_columns = get_power_columns(df)

    df_kw = df[["timestamp", "Time", "Hour", "Minute", "minute_of_day"]].copy()

    kw_columns = []

    for col in power_columns:
        new_col = make_kw_column_name(col)

        df_kw[new_col] = (
            pd.to_numeric(df[col], errors="coerce")
            .fillna(0)
            / W_TO_KW
        )

        kw_columns.append(new_col)

    # ============================================================
    # 1. 產生 1 分鐘 kW 版本
    # ============================================================

    df_1min = df_kw.copy()
    df_1min["step"] = range(len(df_1min))

    if len(kw_columns) > 1:
        df_1min["total_power_kw"] = df_1min[kw_columns].sum(axis=1)
        df_1min["energy_kwh"] = df_1min["total_power_kw"] * DT_1MIN_HOURS

        output_1min_columns = (
            ["step", "Time", "Hour", "Minute", "minute_of_day"]
            + kw_columns
            + ["total_power_kw", "energy_kwh"]
        )
    else:
        df_1min["energy_kwh"] = df_1min[kw_columns[0]] * DT_1MIN_HOURS

        output_1min_columns = (
            ["step", "Time", "Hour", "Minute", "minute_of_day"]
            + kw_columns
            + ["energy_kwh"]
        )

    df_1min = df_1min[output_1min_columns]

    # ============================================================
    # 2. 產生 15 分鐘 kW 版本
    # ============================================================

    df_15min = (
        df_kw
        .set_index("timestamp")[kw_columns]
        .resample("15min", label="left", closed="left")
        .mean()
        .reset_index()
    )

    df_15min["Hour"] = df_15min["timestamp"].dt.hour
    df_15min["Minute"] = df_15min["timestamp"].dt.minute

    df_15min["Time"] = (
        df_15min["Hour"].astype(str).str.zfill(2)
        + ":"
        + df_15min["Minute"].astype(str).str.zfill(2)
    )

    # 這兩行就是你錯誤缺少的欄位
    df_15min["step"] = range(len(df_15min))
    df_15min["minute_of_day"] = df_15min["Hour"] * 60 + df_15min["Minute"]

    if len(kw_columns) > 1:
        df_15min["total_power_kw"] = df_15min[kw_columns].sum(axis=1)
        df_15min["energy_kwh"] = df_15min["total_power_kw"] * DT_15MIN_HOURS

        output_15min_columns = (
            ["step", "Time", "Hour", "Minute", "minute_of_day"]
            + kw_columns
            + ["total_power_kw", "energy_kwh"]
        )
    else:
        df_15min["energy_kwh"] = df_15min[kw_columns[0]] * DT_15MIN_HOURS

        output_15min_columns = (
            ["step", "Time", "Hour", "Minute", "minute_of_day"]
            + kw_columns
            + ["energy_kwh"]
        )

    df_15min = df_15min[output_15min_columns]

    return df_1min, df_15min


def main():
    print("開始轉換 1 分鐘資料 -> 1 分鐘 kW + 15 分鐘 kW 資料")

    for file_info in FILES:
        input_path = file_info["input"]
        output_1min_path = file_info["output_1min"]
        output_15min_path = file_info["output_15min"]

        if not input_path.exists():
            raise FileNotFoundError(f"找不到檔案：{input_path}")

        df_1min, df_15min = convert_one_file(input_path)

        df_1min.to_csv(output_1min_path, index=False, encoding="utf-8-sig")
        df_15min.to_csv(output_15min_path, index=False, encoding="utf-8-sig")

        print(f"完成：{input_path.name}")
        print(f"1 分鐘 kW 輸出：{output_1min_path}")
        print(f"15 分鐘 kW 輸出：{output_15min_path}")
        print(f"1 分鐘筆數：{len(df_1min)}")
        print(f"15 分鐘筆數：{len(df_15min)}")
        print("-" * 50)

    print("全部轉換完成")


if __name__ == "__main__":
    main()
