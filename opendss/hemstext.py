def run_simulation(bess_schedule):
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

    for step in range(total_steps):

        hour=step//60
        current_bess_kw=bess_schedule[hour]
        if current_bess_kw>0:
            dss.Text.Command(f"Edit Storage.Battery_Sys state=DISCHARGING kw={current_bess_kw}")
        elif current_bess_kw < 0:
            dss.Text.Command(f"Edit Storage.Battery_Sys state=CHARGING kW={abs(current_bess_kw)}")
        else:
            dss.Text.Command("Edit Storage.Battery_Sys state=IDLING")

        dss.Text.Command("Solve")

        # 1. 處理時間字串 (將小數小時 0.25, 0.5 轉換為 0:15, 0:30 等格式)
        h = int(step//60)
        m = int(step%60)
        time_str = f"{h:02d}:{m:02d}"

        # 建立這一筆時間的資料字典 (這就是未來 CSV 的一列)

        row_dict = {"time": time_str, "Hour": h, "Minute": m}


        dss.Circuit.SetActiveElement("Storage.Battery_Sys")
        soc_str = dss.Properties.Value("%stored")
        if soc_str:
            row_dict["電池_SOC(%)"] = round(float(soc_str), 2)
        else:
            row_dict["電池_SOC(%)"] = 0.0


        # 抓取各配電盤的電壓與電流
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
    fake_schedule = [0,0,0,0,0,0,0,0,0,0, -3,-3,-3,-3, 0,0,0,0, 3,3,3, 0,0,0]
    df_history = run_simulation(fake_schedule)
    #show_results()

    # 【修改這裡】指定你要儲存的完整路徑
    save_path = r"..\data\sample\Panel_Common_Nodes_History.csv"
    # 存檔，並加入 encoding='utf-8-sig' 確保 Excel 開啟不亂碼
    df_history.to_csv(save_path, index=False, encoding='utf-8-sig')

    # 印出提示訊息確認
    print(f"\n檔案已成功存檔至：{save_path}")
        