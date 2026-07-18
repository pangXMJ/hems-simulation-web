import opendssdirect as dss
import pandas as pd

from hems_circuit import build_circuit  #呼叫opendss電路

def run_basecontrol_simulation():
    """執行未經最佳化的基準測試 (太陽能優先自用 / 淨功率邏輯)"""
    
    # 取得並建立電路
    commands = build_circuit()#呼叫build_circuit()的程式。
    for cmd in commands:#讓cmd去儲存build_circult中每一行的opendss指令
        dss.Text.Command(cmd)#將cmd中的指令傳給opendss去執行
        if dss.Error.Number() != 0:
            print(f"❌ OpenDSS 編譯錯誤: {dss.Error.Description()} \n👉 出錯指令: {cmd}")
            dss.Error.Number(0)

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
        "總電錶T":"KwhT",
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
            print(f"[{time_str}] 🔋電池滿電！ATS切斷市電，EP盤改由「電池供電」。")

        elif soc <= 20.0 and is_island_mode:
            is_island_mode = False
            dss.Text.Command("Edit Line.ATS_to_EP enabled=yes")
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

        # ==========================================
        # 🌟 新增：抓取 1F 16 個設備的真實物理狀態，並輸出給網頁前端
        # ==========================================
        # 根據 hems_circuit.py 定義的 1F 負載名稱對應表
        # ==========================================
        # 🌟 1F 到 3F 所有設備清單設定
        # ==========================================
        floor1_loads = [
            {"id": "f1_dev_1",  "name": "1F 冰箱(EP)", "dss_name": "Load.ep_fridge_an"},
            {"id": "f1_dev_2",  "name": "1F 抽水馬達(EP)", "dss_name": "Load.ep_pump"},
            {"id": "f1_dev_3",  "name": "1F 照明 1(EP)", "dss_name": "Load.ep_1f_lighting1_an"},
            {"id": "f1_dev_4",  "name": "1F 照明 2(EP)", "dss_name": "Load.ep_1f_lighting2_bn"},
            {"id": "f1_dev_5",  "name": "1F WiFi(EP)", "dss_name": "Load.ep_1f_wifi_an"},
            {"id": "f1_dev_6",  "name": "1F 電熱水器(EP)", "dss_name": "Load.ep_1f_waterheater_abn"},
            {"id": "f1_dev_7",  "name": "1F 廚房專插(EP)", "dss_name": "Load.ep_kitchen_bn"},
            {"id": "f1_dev_8",  "name": "1F 冷氣(L1)", "dss_name": "Load.l1_airc_abn"},
            {"id": "f1_dev_9",  "name": "1F 照明 1(L1)", "dss_name": "Load.l1_lighting1_an"},
            {"id": "f1_dev_10", "name": "1F 照明 2(L1)", "dss_name": "Load.l1_lighting2_bn"},
            {"id": "f1_dev_11", "name": "1F 電磁爐(L1)", "dss_name": "Load.l1_inductioncooktop_abn"},
            {"id": "f1_dev_12", "name": "1F 插座 1(L1)", "dss_name": "Load.l1_socket1_an"},
            {"id": "f1_dev_13", "name": "1F 插座 2(L1)", "dss_name": "Load.l1_socket2_bn"},
            {"id": "f1_dev_14", "name": "1F 插座 3(L1)", "dss_name": "Load.l1_socket3_an"},
            {"id": "f1_dev_15", "name": "1F 插座 4(L1)", "dss_name": "Load.l1_socket4_bn"},
            {"id": "f1_dev_16", "name": "1F 插座 5(L1)", "dss_name": "Load.l1_socket5_an"},
            {"id": "f1_dev_17", "name": "1F 插座 6(L1)", "dss_name": "Load.l1_socket6_bn"}
        ]

        floor2_loads = [
            {"id": "f2_dev_1",  "name": "2F 照明 1(EP)", "dss_name": "Load.ep_2f_lighting1_an"},
            {"id": "f2_dev_2",  "name": "2F 照明 2(EP)", "dss_name": "Load.ep_2f_lighting2_bn"},
            {"id": "f2_dev_3",  "name": "2F 除濕機(L2)", "dss_name": "Load.l2_dehumidifier_bn"},
            {"id": "f2_dev_4",  "name": "2F 衛浴 1(L2)", "dss_name": "Load.l2_bathroom1_an"},
            {"id": "f2_dev_5",  "name": "2F 衛浴 2(L2)", "dss_name": "Load.l2_bathroom2_bn"},
            {"id": "f2_dev_6",  "name": "2F 冷氣 1(L2)", "dss_name": "Load.l2_airc1_abn"},
            {"id": "f2_dev_7",  "name": "2F 冷氣 2(L2)", "dss_name": "Load.l2_airc2_abn"},
            {"id": "f2_dev_8",  "name": "2F 照明 1(L2)", "dss_name": "Load.l2_lighting1_an"},
            {"id": "f2_dev_9",  "name": "2F 照明 2(L2)", "dss_name": "Load.l2_lighting2_bn"},
            {"id": "f2_dev_10", "name": "2F 插座 1(L2)", "dss_name": "Load.l2_socket1_an"},
            {"id": "f2_dev_11", "name": "2F 插座 2(L2)", "dss_name": "Load.l2_socket2_bn"},
            {"id": "f2_dev_12", "name": "2F 插座 3(L2)", "dss_name": "Load.l2_socket3_an"},
            {"id": "f2_dev_13", "name": "2F 插座 4(L2)", "dss_name": "Load.l2_socket4_bn"},
            {"id": "f2_dev_14", "name": "2F 插座 5(L2)", "dss_name": "Load.l2_socket5_an"},
            {"id": "f2_dev_15", "name": "2F 插座 6(L2)", "dss_name": "Load.l2_socket6_bn"},
            {"id": "f2_dev_16", "name": "2F 插座 7(L2)", "dss_name": "Load.l2_socket7_an"}
        ]

        floor3_loads = [
            {"id": "f3_dev_1",  "name": "3F 洗衣機(EP)", "dss_name": "Load.ep_washer_an"},
            {"id": "f3_dev_2",  "name": "3F 烘衣機(EP)", "dss_name": "Load.ep_dryer_bn"},
            {"id": "f3_dev_3",  "name": "3F 加壓馬達(EP)", "dss_name": "Load.boosterpump_abn"},
            {"id": "f3_dev_4",  "name": "3F 照明 1(EP)", "dss_name": "Load.ep_3f_lighting1_an"},
            {"id": "f3_dev_5",  "name": "3F 照明 2(EP)", "dss_name": "Load.ep_3f_lighting2_bn"},
            {"id": "f3_dev_6",  "name": "3F 冷氣(L3)", "dss_name": "Load.l3_airc_abn"},
            {"id": "f3_dev_7",  "name": "3F 照明 1(L3)", "dss_name": "Load.l3_lighting1_an"},
            {"id": "f3_dev_8",  "name": "3F 照明 2(L3)", "dss_name": "Load.l3_lighting2_bn"},
            {"id": "f3_dev_9",  "name": "3F 插座 1(L3)", "dss_name": "Load.l3_socket1_an"},
            {"id": "f3_dev_10", "name": "3F 插座 2(L3)", "dss_name": "Load.l3_socket2_bn"},
            {"id": "f3_dev_11", "name": "3F 插座 3(L3)", "dss_name": "Load.l3_socket3_an"},
            {"id": "f3_dev_12", "name": "3F 插座 4(L3)", "dss_name": "Load.l3_socket4_bn"},
            {"id": "f3_dev_13", "name": "3F 插座 5(L3)", "dss_name": "Load.l3_socket5_an"},
            {"id": "f3_dev_14", "name": "3F 插座 6(L3)", "dss_name": "Load.l3_socket6_bn"},
            {"id": "f3_dev_15", "name": "3F 插座 7(L3)", "dss_name": "Load.l3_socket7_an"},
            {"id": "f3_dev_16", "name": "3F 插座 8(L3)", "dss_name": "Load.l3_socket8_bn"}
        ]
# 將三層樓打包，準備自動化批次處理
        all_floors_config = [
            {"floor_name": "floor1", "load_list": floor1_loads},
            {"floor_name": "floor2", "load_list": floor2_loads},
            {"floor_name": "floor3", "load_list": floor3_loads}
        ]

        # 批次處理每一層樓
        for floor in all_floors_config:
            device_data = []
            
            for dev in floor["load_list"]:
                # 將 OpenDSS 內部游標指向該設備
                dss.Circuit.SetActiveElement(dev["dss_name"])
                
                # 計算功率與電流
                total_powers = dss.CktElement.TotalPowers()
                kw = abs(total_powers[0]) if total_powers else 0.0
                watts = kw * 1000.0

                currents = dss.CktElement.CurrentsMagAng()
                amps = currents[0] if currents else 0.0
                status_code = 1 if watts > 1.0 else 0

                # 取得「額定電壓」
                rated_kv = float(dss.Properties.Value("kV"))
                rated_v = rated_kv * 1000.0

                # 計算「真實電壓」
                if status_code == 1 and amps > 0:
                    real_v = watts / amps
                else:
                    voltages = dss.CktElement.VoltagesMagAng()
                    if rated_v >= 200 and len(voltages) >= 3:
                        real_v = voltages[0] + voltages[2]
                    else:
                        real_v = voltages[0] if len(voltages) >= 1 else rated_v

                device_data.append({
                    "id": dev["id"],
                    "name": dev["name"],
                    "status_code": status_code,
                    "power_w": round(watts, 1),
                    "rated_v": int(rated_v),
                    "real_v": round(real_v, 1),
                    "current_a": round(amps, 2)
                })

            # 將該樓層的數據輸出為獨立的 CSV 檔
            df_devices = pd.DataFrame(device_data)
            output_path = rf"C:\projects\hems-simulation-web\results\data\{floor['floor_name']}_devices.csv"
            df_devices.to_csv(output_path, index=False, encoding='utf-8-sig')



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
                    line_name = "Line.Inv_AC_Main"

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





# ==========================================
# 檢查線路與變壓器是否過載 (Thermal Overload Check)
# ==========================================
# First() 會將第一個 PD (Power Delivery) 元件設為 Active    
#OpenDSS 的核心是用 Delphi 語言撰寫的，為了讓 MATLAB、Python、C# 等各種語言都能順利呼叫它，它採用了一種叫做狀態機（State Machine）的設計。
#當呼叫 dss.PDElements.First() 時，OpenDSS 在底層會做兩件事：
#把系統指標指向第一個 PDEntity（例如 Line、Transformer），讓它成為「Active（當前啟動）」的元件。
#回傳一個整數（通常是 1）。如果系統裡完全沒有元件，它會回傳 0。
#讀取名稱是用 dss.CktElement.Name()，裡面完全不需要帶入 idx。因為 OpenDSS 已經知道「現在 Active 的是誰」，它會直接把當前元件的資料吐給你。


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
    