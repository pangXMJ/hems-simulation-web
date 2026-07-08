import opendssdirect as dss
import pandas as pd
from hems_circuit import build_circuit  # 🌟 匯入硬體電路

def run_basecontrol_simulation():
    """執行未經最佳化的基準測試 (太陽能優先自用 / 淨功率邏輯)"""
    
    # 1. 向硬體工程師取得並建立電路
    commands = build_circuit()
    for cmd in commands:
        dss.Text.Command(cmd)

    print("✅ 基準電路建置完成！開始進行 15 分鐘步進模擬 (basecontrol)...\n")
    dss.Text.Command("Set mode=Daily stepsize=15m number=1")

    total_steps = 96
    history_data = []

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

    print("=== 模擬完成！匯出寬表格摘要 (前 5 筆) ===")
    print(df_history.head(5).to_string(index=False))

    return df_history