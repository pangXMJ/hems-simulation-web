# ============================================================
# 0. 匯入套件
# ============================================================
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# ============================================================
# 1. 基本設定
# ============================================================
# 固定亂數種子：讓每次執行結果比較接近，方便除錯與報告比較
np.random.seed(42)

# 模擬時間長度：一天 24 小時
HOURS = 24
DT = 1.0  # 每一筆資料代表 1 小時，所以 dt = 1 hour

# ============================================================
# 2. 電價、PV、BESS、停電參數
# ============================================================
# 時間電價（元/kWh）
# 這裡假設：
# - 半夜與早上較便宜
# - 白天較貴
# - 傍晚尖峰最貴
PRICE = np.array([
    2.23, 2.23, 2.23, 2.23, 2.23, 2.23, 2.23, 2.23, 2.23,
    5.02, 5.02, 5.02, 5.02, 5.02, 5.02, 5.02,
    8.12, 8.12, 8.12, 8.12, 8.12, 8.12,
    5.02, 5.02
])

# 太陽能發電預測（kW）
# 夜間為 0，白天逐漸增加，中午附近最大
P_PV = np.array([
    0, 0, 0, 0, 0, 0,
    0.5, 1.5, 2.5, 3.5, 4.0, 4.2,
    4.0, 3.0, 2.0, 0.8, 0.2,
    0, 0, 0, 0, 0, 0, 0
])

# BESS：Battery Energy Storage System，電池儲能系統
BESS_CAPACITY = 20.0      # 電池總容量，單位 kWh
SOC_MIN = 0.2             # 最低 SOC，20%，用來保留緊急備用電量
SOC_MAX = 0.9             # 最高 SOC，90%，避免電池過充
P_BESS_MAX = 3.0          # 最大充放電功率，單位 kW
INITIAL_SOC = 0.5         # 初始 SOC，50%

# 電池物理極限
# 這裡代表真實電池不可能低於 0%，也不可能高於 100%。
# SOC_MIN / SOC_MAX 則是「希望運轉範圍」，超出會被懲罰；
# PHYSICAL_SOC_MIN / PHYSICAL_SOC_MAX 是「絕對物理限制」，會直接限制充放電。
PHYSICAL_SOC_MIN = 0.0
PHYSICAL_SOC_MAX = 1.0

# 電池充放電效率
ETA_CHARGE = 0.95         # 充電效率
ETA_DISCHARGE = 0.95      # 放電效率

# 最終 SOC 目標
# 例如希望一天結束後，電池仍回到 50%，避免今天把明天的備用電全部用掉
TARGET_FINAL_SOC = 0.5
FINAL_SOC_PENALTY_WEIGHT = 10000

# PV 棄光懲罰
# 若設為 0，代表只計算棄光量，不會把棄光放進成本懲罰
CURTAILMENT_PENALTY_WEIGHT = 0.0

# 停電時間設定
# 這裡代表 t = 18, 19, 20, 21 這幾小時為停電時段
OUTAGE_START = 18
OUTAGE_END = 21

# 關鍵負載供電不足時的懲罰權重
CRITICAL_SHORTAGE_PENALTY_WEIGHT = 100000

# ============================================================
# 3. PSO 參數設定
# ============================================================
NUM_PARTICLES = 50        # 粒子數量
MAX_ITERATIONS = 500       # 最大迭代次數
DIMENSIONS = HOURS        # 每一個粒子有 24 維，代表 24 小時的 BESS 排程

# BESS 排程上下限
# p_bess > 0：充電
# p_bess < 0：放電
BOUNDS = (-P_BESS_MAX, P_BESS_MAX)

# ============================================================
# 4. 資料讀取與前處理
# ============================================================
def load_fixed_load_data(file_path):
    """
    讀取每日 24 小時負載資料。

    檔案中需要有欄位：
    - avg_power_kW：每小時平均負載功率，單位 kW

    回傳：
    - p_fixed：24 筆負載功率資料，單位 kW
    """
    load_df = pd.read_csv(file_path)

    if "avg_power_kW" not in load_df.columns:
        raise ValueError("CSV 檔案中找不到 avg_power_kW 欄位，請確認欄位名稱。")

    p_fixed = load_df["avg_power_kW"].to_numpy()

    if len(p_fixed) != HOURS:
        raise ValueError(f"負載資料需要剛好 {HOURS} 筆，目前讀到 {len(p_fixed)} 筆。")

    return p_fixed

def split_load_into_critical_and_noncritical(p_fixed):
    """
    將總負載切成：
    - 關鍵負載：停電時仍希望供電的負載
    - 非關鍵負載：停電時可以暫停的負載

    目前假設：
    - 關鍵負載 = 40%
    - 非關鍵負載 = 60%
    """
    p_critical = 0.4 * p_fixed
    p_noncritical = 0.6 * p_fixed
    return p_critical, p_noncritical

# ============================================================
# 5. HEMS 計算用小工具函式
# ============================================================
def is_outage_hour(t):
    """
    判斷第 t 小時是否為停電時段。
    """
    return OUTAGE_START <= t <= OUTAGE_END

def limit_bess_power_by_physical_soc(current_energy, requested_p_bess):
    """
    依照電池目前能量，限制 BESS 充放電功率。

    目的：
    - 避免 SOC 被算到 0% 以下
    - 避免 SOC 被算到 100% 以上

    輸入：
    - current_energy：目前電池內的能量，單位 kWh
    - requested_p_bess：PSO 原本要求的充放電功率，單位 kW

    回傳：
    - actual_p_bess：物理限制後，電池實際可執行的充放電功率

    符號定義：
    - actual_p_bess > 0：充電
    - actual_p_bess < 0：放電
    - actual_p_bess = 0：不能再充或不能再放
    """
    min_energy = PHYSICAL_SOC_MIN * BESS_CAPACITY
    max_energy = PHYSICAL_SOC_MAX * BESS_CAPACITY

    if requested_p_bess > 0:
        # 充電時，不能讓電池超過 100%
        remaining_charge_energy = max_energy - current_energy

        if remaining_charge_energy <= 0:
            return 0.0

        # current_energy + p * eta_charge * dt <= max_energy
        max_charge_power = remaining_charge_energy / (ETA_CHARGE * DT)
        actual_p_bess = min(requested_p_bess, max_charge_power)

    elif requested_p_bess < 0:
        # 放電時，不能讓電池低於 0%
        available_discharge_energy = current_energy - min_energy

        if available_discharge_energy <= 0:
            return 0.0

        # current_energy + p / eta_discharge * dt >= min_energy
        # 因為 p 是負值，所以最低只能放到 -available_energy * eta_discharge / dt
        max_discharge_power = available_discharge_energy * ETA_DISCHARGE / DT
        actual_p_bess = max(requested_p_bess, -max_discharge_power)

    else:
        actual_p_bess = 0.0

    return actual_p_bess

def update_battery_energy(current_energy, p_bess):
    """
    根據 BESS 實際充放電功率更新電池能量。

    p_bess > 0：充電，電池能量增加
    p_bess < 0：放電，電池能量減少

    注意：
    - 充電時乘上充電效率
    - 放電時除以放電效率，表示為了輸出 p_bess 的電，需要消耗更多電池能量
    """
    if p_bess >= 0:
        current_energy += p_bess * ETA_CHARGE * DT
    else:
        current_energy += p_bess / ETA_DISCHARGE * DT

    return current_energy

def calculate_soc_penalty(current_soc):
    """
    SOC 超過上下限時給懲罰。

    目的：
    讓 PSO 不要找到超出電池安全範圍的排程。
    """
    penalty = 0.0

    if current_soc < SOC_MIN:
        penalty += 10000 * (SOC_MIN - current_soc) ** 2

    if current_soc > SOC_MAX:
        penalty += 10000 * (current_soc - SOC_MAX) ** 2

    return penalty

def get_load_demand(t, p_critical, p_noncritical):
    """
    取得第 t 小時需要供應的負載。

    正常供電時：
    - 供應全部負載 = 關鍵負載 + 非關鍵負載

    停電時：
    - 只要求供應關鍵負載
    """
    if is_outage_hour(t):
        return p_critical[t]
    return p_critical[t] + p_noncritical[t]

# ============================================================
# 6. 目標函數：PSO 要最小化的函數
# ============================================================

def hems_objective(p_bess_schedule, p_critical, p_noncritical):
    """
    HEMS 目標函數，也稱為 PSO 的適應度函數 fitness function。

    輸入：
    - p_bess_schedule：長度為 24 的陣列，代表每小時 BESS 充放電功率

    輸出：
    - total_cost + penalty：越小越好

    目標：
    1. 降低電費
    2. 避免 SOC 超出限制
    3. 停電時盡量供應關鍵負載
    4. 避免最後 SOC 太低
    """
    total_cost = 0.0
    penalty = 0.0

    # 將初始 SOC 轉成電池能量 kWh
    current_energy = INITIAL_SOC * BESS_CAPACITY

    for t in range(HOURS):
        requested_p_bess = p_bess_schedule[t]

        # 加入電池物理限制，避免 SOC 低於 0% 或高於 100%
        p_bess = limit_bess_power_by_physical_soc(current_energy, requested_p_bess)

        load_demand = get_load_demand(t, p_critical, p_noncritical)

        # 先根據當下實際可執行的充放電更新電池能量
        current_energy = update_battery_energy(current_energy, p_bess)
        current_soc = current_energy / BESS_CAPACITY

        # 若 SOC 超出範圍，加入懲罰
        penalty += calculate_soc_penalty(current_soc)

        if not is_outage_hour(t):
            # 正常供電時，可以向電網買電
            # p_grid > 0 代表需要向電網買電
            # p_grid < 0 代表 PV 或電池過剩，形成棄光/回送
            p_grid = load_demand - P_PV[t] + p_bess
            p_grid_actual = max(p_grid, 0)
            pv_curtailment = max(-p_grid, 0)

            # 電費 = 電價 * 購電功率 * 時間
            total_cost += PRICE[t] * p_grid_actual * DT

            # 若想要避免浪費 PV，可以提高 CURTAILMENT_PENALTY_WEIGHT
            penalty += CURTAILMENT_PENALTY_WEIGHT * pv_curtailment * DT

        else:
            # 停電時，不能向電網買電
            # 電池放電時 p_bess 為負，所以 -p_bess 代表電池輸出的功率
            battery_supply = max(-p_bess, 0)
            available_power = P_PV[t] + battery_supply

            # 若 PV + 電池供應功率不足以供應關鍵負載，加入很大的懲罰
            if available_power < load_demand:
                critical_shortage = load_demand - available_power
                penalty += CRITICAL_SHORTAGE_PENALTY_WEIGHT * critical_shortage * DT

            # 停電時還安排充電通常不合理，因此加入懲罰
            if p_bess > 0:
                penalty += 10000 * p_bess

    # 最終 SOC 限制：避免最後電池太低
    final_soc = current_energy / BESS_CAPACITY

    if final_soc < TARGET_FINAL_SOC:
        penalty += FINAL_SOC_PENALTY_WEIGHT * (TARGET_FINAL_SOC - final_soc) ** 2

    return total_cost + penalty

# ============================================================
# 7. 模擬函式：把某組排程轉成圖表資料
# ============================================================

def simulate_schedule(p_bess_schedule, p_critical, p_noncritical):
    """
    將某一組 BESS 排程轉成可以畫圖與分析的資料。

    回傳：
    - soc_curve：SOC 變化
    - p_grid_curve：電網購電功率
    - shortage_curve：停電時關鍵負載不足量
    - curtailment_curve：PV 多餘但未使用的功率
    - actual_bess_curve：經過電池物理限制後的實際 BESS 充放電功率
    - total_cost：實際電費，不含懲罰項
    """
    soc_curve = []
    p_grid_curve = []
    shortage_curve = []
    curtailment_curve = []
    actual_bess_curve = []

    total_cost = 0.0
    current_energy = INITIAL_SOC * BESS_CAPACITY

    for t in range(HOURS):
        requested_p_bess = p_bess_schedule[t]

        # 將 PSO 要求的功率轉成電池實際做得到的功率
        # 這樣 SOC 最低只會到 0%，不會出現負值
        p_bess = limit_bess_power_by_physical_soc(current_energy, requested_p_bess)
        actual_bess_curve.append(p_bess)

        load_demand = get_load_demand(t, p_critical, p_noncritical)

        current_energy = update_battery_energy(current_energy, p_bess)
        current_soc = current_energy / BESS_CAPACITY
        soc_curve.append(current_soc)

        if not is_outage_hour(t):
            p_grid = load_demand - P_PV[t] + p_bess
            p_grid_actual = max(p_grid, 0)
            pv_curtailment = max(-p_grid, 0)

            total_cost += PRICE[t] * p_grid_actual * DT

            p_grid_curve.append(p_grid_actual)
            shortage_curve.append(0.0)
            curtailment_curve.append(pv_curtailment)

        else:
            battery_supply = max(-p_bess, 0)
            available_power = P_PV[t] + battery_supply
            critical_shortage = max(load_demand - available_power, 0)

            # 停電時不能從電網購電，所以電網購電功率為 0
            p_grid_curve.append(0.0)
            shortage_curve.append(critical_shortage)
            curtailment_curve.append(0.0)

    return {
        "soc_curve": np.array(soc_curve),
        "p_grid_curve": np.array(p_grid_curve),
        "shortage_curve": np.array(shortage_curve),
        "curtailment_curve": np.array(curtailment_curve),
        "actual_bess_curve": np.array(actual_bess_curve),
        "total_cost": total_cost,
    }


# ============================================================
# 8. PSO 演算法
# ============================================================

def run_pso(p_critical, p_noncritical):
    """
    執行粒子群演算法 PSO。

    PSO 概念簡述：
    - 每個粒子代表一組 24 小時 BESS 排程
    - 粒子會記住自己的最佳位置 pbest
    - 整個群體會記住目前找到的最佳位置 gbest
    - 每次迭代時，粒子會同時參考：
      1. 自己過去最好的經驗
      2. 全體目前最好的經驗
    """
    # 初始化粒子位置：每個粒子都是一組 24 小時排程
    positions = np.random.uniform(
        BOUNDS[0],
        BOUNDS[1],
        (NUM_PARTICLES, DIMENSIONS)
    )

    # 初始化粒子速度
    velocities = np.random.uniform(
        -1,
        1,
        (NUM_PARTICLES, DIMENSIONS)
    )

    # 每個粒子自己的最佳位置
    pbest_positions = positions.copy()

    # 每個粒子自己的最佳 fitness
    pbest_fitness = np.array([
        hems_objective(p, p_critical, p_noncritical)
        for p in positions
    ])

    # 找出群體最佳粒子
    gbest_index = np.argmin(pbest_fitness)
    gbest_position = pbest_positions[gbest_index].copy()
    gbest_fitness = pbest_fitness[gbest_index]

    # 保存「迭代前」最佳結果
    before_position = gbest_position.copy()
    before_fitness = gbest_fitness

    # 用來記錄每次迭代的最佳 fitness，最後畫收斂曲線
    convergence_curve = []

    for i in range(MAX_ITERATIONS):
        # 動態慣性權重
        # 前期 w 較大：粒子比較敢探索
        # 後期 w 較小：粒子比較容易收斂
        w = 0.9 - (0.5 * (i / MAX_ITERATIONS))

        # c1：粒子相信自己經驗的程度
        # c2：粒子相信群體最佳經驗的程度
        c1 = 1.5
        c2 = 1.5

        for j in range(NUM_PARTICLES):
            r1 = np.random.rand(DIMENSIONS)
            r2 = np.random.rand(DIMENSIONS)

            # 更新速度
            velocities[j] = (
                w * velocities[j]
                + c1 * r1 * (pbest_positions[j] - positions[j])
                + c2 * r2 * (gbest_position - positions[j])
            )

            # 更新位置
            positions[j] = positions[j] + velocities[j]

            # 限制 BESS 充放電功率不可超過上下限
            positions[j] = np.clip(positions[j], BOUNDS[0], BOUNDS[1])

            # 計算新的 fitness
            fitness = hems_objective(positions[j], p_critical, p_noncritical)

            # 如果目前位置比自己歷史最佳更好，就更新 pbest
            if fitness < pbest_fitness[j]:
                pbest_fitness[j] = fitness
                pbest_positions[j] = positions[j].copy()

                # 如果目前位置也比全體歷史最佳更好，就更新 gbest
                if fitness < gbest_fitness:
                    gbest_fitness = fitness
                    gbest_position = positions[j].copy()

        convergence_curve.append(gbest_fitness)

    # 保存「迭代後」最佳結果
    after_position = gbest_position.copy()
    after_fitness = gbest_fitness

    return {
        "before_position": before_position,
        "before_fitness": before_fitness,
        "after_position": after_position,
        "after_fitness": after_fitness,
        "convergence_curve": np.array(convergence_curve),
    }

# ============================================================
# 核心管線：取代原本的 main()，專門負責計算並傳送資料包
# ============================================================
def run_optimization_pipeline(p_fixed):
    """
    執行完整的最佳化與模擬流程。
    直接接收長度為 24 的負載陣列 (p_fixed)，不再讀寫實體 CSV。
    """
    # 1. 切分關鍵負載與非關鍵負載
    p_critical, p_noncritical = split_load_into_critical_and_noncritical(p_fixed)

    # 2. 執行 PSO 最佳化
    pso_result = run_pso(p_critical, p_noncritical)

    # 3. 取得迭代前與迭代後的排程
    before_position = pso_result["before_position"]
    after_position = pso_result["after_position"]
    convergence_curve = pso_result.get("convergence_curve", [])

    # 4. 將排程轉成詳細的模擬圖表資料
    before_result = simulate_schedule(before_position, p_critical, p_noncritical)
    after_result = simulate_schedule(after_position, p_critical, p_noncritical)

    data_package = {
        "before_position": before_position,
        "after_position": after_position,
        "before_result": before_result,
        "after_result": after_result,
        "convergence_curve": convergence_curve
    }

    return data_package