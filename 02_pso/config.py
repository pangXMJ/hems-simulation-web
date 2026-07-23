"""HEMS + PSO 的共用設定（15 分鐘版本）。"""

from pathlib import Path


# ============================================================
# 1. 檔案路徑
# ============================================================
# ============================================================
# 1. 檔案路徑
# ============================================================
# 預設資料夾結構：
# project/
# ├─ 02_pso/
# │  ├─ config.py
# │  ├─ pso.py
# │  └─ main.py
# ├─ data/01_raw/
# │  ├─ LoadShapes_All_Nodes_15min_kw.csv
# │  ├─ pv_curve_15min.csv
# │  └─ electricity_tariffs_2tage_and_3tage.csv
# └─ results/
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data" / "01_raw"
RESULT_DIR = BASE_DIR / "data" / "04_optimized_pso"

LOAD_CSV_PATH = DATA_DIR / "LoadShapes_All_Nodes_15min_kw.csv"
PV_CSV_PATH = DATA_DIR / "pv_curve_15min.csv"
TARIFF_CSV_PATH = DATA_DIR / "electricity_tariffs_2tage_and_3tage.csv"

TARIFF_TYPES = ("two_stage", "three_stage")
TARIFF_SEASON = "summer"
TARIFF_DAY_TYPE = "weekday"

OUTPUT_CSV_PATHS = {
    "two_stage": RESULT_DIR / "battery_usage_two_stage_summer_weekday_15min.csv",
    "three_stage": RESULT_DIR / "battery_usage_three_stage_summer_weekday_15min.csv",
}

# ============================================================
# 2. 15 分鐘時間設定
# ============================================================
DT = 0.25  # 每個時段長度（小時）
NUM_INTERVALS = 96  # 每日 15 分鐘時段總數
INTERVALS_PER_HOUR = 4  # 每小時包含的 15 分鐘時段數

# 停電區間採「開始時間包含、結束時間不包含」。
# 18:00 <= Time < 22:00，共 16 個 15 分鐘時段。
OUTAGE_START_TIME = "18:00"  # 停電開始時間
OUTAGE_END_TIME = "22:00"  # 停電結束時間（不包含）

# 從停電結束後開始執行終端 SOC 控制，確保 24:00 回到目標 SOC。
TERMINAL_SOC_CONTROL_START_TIME = "22:00"  # 終端 SOC 控制開始時間


def time_to_interval(time_text):
    """將 HH:MM 轉成當日第幾個 15 分鐘時段。"""
    hour, minute = (int(value) for value in time_text.split(":"))  # 小時、分鐘
    if not 0 <= hour <= 24 or not 0 <= minute < 60:
        raise ValueError(f"時間格式錯誤：{time_text}")
    if minute % 15 != 0:
        raise ValueError(f"時間必須落在 15 分鐘刻度：{time_text}")
    if hour == 24 and minute != 0:
        raise ValueError(f"24 時只能寫成 24:00：{time_text}")
    return hour * INTERVALS_PER_HOUR + minute // 15


OUTAGE_START_INDEX = time_to_interval(OUTAGE_START_TIME)  # 停電開始時段索引
OUTAGE_END_INDEX = time_to_interval(OUTAGE_END_TIME)  # 停電結束時段索引
TERMINAL_SOC_CONTROL_START_INDEX = time_to_interval(
    TERMINAL_SOC_CONTROL_START_TIME
)  # 終端 SOC 控制開始時段索引


# ============================================================
# 3. 關鍵負載欄位
# ============================================================
# 停電時只保留以下 16 個設備；數值完全依負載 CSV（包含廚房插座 0.1 kW）。
CRITICAL_LOAD_COLUMNS = [  # 停電時保留供電的關鍵負載欄位
    "ep_fridge_an",  # 變頻冰箱
    "ep_washer_an",  # 洗衣機
    "ep_dryer_bn",  # 烘衣機
    "ep_pump",  # 揚水馬達
    "ep_1f_lighting1_an",  # 1 樓照明 1
    "ep_1f_lighting2_bn",  # 1 樓照明 2
    "ep_2f_lighting1_an",  # 2 樓照明 1
    "ep_2f_lighting2_bn",  # 2 樓照明 2
    "ep_3f_lighting1_an",  # 3 樓照明 1
    "ep_3f_lighting2_bn",  # 3 樓照明 2
    "ep_1f_wifi_an",  # Wi-Fi 路由器
    "ep_1f_WaterHeater_abn",  # 電熱水器
    "boosterpump_abn",  # 加壓馬達
    "ep_kitchen_bn",  # 廚房插座
    "ep_2f_socket1_an",  # 2 樓插座 1
    "ep_2f_socket2_bn",  # 2 樓插座 2
]

# 這些欄位不是設備功率，不納入總負載加總。
LOAD_METADATA_COLUMNS = {  # 不納入設備功率加總的資料欄位
    "Time",
    "Hour",
    "Minute",
    "total_kw",
    "energy_kwh",
    "total_energy_kwh",
}


# ============================================================
# 4. 電池參數
# ============================================================
BESS_CAPACITY_KWH = 20.0  # 電池額定容量（kWh）
INITIAL_SOC = 0.50  # 電池初始荷電狀態
SOC_MIN = 0.20  # 電池最低荷電狀態
SOC_MAX = 0.90  # 電池最高荷電狀態
TARGET_FINAL_SOC = 0.50  # 每日結束目標荷電狀態

P_BESS_MAX_KW = 5.0  # 電池最大充放電功率（kW）
ETA_CHARGE = 0.95  # 電池充電效率
ETA_DISCHARGE = 0.95  # 電池放電效率

# 正常供電時允許電池由電網充電；停電時只能使用剩餘 PV 充電。
ALLOW_GRID_CHARGING = True  # 是否允許由電網替電池充電

# 電池功率符號：
# > 0：Discharging（放電）
# < 0：Charging（充電）
# = 0：Idling（待機）


# ============================================================
# 5. 目標函數權重
# ============================================================
FINAL_SOC_PENALTY_WEIGHT = 10000.0  # 終端 SOC 偏差懲罰權重
CRITICAL_SHORTAGE_PENALTY_WEIGHT = 100000.0  # 停電時關鍵負載缺電懲罰權重
CURTAILMENT_PENALTY_WEIGHT = 0.0  # 多餘電力棄電懲罰權重


# ============================================================
# 6. PSO 參數
# ============================================================
RANDOM_SEED = 42  # PSO 隨機種子
NUM_PARTICLES = 50  # PSO 粒子數量
MAX_ITERATIONS = 500  # PSO 最大迭代次數
INERTIA_MAX = 0.90  # 慣性權重最大值
INERTIA_MIN = 0.40  # 慣性權重最小值
COGNITIVE_COEFFICIENT = 1.50  # 個體最佳學習係數
SOCIAL_COEFFICIENT = 1.50  # 全域最佳學習係數
