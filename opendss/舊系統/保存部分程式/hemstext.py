is_target_outage = (mode == 'island' and outage_start_step <= step < outage_end_step)

        if is_target_outage and not is_island_mode:
            is_island_mode = True
            # 1. 真實物理切斷市電
            dss.Text.Command("Edit Line.ATS_to_EP enabled=no")
            # 2. 💡 關鍵修正：建立「單相 (phases=1)」的變流器電壓源，接在 1、2 節點 (220V)。完美避開三相短路！
            dss.Text.Command("New Vsource.BESS_GFM phases=1 bus1=EP_panel.1.2 basekv=0.22 pu=1.0 angle=0 enabled=yes")
            print(f"⚠️ [{time_str}] 突發停電！ATS 實體切斷，單相變流器啟動 Grid-Forming 接管微電網。")
            
        elif not is_target_outage and is_island_mode:
            is_island_mode = False
            # 恢復市電，關閉微電網變流器與假負載，解封 PV
            dss.Text.Command("Edit Line.ATS_to_EP enabled=yes")
            dss.Text.Command("Edit Vsource.BESS_GFM enabled=no")
            dss.Text.Command("Edit Load.DumpLoad kW=0.0")
            dss.Text.Command("Edit PVSystem.pv_array pmpp=5.0") 
            print(f"🔌 [{time_str}] 市電恢復！結束孤島模式，重新併入大電網。")

        #  初始化電池狀態變數，抓取狀態與計算淨功率
        soc = 0.0
        bess_kw = 0.0
        bess_amp = 0.0

        #嘗試在 OpenDSS 系統中將控制焦點切換到名為 "Storage.Battery_Sys" 的儲能系統物件。如果切換成功（回傳值不為 0），則執行以下資料抓取
        #抓取 SOC (%stored)：獲取該電池的所有內部變數名稱（AllVariableNames）與數值（AllVariableValues）。
        #尋找代表電量百分比的 "%stored"，並將其轉為浮點數賦值給 soc
        #抓取電池功率 (bess_kw)：讀取該元件的總功率（TotalPowers）。通常陣列第一個數值（實功 \(P\)）代表 kW。此處取絕對值（abs），
        #代表不論充電或放電，只紀錄目前的出力大小。抓取電池電流 (bess_amp)：讀取該元件的電流大小與角度（CurrentsMagAng）
        #取陣列第一個數值（通常是 A 相或總電流大小），並四捨五入到小數點後兩位。

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


        #單位轉換：將預先準備好的太陽能發電量（pv_w_list）與總設備用電負載（total_load_w_list）從瓦特（W）除以 1000 轉換為千瓦（kW）

        #淨功率計算 (net_kw)：公式為 太陽能發電 (PV) - 負載 (Load)。
        #net_kw > 0（正值 代表供過於求，當前太陽能發電充足，電力有剩餘（通常可供電池充電）
        #net_kw < 0（負值 代表供不應求，太陽能不夠用，電力有缺口（通常需要靠大電網供電或電池放電支援）。

        pv_kw = pv_w_list[step] / 1000.0   
        load_kw = total_load_w_list[step] / 1000.0
        net_kw = pv_kw - load_kw

        # ==========================================
        # 核心電池充放電控制 (依據 Mode 切換)
        # 根據當前的電網狀態（正常或停電）以及設定的運作模式（基線模式或演算法模式），動態決定儲能電池（BESS）的充放電行為，並在最後驅動 OpenDSS 進行電力潮流物理計算
        # ==========================================
        #電池的額定功率（BATTERY_KWRATED）在此被設定為 5.0 kW，要跟hems buildcircuit的地方一樣，不然模擬結果會不一樣
        BATTERY_KWRATED = 5.0

        #只要前面的時間炸彈判定當前為停電（is_island_mode 為 True）
        #無視目前是什麼模式，強制接管電池控制權。將電池設定為外部控制（DispMode=External），並強制以 3.0 kW 的功率進行放電（DISCHARGING），用來撐住微電網內部的緊急用電。
        if is_island_mode:
            dss.Text.Command("Edit Storage.Battery_Sys DispMode=External")
            
            if net_kw > -0.05: 
                # 【危機：太陽能發電過剩】
                available_charge_space = BATTERY_KWRATED
                if soc >= 99.9: available_charge_space = 0.0 
                
                charge_need = abs(net_kw) 
                
                # --- 第一道防線：電池最大化吸收 ---
                actual_charge_kw = min(charge_need, available_charge_space)
                if actual_charge_kw > 0:
                    dss.Text.Command(f"Edit Storage.Battery_Sys state=CHARGING kW={round(actual_charge_kw, 2)}")
                else:
                    dss.Text.Command("Edit Storage.Battery_Sys state=IDLING")
                
                remaining_excess = charge_need - actual_charge_kw
                
                # --- 第二道防線：洩載電阻 (假負載) 吸收 ---
                actual_dump_kw = min(remaining_excess, DUMP_LOAD_MAX_KW)
                dss.Text.Command(f"Edit Load.DumpLoad kW={round(actual_dump_kw, 2)}")
                
                remaining_excess -= actual_dump_kw
                
                # --- 第三道防線：PV 主動降載 ---
                if remaining_excess > 0.05:
                    curtailed_pv_kw = load_kw + actual_charge_kw + actual_dump_kw
                    dss.Text.Command(f"Edit PVSystem.pv_array pmpp={round(curtailed_pv_kw, 2)}")
                    print(f"   🚨 [{time_str}] PV過剩！充 {round(actual_charge_kw,1)}kW，假負載燒 {round(actual_dump_kw,1)}kW，強制降載 PV 至 {round(curtailed_pv_kw,1)}kW")
                else:
                    dss.Text.Command("Edit PVSystem.pv_array pmpp=5.0")
                    if actual_dump_kw > 0:
                        print(f"   🔥 [{time_str}] 防線作動！充 {round(actual_charge_kw,1)}kW，假負載消耗 {round(actual_dump_kw,1)}kW。")
            
            elif net_kw < 0.05:
                # 【危機：太陽能不足，需電池放電】
                dss.Text.Command("Edit Load.DumpLoad kW=0.0") 
                dss.Text.Command("Edit PVSystem.pv_array pmpp=5.0") 
                
                if soc <= 20.0:
                    dss.Text.Command("Edit Storage.Battery_Sys state=IDLING")
                    print(f"   💀 [{time_str}] 電池耗盡！無法支撐負載，微電網崩潰。")
                else:
                    discharge_need = net_kw
                    actual_discharge_kw = min(discharge_need, BATTERY_KWRATED)
                    if discharge_need > BATTERY_KWRATED:
                        print(f"   ⚠️ [{time_str}] 過載！缺口 ({round(discharge_need,1)}kW) 超過極限 (5kW)")
                    dss.Text.Command(f"Edit Storage.Battery_Sys state=DISCHARGING kW={round(actual_discharge_kw, 2)}")
            else:
                dss.Text.Command("Edit Storage.Battery_Sys state=IDLING")
                dss.Text.Command("Edit Load.DumpLoad kW=0.0")
                dss.Text.Command("Edit PVSystem.pv_array pmpp=5.0")


        #當電網正常且模式為 baseline 時，電池會像「聯絡線平滑控制器」一樣，自動去追蹤系統的淨功率（net_kw = PV - Load，只有當淨功率絕對值大於 0.05 kW 時電池才會動作
        elif mode == 'baseline':
            # 【階段一：傳統淨功率追蹤邏輯】
            dss.Text.Command("Edit Storage.Battery_Sys DispMode=Default")
            #充電邏輯（net_kw > 0.05)，電力過剩
            #若電池已飽和（soc >= 99.9%則進入待機（IDLING）。
            #若未飽和，計算充電量（不超過額定 5kW），並轉換為百分比（%Charge）下達 OpenDSS 充電指令。
            if net_kw > 0.05:
                if soc >= 99.9:
                    dss.Text.Command("Edit Storage.Battery_Sys state=IDLING")
                else:
                    charge_kw = min(net_kw, BATTERY_KWRATED)
                    charge_pct = round((charge_kw / BATTERY_KWRATED) * 100, 2)
                    dss.Text.Command(f"Edit Storage.Battery_Sys state=CHARGING %Charge={charge_pct}")

            #放電邏輯（net_kw < -0.05，電力不足）
            #為保護電池，若電量過低（soc <= 20.0%），強制待機（IDLING）不放電。        
            elif net_kw < -0.05:
                if soc <= 20.0:
                    dss.Text.Command("Edit Storage.Battery_Sys state=IDLING")
                else:
                    discharge_kw = min(abs(net_kw), BATTERY_KWRATED)
                    dss.Text.Command(f"Edit Storage.Battery_Sys state=DISCHARGING kW={round(discharge_kw, 2)}")
            else:
                dss.Text.Command("Edit Storage.Battery_Sys state=IDLING")

        #【演算法模式】PSO 最佳化排程邏輯 (mode in ['pso', 'island'])
        #切換為外部控制，並從前面讀取的列表中抓取當前時間點的排程功率
        #放電（> 0）：排程值為正時，將功率換算為放電百分比（%Discharge）讓電池放電。
        #充電（< 0）：排程值為負時，取絕對值並換算為充電百分比（%Charge）讓電池充電。
        #待機（== 0）：排程值為 0 時，電池待機（IDLING）。
        #問題沒有設計保護程式
        #程式直接盲目執行了排程數值，缺少了像 baseline 模式一樣的 SOC 安全保護機制（例如：沒檢查 soc <= 20% 是否該停止放電，或 soc >= 100% 是否該停止充電）。

        elif mode in ['pso', 'island']:  
            dss.Text.Command("edit Storage.Battery_Sys DispMode=External")
            current_pso_kw = pso_kw_list[step]
            
            # 解決問題 3：補上 SOC 安全保護機制
            if current_pso_kw > 0: # 排程要求放電
                if soc <= 20.0:
                    dss.Text.Command("edit Storage.Battery_Sys State=IDLING")
                else:
                    pct_discharge = (current_pso_kw / BATTERY_KWRATED) * 100.0
                    dss.Text.Command(f"edit Storage.Battery_Sys State=DISCHARGING %Discharge={pct_discharge}")
            elif current_pso_kw < 0: # 排程要求充電
                if soc >= 99.9:
                    dss.Text.Command("edit Storage.Battery_Sys State=IDLING")
                else:
                    pct_charge = (abs(current_pso_kw) / BATTERY_KWRATED) * 100.0
                    dss.Text.Command(f"edit Storage.Battery_Sys State=CHARGING %Charge={pct_charge}")
            else:
                dss.Text.Command("edit Storage.Battery_Sys State=IDLING")

        dss.Text.Command("Solve")



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

        bess_history_data.append({
            "Time": time_str,
            "soc": round(soc, 2),
            "power_kw": round(bess_kw, 2),
            "current_a": round(bess_amp, 2)
        })