"""兩段式與三段式各執行一次 PSO，並輸出兩份電池使用 CSV。"""

import argparse
from pathlib import Path

import pandas as pd

from config import (
    BESS_CAPACITY_KWH,
    CRITICAL_LOAD_COLUMNS,
    CSV_ENCODING,
    INITIAL_SOC,
    LOAD_CSV_PATH,
    LOAD_METADATA_COLUMNS,
    NUM_INTERVALS,
    OUTAGE_END_TIME,
    OUTAGE_START_TIME,
    OUTPUT_CSV_PATHS,
    PV_CSV_PATH,
    TARIFF_CSV_PATH,
    TARIFF_DAY_TYPE,
    TARIFF_SEASON,
    TARIFF_TYPES,
    TARGET_FINAL_SOC,
)
from pso import (
    battery_energy_change_kwh,
    is_outage_interval,
    limit_bess_power_by_power_balance,
    limit_bess_power_by_soc,
    run_pso,
    terminal_soc_request_kw,
)


EXPECTED_TIMES = [  # 一天應有的 96 個標準時間字串
    f"{minute // 60:02d}:{minute % 60:02d}"
    for minute in range(0, 24 * 60, 15)
]


def _read_csv(path, data_name):
    """以固定的 UTF-8-SIG 編碼讀取 CSV。"""
    path = Path(path)  # CSV 檔案路徑
    if not path.exists():
        raise FileNotFoundError(f"找不到{data_name}：{path}")
    return pd.read_csv(path, encoding=CSV_ENCODING)


def _read_tariff_csv(path):
    """以固定的 UTF-8-SIG 編碼讀取電價規則。"""
    return _read_csv(path, "電價 CSV")


def _validate_and_sort_time(df, data_name):
    if "Time" not in df.columns:
        raise ValueError(f"{data_name}缺少 Time 欄位")
    if df["Time"].isna().any():
        raise ValueError(f"{data_name}的 Time 欄位含有空值")

    parsed = pd.to_datetime(  # 解析後的時間資料
        df["Time"].astype(str),
        format="%H:%M",
        errors="coerce",
    )
    if parsed.isna().any():
        bad_values = df.loc[parsed.isna(), "Time"].tolist()  # 無法解析的時間值
        raise ValueError(f"{data_name}有無法辨識的時間：{bad_values}")

    checked = df.copy()  # 完成時間檢查與排序的資料表
    checked["Time"] = parsed.dt.strftime("%H:%M")  # 標準化後的時間字串
    if checked["Time"].duplicated().any():
        duplicates = checked.loc[
            checked["Time"].duplicated(), "Time"
        ].tolist()  # 重複的時間值
        raise ValueError(f"{data_name}有重複時間：{duplicates}")

    checked["_minutes"] = (  # 排序用的當日累計分鐘數
        parsed.dt.hour * 60 + parsed.dt.minute
    )
    checked = (  # 依時間排序完成的資料表
        checked.sort_values("_minutes")
        .drop(columns="_minutes")
        .reset_index(drop=True)
    )

    if len(checked) != NUM_INTERVALS or checked["Time"].tolist() != EXPECTED_TIMES:
        raise ValueError(
            f"{data_name}必須包含 00:00～23:45 的 {NUM_INTERVALS} 筆 15 分鐘資料"
        )
    return checked


def _to_numeric_columns(df, columns, data_name):
    numeric = df.copy()  # 轉換成數值格式後的資料表
    for column in columns:
        numeric[column] = pd.to_numeric(  # 轉換後的數值欄位
            numeric[column],
            errors="coerce",
        )
    if numeric[list(columns)].isna().any().any():
        bad_columns = numeric[list(columns)].columns[
            numeric[list(columns)].isna().any()
        ].tolist()  # 含有非數字或空值的欄位
        raise ValueError(f"{data_name}有非數字或空值欄位：{bad_columns}")
    return numeric


def read_load_data(load_csv_path):
    """讀取設備負載，依實際欄位加總總負載、關鍵負載與非關鍵負載。"""
    load_df = _validate_and_sort_time(  # 檢查完成的負載資料表
        _read_csv(load_csv_path, "負載 CSV"),
        "負載 CSV",
    )

    missing_columns = [  # 缺少的關鍵負載欄位
        column for column in CRITICAL_LOAD_COLUMNS if column not in load_df.columns
    ]
    if missing_columns:
        raise ValueError(f"負載 CSV 缺少關鍵設備欄位：{missing_columns}")

    appliance_columns = [  # 所有設備功率欄位
        column
        for column in load_df.columns
        if column not in LOAD_METADATA_COLUMNS
    ]
    if not appliance_columns:
        raise ValueError("負載 CSV 找不到任何設備功率欄位")

    load_df = _to_numeric_columns(  # 數值化後的負載資料表
        load_df,
        appliance_columns,
        "負載 CSV",
    )
    if (load_df[appliance_columns] < 0).any().any():
        raise ValueError("負載功率不可為負數")

    result = pd.DataFrame({"Time": load_df["Time"]})  # 整理後的負載資料表
    result["load_kw"] = load_df[appliance_columns].sum(axis=1)  # 總負載功率
    result["critical_load_kw"] = load_df[CRITICAL_LOAD_COLUMNS].sum(
        axis=1
    )  # 關鍵負載功率
    result["noncritical_load_kw"] = (  # 非關鍵負載功率
        result["load_kw"] - result["critical_load_kw"]
    )
    return result


def read_pv_data(pv_csv_path):
    """讀取本次提供的 Time、pv_kw 太陽能資料。"""
    pv_df = _validate_and_sort_time(  # 檢查完成的太陽能資料表
        _read_csv(pv_csv_path, "PV CSV"),
        "PV CSV",
    )
    if "pv_kw" not in pv_df.columns:
        raise ValueError("PV CSV 缺少 pv_kw 欄位")

    pv_df = _to_numeric_columns(  # 數值化後的太陽能資料表
        pv_df,
        ["pv_kw"],
        "PV CSV",
    )
    if (pv_df["pv_kw"] < 0).any():
        raise ValueError("pv_kw 不可為負數")
    return pv_df[["Time", "pv_kw"]]


def build_price_curve(
    tariff_df,
    time_series,
    tariff_type,
    season=TARIFF_SEASON,
    day_type=TARIFF_DAY_TYPE,
):
    """將費率規則展開為 00:00～23:45 的 96 筆電價。"""
    required_columns = {  # 電價資料必須包含的欄位
        "tariff_type",
        "charge_type",
        "season",
        "day_type",
        "start_minute",
        "end_minute",
        "price",
    }
    missing_columns = sorted(  # 電價資料缺少的欄位
        required_columns - set(tariff_df.columns)
    )
    if missing_columns:
        raise ValueError(f"電價 CSV 缺少欄位：{missing_columns}")

    rules = tariff_df.loc[  # 符合目前電價方案、季節與日別的規則
        (tariff_df["tariff_type"] == tariff_type)
        & (tariff_df["charge_type"] == "energy")
        & (tariff_df["season"] == season)
        & (tariff_df["day_type"] == day_type)
    ].copy()
    if rules.empty:
        raise ValueError(
            f"找不到 {tariff_type}/{season}/{day_type} 的電量費率規則"
        )

    rules = _to_numeric_columns(  # 數值化後的電價規則
        rules,
        ["start_minute", "end_minute", "price"],
        "電價 CSV",
    )
    prices = []  # 96 個時段的每度電價
    for time_text in time_series:
        hour, minute = (int(value) for value in time_text.split(":"))  # 小時、分鐘
        minute_of_day = hour * 60 + minute  # 從午夜起算的分鐘數
        matched = rules.loc[  # 此時段符合的電價規則
            (rules["start_minute"] <= minute_of_day)
            & (minute_of_day < rules["end_minute"])
        ]
        if len(matched) != 1:
            raise ValueError(
                f"{tariff_type} 在 {time_text} 應恰好符合一條規則，"
                f"目前符合 {len(matched)} 條"
            )
        prices.append(float(matched.iloc[0]["price"]))

    return pd.Series(prices, name="price_per_kwh")


def read_input_data(load_csv_path, pv_csv_path):
    load_data = read_load_data(load_csv_path)  # 負載資料
    pv_data = read_pv_data(pv_csv_path)  # 太陽能資料

    data = load_data.merge(  # 依時間合併後的輸入資料
        pv_data,
        on="Time",
        how="inner",
        validate="one_to_one",
    )
    if len(data) != NUM_INTERVALS:
        raise ValueError("負載與 PV 的時間欄位無法完整對齊")
    return data


def simulate_best_schedule(input_data, requested_schedule_kw):
    """依最佳排程逐點套用硬限制，產生最終 CSV 所需資料。"""
    current_energy_kwh = INITIAL_SOC * BESS_CAPACITY_KWH  # 目前電池能量（kWh）
    records = []  # 每個時段的電池模擬紀錄

    for t, row in input_data.iterrows():
        outage = is_outage_interval(t)  # 目前是否停電
        requested_bess_kw = float(requested_schedule_kw[t])  # PSO 要求的電池功率
        load_demand_kw = (
            row["critical_load_kw"] if outage else row["load_kw"]
        )  # 目前需要供應的負載功率

        terminal_request_kw = terminal_soc_request_kw(  # 終端 SOC 控制要求功率
            current_energy_kwh,
            t,
        )
        if terminal_request_kw is not None:
            requested_bess_kw = terminal_request_kw  # 改用終端 SOC 控制功率

        constrained_request_kw = limit_bess_power_by_power_balance(  # 電力平衡限制後功率
            requested_bess_kw,
            load_demand_kw,
            row["pv_kw"],
            outage,
        )

        actual_bess_kw = limit_bess_power_by_soc(  # SOC 限制後的實際電池功率
            current_energy_kwh,
            constrained_request_kw,
        )
        energy_change_kwh = battery_energy_change_kwh(  # 此時段電池能量變化
            actual_bess_kw
        )
        current_energy_kwh += energy_change_kwh  # 更新目前電池能量

        if actual_bess_kw > 1e-9:
            battery_status = "Discharging"  # 電池狀態：放電
        elif actual_bess_kw < -1e-9:
            battery_status = "Charging"  # 電池狀態：充電
        else:
            battery_status = "Idling"  # 電池狀態：待機

        records.append(
            {
                "Time": row["Time"],
                "battery_power_kw": actual_bess_kw,
                "battery_status": battery_status,
                "battery_energy_change_kwh": energy_change_kwh,
                "battery_energy_kwh": current_energy_kwh,
                "soc_percent": current_energy_kwh / BESS_CAPACITY_KWH * 100.0,
            }
        )

    result = pd.DataFrame(records)  # 最終電池使用結果
    final_soc = result["soc_percent"].iloc[-1] / 100.0  # 最後時段 SOC
    if abs(final_soc - TARGET_FINAL_SOC) > 1e-8:
        raise RuntimeError(
            f"終端 SOC 控制未達目標：{final_soc * 100:.8f}%"
        )
    return result


def run(load_csv_path, pv_csv_path, tariff_csv_path, output_dir):
    """兩種方案各跑一次 PSO，回傳並寫出兩份電池排程。"""
    input_data = read_input_data(load_csv_path, pv_csv_path)  # 負載與 PV 輸入資料
    tariff_df = _read_tariff_csv(tariff_csv_path)  # 電價規則資料表
    output_dir = Path(output_dir)  # 結果輸出目錄
    output_dir.mkdir(parents=True, exist_ok=True)

    all_results = {}  # 兩種電價方案的完整結果
    for tariff_type in TARIFF_TYPES:
        tariff_input = input_data.copy()  # 加入目前方案電價的模擬輸入
        tariff_input["price_per_kwh"] = build_price_curve(  # 每時段電價
            tariff_df,
            tariff_input["Time"],
            tariff_type,
        )

        pso_result = run_pso(  # PSO 最佳化結果
            tariff_input["critical_load_kw"].to_numpy(),
            tariff_input["noncritical_load_kw"].to_numpy(),
            tariff_input["pv_kw"].to_numpy(),
            tariff_input["price_per_kwh"].to_numpy(),
        )
        battery_result = simulate_best_schedule(  # 最佳排程的逐時段模擬結果
            tariff_input,
            pso_result["after_position"],
        )

        output_path = (  # 目前電價方案的結果檔案路徑
            output_dir / OUTPUT_CSV_PATHS[tariff_type].name
        )
        battery_result.to_csv(
            output_path,
            index=False,
            encoding=CSV_ENCODING,
            float_format="%.6f",
        )
        all_results[tariff_type] = {  # 保存目前電價方案的結果
            "battery_result": battery_result,
            "pso_result": pso_result,
            "output_path": output_path,
        }

        print(
            f"{tariff_type}: fitness={pso_result['after_fitness']:.6f}, "
            f"final_soc={battery_result['soc_percent'].iloc[-1]:.6f}%, "
            f"output={output_path}"
        )

    print(f"停電時段：{OUTAGE_START_TIME}～{OUTAGE_END_TIME}（結束時間不包含）")
    return all_results


def parse_args():
    parser = argparse.ArgumentParser(  # 命令列參數解析器
        description="夏月平日兩段式／三段式 15 分鐘 HEMS + PSO 模擬"
    )
    parser.add_argument("--load", type=Path, default=LOAD_CSV_PATH)
    parser.add_argument("--pv", type=Path, default=PV_CSV_PATH)
    parser.add_argument("--tariff", type=Path, default=TARIFF_CSV_PATH)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_CSV_PATHS["two_stage"].parent)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()  # 使用者輸入的命令列參數
    run(args.load, args.pv, args.tariff, args.output_dir)
