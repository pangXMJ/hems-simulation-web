import sys
import os
import datetime
import uvicorn
from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
import opendssdirect as dss
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
    df_pso = pd.read_csv(os.path.join(TARGET_DATA_DIR, "battery_usage_two_stage_summer_weekday_15min.csv"))
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
    
    # 奪取 OpenDSS 電池控制權
    dss.Text.Command("Edit Storage.Battery_Sys DispMode=External")
    
    print("✅ OpenDSS 數位孿生模型已載入，等待 API 呼叫執行潮流計算...")

# 🌟 API 參數新增 web_island_mode_active，預設為 False
@app.get("/api/grid_status")
def get_grid_status(web_island_mode_active: bool = False):
    """動態 API 端點：每呼叫一次，OpenDSS 就推進 15 分鐘並回傳與更新狀態"""
    global current_step, is_island_mode, meters_history_data

    # 1. 接收網頁傳來的停電按鈕狀態
    is_island_mode = web_island_mode_active

    # 如果跑完一天，就重新從 00:00 開始
    if current_step >= MAX_STEPS:
        startup_event()
        current_step = 0
        # 這裡不重設 is_island_mode，交由前端網頁狀態決定

    # 換算當下時間戳記
    current_minute = current_step * 15
    h = int(current_minute // 60)
    m = int(current_minute % 60)
    sim_time_str = f"{h:02d}:{m:02d}"   

    # ==========================================
    # 🌟 ATS 物理開關切換 (依據網頁按鈕狀態)
    # ==========================================
    if is_island_mode:
        dss.Text.Command("Edit Line.ATS_to_EP enabled=no")
    else:
        dss.Text.Command("Edit Line.ATS_to_EP enabled=yes")

    # ==========================================
    # 🌟 每次呼叫都強制奪取外部控制權
    # ==========================================
    dss.Text.Command("Edit Storage.Battery_Sys DispMode=External")

    # 抓取運算前的電池 SOC
    dss.Circuit.SetActiveElement("Storage.Battery_Sys")
    soc_str = dss.Properties.Value("%stored")
    decision_soc = float(soc_str.replace('%', '').strip()) if soc_str else 0.0

    # 計算淨功率供其他參考
    pv_kw = pv_w_list[current_step] / 1000.0   
    load_kw = total_load_w_list[current_step] / 1000.0
    net_kw = pv_kw - load_kw  

    # 抓取當前時間步的 PSO 指令
    current_pso_kw = float(pso_kw_list[current_step])
    current_bess_state = "IDLING"
    
    # 宣告電池最大額定功率
    BATTERY_KWRATED = 5.0

    # ==========================================
    # 🧠 HEMS 大腦控制邏輯 (動態百分比換算法)
    # ==========================================
    if is_island_mode:
        # 停電模式：強制放電 3.0kW (60%)
        dss.Text.Command("Edit Storage.Battery_Sys State=DISCHARGING")
        dss.Text.Command("Edit Storage.Battery_Sys %Discharge=60.0")
        current_bess_state = "DISCHARGING"
    else:
        if current_pso_kw > 0.05:
            # PSO 要求放電
            pct_discharge = (current_pso_kw / BATTERY_KWRATED) * 100.0
            dss.Text.Command("Edit Storage.Battery_Sys State=DISCHARGING")
            dss.Text.Command(f"Edit Storage.Battery_Sys %Discharge={pct_discharge}")
            current_bess_state = "DISCHARGING"
                
        elif current_pso_kw < -0.05:
            # PSO 要求充電
            pct_charge = (abs(current_pso_kw) / BATTERY_KWRATED) * 100.0
            dss.Text.Command("Edit Storage.Battery_Sys State=CHARGING")
            dss.Text.Command(f"Edit Storage.Battery_Sys %Charge={pct_charge}")
            current_bess_state = "CHARGING"
        else:
            # PSO 要求待機
            dss.Text.Command("Edit Storage.Battery_Sys State=IDLING")
            current_bess_state = "IDLING"

    # ==========================================
    # 推進 15 分鐘並解算潮流
    # ==========================================
    dss.Text.Command("Solve")

    # ==========================================
    # Solve 執行完後，重新抓取最新的物理數值
    # ==========================================
    dss.Circuit.SetActiveElement("Storage.Battery_Sys")
    updated_soc_str = dss.Properties.Value("%stored")
    current_soc = float(updated_soc_str.replace('%', '').strip()) if updated_soc_str else decision_soc
    
    total_powers = dss.CktElement.TotalPowers()
    bess_kw = abs(total_powers[0]) if total_powers else 0.0
    currents_mag = dss.CktElement.CurrentsMagAng()
    bess_amp = round(currents_mag[0], 2) if currents_mag else 0.0


    meter_targets = {
        "總電錶T": "KwhT", "PV電錶": "MeterPV", "A電錶": "KwhA",
        "B電錶": "KwhB", "C電錶": "KwhC", "D電錶": "KwhD"
    }

    # ==========================================
    # 抓取電錶歷史資料
    # ==========================================
    meter_row = {"Time": sim_time_str}
    for ch_name, dss_name in meter_targets.items():
        dss.Meters.Name(dss_name)
        regs = dss.Meters.RegisterValues()
        names = dss.Meters.RegisterNames()
        reg_map = dict(zip(names, regs)) if regs else {}
        kwh = round(reg_map.get('kWh', 0), 5)
        meter_row[f"{ch_name}_當前累積功率(kWh)"] = kwh

    meters_history_data.append(meter_row)

   



    # ==========================================
    # 抓取 1F 到 3F 設備的真實物理狀態
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
    # 👇 ================= 新增這一段 ================= 👇
    all_floors_config = [
        {"floor_name": "floor1", "load_list": floor1_loads},
        {"floor_name": "floor2", "load_list": floor2_loads},
        {"floor_name": "floor3", "load_list": floor3_loads}
    ]
    # 👆 ============================================== 👆
    all_floors_results = {}

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

        # 🌟 將算好的這層樓設備清單，存入字典中 (取代原本寫入 CSV 的動作)
        all_floors_results[floor['floor_name']] = device_data

    # ==========================================
    # 電壓與電流抓取 (維持原樣)
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
    
    current_step += 1 

    # ==========================================
    # 🌟 最終回傳 JSON：將所有資料全部打包出去！
    # ==========================================
    return {
        "status": "success",
        "timestamp": sim_time_str,
        "source": source_status, 
        "pv_active": bool(pv_kw > 0),         
        "bess_state": current_bess_state,
        
        # 電池詳細資料
        "bess_soc": round(current_soc, 2),
        "bess_power_kw": round(bess_kw, 2),
        "bess_current_a": round(bess_amp, 2),
        
        # 電錶資料
        "meters": meter_row,

        # 樓層總表電壓電流
        "ep": {"v1": ep_v1, "a1": ep_a1, "v2": ep_v2, "a2": ep_a2},
        "l1": {"v1": l1_v1, "a1": l1_a1, "v2": l1_v2, "a2": l1_a2},
        "l2": {"v1": l2_v1, "a1": l2_a1, "v2": l2_v2, "a2": l2_a2},
        "l3": {"v1": l3_v1, "a1": l3_a1, "v2": l3_v2, "a2": l3_a2},
        
        # 🌟 1F~3F 所有各別設備的即時資料清單
        "floor1_devices": all_floors_results["floor1"],
        "floor2_devices": all_floors_results["floor2"],
        "floor3_devices": all_floors_results["floor3"]
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