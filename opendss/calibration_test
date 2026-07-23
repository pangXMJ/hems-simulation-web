import opendssdirect as dss
from hems_circuit import build_circuit

def run_calibration_test():
    print("="*60)
    print("🛠️ 啟動 OpenDSS 模型靜態基準測試 (Snapshot Calibration)")
    print("="*60)

    # 1. 建立基準電路
    commands = build_circuit()
    for cmd in commands:
        dss.Text.Command(cmd)

    # 2. 切換為單步靜態模式 (不使用 24 小時 LoadShape)
    dss.Text.Command("Set mode=Snap")

    # 3. 暴力覆寫狀態：關閉所有負載，創造「純淨」的實驗環境
    dss.Text.Command("Batchedit Load..* kW=0.001") 

    # 4. 設定測試情境：固定 PV 與 單一負載
    # 在 220V 系統下，2.2kW 負載的理論電流應為 10A
    TEST_PV_KW = 5.0
    TEST_LOAD_KW = 2.2 
    
    print(f"👉 測試條件設定：PV 發電 = {TEST_PV_KW} kW, L1 冷氣負載 = {TEST_LOAD_KW} kW")
    dss.Text.Command(f"Edit PVSystem.pv_array pmpp={TEST_PV_KW}")
    dss.Text.Command(f"Edit Load.l1_airc_abn kW={TEST_LOAD_KW}")

    # 5. 執行單步潮流計算
    dss.Text.Command("Solve")

    # 檢查潮流計算是否收斂
    if not dss.Solution.Converged():
        print("❌ 警告：潮流計算未收斂！")
        return

    # ==========================================
    # 🔍 擷取並驗證感測器數值
    # ==========================================
    print("\n📊 --- 物理量驗證報告 ---")

    # 驗證 A：讀取 KwhT 總電錶的即時功率
    dss.Meters.Name("KwhT")
    meter_registers = dss.Meters.RegisterValues()
    if meter_registers:
        print(f"⚡ [總電錶 KwhT] 紀錄功率: {round(meter_registers[0], 2)} kW")

    # 驗證 B：讀取 L1 冷氣負載的真實電壓與電流
    dss.Circuit.SetActiveElement("Load.l1_airc_abn")
    
    # 取得電壓 (Magnitude, Angle 格式)
    voltages = dss.CktElement.VoltagesMagAng()
    v_mag = voltages[0] if voltages else 0.0
    
    # 取得電流 (Magnitude, Angle 格式)
    currents = dss.CktElement.CurrentsMagAng()
    i_mag = currents[0] if currents else 0.0
    
    print(f"🔌 [負載端 Load.l1_airc_abn]")
    print(f"   - 終端電壓: {round(v_mag, 2)} V (理論值約 220 V)")
    print(f"   - 終端電流: {round(i_mag, 3)} A (理論值約 {round((TEST_LOAD_KW*1000)/220, 3)} A)")

    # 驗證 C：檢查 PV 逆變器的輸出
    dss.Circuit.SetActiveElement("PVSystem.pv_array")
    pv_power = dss.CktElement.Powers()
    # Powers 陣列格式為 [kW1, kvar1, kW2, kvar2...]，輸出功率為負值
    pv_kw_out = abs(pv_power[0]) if pv_power else 0.0
    print(f"☀️ [太陽能 PV_Array] 真實輸出功率: {round(pv_kw_out, 3)} kW")

    print("="*60)

if __name__ == "__main__":
    run_calibration_test()