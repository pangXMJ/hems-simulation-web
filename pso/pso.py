"""PSO 演算法、目標函數與共用電池計算。"""

import numpy as np

from config import (
    ALLOW_GRID_CHARGING,
    BESS_CAPACITY_KWH,
    COGNITIVE_COEFFICIENT,
    CRITICAL_SHORTAGE_PENALTY_WEIGHT,
    CURTAILMENT_PENALTY_WEIGHT,
    DT,
    EARLY_STOP_PATIENCE,
    EARLY_STOP_REL_TOLERANCE,
    ETA_CHARGE,
    ETA_DISCHARGE,
    FINAL_SOC_PENALTY_WEIGHT,
    INERTIA_MAX,
    INERTIA_MIN,
    INITIAL_SOC,
    MAX_ITERATIONS,
    MIN_ITERATIONS,
    NUM_INTERVALS,
    NUM_PARTICLES,
    OUTAGE_END_INDEX,
    OUTAGE_SOC_MIN,
    OUTAGE_START_INDEX,
    P_BESS_MAX_KW,
    RANDOM_SEED,
    SOCIAL_COEFFICIENT,
    SOC_MAX,
    SOC_MIN,
    TERMINAL_SOC_CONTROL_START_INDEX,
    TARGET_FINAL_SOC,
)


def is_outage_interval(
    interval_index,
    outage_start_index=OUTAGE_START_INDEX,
    outage_end_index=OUTAGE_END_INDEX,
):
    """判斷目前是否為停電時段；結束時間不包含在內。

    outage_start_index / outage_end_index 預設用 config.py 的固定值，
    但呼叫端（例如 scenario_switch.py）可以直接傳入不同的停電區間，
    不用再依賴「monkeypatch config 屬性 + importlib.reload」這種暫代做法。
    """
    return outage_start_index <= interval_index < outage_end_index


def battery_energy_change_kwh(actual_bess_kw):
    """回傳一個時段的電池內部能量變化；充電為正、放電為負。"""
    if actual_bess_kw > 0:  # 放電
        return -(actual_bess_kw * DT / ETA_DISCHARGE)
    if actual_bess_kw < 0:  # 充電
        return (-actual_bess_kw * DT * ETA_CHARGE)
    return 0.0


def limit_bess_power_by_soc(current_energy_kwh, requested_bess_kw, outage=False):
    """依供電狀態套用 SOC 下限，並限制在電池額定功率與 SOC 上限內。"""
    requested_bess_kw = float(  # 額定功率限制後的要求功率
        np.clip(requested_bess_kw, -P_BESS_MAX_KW, P_BESS_MAX_KW)
    )
    minimum_soc = OUTAGE_SOC_MIN if outage else SOC_MIN  # 目前供電狀態的 SOC 下限
    min_energy_kwh = minimum_soc * BESS_CAPACITY_KWH  # SOC 下限對應的最低能量
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


def terminal_soc_request_kw(
    current_energy_kwh,
    interval_index,
    terminal_soc_control_start_index=TERMINAL_SOC_CONTROL_START_INDEX,
):
    """停電結束後平均修正能量，使最後一筆 SOC 精確回到目標值。

    terminal_soc_control_start_index 預設用 config.py 的固定值（22:00），
    但如果停電時間是動態指定的，呼叫端應該傳入「不早於停電結束時段」的值，
    否則電池可能沒有足夠時間從緊急下限（例如 5%）回到目標 SOC。
    """
    if interval_index < terminal_soc_control_start_index:
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


def progressive_price_lookup(cumulative_kwh_before, energy_kwh, tier_table):
    """依累進電價級距表，計算一筆能量對應的電費。

    cumulative_kwh_before：這筆能量發生之前，本月已累計的購電度數。
    energy_kwh：本次要計價的能量（kWh），必須 >= 0。
    tier_table：級距列表，每個元素是
        {"lower": 下限kWh, "upper": 上限kWh或None(無上限), "price": 每度電價}，
        必須依 lower 由小到大排序、且彼此不重疊不留縫隙。

    如果這筆能量跨越了一個以上的級距門檻（例如從 119 度用到 122 度，
    橫跨 120 度那條線），會自動拆成好幾段、各自套用該段所在級距的價格，
    不會整筆都用同一個級距的價格去算。
    """
    remaining_kwh = energy_kwh
    cursor_kwh = cumulative_kwh_before
    cost = 0.0

    for tier in tier_table:
        if remaining_kwh <= 1e-12:
            break

        lower = tier["lower"]
        upper = tier["upper"]  # None 代表這一級沒有上限

        if upper is not None and cursor_kwh >= upper:
            continue  # 目前累計值已經超過這一級的範圍，跳到下一級

        # 這一級還能容納的度數（沒有上限就是剩下要算的全部）
        tier_capacity_kwh = (
            remaining_kwh if upper is None else max(upper - cursor_kwh, 0.0)
        )
        portion_kwh = min(remaining_kwh, tier_capacity_kwh)
        if portion_kwh <= 0:
            continue

        cost += portion_kwh * tier["price"]
        cursor_kwh += portion_kwh
        remaining_kwh -= portion_kwh

    return cost


def hems_objective(
    bess_schedule_kw,
    critical_load_kw,
    noncritical_load_kw,
    pv_kw,
    price_per_kwh,
    outage_start_index=OUTAGE_START_INDEX,
    outage_end_index=OUTAGE_END_INDEX,
    terminal_soc_control_start_index=TERMINAL_SOC_CONTROL_START_INDEX,
):
    """計算一組 96 點電池排程的電費與限制懲罰，數值越小越好。

    outage_start_index / outage_end_index / terminal_soc_control_start_index
    預設沿用 config.py 的固定值，但可依需求傳入不同的停電區間。
    """
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
        outage = is_outage_interval(  # 目前是否停電
            t, outage_start_index, outage_end_index
        )

        terminal_request_kw = terminal_soc_request_kw(  # 終端 SOC 控制要求功率
            current_energy_kwh,
            t,
            terminal_soc_control_start_index,
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
            outage,
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


def hems_objective_progressive(
    bess_schedule_kw,
    critical_load_kw,
    noncritical_load_kw,
    pv_kw,
    tier_table,
    monthly_cumulative_kwh_start,
    outage_start_index=OUTAGE_START_INDEX,
    outage_end_index=OUTAGE_END_INDEX,
    terminal_soc_control_start_index=TERMINAL_SOC_CONTROL_START_INDEX,
):
    """累進電價版本的目標函數。

    跟 hems_objective() 的差異只在「怎麼把購電量換算成電費」：
    這裡不是逐時段查固定電價，而是把每個時段的購電量累加到
    monthly_cumulative_kwh_start 這個起點上，依當下累計到哪個級距，
    用 progressive_price_lookup() 查對應價格。物理面的電池/SOC/停電/
    終端 SOC 控制邏輯完全跟 hems_objective() 一樣，沒有改動。

    tier_table：累進電價級距表，格式見 progressive_price_lookup()。
    monthly_cumulative_kwh_start：本月、今天開始之前，已經累計用了幾度電。
    """
    _validate_series(
        bess_schedule_kw,
        critical_load_kw,
        noncritical_load_kw,
        pv_kw,
    )

    current_energy_kwh = INITIAL_SOC * BESS_CAPACITY_KWH  # 目前電池能量（kWh）
    cumulative_grid_kwh = monthly_cumulative_kwh_start  # 本月累計購電度數
    total_cost = 0.0  # 累計購電費用
    penalty = 0.0  # 累計限制懲罰值

    for t in range(NUM_INTERVALS):
        requested_bess_kw = bess_schedule_kw[t]  # 排程要求的電池功率
        outage = is_outage_interval(  # 目前是否停電
            t, outage_start_index, outage_end_index
        )

        terminal_request_kw = terminal_soc_request_kw(  # 終端 SOC 控制要求功率
            current_energy_kwh,
            t,
            terminal_soc_control_start_index,
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
            outage,
        )
        current_energy_kwh += battery_energy_change_kwh(  # 更新目前電池能量
            actual_bess_kw
        )

        if outage:
            # 停電時非關鍵負載卸載，電網功率固定為 0，這個時段不會有購電量。
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

            grid_import_kwh_this_step = grid_import_kw * DT  # 這個時段的購電量
            if grid_import_kwh_this_step > 0:
                total_cost += progressive_price_lookup(  # 依累計度數查級距算錢
                    cumulative_grid_kwh,
                    grid_import_kwh_this_step,
                    tier_table,
                )
                cumulative_grid_kwh += grid_import_kwh_this_step  # 更新累計購電度數

        penalty += (  # 累加棄電懲罰
            CURTAILMENT_PENALTY_WEIGHT * curtailment_kw * DT
        )

    final_soc = current_energy_kwh / BESS_CAPACITY_KWH  # 模擬結束時的 SOC
    penalty += (  # 累加終端 SOC 偏差懲罰
        FINAL_SOC_PENALTY_WEIGHT
        * (TARGET_FINAL_SOC - final_soc) ** 2
    )

    return total_cost + penalty

# 改成
def run_pso(
    critical_load_kw,
    noncritical_load_kw,
    pv_kw,
    price_per_kwh=None,
    tier_table=None,
    monthly_cumulative_kwh_start=0.0,
    outage_start_index=OUTAGE_START_INDEX,
    outage_end_index=OUTAGE_END_INDEX,
    terminal_soc_control_start_index=TERMINAL_SOC_CONTROL_START_INDEX,
):
    """執行 96 維 PSO，回傳最佳排程、fitness 與逐代指標。

    price_per_kwh：時間電價（二段式/三段式）用的 96 點電價陣列。
    tier_table / monthly_cumulative_kwh_start：累進電價用，提供 tier_table
        時會改用累進計費邏輯，並忽略 price_per_kwh；monthly_cumulative_kwh_start
        是本月、今天開始之前已經累計用了幾度電。
    price_per_kwh 與 tier_table 兩者必須且只能提供一種，不能同時給、也不能
    都不給，否則會丟 ValueError，避免不小心算成錯的計費方式卻沒人發現。

    outage_start_index / outage_end_index：本次要最佳化的停電區間（半開區間，
    結束時段不包含）。預設沿用 config.py 的固定 18:00～22:00，呼叫端（例如
    scenario_switch.py）可以直接傳入不同的值，取代舊有的
    「monkeypatch config 屬性 + importlib.reload(pso)」暫代做法。

    terminal_soc_control_start_index：終端 SOC 控制開始時段，必須不早於
    outage_end_index，否則電池可能沒有足夠時間從緊急下限回到目標 SOC。
    """
    if tier_table is not None and price_per_kwh is not None:
        raise ValueError(
            "price_per_kwh（時間電價）與 tier_table（累進電價）只能提供一種，"
            "不能同時給，請確認呼叫端的電價方案判斷邏輯。"
        )
    if tier_table is None and price_per_kwh is None:
        raise ValueError(
            "必須提供 price_per_kwh（時間電價）或 tier_table（累進電價）其中一種。"
        )
    pricing_mode = "progressive" if tier_table is not None else "time_of_use"

    if pricing_mode == "time_of_use":
        _validate_series(
            critical_load_kw,
            noncritical_load_kw,
            pv_kw,
            price_per_kwh,
        )
    else:
        _validate_series(
            critical_load_kw,
            noncritical_load_kw,
            pv_kw,
        )

    if outage_start_index > outage_end_index:
        raise ValueError(
            f"停電開始時段（index={outage_start_index}）不可晚於"
            f"停電結束時段（index={outage_end_index}）"
        )
    if outage_end_index > terminal_soc_control_start_index:
        raise ValueError(
            f"停電結束時段（index={outage_end_index}）晚於終端 SOC 控制"
            f"開始時段（index={terminal_soc_control_start_index}），電池會沒有"
            f"足夠時間回到目標 SOC。請一併傳入較晚的 "
            f"terminal_soc_control_start_index。"
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
# 改成
    def fitness(position):
        if pricing_mode == "progressive":
            return hems_objective_progressive(
                position,
                critical_load_kw,
                noncritical_load_kw,
                pv_kw,
                tier_table,
                monthly_cumulative_kwh_start,
                outage_start_index,
                outage_end_index,
                terminal_soc_control_start_index,
            )
        return hems_objective(
            position,
            critical_load_kw,
            noncritical_load_kw,
            pv_kw,
            price_per_kwh,
            outage_start_index,
            outage_end_index,
            terminal_soc_control_start_index,
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

    def calculate_swarm_diversity():
        """計算所有粒子與粒子群中心的平均歐氏距離。"""
        centroid = positions.mean(axis=0)
        distances = np.linalg.norm(positions - centroid, axis=1)
        return float(distances.mean())

    iteration_history = [  # iteration=0 代表尚未開始更新粒子的初始狀態
        {
            "iteration": 0,
            "inertia": float(INERTIA_MAX),
            "gbest_fitness": gbest_fitness,
            "iteration_best_fitness": float(pbest_fitness.min()),
            "mean_fitness": float(pbest_fitness.mean()),
            "std_fitness": float(pbest_fitness.std()),
            "swarm_diversity": calculate_swarm_diversity(),
        }
    ]
    stopped_early = False
    stop_reason = f"已達最大迭代次數 {MAX_ITERATIONS}"
    last_relative_improvement = None

    for iteration in range(MAX_ITERATIONS):
        progress = iteration / max(MAX_ITERATIONS - 1, 1)  # 目前迭代進度
        inertia = (  # 線性遞減的慣性權重
            INERTIA_MAX + (INERTIA_MIN - INERTIA_MAX) * progress
        )
        current_fitness_values = np.empty(NUM_PARTICLES)

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
            current_fitness_values[particle] = current_fitness
            if current_fitness < pbest_fitness[particle]:
                pbest_fitness[particle] = current_fitness  # 更新個體最佳適應值
                pbest_positions[particle] = (  # 更新個體最佳位置
                    positions[particle].copy()
                )

                if current_fitness < gbest_fitness:
                    gbest_fitness = float(current_fitness)  # 更新全域最佳適應值
                    gbest_position = positions[particle].copy()  # 更新全域最佳位置

        iteration_history.append(
            {
                "iteration": iteration + 1,
                "inertia": float(inertia),
                "gbest_fitness": gbest_fitness,
                "iteration_best_fitness": float(current_fitness_values.min()),
                "mean_fitness": float(current_fitness_values.mean()),
                "std_fitness": float(current_fitness_values.std()),
                "swarm_diversity": calculate_swarm_diversity(),
            }
        )

        actual_iteration = iteration + 1
        if (
            actual_iteration >= MIN_ITERATIONS
            and actual_iteration >= EARLY_STOP_PATIENCE
        ):
            previous_gbest = iteration_history[
                -EARLY_STOP_PATIENCE - 1
            ]["gbest_fitness"]
            last_relative_improvement = (
                previous_gbest - gbest_fitness
            ) / max(abs(previous_gbest), 1e-12)

            if last_relative_improvement < EARLY_STOP_REL_TOLERANCE:
                stopped_early = True
                stop_reason = (
                    f"最近 {EARLY_STOP_PATIENCE} 代的 gbest_fitness "
                    f"相對改善率 {last_relative_improvement:.8e} "
                    f"小於門檻 {EARLY_STOP_REL_TOLERANCE:.8e}"
                )
                break

    return {
        "before_position": before_position,
        "before_fitness": before_fitness,
        "after_position": gbest_position,
        "after_fitness": gbest_fitness,
        "convergence_curve": np.asarray(
            [record["gbest_fitness"] for record in iteration_history]
        ),
        "iteration_history": iteration_history,
        "actual_iterations": len(iteration_history) - 1,
        "stopped_early": stopped_early,
        "stop_reason": stop_reason,
        "last_relative_improvement": last_relative_improvement,
    }
