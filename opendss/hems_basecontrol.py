import opendssdirect as dss
import pandas as pd
import os

from hems_circuit import build_circuit  # 呼叫opendss電路
from battery_control_logic import decide_battery_action
# ==========================================
# 🆕 資料夾路徑（依實際專案規劃：C:\projects\hems-simulation-web\data 底下）
#   只有 PROJECT_ROOT 這一行需要依每個人電腦上的實際路徑調整，
#   RAW_DATA_DIR / PSO_OUTPUT_DIR / OUTPUT_DIR 都從它推導出來，跟 server.py 用同一套規則：
#   RAW_DATA_DIR   -> data/01_raw，負載/PV 原始資料（會被設備控制頁面改寫的那份）
#   PSO_OUTPUT_DIR -> data/04_optimized_pso，PSO 算出來的電池排程
#   OUTPUT_DIR     -> data/sample，只放這支程式自己輸出的計算結果
# ==========================================

PROJECT_ROOT = r"C:\projects\hems-simulation-web"
RAW_DATA_DIR = os.path.join(PROJECT_ROOT, "data", "01_raw")
PSO_OUTPUT_DIR = os.path.join(PROJECT_ROOT, "data", "04_optimized_pso")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "data", "sample")



def run_simulation(mode='baseline', outage_start_step=-1, outage_end_step=-1,battery_schedule_filename='battery_usage_two_stage_summer_weekday_15min.csv'):
    """
    執行全日模擬引擎
    :param mode: 'baseline' (未經最佳化), 'pso' (排程最佳化), 'island' (動態突發停電)
    :param outage_start_step: 停電開始步數 (0-95)
    :param outage_end_step: 停電結束步數 (0-95)
    """
    # 決定輸出檔案的前綴名稱
    prefix = mode.capitalize() 

    # 取得並建立電路
    commands = build_circuit()
    for cmd in commands:
        dss.Text.Command(cmd)
        if dss.Error.Number() != 0:
            print(f"❌ OpenDSS 編譯錯誤: {dss.Error.Description()} \n👉 出錯指令: {cmd}")
            dss.Error.Number(0)

    print(f"✅ 基準電路建置完成！開始進行 15 分鐘步進模擬 (模式: {mode.upper()})...\n")
    dss.Text.Command("Set mode=Daily stepsize=15m number=1")

    total_steps = 96
    
    # ==========================================
    # 🌟 歷史資料累積陣列 (儲存 96 步的所有資料)
    # ==========================================
    history_data = []
    all_violation = []
    meters_history_data = []
    bess_history_data = []
    
    # 樓層設備資料累積字典
    floor_history = {"floor1": [], "floor2": [], "floor3": []}#這是給01~03顯示用的

    # 配電盤與電錶定義
    panel_configs = {
        "EP": {"bus": "EP_panel", "line": "Line.ATS_to_EP"},
        "L1": {"bus": "panel1F", "line": "Line.home1F"},
        "L2": {"bus": "panel2F", "line": "Line.home2F"},
        "L3": {"bus": "panel3F", "line": "Line.home3F"}#這是給Panel_Common_Nodes_15m_History.csv產生總覽表用的
    }

    meter_targets = {
        "總電錶T":"KwhT", "PV電錶": "MeterPV", "A電錶": "KwhA",
        "B電錶": "KwhB", "C電錶": "KwhC", "D電錶": "KwhD"
    }#這是給Panel_Common_Nodes_15m_History.csv產生總覽表用的

    # 讀取 PV 與 Load 為了後續作判斷用的
    df_pv_brain = pd.read_csv(os.path.join(RAW_DATA_DIR, "pv_curve_15min.csv"))# 從data\01_raw\pv_curve_15min.csv讀取時間跟pv發電量
    pv_w_list = df_pv_brain['pv_kw'].tolist() #儲存 時間跟pv發電量


   # 從data\01_raw\LoadShapes_All_Nodes_15min.csv 讀取所有設備的 消耗功率（這份是設備控制頁面會改寫的那份）
    df_loads_brain = pd.read_csv(os.path.join(RAW_DATA_DIR, "LoadShapes_All_Nodes_15min.csv"))
    
    #過濾欄位：排除 Time（時間）、Hour（小時）、Minute（分鐘）等時間標籤欄位，只留下純設備名稱的欄位。
    load_cols = [c for c in df_loads_brain.columns if c not in ['Time', 'Hour', 'Minute']]
  
    #將所有設備在相同時間點的消耗功率相加（sum(axis=1)），並轉換成 Python 列表（total_load_w_list），代表整個系統在各個時間點的總負載瓦數
    total_load_w_list = df_loads_brain[load_cols].sum(axis=1).tolist()

    #  ===== 新增這兩行：將 EP (緊急負載) 獨立計算出來 ===== 
    #ep_cols = [c for c in load_cols if 'ep_' in c.lower()]
    ep_cols = [c for c in load_cols if c.lower().startswith('ep_')]
    ep_load_w_list = df_loads_brain[ep_cols].sum(axis=1).tolist()
    #  ==================================================
    try:
        import config
        print(f"🔎 [清單比對] ep_cols (真實電路): {sorted(ep_cols)}")
        print(f"🔎 [清單比對] CRITICAL_LOAD_COLUMNS (PSO): {sorted(config.CRITICAL_LOAD_COLUMNS)}")
        print(f"🔎 [清單比對] 兩者是否相同: {set(ep_cols) == set(config.CRITICAL_LOAD_COLUMNS)}")
        print(f"🔎 [清單比對] 只在真實電路有、PSO沒有: {set(ep_cols) - set(config.CRITICAL_LOAD_COLUMNS)}")
        print(f"🔎 [清單比對] 只在PSO有、真實電路沒有: {set(config.CRITICAL_LOAD_COLUMNS) - set(ep_cols)}")
    except ImportError:
        print("⚠️ [清單比對] 找不到 config 模組，可能沒有透過 scenario_switch.py 這條路徑執行")

    # 如果是 pso 或 island 模式，才讀取 PSO 電池排程，系統會檢查變數 mode。只有當模式為 'pso'（粒子群最佳化演算法模式）或 'island'（孤島/斷網模式）時，才會執行電池排程的讀取。
    #初始化排程：預先建立一個長度為 total_steps、數值全為 0.0 的列表
    pso_kw_list = [0.0] * total_steps

    #檢查變數 mode
    if mode in ['pso', 'island']:
        
        try:
            df_pso = pd.read_csv(os.path.join(PSO_OUTPUT_DIR, battery_schedule_filename))#🆕 從 04_optimized_pso 閱讀pso的排程資料
            #讀取 pso_battery_power.csv 檔案，並將其中的 Power_kW（電池功率千瓦值）欄位轉成列表，覆蓋掉原本的預設值
            pso_kw_list = df_pso['battery_power_kw'].tolist()
            print(f"✅ 成功載入 PSO 電池排程！({battery_schedule_filename})")
        except FileNotFoundError:
            print("⚠️ 找不到 PSO 檔案，退回全天待機 (0 kW)。")

    if len(pv_w_list) < total_steps:
        pv_w_list.extend([0.0] * (total_steps - len(pv_w_list)))
    if len(total_load_w_list) < total_steps:
        total_load_w_list.extend([0.0] * (total_steps - len(total_load_w_list)))
    if len(pso_kw_list) < total_steps:
        pso_kw_list.extend([0.0] * (total_steps - len(pso_kw_list)))        

    # 初始狀態：市電供電
    #在 Python 程式中定義一個布林值變數，將「孤島模式（Island Mode）」設為關閉（False）。
    is_island_mode = False
    #透過 Python API 向 OpenDSS 模擬引擎發送一段文字指令 修改名為 ATS_to_EP 的線路（Line）物件屬性，將連接 meterA到EP panel的 線路社為導通(將來ATS斷掉的時候不知道meterA會不會記錄到功率數值可以留意一下)
    dss.Text.Command("Edit Line.ATS_to_EP enabled=yes")


    DUMP_LOAD_MAX_KW = 2.0
    total_losses_kwh = 0.0

#注意是不是要有跨日模擬（時間重回 00:00）
    for step in range(total_steps):
    
        
       # 解決問題 4：時間跨日歸零處理 (% 24)
        current_minute = step * 15
        h = int((current_minute // 60) % 24) 
        m = int(current_minute % 60)
        time_str = f"{h:02d}:{m:02d}"



        # ==========================================
        # 2. 突發停電時間炸彈邏輯 (ATS 控制) 要注意meterA是否記錄得到功率
        # ==========================================
        #利用 is_island_mode 變數記錄當前狀態，只在狀態切換的瞬間執行一次 OpenDSS 指令，避免重複發送指令
        #is_target_outage 是一個布林值變數（Boolean Variable），在程式中代表「當前時間步長是否處於預設的停電事件區間內」。
        #必須同時滿足兩個條件，才會判定為停電狀態：模擬模式 mode 必須是 'island'（孤島模式）。當前的步長 step 剛好落在設定的停電區間內（從 outage_start_step 開始，到 outage_end_step 結束）。


        #時間進入停電區間（is_target_outage 為真）
        #將 is_island_mode 改為 True，並下達 OpenDSS 指令 enabled=no 切斷 ATS_to_EP 這條市電聯絡線路
        is_target_outage = (
            outage_start_step >= 0
            and outage_end_step >= 0
            and outage_start_step <= step < outage_end_step
        )


        if is_target_outage and not is_island_mode:
            is_island_mode = True
            # 1. 真實物理切斷市電
            dss.Text.Command("Edit Line.ATS_to_EP enabled=no")
            
            # 2.修正：將 New 改為 Edit 打開電壓源，並實體切斷非緊急負載！
            dss.Text.Command("Edit Vsource.BESS_GFM_L1 enabled=yes")
            dss.Text.Command("Edit Vsource.BESS_GFM_L2 enabled=yes")
            dss.Text.Command("Edit Line.home1F enabled=no")
            dss.Text.Command("Edit Line.home2F enabled=no")
            dss.Text.Command("Edit Line.home3F enabled=no")
            print(f"⚠️ [{time_str}] 突發停電！ATS 切斷，啟動單相三線 Inverter，並切斷 L1~L3 非緊急負載。")
            
        elif not is_target_outage and is_island_mode:
            is_island_mode = False
            # 恢復市電，關閉孤島變流器，接回一般負載
            dss.Text.Command("Edit Line.ATS_to_EP enabled=yes")
            dss.Text.Command("Edit Vsource.BESS_GFM_L1 enabled=no")
            dss.Text.Command("Edit Vsource.BESS_GFM_L2 enabled=no")
            dss.Text.Command("Edit Line.home1F enabled=yes")
            dss.Text.Command("Edit Line.home2F enabled=yes")
            dss.Text.Command("Edit Line.home3F enabled=yes")
            
            dss.Text.Command("Edit Load.DumpLoad kW=0.0")
            print(f"🔌 [{time_str}] 市電恢復！結束孤島模式，重新併入大電網。")
        if is_island_mode:
            dss.Circuit.SetActiveElement("Storage.Battery_Sys")
            soc_str_for_island_check = dss.Properties.Value("%stored")
            soc_for_island_check = float(soc_str_for_island_check.replace('%', '').strip()) if soc_str_for_island_check else 0.0

            if soc_for_island_check <= 1.0:
                dss.Text.Command("Edit Vsource.BESS_GFM_L1 enabled=no")
                dss.Text.Command("Edit Vsource.BESS_GFM_L2 enabled=no")
            else:
                dss.Text.Command("Edit Vsource.BESS_GFM_L1 enabled=yes")
                dss.Text.Command("Edit Vsource.BESS_GFM_L2 enabled=yes")    

        #  初始化電池狀態變數，抓取狀態與計算淨功率
        soc = 0.0
        bess_kw = 0.0
        bess_amp = 0.0

        if dss.Circuit.SetActiveElement("Storage.Battery_Sys") != 0:
            var_names = dss.CktElement.AllVariableNames()
            var_values = dss.CktElement.AllVariableValues()
            
            # 🚨 修正 1：恢復您原本最穩健的 SOC 讀取邏輯
            if "%stored" in var_names:
                soc = float(var_values[var_names.index("%stored")])
            else:
                soc_str = dss.Properties.Value("%stored")
                if soc_str:
                    soc = float(soc_str.replace('%', '').strip())
            
            total_powers = dss.CktElement.TotalPowers()
            if total_powers: 
                bess_kw = -total_powers[0] 
            
            currents_mag = dss.CktElement.CurrentsMagAng()
            if currents_mag: bess_amp = round(currents_mag[0], 2)


        # 🚨 關鍵修復：停電時，大腦只看 EP 負載，避免誤判過載！
        pv_kw = pv_w_list[step] / 1000.0   
        if is_island_mode:
            load_kw = ep_load_w_list[step] / 1000.0
        else:
            load_kw = total_load_w_list[step] / 1000.0
            
        net_kw = pv_kw - load_kw
        if is_island_mode:
            print(f"🔎 [停電PV比對] {time_str} 真實電路pv_kw={round(pv_kw,3)} load_kw(EP)={round(load_kw,3)} net_kw={round(net_kw,3)}")

        # ==========================================
        # 核心電池充放電控制 (依據 Mode 切換)
        # 根據當前的電網狀態（正常或停電）以及設定的運作模式（基線模式或演算法模式），動態決定儲能電池（BESS）的充放電行為，並在最後驅動 OpenDSS 進行電力潮流物理計算
        # ==========================================
        #電池的額定功率（BATTERY_KWRATED）在此被設定為 5.0 kW，要跟hems buildcircuit的地方一樣，不然模擬結果會不一樣
        BATTERY_KWRATED = 5.0


         #把 mode 換算成共用模組看得懂的 operation_mode（AUTO / PSO）
         #is_island_mode 已經在前面算好了，直接傳進去即可
        if mode == 'baseline':
            operation_mode = "AUTO"
        else:  # mode in ['pso', 'island']
            operation_mode = "PSO"

        current_pso_kw = pso_kw_list[step] if mode in ['pso', 'island'] else 0.0

        dss.Text.Command("Edit Storage.Battery_Sys DispMode=External")    

        # ==========================================
        # 呼叫共用電池決策模組（跟 server.py 共用同一份邏輯）
        # ==========================================
        action = decide_battery_action(
            is_island_mode=is_island_mode,
            operation_mode=operation_mode,
            net_kw=net_kw,
            soc=soc,
            current_pso_kw=current_pso_kw,
            load_kw=load_kw,
            battery_kwrated=BATTERY_KWRATED,
            dump_load_max_kw=DUMP_LOAD_MAX_KW,
            time_str=time_str,
        )
        if is_island_mode:
            print(f"🔎 [決策輸出] {time_str} battery_state={action['battery_state']} battery_command_kw={action['battery_command_kw']}")

        for log_line in action["logs"]:
            print(f"   {log_line}")

        # 把決策結果下達給 OpenDSS
        if action["battery_command_kw"] is not None:
            dss.Text.Command(
                f"Edit Storage.Battery_Sys state={action['battery_state']} kW={action['battery_command_kw']}"
            )
        elif action["battery_command_pct"] is not None:
            pct_keyword = "%Charge" if action["battery_state"] == "CHARGING" else "%Discharge"
            dss.Text.Command(
                f"Edit Storage.Battery_Sys state={action['battery_state']} {pct_keyword}={action['battery_command_pct']}"
            )
        else:
            dss.Text.Command(f"Edit Storage.Battery_Sys state={action['battery_state']}")

        dss.Text.Command(f"Edit Load.DumpLoad kW={action['dump_load_kw']}")
        if action["pv_pmpp"] is not None:
            dss.Text.Command(f"Edit PVSystem.pv_array pmpp={action['pv_pmpp']}")

        dss.Text.Command("Solve")


        if dss.Circuit.SetActiveElement("Storage.Battery_Sys") != 0: #[cite: 7]
            expected_pso = pso_kw_list[step]
            actual_powers = dss.CktElement.Powers() #[cite: 7]
            actual_kw = actual_powers[0] if actual_powers else 0.0
            
            internal_vars = dict(zip(dss.CktElement.AllVariableNames(), dss.CktElement.AllVariableValues())) #[cite: 7]
            internal_state = internal_vars.get('State', 0)
            
            # 💡 修正 2：釐清當下是誰在控制電池

            if is_island_mode:
                print(f"[{time_str}] 控制權: 孤島緊急控制器 (無視 PSO 預設排程)")
            elif mode == 'baseline':
                print(f"[{time_str}] 控制權: Baseline 削峰填谷 (無 PSO 排程，依 net_kw 判斷)")
            else:  # mode in ['pso', 'island']
                print(f"[{time_str}] 控制權: PSO 最佳化排程 (指令: {expected_pso} kW)")

            #    print(f"   物理端輸出: {round(actual_kw, 3)} kW (State: {internal_state})")
        # ==========================================
        # 🩺 系統健康度稽核 (物理驗證)
        # ==========================================
        # 1. 取得市電輸入總功率 (kW)
        sys_power = dss.Circuit.TotalPower() #[cite: 7]
        total_in_kw = -sys_power[0] if sys_power else 0.0 

        # 2. 取得全系統線損 (kW)
        sys_losses = dss.Circuit.Losses() #[cite: 7]
        loss_kw = sys_losses[0] / 1000.0 if sys_losses else 0.0
        total_losses_kwh += loss_kw * 0.25

        # 3. 取得真實太陽能發電功率 (kW)
        if dss.Circuit.SetActiveElement("PVSystem.PV_Array") != 0: #[cite: 7]
            pv_powers = dss.CktElement.Powers() #[cite: 7]
            actual_pv_kw = abs(pv_powers[0]) if pv_powers else 0.0
        else:
            actual_pv_kw = 0.0

        # 4. 利用能量守恆定律反推真實負載：P_load = P_grid + P_pv + P_battery - P_loss
        actual_load_kw = total_in_kw + actual_pv_kw + actual_kw - loss_kw

        # 5. 比對「CSV理論預期負載」與「物理真實負載」的偏差
        expected_load_kw = (ep_load_w_list[step] / 1000.0) if is_island_mode else (total_load_w_list[step] / 1000.0)
        expected_load_kw += action["dump_load_kw"] if is_island_mode else 0.0
        
        load_deviation = abs(actual_load_kw - expected_load_kw)

        if load_deviation > 0.5: 
            print(f" {time_str}   ⚠️ [負載壓降偏移] 理論應耗: {round(expected_load_kw,2)}kW | 物理實耗: {round(actual_load_kw,2)}kW | 偏差: {round(load_deviation,2)}kW")
        

        if dss.Circuit.SetActiveElement("Storage.Battery_Sys") != 0:
            
            # 1. 抓取我們「期望」的指令
            expected_pso = pso_kw_list[step]
            
            # 2. 抓取 OpenDSS 「最終決定」的終端實功率 (P) 與 虛功率 (Q)
            actual_powers = dss.CktElement.Powers()
            actual_kw = actual_powers[0] if actual_powers else 0.0
            
            # 3. 抓取 OpenDSS 內部隱藏的所有變數狀態
            var_names = dss.CktElement.AllVariableNames()
            var_values = dss.CktElement.AllVariableValues()
            
            # 將兩個陣列打包成字典，方便尋找
            internal_vars = dict(zip(var_names, var_values))
            
            # 提取關鍵的內部判斷依據
            internal_state = internal_vars.get('State', 0)
            internal_soc = internal_vars.get('%stored', 0)
            internal_losses = internal_vars.get('Losses', 0)
            
            #print(f"🕒 [{time_str}] PSO指令: {expected_pso} kW")
            #print(f"   👉 物理端輸出: {round(actual_kw, 3)} kW (差距: {round(expected_pso - actual_kw, 3)} kW)")
            #print(f"   👉 內部狀態碼 (State): {internal_state} (1=放電, -1=充電, 0=待機)")
            #print(f"   👉 當前深層 SOC: {round(internal_soc, 2)} %")
            #print(f"   👉 內部熱損耗: {round(internal_losses, 3)} kW")
            #print("-" * 40)



        # 再次更新狀態 (抓取 Solve 後的最終結果)
        if dss.Circuit.SetActiveElement("Storage.Battery_Sys") != 0:
            var_names = dss.CktElement.AllVariableNames()
            var_values = dss.CktElement.AllVariableValues()
            
            if "%stored" in var_names:
                soc = float(var_values[var_names.index("%stored")])
            else:
                soc_str = dss.Properties.Value("%stored")
                if soc_str:
                    soc = float(soc_str.replace('%', '').strip())

            total_powers = dss.CktElement.TotalPowers()
            if total_powers: 
                bess_kw = -total_powers[0] 
            currents_mag = dss.CktElement.CurrentsMagAng()
            if currents_mag: bess_amp = round(currents_mag[0], 2)

        if is_island_mode:
            print(f"🔎 [最終記錄] {time_str} bess_kw={round(bess_kw,3)}")

        bess_history_data.append({
            "Time": time_str,
            "soc": round(soc, 2),
            "battery_power_kw": round(bess_kw, 2),
            "current_a": round(bess_amp, 2)
        })

        # ==========================================
        # 5. 抓取 01~03 樓層設備資料 (並加入 Time)
        #每一個用電設備都打包成一個 Python 字典（Dictionary）
        #包含三個核心欄位：id：設備的唯一識別碼（如 f1_dev_1方便資料庫或前端網頁管理
        #name：設備中文名稱（如 1F 冰箱(EP)）
        # dss_name：該設備在 OpenDSS 模擬軟體中的精確物件名稱（如 Load.ep_fridge_an，用來下達控制或讀取指令。
        # ==========================================
        floor1_loads = [
            {"id": "f1_dev_1", "name": "1F 冰箱(EP)", "dss_name": "Load.ep_fridge_an"},
            {"id": "f1_dev_2", "name": "1F 抽水馬達(EP)", "dss_name": "Load.ep_pump"},
            {"id": "f1_dev_3", "name": "1F 照明 1(EP)", "dss_name": "Load.ep_1f_lighting1_an"},
            {"id": "f1_dev_4", "name": "1F 照明 2(EP)", "dss_name": "Load.ep_1f_lighting2_bn"},
            {"id": "f1_dev_5", "name": "1F WiFi(EP)", "dss_name": "Load.ep_1f_wifi_an"},
            {"id": "f1_dev_6", "name": "1F 電熱水器(EP)", "dss_name": "Load.ep_1f_waterheater_abn"},
            {"id": "f1_dev_7", "name": "1F 廚房專插(EP)", "dss_name": "Load.ep_kitchen_bn"},
            {"id": "f1_dev_8", "name": "1F 冷氣(L1)", "dss_name": "Load.l1_airc_abn"},
            {"id": "f1_dev_9", "name": "1F 照明 1(L1)", "dss_name": "Load.l1_lighting1_an"},
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
            {"id": "f2_dev_1", "name": "2F 照明 1(EP)", "dss_name": "Load.ep_2f_lighting1_an"},
            {"id": "f2_dev_2", "name": "2F 照明 2(EP)", "dss_name": "Load.ep_2f_lighting2_bn"},
            {"id": "f2_dev_3", "name": "2F 除濕機(L2)", "dss_name": "Load.l2_dehumidifier_bn"},
            {"id": "f2_dev_4", "name": "2F 衛浴 1(L2)", "dss_name": "Load.l2_bathroom1_an"},
            {"id": "f2_dev_5", "name": "2F 衛浴 2(L2)", "dss_name": "Load.l2_bathroom2_bn"},
            {"id": "f2_dev_6", "name": "2F 冷氣 1(L2)", "dss_name": "Load.l2_airc1_abn"},
            {"id": "f2_dev_7", "name": "2F 冷氣 2(L2)", "dss_name": "Load.l2_airc2_abn"},
            {"id": "f2_dev_8", "name": "2F 照明 1(L2)", "dss_name": "Load.l2_lighting1_an"},
            {"id": "f2_dev_9", "name": "2F 照明 2(L2)", "dss_name": "Load.l2_lighting2_bn"},
            {"id": "f2_dev_10", "name": "2F 插座 1(L2)", "dss_name": "Load.l2_socket1_an"},
            {"id": "f2_dev_11", "name": "2F 插座 2(L2)", "dss_name": "Load.l2_socket2_bn"},
            {"id": "f2_dev_12", "name": "2F 插座 3(L2)", "dss_name": "Load.l2_socket3_an"},
            {"id": "f2_dev_13", "name": "2F 插座 4(L2)", "dss_name": "Load.l2_socket4_bn"},
            {"id": "f2_dev_14", "name": "2F 插座 5(L2)", "dss_name": "Load.l2_socket5_an"},
            {"id": "f2_dev_15", "name": "2F 插座 6(L2)", "dss_name": "Load.l2_socket6_bn"},
            {"id": "f2_dev_16", "name": "2F 插座 7(L2)", "dss_name": "Load.l2_socket7_an"},
            {"id": "f2_dev_17", "name": "2F 插座 1(EP)", "dss_name": "Load.EP_2F_socket1_an"},
            {"id": "f2_dev_18", "name": "2F 插座 2(EP)", "dss_name": "Load.EP_2F_socket2_bn"}

        ]
        floor3_loads = [
            {"id": "f3_dev_1", "name": "3F 洗衣機(EP)", "dss_name": "Load.ep_washer_an"},
            {"id": "f3_dev_2", "name": "3F 烘衣機(EP)", "dss_name": "Load.ep_dryer_bn"},
            {"id": "f3_dev_3", "name": "3F 加壓馬達(EP)", "dss_name": "Load.ep_boosterpump_abn"},
            {"id": "f3_dev_4", "name": "3F 照明 1(EP)", "dss_name": "Load.ep_3f_lighting1_an"},
            {"id": "f3_dev_5", "name": "3F 照明 2(EP)", "dss_name": "Load.ep_3f_lighting2_bn"},
            {"id": "f3_dev_6", "name": "3F 冷氣(L3)", "dss_name": "Load.l3_airc_abn"},
            {"id": "f3_dev_7", "name": "3F 照明 1(L3)", "dss_name": "Load.l3_lighting1_an"},
            {"id": "f3_dev_8", "name": "3F 照明 2(L3)", "dss_name": "Load.l3_lighting2_bn"},
            {"id": "f3_dev_9", "name": "3F 插座 1(L3)", "dss_name": "Load.l3_socket1_an"},
            {"id": "f3_dev_10", "name": "3F 插座 2(L3)", "dss_name": "Load.l3_socket2_bn"},
            {"id": "f3_dev_11", "name": "3F 插座 3(L3)", "dss_name": "Load.l3_socket3_an"},
            {"id": "f3_dev_12", "name": "3F 插座 4(L3)", "dss_name": "Load.l3_socket4_bn"},
            {"id": "f3_dev_13", "name": "3F 插座 5(L3)", "dss_name": "Load.l3_socket5_an"},
            {"id": "f3_dev_14", "name": "3F 插座 6(L3)", "dss_name": "Load.l3_socket6_bn"},
            {"id": "f3_dev_15", "name": "3F 插座 7(L3)", "dss_name": "Load.l3_socket7_an"},
            {"id": "f3_dev_16", "name": "3F 插座 8(L3)", "dss_name": "Load.l3_socket8_bn"}
        ]
        #將 floor1_loads、floor2_loads、floor3_loads 三個列表，統一打包進一個名為 all_floors_config 的大列表中。
        all_floors_config = [
            {"floor_name": "floor1", "load_list": floor1_loads},
            {"floor_name": "floor2", "load_list": floor2_loads},
            {"floor_name": "floor3", "load_list": floor3_loads}
        ]

        #雙重嵌入式迴圈（Nested Loop，利用前面定義好的樓層設備對照表，逐一向 OpenDSS 引擎撈取大樓內每個電器在當前時間點（Step）的真實電力數據（包含功率、電流、電壓、運作狀態）
        #外部迴圈：遍歷每一個樓層（floor1 ➔ floor2 ➔ floor3）。內部迴圈：遍歷該樓層裡面的每一個設備。
        #計算消耗瓦數 (watts)：讀取該元件的總功率（TotalPowers()），取第一項（kW 實功）的絕對值，並乘以 1000 轉回瓦特 (W)。
        #讀取電流 (amps)：讀取電流大小（CurrentsMagAng()，取第一項作為該設備的線電流（安培 A）。
        #判定開關狀態 (status_code)：如果計算出的瓦數大於 1.0 W，判定該電器正在開機運作中（1）；小於等於 1.0 W 則判定為關機或待機（0）
        #讀取額定電壓 (rated_v)：從 OpenDSS 讀取該負載設定的額定電壓（kV），並乘以 1000 轉換為伏特 (V)（例如 0.11 kV ➔ 110V、0.22 kV ➔ 220V）。

        for floor in all_floors_config:
            for dev in floor["load_list"]:
                dss.Circuit.SetActiveElement(dev["dss_name"])
                total_powers = dss.CktElement.TotalPowers()
                kw = abs(total_powers[0]) if total_powers else 0.0
                watts = kw * 1000.0
                currents = dss.CktElement.CurrentsMagAng()
                amps = currents[0] if currents else 0.0
                status_code = 1 if watts > 1.0 else 0
                rated_kv = float(dss.Properties.Value("kV"))
                rated_v = rated_kv * 1000.0
                #動態電壓計算邏輯 (real_v)
                if status_code == 1 and amps > 0:#當設備有運作且有電流時
                    real_v = watts / amps #功率/電流 反推電壓
                else:# 當設備關機或沒電流時：透過讀取節點電壓陣列（VoltagesMagAng()）來獲取
                    voltages = dss.CktElement.VoltagesMagAng()
                    if rated_v >= 200 and len(voltages) >= 3:
                        real_v = voltages[0] + voltages[2]#將第一相和第二相的電壓大小相加（voltages[0] + voltages[2]），還原出線電壓
                    else:
                        real_v = voltages[0] if len(voltages) >= 1 else rated_v#備援機制：若都撈不到電票數據，則退回使用預設的額定電壓（rated_v）。

                # 將帶有時間的資料，累積放入該樓層的歷史陣列中
                #程式將處理好、帶有當前時間標記（time_str）的精準數據，以字典形式 .append() 到 floor_history 之中對應的樓層鍵值（Key）下方。
                floor_history[floor["floor_name"]].append({
                    "Time": time_str,
                    "id": dev["id"],
                    "name": dev["name"],
                    "status_code": status_code,
                    "power_w": round(watts, 1),
                    "rated_v": int(rated_v),
                    "real_v": round(real_v, 1),
                    "current_a": round(amps, 2)
                })



        #----------------------------------------------------------------------------------------
        #這段程式碼的主要功能是在每個模擬時間點，
        #進行電力系統的異常檢測（違規判定）並動態撈取各大樓配電盤（Panel）匯流排的實際電壓與線路總電流
        #最後將這些關鍵資訊整合，生成一筆全系統的歷史摘要紀錄（Summary Row）
        #-----------------------------------------------------------------------------------------

        # 異常檢測，呼叫一個外部函式 check_system_violations，傳入目前時間，如果檢測到異常（current_violations 不為空，就把這些異常事件全部追加到總紀錄列表 all_violation 中
        current_violations = check_system_violations(time_str)
        if current_violations:
            all_violation.extend(current_violations)
        
        # 紀錄歷史摘要表
        row_dict = {"Time": time_str, "Hour": h, "Minute": m}
        row_dict["電池_SOC(%)"] = round(soc, 2)
        row_dict["供電模式"] = "電池供電" if is_island_mode else "市電供電"
        row_dict["線路損失(kW)"] = round(loss_kw, 4)
  
        # 抓取電壓電流
        for p_name, config in panel_configs.items():
            dss.Circuit.SetActiveBus(config["bus"])
            nodes = dss.Bus.Nodes()
            v_mag_ang = dss.Bus.VMagAngle()
            v_dict = {node: v_mag_ang[i * 2] for i, node in enumerate(nodes)}
            row_dict[f"{p_name}_匯流排電壓_L1(V)"] = round(v_dict.get(1, 0), 3)
            row_dict[f"{p_name}_匯流排電壓_L2(V)"] = round(v_dict.get(2, 0), 3)

            #一旦停電，線路斷開，電流就會變成 0；在此時主動切換去讀取 Inv_AC_Main，抓到微電網在孤島模式下，電池到底送了多少電流給 EP 盤的設備
            line_name = config["line"]
            if p_name == "EP":
                dss.Circuit.SetActiveElement("Line.ATS_to_EP")
                if not dss.CktElement.Enabled(): line_name = "Line.Inv_AC_Main"
            dss.Circuit.SetActiveElement(line_name)
            curr_mag_ang = dss.CktElement.CurrentsMagAng()
            row_dict[f"{p_name}_總電流_L1(A)"] = round(curr_mag_ang[0], 2) if len(curr_mag_ang) > 0 else 0
            row_dict[f"{p_name}_總電流_L2(A)"] = round(curr_mag_ang[2], 2) if len(curr_mag_ang) > 2 else 0


        #利用 OpenDSS 的電錶物件（EnergyMeter，撈取各監測點的當前累積用電量（kWh）
        #將這些數據同步更新到整體的歷史摘要表（history_data）與獨立的電錶歷史紀錄（meters_history_data中
        #抓取電錶
        meter_row = {"Time": time_str}
        for ch_name, dss_name in meter_targets.items():
            #if ch_name == "PV電錶":
                #print(f"🔎 [PV電表除錯] 所有暫存器: {dict(zip(names, regs))}")
            dss.Meters.Name(dss_name)
            regs = dss.Meters.RegisterValues()
            names = dss.Meters.RegisterNames()
            reg_map = dict(zip(names, regs)) if regs else {}
            kwh = round(reg_map.get('kWh', 0), 5)
            row_dict[f"{ch_name}_當前累積功率(kWh)"] = kwh
            meter_row[f"{ch_name}_當前累積功率(kWh)"] = kwh

        meters_history_data.append(meter_row)
        history_data.append(row_dict)

    # ==========================================
    # 6. 一次性輸出全日所有資料表 (加入前綴)
    # ==========================================
    base_path = OUTPUT_DIR
    os.makedirs(base_path, exist_ok=True)

    print(f"📊 [{prefix}] 全天累積線損: {round(total_losses_kwh, 3)} kWh")
    
    # 輸出電池狀態與電錶總覽
    pd.DataFrame(bess_history_data).to_csv(os.path.join(base_path, f"{prefix}_bess_status.csv"), index=False, encoding='utf-8-sig')
    pd.DataFrame(meters_history_data).to_csv(os.path.join(base_path, f"{prefix}_All_meter.csv"), index=False, encoding='utf-8-sig')

    # 輸出 01~03 樓層設備資料 (96步完整版)
    for f_name, f_data in floor_history.items():
        pd.DataFrame(f_data).to_csv(os.path.join(base_path, f"{prefix}_{f_name}_devices.csv"), index=False, encoding='utf-8-sig')

    df_history = pd.DataFrame(history_data)
    df_warning = pd.DataFrame(all_violation)

    print(f"=== {prefix} 模式模擬完成！匯出寬表格摘要 ===")
    print("PVSystems 列表:", dss.PVsystems.AllNames())
    print("Generators 列表:", dss.Generators.AllNames())

    return df_history, df_warning

# (下方 check_system_violations 函式維持原樣，無需變更)
def check_system_violations(time_str):
    # ... [與您原本提供的程式碼完全相同，無需變更] ...
    violations = []
    idx = dss.PDElements.First() 
    while idx > 0:
        element_name = dss.CktElement.Name().lower()
        norm_amps = dss.CktElement.NormalAmps() 
        if norm_amps > 0:
            currents = dss.CktElement.CurrentsMagAng() 
            mags = currents[0::2]  
            if mags:
                if "transformer" in element_name: max_current = mags[0]
                else: max_current = max(mags)

                if max_current > norm_amps:
                    violations.append({
                        "Time": time_str,
                        "Violation_Type": "Thermal_Overload",
                        "Element": dss.CktElement.Name(),
                        "Value": round(max_current, 2),
                        "Limit": round(norm_amps, 2),
                        "Message": f"過載！負載率為 {round((max_current/norm_amps)*100, 1)}%"
                    })
        idx = dss.PDElements.Next() 

    node_names = dss.Circuit.AllNodeNames()
    pu_voltages = dss.Circuit.AllBusMagPu()
    act_voltages = dss.Circuit.AllBusVMag()

    for node, pu_v, act_v in zip(node_names, pu_voltages, act_voltages):
        if act_v > 10.0: 
            if 90 <= act_v <= 140: base_V = 110.0
            elif 190 <= act_v <= 250: base_V = 220.0
            else: base_V = act_v / pu_v if pu_v > 0 else 1.0
            
            real_pu = act_v / base_V
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