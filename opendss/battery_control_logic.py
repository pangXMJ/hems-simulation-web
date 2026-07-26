"""
battery_control_logic.py
共用的『電池決策大腦』。

設計原則：
- 這裡的函式只負責『算出應該下達什麼指令』，不直接呼叫 dss.Text.Command，
  也不知道自己是被離線批次模擬(hems_basecontrol.py)還是即時網站(server.py)呼叫。
- 呼叫端拿到回傳的 dict 之後，自己決定怎麼組成 OpenDSS 指令字串並執行。
- 之後如果要修電池控制邏輯（SOC 保護、三道防線、功率上限...），
  只要改這一份檔案，離線驗證跟即時網站會同時套用到，不會再兩邊不同步。
"""

BATTERY_KWRATED_DEFAULT = 5.0
DUMP_LOAD_MAX_KW_DEFAULT = 2.0

#函數宣告
#is_island_mode: bool 限制該參數必須是布林值（True/False）
def decide_battery_action(
    is_island_mode: bool,
    operation_mode: str,          # "AUTO" 或 "PSO"（併網時才有意義，孤島時忽略這個參數）
    net_kw: float,                 # net_kw = pv_kw - load_kw
    soc: float,                    # 決策當下的電池 SOC（百分比，0~100）
    current_pso_kw: float,         # 這一步的 PSO 排程功率（正值=放電，負值=充電）
    load_kw: float = 0.0,          # 這一步的負載功率，孤島模式 PV 降載計算需要用到
    battery_kwrated: float = BATTERY_KWRATED_DEFAULT,
    dump_load_max_kw: float = DUMP_LOAD_MAX_KW_DEFAULT,
    time_str: str = "",
):
    """
    回傳一個 dict，描述電池/假負載/PV 應該被設定成什麼狀態：
    {
        "battery_state": "CHARGING" / "DISCHARGING" / "IDLING",
        "battery_command_kw": float | None,   # 孤島模式用絕對 kW 下指令
        "battery_command_pct": float | None,  # 併網模式(AUTO/PSO)用 %Charge / %Discharge 下指令
        "dump_load_kw": float,                # 假負載（只有孤島模式會用到，其餘固定 0）
        "pv_pmpp": float,                      # PV 額定功率上限（孤島降載時會 < 5.0，其餘固定 5.0）
        "logs": [str, ...]                     # 這一步想印出來的除錯訊息，呼叫端自行 print
    }
    """
    result = {
        "battery_state": "IDLING",
        "battery_command_kw": None,
        "battery_command_pct": None,
        "dump_load_kw": 0.0,
        "pv_pmpp": None,
        "logs": [],
    }

    # ==========================================
    # 模式一：孤島模式（最高優先級，三道防線）
    # ==========================================
    if is_island_mode:
        if net_kw > 0.05:
            # 【危機：太陽能發電過剩】
            available_charge_space = battery_kwrated if soc < 99.9 else 0.0
            charge_need = abs(net_kw)

            # --- 第一道防線：電池最大化吸收 ---
            actual_charge_kw = min(charge_need, available_charge_space)
            if actual_charge_kw > 0:
                result["battery_state"] = "CHARGING"
                result["battery_command_kw"] = round(actual_charge_kw, 2)
            else:
                result["battery_state"] = "IDLING"

            remaining_excess = charge_need - actual_charge_kw

            # --- 第二道防線：洩載電阻（假負載）吸收 ---
            actual_dump_kw = min(remaining_excess, dump_load_max_kw)
            result["dump_load_kw"] = round(actual_dump_kw, 2)
            remaining_excess -= actual_dump_kw

            # --- 第三道防線：PV 主動降載 ---
            if remaining_excess > 0.05:
                curtailed_pv_kw = load_kw + actual_charge_kw + actual_dump_kw
                result["pv_pmpp"] = round(curtailed_pv_kw, 2)
                result["logs"].append(
                    f"🚨 [{time_str}] PV過剩！充 {round(actual_charge_kw,1)}kW，"
                    f"假負載燒 {round(actual_dump_kw,1)}kW，需要降載 PV"
                )
            else:
                result["pv_pmpp"] = None 
                if actual_dump_kw > 0:
                    result["logs"].append(
                        f"🔥 [{time_str}] 防線作動！充 {round(actual_charge_kw,1)}kW，"
                        f"假負載消耗 {round(actual_dump_kw,1)}kW。"
                    )

        elif net_kw < -0.05:
            # 【危機：太陽能不足，需電池放電】
            result["dump_load_kw"] = 0.0
            result["pv_pmpp"] = None

            if soc <= 20.0:
                result["battery_state"] = "IDLING"
                result["logs"].append(f"💀 [{time_str}] 電池耗盡！無法支撐負載，微電網崩潰。")
            else:
                discharge_need = abs(net_kw)
                actual_discharge_kw = min(discharge_need, battery_kwrated)
                if discharge_need > battery_kwrated:
                    result["logs"].append(
                        f"⚠️ [{time_str}] 過載！缺口 ({round(discharge_need,1)}kW) 超過極限 ({battery_kwrated}kW)"
                    )
                result["battery_state"] = "DISCHARGING"
                result["battery_command_kw"] = round(actual_discharge_kw, 2)
        else:
            result["battery_state"] = "IDLING"
            result["dump_load_kw"] = 0.0
            result["pv_pmpp"] = None

        return result

    # ==========================================
    # 模式二：併網 + AUTO（自發自用，削峰填谷）
    # ==========================================
    if operation_mode == "AUTO":
        result["dump_load_kw"] = 0.0
        result["pv_pmpp"] = None

        if net_kw > 0.05:
            if soc >= 99.9:
                result["battery_state"] = "IDLING"
            else:
                charge_kw = min(net_kw, battery_kwrated)
                result["battery_state"] = "CHARGING"
                result["battery_command_pct"] = round((charge_kw / battery_kwrated) * 100, 2)

        elif net_kw < -0.05:
            if soc <= 20.0:
                result["battery_state"] = "IDLING"
            else:
                discharge_kw = min(abs(net_kw), battery_kwrated)
                result["battery_state"] = "DISCHARGING"
                result["battery_command_pct"] = round((discharge_kw / battery_kwrated) * 100, 2)
        else:
            result["battery_state"] = "IDLING"

        return result

    # ==========================================
    # 模式三：併網 + PSO（排程模式，含 SOC 保護 + 功率上限保護）
    # ==========================================
    if operation_mode == "PSO":
        result["dump_load_kw"] = 0.0
        result["pv_pmpp"] =  None

        if current_pso_kw > 0.05:
            if soc <= 20.0:
                result["battery_state"] = "IDLING"
            else:
                useful_discharge_kw = max(-net_kw, 0.0)
                actual_discharge_kw = min(current_pso_kw, battery_kwrated, useful_discharge_kw)
                if current_pso_kw > battery_kwrated:
                    result["logs"].append(
                f"⚠️ [{time_str}] PSO排程過載！要求 {round(current_pso_kw,1)}kW 超過額定 {battery_kwrated}kW"
            )
                result["battery_state"] = "DISCHARGING"
                result["battery_command_pct"] = round((actual_discharge_kw / battery_kwrated) * 100.0, 2)

        elif current_pso_kw < -0.05:
            if soc >= 99.9:
                result["battery_state"] = "IDLING"
            else:
                actual_charge_kw = min(abs(current_pso_kw), battery_kwrated)
                if abs(current_pso_kw) > battery_kwrated:
                    result["logs"].append(
                        f"⚠️ [{time_str}] PSO排程過載！要求 {round(abs(current_pso_kw),1)}kW 超過額定 {battery_kwrated}kW"
                    )
                result["battery_state"] = "CHARGING"
                result["battery_command_pct"] = round((actual_charge_kw / battery_kwrated) * 100.0, 2)
        else:
            result["battery_state"] = "IDLING"

        return result

    # 保底：未知的 operation_mode，維持待機，避免整個 API 出錯
    result["logs"].append(f"⚠️ [{time_str}] 未知的 operation_mode='{operation_mode}'，電池維持 IDLING")
    return result