import opendssdirect as dss
import pandas as pd
from hems_circuit import build_circuit  #呼叫opendss電路

def run_basecontrol_simulation():
    """執行未經最佳化的基準測試 (太陽能優先自用 / 淨功率邏輯)"""
    
    # 取得並建立電路
    commands = build_circuit()#呼叫build_circuit()的程式。
    for cmd in commands:#讓cmd去儲存build_circult中每一行的opendss指令
        dss.Text.Command(cmd)#將cmd中的指令傳給opendss去執行

    print("✅ 基準電路建置完成！開始進行 15 分鐘步進模擬 (basecontrol)...\n")
    dss.Text.Command("Set mode=Daily stepsize=15m number=1")

    total_steps = 96
    history_data = []#等等儲存結果的陣列
    all_violation=[]#儲存電路異常的數值


    # 配電盤定義
    panel_configs = {
        "EP": {"bus": "EP_panel", "line": "Line.ATS_to_EP"},
        "L1": {"bus": "panel1F", "line": "Line.home1F"},
        "L2": {"bus": "panel2F", "line": "Line.home2F"},
        "L3": {"bus": "panel3F", "line": "Line.home3F"}
    }

    # 電錶定義
    meter_targets = {
        "PV電錶": "MeterPV",
        "A電錶": "KwhA",
        "B電錶": "KwhB",
        "C電錶": "KwhC",
        "D電錶": "KwhD"
    }

    # ==========================================
    # 🌟 讀取大腦決策需要的資料 (PV 與 Load)
    # ==========================================
    df_pv_brain = pd.read_csv(r".\data\sample\pv_curve_15min.csv")
    pv_w_list = df_pv_brain['pv_kw'].tolist() 

    df_loads_brain = pd.read_csv(r".\data\sample\LoadShapes_All_Nodes_15min.csv")
    load_cols = [c for c in df_loads_brain.columns if c not in ['Time', 'Hour', 'Minute']]
    total_load_w_list = df_loads_brain[load_cols].sum(axis=1).tolist()

    is_island_mode = False

    for step in range(total_steps):
        # 1. 處理時間 (15分鐘解析度)
        current_minute = step * 15
        h = int(current_minute // 60)
        m = int(current_minute % 60)
        time_str = f"{h:02d}:{m:02d}"

        # 2. 抓取狀態與計算淨功率
        dss.Circuit.SetActiveElement("Storage.Battery_Sys")
        soc_str = dss.Properties.Value("%stored")
        soc = float(soc_str) if soc_str else 0.0
        
        pv_kw = pv_w_list[step] / 1000.0   
        load_kw = total_load_w_list[step] / 1000.0
        net_kw = pv_kw - load_kw  # 計算淨功率

        # 3. ATS 切換邏輯 (狀態機)
        if soc >= 99.9 and not is_island_mode:
            is_island_mode = True
            dss.Text.Command("Edit Line.ATS_to_EP enabled=no")
            dss.Text.Command("Edit Line.Inv_to_EP enabled=yes")
            print(f"[{time_str}] 🔋電池滿電！ATS切斷市電，EP盤改由「電池供電」。")

        elif soc <= 20.0 and is_island_mode:
            is_island_mode = False
            dss.Text.Command("Edit Line.ATS_to_EP enabled=yes")
            dss.Text.Command("Edit Line.Inv_to_EP enabled=no")
            print(f"[{time_str}] ⚠️電池低電量！ATS接回市電，開始充電。")  

        # ==========================================
        # ⚡ 4. 全新電池充放電行為控制 (淨功率追蹤)
        # ==========================================
        if is_island_mode:
            # 停電模式：強制放電給家裡用
            dss.Text.Command("Edit Storage.Battery_Sys state=DISCHARGING kW=3.0")
        else:
            if net_kw > 0.05:
                # 狀況 A：有餘電 (太陽能 > 家裡用電) -> 充電
                if soc >= 99.9:
                    dss.Text.Command("Edit Storage.Battery_Sys state=IDLING")
                else:
                    charge_kw = min(net_kw, 5.0)
                    charge_pct = round((charge_kw / 5.0) * 100, 2)
                    dss.Text.Command(f"Edit Storage.Battery_Sys state=CHARGING %Charge={charge_pct}")
                    
            elif net_kw < -0.05:
                # 狀況 B：太陽能不夠用 (或晚上沒太陽) -> 放電補足
                if soc <= 20.0:
                    dss.Text.Command("Edit Storage.Battery_Sys state=IDLING")
                else:
                    discharge_kw = min(abs(net_kw), 5.0)
                    dss.Text.Command(f"Edit Storage.Battery_Sys state=DISCHARGING kW={round(discharge_kw, 2)}")
                    
            else:
                # 狀況 C：剛剛好抵銷 -> 待機
                dss.Text.Command("Edit Storage.Battery_Sys state=IDLING")

        # 5. 執行這 15 分鐘的物理計算 (Solve)
        dss.Text.Command("Solve")


        #執行異常檢測
        current_violations = check_system_violations(time_str)
        if current_violations:
            all_violation.extend(current_violations)
        
        # 6. 整理數據字典
        row_dict = {"Time": time_str, "Hour": h, "Minute": m}
        row_dict["電池_SOC(%)"] = round(soc, 2)
        row_dict["供電模式"] = "電池供電" if is_island_mode else "市電供電"
  

        # ==========================================
        # 抓取各配電盤的電壓與電流
        # ==========================================

        for p_name, config in panel_configs.items():

            # [電壓]
            dss.Circuit.SetActiveBus(config["bus"])
            nodes = dss.Bus.Nodes()
            v_mag_ang = dss.Bus.VMagAngle()
            v_dict = {node: v_mag_ang[i * 2] for i, node in enumerate(nodes)}
            v1 = round(v_dict.get(1, 0), 3)
            v2 = round(v_dict.get(2, 0), 3)

            # [電流]
            line_name = config["line"]
            if p_name == "EP":
                dss.Circuit.SetActiveElement("Line.ATS_to_EP")
                if not dss.CktElement.Enabled():
                    line_name = "Line.Inv_to_EP"

            dss.Circuit.SetActiveElement(line_name)
            curr_mag_ang = dss.CktElement.CurrentsMagAng()
            i1 = round(curr_mag_ang[0], 2) if len(curr_mag_ang) > 0 else 0
            i2 = round(curr_mag_ang[2], 2) if len(curr_mag_ang) > 2 else 0

            # 寫入字典 (區分 L1 與 L2)
            row_dict[f"{p_name}_匯流排電壓_L1(V)"] = v1
            row_dict[f"{p_name}_匯流排電壓_L2(V)"] = v2
            row_dict[f"{p_name}_總電流_L1(A)"] = i1
            row_dict[f"{p_name}_總電流_L2(A)"] = i2

        # 抓取各電錶當前的累積用電量 (kWh)
        for ch_name, dss_name in meter_targets.items():
            dss.Meters.Name(dss_name)
            regs = dss.Meters.RegisterValues()
            names = dss.Meters.RegisterNames()
            reg_map = dict(zip(names, regs)) if regs else {}
            # 抓取 kWh，保留小數點後 2 位
            kwh = round(reg_map.get('kWh', 0), 5)
            row_dict[f"{ch_name}_當前累積功率(kWh)"] = kwh

        # 將這一列整合完畢的資料放入總表中
        history_data.append(row_dict)

    # 迴圈結束，轉換為 DataFrame
    df_history = pd.DataFrame(history_data)
    df_warning=pd.DataFrame(all_violation)

    print("=== 模擬完成！匯出寬表格摘要 (前 5 筆) ===")
    print(df_history.head(5).to_string(index=False))

    return df_history,df_warning



def check_system_violations(time_str):
    print("檢查線路負載情形")
    violations=[]
    idx = dss.PDElements.First()

# ==========================================
# 檢查線路與變壓器是否過載 (Thermal Overload Check)
# ==========================================
# First() 會將第一個 PD (Power Delivery) 元件設為 Active    
#OpenDSS 的核心是用 Delphi 語言撰寫的，為了讓 MATLAB、Python、C# 等各種語言都能順利呼叫它，它採用了一種叫做狀態機（State Machine）的設計。
#當呼叫 dss.PDElements.First() 時，OpenDSS 在底層會做兩件事：
#把系統指標指向第一個 PDEntity（例如 Line、Transformer），讓它成為「Active（當前啟動）」的元件。
#回傳一個整數（通常是 1）。如果系統裡完全沒有元件，它會回傳 0。
#讀取名稱是用 dss.CktElement.Name()，裡面完全不需要帶入 idx。因為 OpenDSS 已經知道「現在 Active 的是誰」，它會直接把當前元件的資料吐給你。

    while idx>0:
        elem_name=dss.CktElement.Name()#取得元件名稱
        norm_amps=dss.CktElement.NormalAmps()
        if norm_amps>0:
            currents_date=dss.CktElement.CurrentsMagAng()
            currents_mags=currents_date[0::2] if currents_date else [0]

            max_currents=max(currents_mags)
            loading_pct=(max_currents/norm_amps)*100


            if loading_pct>100:
                violations.append({
                    "time":time_str,
                    "violation_Type":"over load",
                    "Element":elem_name,
                    "value":round(max_currents,2),
                    "Limit":norm_amps,
                    "Message":f"過載!負載率為{round(loading_pct,1)}%"
                })
        idx=dss.PDElements.Next()
    node_names = dss.Circuit.AllNodeNames() 
    # 取得所有節點的標么電壓 (pu) 大小
    pu_voltages = dss.Circuit.AllBusMagPu() 
        
    for node, pu_v in zip(node_names, pu_voltages):
        if pu_v > 0.1:  # 忽略沒接電的空節點 (電壓接近0)
            if pu_v < 0.95 or pu_v > 1.05:
                violations.append({
                    "Time": time_str,
                    "Violation_Type": "Voltage_Limit",
                    "Element": f"Node_{node}",
                    "Value": round(pu_v, 4),
                    "Limit": "0.95 ~ 1.05 pu",
                    "Message": "電壓越限！(過低或過壓)"
                })
                    
    return violations



    