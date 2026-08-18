"""
scenario_switch.py
「設備控制與停電控制頁面」按下確認後觸發的同步切換流程（做法 A）。

流程：
  1. 依前端傳來的 JSON，寫出新的負載 CSV（含設備強制開啟時段的 0/1 遮罩）到
     共用的 data/01_raw 資料夾——PSO、離線驗證線、即時引擎三邊都讀這一份。
  2. 呼叫 PSO，算出新的電池排程 CSV，輸出到共用的 data/04_optimized_pso。
  3. 呼叫離線驗證線（hems_basecontrol.run_simulation），跑一次完整 24 小時：
       a. 針對「PSO 優化過」的排程驗證有沒有過載
       b. 🆕 針對「同一份負載資料、不套用 PSO」的 baseline 版本也跑一次，
          算出對比用的「未優化總電價」
  4. 若 PSO 版本過載，重試最多 3 次（⚠️ 目前是「暫代版」，見下方說明）
  5. 驗證過關後，重置即時引擎（server.py 裡常駐的 OpenDSS），讓 01~06 頁面之後讀到新設定
  6. 回傳 PSO 總電價、baseline 總電價、省下金額給前端主頁顯示對比

🆕 資料夾架構（2024 修訂版）：PSO / hems_basecontrol.py / server.py 三邊現在都
   讀寫同一個共用的 data 資料夾（data/01_raw、data/04_optimized_pso、data/sample），
   不是三個各自獨立的位置，所以不再需要「算完之後複製一份過去」的同步步驟。
   PROJECT_ROOT 是唯一需要依每個人電腦上實際路徑調整的地方。

⚠️ 這支檔案裡有已知的「暫代方案」，不是完整實作，使用前務必看清楚對應的註解：
  - PSO 的停電時間目前是寫死在 config.py 的模組常數，這裡用 monkeypatch + reload
    的方式讓它能依請求動態調整，這是權宜做法，長期建議請隊友把 pso.py 改成
    接受參數，而不是讀模組常數。
  - 同樣用 monkeypatch 的方式，強制把 config.py 的 DATA_DIR/RESULT_DIR 等路徑
    覆蓋成本檔案的共用路徑，不管隊友的 config.py 實際檔案放在專案裡哪個位置。
  - 過載時的「重新計算排程」目前沒有真正的自我修正邏輯（PSO 不知道電路拓樸,
    不知道怎麼把「某節點過載」轉換成「某時段功率上限要調低」），這裡先用一個
    非常粗略的暫代：每重試一次就把電池最大功率打折，讓下一輪 PSO 更保守。
    這不是規格書要的「讀警告修正邊界條件」，只是先讓流程能跑通、有機會避開過載。

🆕 這次修正額外解決的兩個架構耦合問題（連帶修改了 hems_basecontrol.py）：
  - is_target_outage 原本寫死「必須 mode == 'island'」，導致 baseline 模式永遠
    無法經歷停電，「未優化+停電」這個情境組合根本跑不出來。已改成只要有傳入
    合法的 outage_start_step/outage_end_step，任何 mode 都能進入孤島邏輯。
  - hems_basecontrol.run_simulation() 原本寫死讀取「兩段式」的 PSO 排程檔名，
    三段式電價情境會誤讀兩段式排程去驗證。已改成可傳入 battery_schedule_filename 參數。
"""

import importlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# ==========================================
# 路徑設定
# 🆕 只有 PROJECT_ROOT 這一行需要依每個人電腦上的實際路徑調整。
#    三支子系統（PSO / 離線驗證 hems_basecontrol.py / 即時引擎 server.py）
#    現在都讀寫同一個共用的 data 資料夾，不再是三個各自獨立的位置，
#    所以也不再需要「算完之後複製一份過去」這個同步步驟了。
# ==========================================
PROJECT_ROOT = Path(r"C:\projects\hems-simulation-web")
DATA_DIR = PROJECT_ROOT / "data"

RAW_DATA_DIR = DATA_DIR / "01_raw"
PSO_OUTPUT_DIR = DATA_DIR / "04_optimized_pso"
OUTPUT_DIR = DATA_DIR / "sample"  # 🆕 離線驗證輸出的 _History.csv / _Warning_Report.csv 存這裡

# 🆕 PSO 的三支檔案（config.py / main.py / pso.py）放在獨立的 pso 資料夾，
#    跟 scenario_switch.py 自己所在的 opendss 資料夾不同，
#    要先把這個資料夾加進 sys.path，底下的 `import config`/`import main`/`import pso` 才找得到。
PSO_CODE_DIR = PROJECT_ROOT / "pso"
if str(PSO_CODE_DIR) not in sys.path:
    sys.path.append(str(PSO_CODE_DIR))

TEMPLATE_LOAD_CSV = RAW_DATA_DIR / "LoadShapes_All_Nodes_15min_template.csv"  # 永久保留的基準模板
PATTERN_LOAD_CSV = RAW_DATA_DIR / "LoadShapes_All_pattern.csv"                # 提供負載曲線
ACTIVE_LOAD_CSV = RAW_DATA_DIR / "LoadShapes_All_Nodes_15min.csv"             # 設備控制頁面改寫、PSO/離線驗證/即時引擎共用讀取


BATTERY_SCHEDULE_FILENAMES = {
    "two_stage": "battery_usage_two_stage_summer_weekday_15min.csv",
    "three_stage": "battery_usage_three_stage_summer_weekday_15min.csv",
}

MAX_RETRY = 3


# ==========================================
# 階段一：寫出新的負載 CSV（設備強制開啟時段的 0/1 遮罩）
# ==========================================
def _time_to_step(time_str: str) -> int:
    h, m = map(int, time_str.split(":"))
    return h * 4 + (m // 15)


def _build_pattern_baseline() -> pd.DataFrame:
    """讀 template（額定最大值）+ pattern（0~1 日用電曲線),逐欄相乘出有真實形狀的基準負載"""
    if not TEMPLATE_LOAD_CSV.exists():
        raise FileNotFoundError(f"找不到負載模板：{TEMPLATE_LOAD_CSV}")
    if not PATTERN_LOAD_CSV.exists():
        raise FileNotFoundError(f"找不到日用電曲線檔：{PATTERN_LOAD_CSV}")

    df_template = pd.read_csv(TEMPLATE_LOAD_CSV)
    df_pattern = pd.read_csv(PATTERN_LOAD_CSV)

    if len(df_template) != len(df_pattern):
        raise ValueError(f"template（{len(df_template)}列）跟 pattern（{len(df_pattern)}列）筆數不一致")
    if not (df_template["Time"].reset_index(drop=True) == df_pattern["Time"].reset_index(drop=True)).all():
        raise ValueError("template 跟 pattern 的 Time 欄位對不上")

    device_cols = [c for c in df_template.columns if c not in ("Time", "Hour", "Minute")]
    pattern_cols = [c for c in df_pattern.columns if c not in ("Time", "Hour", "Minute")]
    missing = set(device_cols) - set(pattern_cols)
    extra = set(pattern_cols) - set(device_cols)
    if missing or extra:
        raise ValueError(f"pattern.csv 欄位對不上：缺 {missing or '無'}，多 {extra or '無'}")

    df_baseline = df_template.copy()
    df_baseline[device_cols] = df_template[device_cols].to_numpy() * df_pattern[device_cols].to_numpy()
    return df_baseline



def write_load_csv(device_schedules: list):
    """
    device_schedules 範例：
    [
        {"column": "l1_airc_abn", "start_time": "14:00", "end_time": "16:00", "on_power_kw": 1.2},
        ...
    ]
    邏輯：該設備欄位在指定時段內填入 on_power_kw（強制開啟),其餘時間全部填 0，
    完全覆蓋掉模板裡原本的用電模式（符合規格書「其餘時間皆為 OFF (0)」的定義）。

    🆕 寫入的 ACTIVE_LOAD_CSV 本來就在共用的 data/01_raw 資料夾，
    hems_basecontrol.py / server.py 會直接讀到同一份，不用另外複製。
    """

    df = _build_pattern_baseline()
    n_rows = len(df)


    device_masks={}
    for dev in device_schedules:
        col = dev["column"]
        if col not in df.columns:
            raise ValueError(f"負載 CSV 沒有這個設備欄位：{col}")
        if col not in device_masks:
            device_masks[col] = np.zeros(n_rows)

        start_step = _time_to_step(dev["start_time"])
        end_step = _time_to_step(dev["end_time"])
        if start_step >= end_step:
            raise ValueError(f"設備 {col} 的開始時間必須早於結束時間（目前 {dev['start_time']} ~ {dev['end_time']}）")

        # 🆕 修正單位換算：dev["on_power_kw"] 是使用者從前端輸入的「千瓦」數值，
        #    但 LoadShapes_All_Nodes_15min.csv 本身是「瓦特(W)」為單位的檔案，
        #    所以只有『使用者明確指定的 kW 值』才需要乘 1000 轉成 W；
        #    如果沒傳 on_power_kw，退回用模板裡的最大值當預設 —— 這個預設值本來就
        #    是從 W 單位的模板讀出來的，不需要再轉換，不能跟上面那條路徑共用同一次轉換。
        
        if "on_power_kw" in dev:
            on_power_w = float(dev["on_power_kw"]) * 1000.0
        else:
            on_power_w = float(df[col].max())

        device_masks[col][start_step:end_step] = on_power_w
        
    for col, mask in device_masks.items():
        df[col] = mask

    df.to_csv(ACTIVE_LOAD_CSV, index=False)
    # 🆕 不用再複製了：ACTIVE_LOAD_CSV 本來就在 hems_basecontrol.py / server.py 讀取的
    #    同一個共用 data/01_raw 資料夾裡，三邊本來就會讀到同一份。

    return ACTIVE_LOAD_CSV


# ==========================================
# 階段二：呼叫 PSO
# ==========================================
def _patch_config_and_reload_pso(outage_start: str | None, outage_end: str | None):
    """
    ⚠️ 暫代做法：config.py 的停電時間是模組載入時就算好的常數
    （OUTAGE_START_INDEX / OUTAGE_END_INDEX），pso.py 用
    `from config import OUTAGE_START_INDEX` 這種方式在「匯入當下」把值複製走，
    之後就算改 config 模組的屬性，pso.py 內部已經綁死舊的值不會變。

    這裡用『先改 config 模組屬性 → importlib.reload(pso)』的方式，
    強迫 pso.py 重新從 config 讀一次最新值。這樣做得到效果，
    但不是乾淨的架構——長期應該讓 pso.py 的函式改成接受
    outage_start_index / outage_end_index 當參數，不要依賴模組常數。

    🆕 這裡也順便強制把 config 的資料夾路徑覆蓋成本檔案開頭定義的共用路徑
    （RAW_DATA_DIR / PSO_OUTPUT_DIR），不管隊友的 config.py 檔案實際放在
    專案裡哪個位置、它自己算出來的 BASE_DIR 是什麼，統一都用這裡的路徑為準。
    這樣就不用要求隊友把 config.py 搬到特定資料夾，或跟他協調路徑寫法。
    """
    import config
    import pso

    # 🆕 強制覆蓋資料夾路徑，統一指向共用的 data/01_raw、data/04_optimized_pso
    config.DATA_DIR = RAW_DATA_DIR
    config.RESULT_DIR = PSO_OUTPUT_DIR
    config.LOAD_CSV_PATH = RAW_DATA_DIR / "LoadShapes_All_Nodes_15min.csv"
    config.PV_CSV_PATH = RAW_DATA_DIR / "pv_curve_15min.csv"
    config.TARIFF_CSV_PATH = RAW_DATA_DIR / "electricity_tariffs_2tage_and_3tage_UTF-8.csv"
    config.OUTPUT_CSV_PATHS = {
        "two_stage": PSO_OUTPUT_DIR / BATTERY_SCHEDULE_FILENAMES["two_stage"],
        "three_stage": PSO_OUTPUT_DIR / BATTERY_SCHEDULE_FILENAMES["three_stage"],
    }

    if outage_start and outage_end:
        config.OUTAGE_START_TIME = outage_start
        config.OUTAGE_END_TIME = outage_end
        config.OUTAGE_START_INDEX = config.time_to_interval(outage_start)
        config.OUTAGE_END_INDEX = config.time_to_interval(outage_end)
    else:
        # 沒有勾選停電：把停電區間設成長度 0，等同於整天都不會進入停電邏輯
        config.OUTAGE_START_TIME = "00:00"
        config.OUTAGE_END_TIME = "00:00"
        config.OUTAGE_START_INDEX = 0
        config.OUTAGE_END_INDEX = 0

    importlib.reload(pso)  # 讓 pso.py 重新從 config 抓最新的停電區間常數
    import pso
    print(f"🔎 [停電區間檢查] OUTAGE_START_INDEX={pso.OUTAGE_START_INDEX}, OUTAGE_END_INDEX={pso.OUTAGE_END_INDEX}")
    return config, pso


def run_pso_for_scenario(pricing: str, outage_start: str | None, outage_end: str | None, bess_max_kw_override: float | None = None):
    """
    回傳 (battery_result DataFrame, 輸出檔案路徑)。
    🆕 現在直接輸出到共用的 PSO_OUTPUT_DIR，不用再複製到別的地方，
    hems_basecontrol.py / server.py 本來就會讀同一個資料夾。
    """
    import main as pso_main  # 隊友的 main.py

    config, pso = _patch_config_and_reload_pso(outage_start, outage_end)

    if bess_max_kw_override is not None:
        # ⚠️ 過載重試時的暫代降級：直接調低電池最大功率上限，逼 PSO 算出更保守的排程
        config.P_BESS_MAX_KW = bess_max_kw_override
        importlib.reload(pso)

    importlib.reload(pso_main)  # main.py 內部也 import 了 config 的常數，一併重載

    tariff_type = "two_stage" if pricing == "two_stage" else "three_stage"
    input_data = pso_main.read_input_data(ACTIVE_LOAD_CSV, config.PV_CSV_PATH)
    tariff_df = pso_main._read_tariff_csv(config.TARIFF_CSV_PATH)

    tariff_input = input_data.copy()
    tariff_input["price_per_kwh"] = pso_main.build_price_curve(
        tariff_df, tariff_input["Time"], tariff_type
    )
    print(f"🔎 [PSO輸入比對] 13:00~15:00 PV: {tariff_input[(tariff_input['Time']>='13:00')&(tariff_input['Time']<='14:45')]['pv_kw'].tolist()}")
    print(f"🔎 [PSO輸入比對] 13:00~15:00 critical_load_kw: {tariff_input[(tariff_input['Time']>='13:00')&(tariff_input['Time']<='14:45')]['critical_load_kw'].tolist()}")

    pso_result = pso.run_pso(
        tariff_input["critical_load_kw"].to_numpy(),
        tariff_input["noncritical_load_kw"].to_numpy(),
        tariff_input["pv_kw"].to_numpy(),
        tariff_input["price_per_kwh"].to_numpy(),
    )
    print(f"🎯 [PSO自評] PSO演算法自己算出來的理論總成本(電費+懲罰項): {pso_result['after_fitness']}")  # 🆕 除錯用，驗證完可刪
    battery_result = pso_main.simulate_best_schedule(tariff_input, pso_result["after_position"])

    output_path = config.OUTPUT_CSV_PATHS[tariff_type]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    battery_result.to_csv(output_path, index=False, float_format="%.6f")
    # 🆕 不用再複製了：output_path 本來就在共用的 data/04_optimized_pso，
    #    hems_basecontrol.py / server.py 會直接從這裡讀。

    return battery_result, output_path


# ==========================================
# 階段三：呼叫離線驗證線
# ==========================================
def run_offline_validation(mode: str, pricing: str,
                            outage_start_step: int, outage_end_step: int):
    """
    呼叫 hems_basecontrol.run_simulation() 跑一次完整 24 小時。
    回傳 (是否過載, 警告 DataFrame, 歷史 DataFrame)

    🆕 現在 is_target_outage 已經跟 mode 解耦，不用再用 mode='island' 當
    「觸發停電」的暫代手法了：baseline/pso 都可以直接傳入 outage_start_step/
    outage_end_step，沒有停電就傳 -1, -1（函式預設值）。

    🆕 補上存檔步驟：run_simulation() 本身只會存 bess_status/All_meter/floor
    設備明細這三種 CSV，不會存 _History.csv / _Warning_Report.csv——這兩個
    原本只有 main_hems.py 自己呼叫 save_history_and_warning() 才會產生。
    透過 API（scenario_switch.py）觸發時之前完全沒有這一步，導致離線驗證
    跑完卻找不到 _History.csv，這裡補上，讓兩條路徑產生的檔案一致。
    """
    import hems_basecontrol

    schedule_filename = BATTERY_SCHEDULE_FILENAMES["two_stage" if pricing == "two_stage" else "three_stage"]

    df_history, df_warning = hems_basecontrol.run_simulation(
        mode=mode,
        outage_start_step=outage_start_step,
        outage_end_step=outage_end_step,
        battery_schedule_filename=schedule_filename,
    )

    has_overload = not df_warning.empty

    # 🆕 存檔，檔名前綴跟 main_hems.py 的慣例一致（mode.capitalize()）
    prefix = mode.capitalize()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df_history.to_csv(OUTPUT_DIR / f"{prefix}_History.csv", index=False, encoding="utf-8-sig")
    if not df_warning.empty:
        df_warning.to_csv(OUTPUT_DIR / f"{prefix}_Warning_Report.csv", index=False, encoding="utf-8-sig")

    return has_overload, df_warning, df_history


# ==========================================
# 🆕 電費計算：拿離線驗證跑出來的累積 kWh，套用 PSO 已經有的電價表
# ==========================================
METER_COLUMN = "總電錶T_當前累積功率(kWh)"  # ⚠️ 請對照你實際 df_history 的欄位名稱，若不同要改這裡


def compute_total_cost(df_history: pd.DataFrame, pricing: str) -> float:
    """
    用「總電錶T」這個電表的累積 kWh（df_history 每步都有記錄）算出全天總電費。
    價格曲線直接重用 PSO 隊友已經寫好的 build_price_curve()，不用另外重造輪子。
    """
    import config
    import main as pso_main

    if METER_COLUMN not in df_history.columns:
        raise KeyError(
            f"df_history 裡沒有欄位 '{METER_COLUMN}'，"
            f"請確認 hems_basecontrol.py 實際輸出的電表欄位名稱，並修改這裡的 METER_COLUMN。"
            f"目前欄位有：{list(df_history.columns)[:20]} ..."
        )

    tariff_df = pso_main._read_tariff_csv(config.TARIFF_CSV_PATH)
    tariff_type = "two_stage" if pricing == "two_stage" else "three_stage"
    price_series = pso_main.build_price_curve(tariff_df, df_history["Time"], tariff_type)

    cumulative_kwh = df_history[METER_COLUMN].to_numpy()
    # 第一步的用電量 = 累積值本身（假設從 0 開始）；之後每步 = 跟前一步的差值
    kwh_per_step = np.diff(cumulative_kwh, prepend=0.0)
    kwh_per_step = np.clip(kwh_per_step, 0, None)  # 避免电表重置或雜訊造成負值

    total_cost = float(np.sum(kwh_per_step * price_series.to_numpy()))
    return round(total_cost, 2)

SOC_COLUMN = "電池_SOC(%)"

def compute_battery_asset_value(df_history: pd.DataFrame, pricing: str) -> float:
    """
    把「這一天結束時，電池裡還剩多少電」折算成金錢價值。
    邏輯：剩餘電量的價值 = 剩餘 kWh × 這份電價表裡最便宜的離峰價格
    （因為正常情況下，要把電池充回這些電量，本來就會挑最便宜的時段充，
    所以「省下的錢」應該用離峰價格去估算，這樣比較保守、不會高估）
    """
    import config
    import main as pso_main

    tariff_df = pso_main._read_tariff_csv(config.TARIFF_CSV_PATH)
    tariff_type = "two_stage" if pricing == "two_stage" else "three_stage"

    rules = tariff_df.loc[
        (tariff_df["tariff_type"] == tariff_type)
        & (tariff_df["charge_type"] == "energy")
        & (tariff_df["season"] == config.TARIFF_SEASON)
        & (tariff_df["day_type"] == config.TARIFF_DAY_TYPE)
    ]
    cheapest_price = rules["price"].min()

    final_soc_percent = df_history[SOC_COLUMN].iloc[-1]
    final_soc_kwh = (final_soc_percent / 100.0) * config.BESS_CAPACITY_KWH

    return round(final_soc_kwh * cheapest_price, 2)    


# ==========================================
# 階段四：重置即時引擎（讓 server.py 常駐的 OpenDSS 讀新設定）
# ==========================================
def reset_online_engine(server_module, pricing: str, outage: dict | None = None,outage_start_step: int = -1, outage_end_step: int = -1):
    """
    server_module：直接把 server.py 這個已載入的模組傳進來，
    這樣可以直接改它的全域變數，不用重啟整個 process。

    🆕 改用 server_module.RAW_DATA_DIR / server_module.PSO_OUTPUT_DIR
    （對應 server.py 裡新的路徑常數），不再是單一個 TARGET_DATA_DIR。
    """
    schedule_filename = BATTERY_SCHEDULE_FILENAMES["two_stage" if pricing == "two_stage" else "three_stage"]

    server_module.df_pv_brain = pd.read_csv(server_module.os.path.join(
        server_module.RAW_DATA_DIR, "pv_curve_15min.csv"
    ))
    server_module.pv_w_list = server_module.df_pv_brain["pv_kw"].tolist()

    server_module.df_loads_brain = pd.read_csv(ACTIVE_LOAD_CSV)
    load_cols = [c for c in server_module.df_loads_brain.columns if c not in ("Time", "Hour", "Minute")]
    server_module.total_load_w_list = server_module.df_loads_brain[load_cols].sum(axis=1).tolist()

    df_pso = pd.read_csv(server_module.os.path.join(
        server_module.PSO_OUTPUT_DIR, schedule_filename
    ))

    
    server_module.pso_kw_list = df_pso["battery_power_kw"].tolist()

    server_module.startup_event()  # 重建電路
    server_module.current_step = 0
    server_module.is_island_mode = False
    server_module.outage_start_step = outage_start_step
    server_module.outage_end_step = outage_end_step
    server_module.CURRENT_SCENARIO = {"pricing": pricing, "outage": outage}
    
   








# ==========================================
# 主流程：串起以上四個階段，含過載重試 + baseline 對比
# ==========================================
def switch_scenario(config_json: dict, server_module):
    """
    config_json 範例：
    {
        "pricing": "two_stage",
        "outage": {"start_time": "13:00", "end_time": "15:00"},   # 沒勾選就傳 null
        "device_schedules": [
            {"column": "l1_airc_abn", "start_time": "14:00", "end_time": "16:00", "on_power_kw": 1.2}
        ]
    }
    回傳 dict，直接被 FastAPI 路由回傳給前端，包含 PSO / Baseline 兩邊的總電價可供主頁對比。
    """
    pricing = config_json["pricing"]

    # 🆕 提早驗證，避免打錯字被靜默當成 three_stage 處理
    if pricing not in ("two_stage", "three_stage"):
        raise ValueError(f"pricing 必須是 'two_stage' 或 'three_stage'，收到的是：{pricing}")

    outage = config_json.get("outage")
    device_schedules = config_json.get("device_schedules", [])

    outage_start = outage["start_time"] if outage else None
    outage_end = outage["end_time"] if outage else None
    outage_start_step = _time_to_step(outage_start) if outage else -1
    outage_end_step = _time_to_step(outage_end) if outage else -1

    # --- 前端防呆的最後一道防線：停電時段跟強制開啟的設備時段有沒有重疊 ---
    if outage:
        for dev in device_schedules:
            dev_start = _time_to_step(dev["start_time"])
            dev_end = _time_to_step(dev["end_time"])
            overlap = max(dev_start, outage_start_step) < min(dev_end, outage_end_step)
            if overlap:
                return {
                    "status": "conflict",
                    "message": f"設備 {dev['column']} 的強制開啟時段與停電時段重疊，請調整設定"
                }

    # --- 階段一：寫負載 CSV（PSO 版跟 baseline 版共用同一份負載資料）---
    write_load_csv(device_schedules)

    # --- 階段二 + 三：跑 PSO，並驗證有沒有過載，過載就重試 ---
    bess_override = None
    df_pso_history = None
    for attempt in range(MAX_RETRY + 1):
        print(f"🔁 [重試檢查] 第 {attempt} 次嘗試，目前 bess_override={bess_override}")
        run_pso_for_scenario(pricing, outage_start, outage_end, bess_override)

        has_overload, df_warning, df_pso_history = run_offline_validation(
            mode="pso",
            pricing=pricing,
            outage_start_step=outage_start_step,
            outage_end_step=outage_end_step,
        )

        if not has_overload:
            break

        if attempt == MAX_RETRY:
            return {
                "status": "failed",
                "message": "目前設定下找不到不過載的排程方案，請調整設備或停電時間",
                "warning_preview": df_warning.head(10).to_dict(orient="records"),
            }

        # ⚠️ 暫代的「修正」：每次重試把電池最大功率打八折，不是真正讀懂警告內容去修正
        current_max = bess_override or 5.0
        bess_override = round(current_max * 0.8, 2)

    # --- 🆕 階段三之二：用同一份負載資料額外跑一次 baseline，取得對比用的未優化總電價 ---
    # 這裡不管過不過載都會記錄下來，因為規格書寫的是「未優化電路不放上網站即時運行，
    # 只在背景跑一次拿電價做對比」，baseline 過載與否不影響 PSO 版本能不能上線。
    _, df_baseline_warning, df_baseline_history = run_offline_validation(
        mode="baseline",
        pricing=pricing,
        outage_start_step=outage_start_step,
        outage_end_step=outage_end_step,
    )

    pso_total_cost = compute_total_cost(df_pso_history, pricing)
    baseline_total_cost = compute_total_cost(df_baseline_history, pricing)

    # 🆕 把「電池剩餘電量」折算成資產價值，從成本裡扣掉（存越多電，等於變相少花錢）
    pso_battery_asset_value = compute_battery_asset_value(df_pso_history, pricing)
    baseline_battery_asset_value = compute_battery_asset_value(df_baseline_history, pricing)

    pso_adjusted_cost = round(pso_total_cost - pso_battery_asset_value, 2)
    baseline_adjusted_cost = round(baseline_total_cost - baseline_battery_asset_value, 2)

    savings = round(baseline_adjusted_cost - pso_adjusted_cost, 2)

    # --- 階段四：重置即時引擎（只有 PSO 版本會被搬上網站即時展示）---
    reset_online_engine(server_module, pricing, outage, outage_start_step, outage_end_step)

    server_module.CURRENT_COST_SUMMARY = {
        "pso_cost": pso_adjusted_cost,
        "baseline_cost": baseline_adjusted_cost,
        "savings": savings,
}

    return {
        "status": "success",
        "message": "切換成功",
        "pso_total_cost": pso_total_cost,              # 原始電費（未考慮電池餘電）
        "baseline_total_cost": baseline_total_cost,     # 原始電費（未考慮電池餘電）
        "pso_battery_asset_value": pso_battery_asset_value,
        "baseline_battery_asset_value": baseline_battery_asset_value,
        "pso_adjusted_cost": pso_adjusted_cost,          # 扣掉電池餘電價值後的「真實成本」
        "baseline_adjusted_cost": baseline_adjusted_cost,
        "savings": savings,                              # 現在是用 adjusted_cost 算出來的
        "baseline_had_overload": not df_baseline_warning.empty,
    }