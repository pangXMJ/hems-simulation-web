# ============================================================
# simulation.py
# 將 BESS 排程轉成圖表與分析資料
# ============================================================

import numpy as np

from .config import (
    HOURS,
    DT,
    PRICE,
    P_PV,
    BESS_CAPACITY,
    INITIAL_SOC
)

from .battery_model import (
    limit_bess_power_by_physical_soc,
    update_battery_energy
)

from .objective import (
    is_outage_hour,
    get_load_demand
)


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
        p_bess = limit_bess_power_by_physical_soc(
            current_energy,
            requested_p_bess
        )

        actual_bess_curve.append(p_bess)

        load_demand = get_load_demand(
            t,
            p_critical,
            p_noncritical
        )

        current_energy = update_battery_energy(
            current_energy,
            p_bess
        )

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