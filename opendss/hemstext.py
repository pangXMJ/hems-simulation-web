
def run_simulation():
    """執行電路建構與步進求解，並以寬表格(Wide Format)整理所有狀態與電錶累積值。"""
    commands = build_circuit()

    for cmd in commands:
        dss.Text.Command(cmd)

    print("✅ 電路建置完成！開始進行 24 小時步進模擬 (Step-by-Step Simulation)...\n")

    dss.Text.Command("Set mode=Daily stepsize=1m number=1")

    total_steps = 1440
    history_data = []  # 準備收集寬表格資料的陣列

    # 配電盤定義
    panel_configs = {
        "EP": {"bus": "EP_panel", "line": "Line.ATS_to_EP"},
        "L1": {"bus": "panel1F", "line": "Line.home1F"},
        "L2": {"bus": "panel2F", "line": "Line.home2F"},
        "L3": {"bus": "panel3F", "line": "Line.home3F"}
    }

    # 電錶定義 (對應你 OpenDSS 裡面的名稱)
    meter_targets = {
        "PV電錶": "MeterPV",
        "A電錶": "KwhA",
        "B電錶": "KwhB",
        "C電錶": "KwhC",
        "D電錶": "KwhD"
    }

    is_island_mode=False



    for step in range(total_steps):
    

        # 1. 處理時間字串 (將小數小時 0.25, 0.5 轉換為 0:15, 0:30 等格式)
        h = int(step//60)
        m = int(step%60)
        time_str = f"{h:02d}:{m:02d}"



        dss.Circuit.SetActiveElement("Storage.Battery_Sys")
        soc_str = dss.Properties.Value("%stored")
        soc = float(soc_str) if soc_str else 0.0

        dss.Circuit.SetActiveElement("PVSystem.PV_Array")
        pv_powers = dss.CktElement.Powers()
        # OpenDSS 中發電輸出為負值，取絕對值獲得實際發電量
        pv_kw = abs(pv_powers[0]) if len(pv_powers) > 0 else 0.0

        # ==========================================
        # 🔄 3. ATS 切換邏輯 (狀態機)
        # ==========================================
        if soc >= 99.9 and not is_island_mode:
            # 觸發條件：充飽 100%，且原本還在市電模式
            is_island_mode = True
            # [切換 ATS]：關閉市電進線，開啟逆變器進線
            dss.Text.Command("Edit Line.ATS_to_EP enabled=no")
            dss.Text.Command("Edit Line.Inv_to_EP enabled=yes")
            print(f"[{time_str}] 🔋電池滿電！ATS切斷市電，EP盤改由「電池供電」。")

        elif soc <= 20.0 and is_island_mode:
            # 觸發條件：掉到 20%，且原本在電池模式
            is_island_mode = False
            # [切換 ATS]：恢復市電進線，關閉逆變器進線
            dss.Text.Command("Edit Line.ATS_to_EP enabled=yes")
            dss.Text.Command("Edit Line.Inv_to_EP enabled=no")
            print(f"[{time_str}] ⚠️電池低電量！ATS接回市電，開始充電。")  


        # ==========================================
        # ⚡ 4. 電池充放電行為控制
        # ==========================================
        if is_island_mode:
            # 【模式 A：電池供電】
            # 強制放電供應家裡，設定放電 3kW
            dss.Text.Command("Edit Storage.Battery_Sys state=DISCHARGING kW=3.0")
        else:
            # 【模式 B：市電供電 (太陽能優先充電池)】
            if pv_kw > 0.05: # 如果太陽能有發電 (大於 50W 才充)
                # 將「充電功率」精準設定為「太陽能發電量」 (不超過 5kW 上限)
                charge_kw = round(min(pv_kw, 5.0), 2)
                dss.Text.Command(f"Edit Storage.Battery_Sys state=CHARGING kW={charge_kw}")
            else:
                # 沒太陽的時候就休息
                dss.Text.Command("Edit Storage.Battery_Sys state=IDLING")


        # ==# ==========================================
        # 5. 執行這 1 分鐘的物理計算 (Solve)
        # ==========================================
        dss.Text.Command("Solve")
        
        # 建立這一步的數據字典
        row_dict = {"Time": time_str, "Hour": h, "Minute": m}
        row_dict["電池_SOC(%)"] = round(soc, 2)
        row_dict["PV_發電量(kW)"] = round(pv_kw, 2)
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


if __name__ == "__main__":
    df_history = run_simulation()
    #show_results()

    # 【修改這裡】指定你要儲存的完整路徑
    save_path = r".\data\sample\Panel_Common_Nodes_History.csv"
    try:
        # 存檔，並加入 encoding='utf-8-sig' 確保 Excel 開啟不亂碼
        df_history.to_csv(save_path, index=False, encoding='utf-8-sig')
        # 印出提示訊息確認
        print(f"\n✅ 檔案已成功存檔至：{save_path}")
    except PermissionError:
        print(f"\n❌ 存檔失敗！請檢查是否正在使用 Excel 開啟該 CSV 檔案，請關閉後再執行一次！")
        
