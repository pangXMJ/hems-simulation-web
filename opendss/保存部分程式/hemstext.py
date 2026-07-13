

def check_system_violations(time_str):
    """
    執行系統體檢：檢查「元件熱過載 (Thermal Overload)」與「節點電壓越限 (Voltage Violations)」
    """
    violations = []

    # ==========================================
    # 1. 檢查元件是否過載 (Thermal Overload Check)
    # ==========================================
    # 將游標移至第一個 PD Element (Power Delivery Element: 線路、變壓器等)
    idx = dss.PDElements.First() 
    
    while idx > 0:
        element_name = dss.CktElement.Name().lower() # 取得元件名稱[cite: 2]
        norm_amps = dss.CktElement.NormalAmps()          # 取得元件安培上限[cite: 2]
        
        # 只有設定了額定上限的元件才需要檢查
        if norm_amps > 0:
            # 取得該元件所有端子的電流大小與角度[cite: 2]
            currents = dss.CktElement.CurrentsMagAng() 
            # 陣列切片：只取出實部 (大小 Magnitude)[cite: 2]
            mags = currents[0::2]  
            
            if mags:
                # 🌟 [動態過載判定] 區分變壓器與一般線路的取值方式
                if "transformer" in element_name:
                    # 變壓器只看 Terminal 1 (一次側高壓端) 的第一相電流
                    max_current = mags[0]
                else:
                    # 一般線路看所有導線中的最大電流
                    max_current = max(mags)

                # 判定是否超過上限
                if max_current > norm_amps:
                    violations.append({
                        "Time": time_str,
                        "Violation_Type": "Thermal_Overload",
                        "Element": dss.CktElement.Name(), #[cite: 2]
                        "Value": round(max_current, 2),
                        "Limit": round(norm_amps, 2),
                        "Message": f"過載！負載率為 {round((max_current/norm_amps)*100, 1)}%"
                    })
        
        # 移至下一個元件[cite: 2]
        idx = dss.PDElements.Next() 

    # ==========================================
    # 2. 檢查全系統節點電壓是否異常 (Voltage Violation Check)
    # ==========================================
    node_names = dss.Circuit.AllNodeNames() # 取得節點名稱[cite: 2]
    pu_voltages = dss.Circuit.AllBusMagPu() # 取得 OpenDSS 預設 PU 值[cite: 2]
    act_voltages = dss.Circuit.AllBusVMag() # 取得實際電壓大小 (Volts)[cite: 2]

    # 確保三個陣列長度一致，進行迭代
    for node, pu_v, act_v in zip(node_names, pu_voltages, act_voltages):
        if act_v > 10.0:  # 忽略沒接電的空節點 (過濾電壓接近0的雜訊)
            
            # 🌟 [動態基準電壓分類器]
            if 90 <= act_v <= 140:
                base_V = 110.0  # 判定為單相 110V 系統
            elif 190 <= act_v <= 250:
                base_V = 220.0  # 判定為單相 220V 系統
            else:
                # 若找不到歸屬，退回 OpenDSS 原始的計算基準
                base_V = act_v / pu_v if pu_v > 0 else 1.0
            
            # 計算我們工程師自己定義的「真實標么值 (Real PU)」
            real_pu = act_v / base_V

            # 檢查是否落在 0.95 ~ 1.05 pu 之間 (ANSI C84.1 Range A)
            if real_pu < 0.95 or real_pu > 1.05:
                violations.append({
                    "Time": time_str,
                    "Violation_Type": "Voltage_Limit",
                    "Element": f"Node_{node}",
                    "Value": f"{round(real_pu, 4)} pu ({round(act_v, 1)} V)",
                    "Limit": "0.95 ~ 1.05 pu",
                    "Message": f"電壓越限！({int(base_V)}V系統異常)"
                })

    return violations
