import sys
import os
import datetime
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import opendssdirect as dss
import pandas as pd
from fastapi.staticfiles import StaticFiles
import pandas as pd

# ==========================================
# 🔍 系統路徑與資料夾設定
# ==========================================
# OpenDSS 檔案所在的資料夾
OPENDSS_DIR = r"C:\projects\hems-simulation-web\opendss"
# 前端網頁與靜態檔案所在的資料夾
SERVER_DIR = r"C:\projects\hems-simulation-web\results"
# 產生的 CSV 即時數據存放資料夾
DATA_SAVE_DIR = r"C:\projects\hems-simulation-web\data\sample"

# 將 OpenDSS 路徑強制加入 Python 的大腦 (搜尋清單) 中
if OPENDSS_DIR not in sys.path:
    sys.path.append(OPENDSS_DIR)

# 載入 hems_circuit 的建置電路函數
from hems_circuit import build_circuit 

# ==========================================
# 🚀 FastAPI 伺服器設定
# ==========================================
app = FastAPI(
    title="HEMS Smart Switchboard API",
    description="Backend server for Microgrid EMS and ESP32 IoT integration" 
)


# 1. 取得 server.py 當前所在的資料夾 (C:\...\results)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 2. 往上一層 (..)，然後進入 data\sample (C:\...\data\sample)
TARGET_DATA_DIR = os.path.join(BASE_DIR, "..", "data", "sample")

# 3. 把這個正確的路徑掛載給網頁
app.mount("/data", StaticFiles(directory=TARGET_DATA_DIR), name="data")
  

# 全域變數：紀錄當前的模擬步數 (15分鐘為一步)
current_step = 0
MAX_STEPS = 96  # 一天 24 小時 * 4 

current_step = 0
MAX_STEPS = 96  # 一天 24 小時 * 4 
is_island_mode = False  # 紀錄是否處於斷網獨立供電模式

# 🌟 新增：存放電錶累積折線圖的歷史資料
meters_history_data = []

df_pv_brain = pd.read_csv(os.path.join(TARGET_DATA_DIR, "pv_curve_15min.csv"))
pv_w_list = df_pv_brain['pv_kw'].tolist() 

df_loads_brain = pd.read_csv(os.path.join(TARGET_DATA_DIR, "LoadShapes_All_Nodes_15min.csv"))
load_cols = [c for c in df_loads_brain.columns if c not in ['Time', 'Hour', 'Minute']]
total_load_w_list = df_loads_brain[load_cols].sum(axis=1).tolist()


try:
    df_pso = pd.read_csv(os.path.join(TARGET_DATA_DIR, "pso_battery_power.csv"))
    # 無視欄位名稱，強制讀取第 2 欄 (Index 1) 的數值
    pso_kw_list = df_pso.iloc[:, 1].tolist() 
except Exception as e:
    print(f"⚠️ PSO 讀取失敗，預設為全天待機。原因: {e}")
    pso_kw_list = [0.0] * 96

@app.on_event("startup")
def startup_event():
    """伺服器啟動時，載入並初始化 OpenDSS 實體電路"""
    commands = build_circuit()
    for cmd in commands:
        dss.Text.Command(cmd)
    
    # 設定為 Daily 模式，步長 15 分鐘，但每次呼叫 API 時只解 1 步 (number=1)
    dss.Text.Command("Set mode=Daily stepsize=15m number=1")
    
    # 🌟 修正 1：加上這行，奪取 OpenDSS 電池控制權
    dss.Text.Command("Edit Storage.Battery_Sys DispMode=External")
    
    print("✅ OpenDSS 數位孿生模型已載入，等待 API 呼叫執行潮流計算...")

@app.get("/api/grid_status")
def get_grid_status():
    """動態 API 端點：每呼叫一次，OpenDSS 就推進 15 分鐘並回傳與更新狀態"""
    global current_step, is_island_mode, meters_history_data

    # 如果跑完一天，就重新從 00:00 開始[cite: 4]
    if current_step >= MAX_STEPS:
        startup_event()
        current_step = 0
        is_island_mode = False 

    # 1. 換算當下時間戳記[cite: 4]
    current_minute = current_step * 15
    h = int(current_minute // 60)
    m = int(current_minute % 60)
    sim_time_str = f"{h:02d}:{m:02d}"   

    # ==========================================
    # 🌟 關鍵修正 1：每次呼叫都強制奪取外部控制權
    # ==========================================
    dss.Text.Command("Edit Storage.Battery_Sys DispMode=External")

    # 抓取運算前的電池 SOC (僅供狀態機判斷用)[cite: 4]
    dss.Circuit.SetActiveElement("Storage.Battery_Sys")
    soc_str = dss.Properties.Value("%stored")
    decision_soc = float(soc_str.replace('%', '').strip()) if soc_str else 0.0

    # 計算淨功率供其他參考[cite: 4]
    pv_kw = pv_w_list[current_step] / 1000.0   
    load_kw = total_load_w_list[current_step] / 1000.0
    net_kw = pv_kw - load_kw  

    # ==========================================
    # 🧠 HEMS 大腦控制邏輯 (絕對值 kW 控制法)
    # ==========================================
    if decision_soc >= 99.9 and not is_island_mode:
        is_island_mode = True
        dss.Text.Command("Edit Line.ATS_to_EP enabled=no")
    elif decision_soc <= 20.0 and is_island_mode:
        is_island_mode = False
        dss.Text.Command("Edit Line.ATS_to_EP enabled=yes")

    # 抓取當前時間步的 PSO 指令 (強制轉為 float 避免型態錯誤)
    current_pso_kw = float(pso_kw_list[current_step])
    current_bess_state = "IDLING"

    if is_island_mode:
        # 停電模式：強制放電
        dss.Text.Command("Edit Storage.Battery_Sys kW=3.0 State=DISCHARGING")
        current_bess_state = "DISCHARGING"
    else:
        if current_pso_kw > 0.05:
            # PSO 要求放電
            if decision_soc <= 20.0:
                dss.Text.Command("Edit Storage.Battery_Sys State=IDLING")
                current_bess_state = "IDLING"
            else:
                dss.Text.Command(f"Edit Storage.Battery_Sys kW={current_pso_kw} State=DISCHARGING")
                current_bess_state = "DISCHARGING"
                
        elif current_pso_kw < -0.05:
            # PSO 要求充電
            if decision_soc >= 99.9:
                dss.Text.Command("Edit Storage.Battery_Sys State=IDLING")
                current_bess_state = "IDLING"
            else:
                # 確保充電功率為正值
                dss.Text.Command(f"Edit Storage.Battery_Sys kW={abs(current_pso_kw)} State=CHARGING")
                current_bess_state = "CHARGING"
        else:
            # PSO 要求待機
            dss.Text.Command("Edit Storage.Battery_Sys State=IDLING")
            current_bess_state = "IDLING"

    # ==========================================
    # 推進 15 分鐘並解算潮流[cite: 4]
    # ==========================================
    dss.Text.Command("Solve")

    # ==========================================
    # 🌟 關鍵修正 2：Solve 執行完後，重新抓取最新的物理數值
    # ==========================================
    dss.Circuit.SetActiveElement("Storage.Battery_Sys")
    updated_soc_str = dss.Properties.Value("%stored")
    current_soc = float(updated_soc_str.replace('%', '').strip()) if updated_soc_str else decision_soc
    
    total_powers = dss.CktElement.TotalPowers()
    bess_kw = abs(total_powers[0]) if total_powers else 0.0
    currents_mag = dss.CktElement.CurrentsMagAng()
    bess_amp = round(currents_mag[0], 2) if currents_mag else 0.0

    # 寫入最新資料供網頁即時讀取 (使用算完後的 current_soc)
    bess_data = [{
        "time": sim_time_str,
        "soc": round(current_soc, 2),
        "power_kw": round(bess_kw, 2),
        "current_a": round(bess_amp, 2)
    }]
    
    bess_csv_path = os.path.join(DATA_SAVE_DIR, "bess_status.csv")
    try:
        pd.DataFrame(bess_data).to_csv(bess_csv_path, index=False, encoding='utf-8-sig')
    except PermissionError:
        print(f"⚠️ 警告：無法寫入 {bess_csv_path}，請確認檔案是否被 Excel 開啟！")

    # ==========================================
    # 抓取電錶歷史資料 (與您原本的程式碼相同)[cite: 4]
    # ==========================================
    meter_targets = {
        "總電錶T": "KwhT", "PV電錶": "MeterPV", "A電錶": "KwhA",
        "B電錶": "KwhB", "C電錶": "KwhC", "D電錶": "KwhD"
    }
    
    meter_row = {"Time": sim_time_str}
    for ch_name, dss_name in meter_targets.items():
        dss.Meters.Name(dss_name)
        regs = dss.Meters.RegisterValues()
        names = dss.Meters.RegisterNames()
        reg_map = dict(zip(names, regs)) if regs else {}
        kwh = round(reg_map.get('kWh', 0), 5)
        meter_row[f"{ch_name}_當前累積功率(kWh)"] = kwh

    meters_history_data.append(meter_row)
    meters_csv_path = os.path.join(DATA_SAVE_DIR, "All_meter.csv") 
    try:
        pd.DataFrame(meters_history_data).to_csv(meters_csv_path, index=False, encoding='utf-8-sig')
    except PermissionError:
        pass

    # ==========================================
    # 抓取 1F 到 3F 設備的真實物理狀態 (與您原本的程式碼相同)[cite: 4]
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
        {"id": "f2_dev_16", "name": "2F 插座 7(L2)", "dss_name": "Load.l2_socket7_an"}
    ]
    floor3_loads = [
        {"id": "f3_dev_1", "name": "3F 洗衣機(EP)", "dss_name": "Load.ep_washer_an"},
        {"id": "f3_dev_2", "name": "3F 烘衣機(EP)", "dss_name": "Load.ep_dryer_bn"},
        {"id": "f3_dev_3", "name": "3F 加壓馬達(EP)", "dss_name": "Load.boosterpump_abn"},
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

    all_floors_config = [
        {"floor_name": "floor1", "load_list": floor1_loads},
        {"floor_name": "floor2", "load_list": floor2_loads},
        {"floor_name": "floor3", "load_list": floor3_loads}
    ]

    os.makedirs(DATA_SAVE_DIR, exist_ok=True)
    for floor in all_floors_config:
        device_data = []
        for dev in floor["load_list"]:
            dss.Circuit.SetActiveElement(dev["dss_name"])
            total_powers = dss.CktElement.TotalPowers()
            kw = abs(total_powers[0]) if total_powers else 0.0
            watts = kw * 1000.0
            currents = dss.CktElement.CurrentsMagAng()
            amps = currents[0] if currents else 0.0
            status_code = 1 if watts > 1.0 else 0
            rated_v = float(dss.Properties.Value("kV")) * 1000.0

            if status_code == 1 and amps > 0:
                real_v = watts / amps
            else:
                voltages = dss.CktElement.VoltagesMagAng()
                if rated_v >= 200 and len(voltages) >= 3:
                    real_v = voltages[0] + voltages[2]
                else:
                    real_v = voltages[0] if len(voltages) >= 1 else rated_v

            device_data.append({
                "id": dev["id"], "name": dev["name"], "status_code": status_code,
                "power_w": round(watts, 1), "rated_v": int(rated_v),
                "real_v": round(real_v, 1), "current_a": round(amps, 2)
            })

        csv_path = os.path.join(DATA_SAVE_DIR, f"{floor['floor_name']}_devices.csv")
        pd.DataFrame(device_data).to_csv(csv_path, index=False, encoding='utf-8-sig')

    # ==========================================
    # 電壓與電流抓取 (回傳 JSON 給前端)[cite: 4]
    # ==========================================
    def get_bus_voltage(bus_name):
        dss.Circuit.SetActiveBus(bus_name)
        v_mag_angle = dss.Bus.VMagAngle()
        if len(v_mag_angle) >= 4:
            return round(v_mag_angle[0], 1), round(v_mag_angle[2], 1)
        return 0.0, 0.0

    def get_line_current(line_name):
        dss.Circuit.SetActiveElement(f"Line.{line_name}")
        currents = dss.CktElement.CurrentsMagAng()
        if currents and len(currents) >= 4:
            return round(currents[0], 2), round(currents[2], 2)
        return 0.0, 0.0

    ep_v1, ep_v2 = get_bus_voltage("EP_Panel")
    l1_v1, l1_v2 = get_bus_voltage("panel1F")
    l2_v1, l2_v2 = get_bus_voltage("panel2F")
    l3_v1, l3_v2 = get_bus_voltage("panel3F")

    l1_a1, l1_a2 = get_line_current("home1F")
    l2_a1, l2_a2 = get_line_current("home2F")
    l3_a1, l3_a2 = get_line_current("home3F")
    ats_a1, ats_a2 = get_line_current("ATS_to_EP")
    inv_a1, inv_a2 = get_line_current("Inv_AC_Main")
    
    if is_island_mode:
        ep_a1, ep_a2 = inv_a1, inv_a2
    else:
        ep_a1, ep_a2 = ats_a1, ats_a2

    source_status = "BATT" if is_island_mode else "GRID"
    
    # 推進時間步數
    current_step += 1 

    return {
        "status": "success",
        "timestamp": sim_time_str,
        "source": source_status, 
        "pv_active": bool(pv_kw > 0),         
        "bess_state": current_bess_state,     
        "ep": {"v1": ep_v1, "a1": ep_a1, "v2": ep_v2, "a2": ep_a2},
        "l1": {"v1": l1_v1, "a1": l1_a1, "v2": l1_v2, "a2": l1_a2},
        "l2": {"v1": l2_v1, "a1": l2_a1, "v2": l2_v2, "a2": l2_a2},
        "l3": {"v1": l3_v1, "a1": l3_a1, "v2": l3_v2, "a2": l3_a2}
    }


# ==========================================
# 🌐 靜態網頁伺服器
# ==========================================
# 掛載 index.html 所在的資料夾
app.mount("/", StaticFiles(directory=SERVER_DIR, html=True), name="static")

if __name__ == "__main__":
    print("🚀 FastAPI 伺服器啟動中...")
    print("👉 測試 API 端點: http://127.0.0.1:8000/api/grid_status")
    print("👉 觀看儀表板: http://127.0.0.1:8000/hems_index.html")
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)