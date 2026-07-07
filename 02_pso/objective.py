# ============================================================
# objective.py
# HEMS 目標函數 / PSO fitness function
# ============================================================

from .config import (
    HOURS,
    DT,
    PRICE,
    P_PV,
    BESS_CAPACITY,
    INITIAL_SOC,
    OUTAGE_START,
    OUTAGE_END,
    TARGET_FINAL_SOC,
    FINAL_SOC_PENALTY_WEIGHT,
    CURTAILMENT_PENALTY_WEIGHT,
    CRITICAL_SHORTAGE_PENALTY_WEIGHT
)

from .battery_model import (
    limit_bess_power_by_physical_soc,
    update_battery_energy,
    calculate_soc_penalty
)


def is_outage_hour(t):
    """
    判斷第 t 小時是否為停電時段。
    """
    return OUTAGE_START <= t <= OUTAGE_END


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
        p_bess = limit_bess_power_by_physical_soc(
            current_energy,
            requested_p_bess
        )

        load_demand = get_load_demand(
            t,
            p_critical,
            p_noncritical
        )

        # 先根據當下實際可執行的充放電更新電池能量
        current_energy = update_battery_energy(
            current_energy,
            p_bess
        )

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
                penalty += (
                    CRITICAL_SHORTAGE_PENALTY_WEIGHT
                    * critical_shortage
                    * DT
                )

            # 停電時還安排充電通常不合理，因此加入懲罰
            if p_bess > 0:
                penalty += 10000 * p_bess

    # 最終 SOC 限制：避免最後電池太低
    final_soc = current_energy / BESS_CAPACITY

    if final_soc < TARGET_FINAL_SOC:
        penalty += (
            FINAL_SOC_PENALTY_WEIGHT
            * (TARGET_FINAL_SOC - final_soc) ** 2
        )

    return total_cost + penalty