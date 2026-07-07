# ============================================================
# battery_model.py
# 電池模型相關函式
# ============================================================

from .config import (
    BESS_CAPACITY,
    SOC_MIN,
    SOC_MAX,
    PHYSICAL_SOC_MIN,
    PHYSICAL_SOC_MAX,
    ETA_CHARGE,
    ETA_DISCHARGE,
    DT
)


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