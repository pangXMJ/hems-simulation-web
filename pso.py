"""PSO 演算法、目標函數與共用電池計算。"""

import numpy as np

from config import (
    ALLOW_GRID_CHARGING,
    BESS_CAPACITY_KWH,
    COGNITIVE_COEFFICIENT,
    CRITICAL_SHORTAGE_PENALTY_WEIGHT,
    CURTAILMENT_PENALTY_WEIGHT,
    DT,
    ETA_CHARGE,
    ETA_DISCHARGE,
    FINAL_SOC_PENALTY_WEIGHT,
    INERTIA_MAX,
    INERTIA_MIN,
    INITIAL_SOC,
    MAX_ITERATIONS,
    NUM_INTERVALS,
    NUM_PARTICLES,
    OUTAGE_END_INDEX,
    OUTAGE_START_INDEX,
    P_BESS_MAX_KW,
    RANDOM_SEED,
    SOCIAL_COEFFICIENT,
    SOC_MAX,
    SOC_MIN,
    TERMINAL_SOC_CONTROL_START_INDEX,
    TARGET_FINAL_SOC,
)


def is_outage_interval(interval_index):
    """判斷目前是否為停電時段；結束時間不包含在內。"""
    return OUTAGE_START_INDEX <= interval_index < OUTAGE_END_INDEX


def battery_energy_change_kwh(actual_bess_kw):
    """回傳一個時段的電池內部能量變化；充電為正、放電為負。"""
    if actual_bess_kw > 0:  # 放電
        return -(actual_bess_kw * DT / ETA_DISCHARGE)
    if actual_bess_kw < 0:  # 充電
        return (-actual_bess_kw * DT * ETA_CHARGE)
    return 0.0


def limit_bess_power_by_soc(current_energy_kwh, requested_bess_kw):
    """將要求功率限制在額定功率及 SOC 20%～90% 的硬限制內。"""
    requested_bess_kw = float(  # 額定功率限制後的要求功率
        np.clip(requested_bess_kw, -P_BESS_MAX_KW, P_BESS_MAX_KW)
    )
    min_energy_kwh = SOC_MIN * BESS_CAPACITY_KWH  # SOC 下限對應的最低能量
    max_energy_kwh = SOC_MAX * BESS_CAPACITY_KWH  # SOC 上限對應的最高能量

    if requested_bess_kw > 0:  # 放電
        available_energy_kwh = max(  # 可供放電的能量
            current_energy_kwh - min_energy_kwh,
            0.0
        )
        max_discharge_kw = (  # SOC 限制下的最大放電功率
            available_energy_kwh * ETA_DISCHARGE / DT
        )
        return min(requested_bess_kw, max_discharge_kw)

    if requested_bess_kw < 0:  # 充電
        remaining_capacity_kwh = max(  # 可供充電的剩餘容量
            max_energy_kwh - current_energy_kwh,
            0.0
        )
        max_charge_kw = (  # SOC 限制下的最大充電功率
            remaining_capacity_kwh / (ETA_CHARGE * DT)
        )
        return max(requested_bess_kw, -max_charge_kw)

    return 0.0


def limit_bess_power_by_power_balance(
    requested_bess_kw,
    load_demand_kw,
    pv_kw,
    outage,
):
    """依電力平衡修正命令；停電時關鍵負載供電具有最高優先權。"""
    if outage:
        critical_shortfall_kw = max(  # 關鍵負載的功率缺口
            load_demand_kw - pv_kw,
            0.0,
        )
        if critical_shortfall_kw > 0:
            # 有缺口時自動要求電池補足；之後仍會套用 SOC 與額定功率限制。
            return critical_shortfall_kw

        # PV 已足夠供應關鍵負載：不需要放電，只能用剩餘 PV 充電。
        surplus_pv_kw = max(pv_kw - load_demand_kw, 0.0)  # 剩餘太陽能功率
        if requested_bess_kw < 0:
            return max(requested_bess_kw, -surplus_pv_kw)
        return 0.0

    if requested_bess_kw > 0:
        # 系統不賣電，因此電池放電最多只補足「負載 - PV」的缺口。
        useful_discharge_kw = max(  # 不會造成逆送電的有效放電功率
            load_demand_kw - pv_kw,
            0.0,
        )
        return min(requested_bess_kw, useful_discharge_kw)

    if requested_bess_kw < 0 and not ALLOW_GRID_CHARGING:
        # 若未允許電網充電，正常供電時也只能使用剩餘 PV 充電。
        surplus_pv_kw = max(pv_kw - load_demand_kw, 0.0)  # 剩餘太陽能功率
        return max(requested_bess_kw, -surplus_pv_kw)

    return requested_bess_kw


def terminal_soc_request_kw(current_energy_kwh, interval_index):
    """22:00 後平均修正能量，使最後一筆 SOC 精確回到 50%。"""
    if interval_index < TERMINAL_SOC_CONTROL_START_INDEX:
        return None

    remaining_intervals = NUM_INTERVALS - interval_index  # 剩餘時段數
    target_energy_kwh = (  # 目標終端 SOC 對應的電池能量
        TARGET_FINAL_SOC * BESS_CAPACITY_KWH
    )
    energy_gap_kwh = target_energy_kwh - current_energy_kwh  # 距離目標的能量差

    if energy_gap_kwh > 0:  # 電量不足，需要充電（功率為負）
        return -energy_gap_kwh / (
            ETA_CHARGE * DT * remaining_intervals
        )
    if energy_gap_kwh < 0:  # 電量過多，需要放電（功率為正）
        return (
            -energy_gap_kwh
            * ETA_DISCHARGE
            / (DT * remaining_intervals)
        )
    return 0.0


def _validate_series(*series):
    if any(len(values) != NUM_INTERVALS for values in series):
        lengths = [len(values) for values in series]  # 各輸入序列的實際長度
        raise ValueError(f"所有輸入必須各有 {NUM_INTERVALS} 筆，目前長度為 {lengths}")


def hems_objective(
    bess_schedule_kw,
    critical_load_kw,
    noncritical_load_kw,
    pv_kw,
    price_per_kwh,
):
    """計算一組 96 點電池排程的電費與限制懲罰，數值越小越好。"""
    _validate_series(
        bess_schedule_kw,
        critical_load_kw,
        noncritical_load_kw,
        pv_kw,
        price_per_kwh,
    )

    current_energy_kwh = INITIAL_SOC * BESS_CAPACITY_KWH  # 目前電池能量（kWh）
    total_cost = 0.0  # 累計購電費用
    penalty = 0.0  # 累計限制懲罰值

    for t in range(NUM_INTERVALS):
        requested_bess_kw = bess_schedule_kw[t]  # 排程要求的電池功率
        outage = is_outage_interval(t)  # 目前是否停電

        terminal_request_kw = terminal_soc_request_kw(  # 終端 SOC 控制要求功率
            current_energy_kwh,
            t,
        )
        if terminal_request_kw is not None:
            requested_bess_kw = terminal_request_kw  # 改用終端 SOC 控制功率

        load_demand_kw = (
            critical_load_kw[t]
            if outage
            else critical_load_kw[t] + noncritical_load_kw[t]
        )  # 目前需要供應的負載功率
        requested_bess_kw = limit_bess_power_by_power_balance(  # 電力平衡限制後功率
            requested_bess_kw,
            load_demand_kw,
            pv_kw[t],
            outage,
        )

        actual_bess_kw = limit_bess_power_by_soc(  # SOC 限制後的實際電池功率
            current_energy_kwh,
            requested_bess_kw,
        )
        current_energy_kwh += battery_energy_change_kwh(  # 更新目前電池能量
            actual_bess_kw
        )

        if outage:
            # 停電時非關鍵負載卸載，電網功率固定為 0。
            power_balance_kw = (  # PV 與電池供應關鍵負載後的功率餘額
                pv_kw[t]
                + actual_bess_kw
                - critical_load_kw[t]
            )
            shortage_kw = max(-power_balance_kw, 0.0)  # 關鍵負載缺電功率
            curtailment_kw = max(power_balance_kw, 0.0)  # 多餘棄電功率
            penalty += (  # 累加關鍵負載缺電懲罰
                CRITICAL_SHORTAGE_PENALTY_WEIGHT
                * shortage_kw
                * DT
            )
        else:
            net_grid_kw = (  # 扣除 PV 與電池後的電網淨功率
                load_demand_kw - pv_kw[t] - actual_bess_kw
            )
            grid_import_kw = max(net_grid_kw, 0.0)  # 電網購電功率
            curtailment_kw = max(-net_grid_kw, 0.0)  # 多餘棄電功率
            total_cost += (  # 累加此時段購電費用
                grid_import_kw * DT * price_per_kwh[t]
            )

        penalty += (  # 累加棄電懲罰
            CURTAILMENT_PENALTY_WEIGHT * curtailment_kw * DT
        )

    final_soc = current_energy_kwh / BESS_CAPACITY_KWH  # 模擬結束時的 SOC
    penalty += (  # 累加終端 SOC 偏差懲罰
        FINAL_SOC_PENALTY_WEIGHT
        * (TARGET_FINAL_SOC - final_soc) ** 2
    )

    return total_cost + penalty


def run_pso(critical_load_kw, noncritical_load_kw, pv_kw, price_per_kwh):
    """執行 96 維 PSO，回傳最佳排程、fitness 與收斂曲線。"""
    _validate_series(
        critical_load_kw,
        noncritical_load_kw,
        pv_kw,
        price_per_kwh,
    )

    rng = np.random.default_rng(RANDOM_SEED)  # 固定種子的亂數產生器
    lower_bound = -P_BESS_MAX_KW  # 粒子位置下限（最大充電功率）
    upper_bound = P_BESS_MAX_KW  # 粒子位置上限（最大放電功率）

    positions = rng.uniform(  # 所有粒子的目前位置（電池功率排程）
        lower_bound,
        upper_bound,
        size=(NUM_PARTICLES, NUM_INTERVALS),
    )
    velocities = rng.uniform(  # 所有粒子的目前速度
        -P_BESS_MAX_KW,
        P_BESS_MAX_KW,
        size=(NUM_PARTICLES, NUM_INTERVALS),
    )

    def fitness(position):
        return hems_objective(
            position,
            critical_load_kw,
            noncritical_load_kw,
            pv_kw,
            price_per_kwh,
        )

    pbest_positions = positions.copy()  # 各粒子的歷史最佳位置
    pbest_fitness = np.array(  # 各粒子的歷史最佳適應值
        [fitness(position) for position in positions]
    )

    gbest_index = int(np.argmin(pbest_fitness))  # 全域最佳粒子索引
    gbest_position = pbest_positions[gbest_index].copy()  # 全域最佳位置
    gbest_fitness = float(pbest_fitness[gbest_index])  # 全域最佳適應值

    before_position = gbest_position.copy()  # PSO 迭代前的最佳位置
    before_fitness = gbest_fitness  # PSO 迭代前的最佳適應值
    convergence_curve = []  # 每次迭代的最佳適應值紀錄

    for iteration in range(MAX_ITERATIONS):
        progress = iteration / max(MAX_ITERATIONS - 1, 1)  # 目前迭代進度
        inertia = (  # 線性遞減的慣性權重
            INERTIA_MAX + (INERTIA_MIN - INERTIA_MAX) * progress
        )

        for particle in range(NUM_PARTICLES):
            r1 = rng.random(NUM_INTERVALS)  # 個體學習隨機係數
            r2 = rng.random(NUM_INTERVALS)  # 群體學習隨機係數
            velocities[particle] = (  # 更新目前粒子的速度
                inertia * velocities[particle]
                + COGNITIVE_COEFFICIENT
                * r1
                * (pbest_positions[particle] - positions[particle])
                + SOCIAL_COEFFICIENT
                * r2
                * (gbest_position - positions[particle])
            )
            positions[particle] = np.clip(  # 更新目前粒子的位置
                positions[particle] + velocities[particle],
                lower_bound,
                upper_bound,
            )

            current_fitness = fitness(positions[particle])  # 目前粒子的適應值
            if current_fitness < pbest_fitness[particle]:
                pbest_fitness[particle] = current_fitness  # 更新個體最佳適應值
                pbest_positions[particle] = (  # 更新個體最佳位置
                    positions[particle].copy()
                )

                if current_fitness < gbest_fitness:
                    gbest_fitness = float(current_fitness)  # 更新全域最佳適應值
                    gbest_position = positions[particle].copy()  # 更新全域最佳位置

        convergence_curve.append(gbest_fitness)

    return {
        "before_position": before_position,
        "before_fitness": before_fitness,
        "after_position": gbest_position,
        "after_fitness": gbest_fitness,
        "convergence_curve": np.asarray(convergence_curve),
    }
