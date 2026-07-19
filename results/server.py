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

@app.on_event("startup")
def startup_event():
    """伺服器啟動時，載入並初始化 OpenDSS 實體電路"""
    commands = build_circuit()
    for cmd in commands:
        dss.Text.Command(cmd)
    
    # 設定為 Daily 模式，步長 15 分鐘，但每次呼叫 API 時只解 1 步 (number=1)
    dss.Text.Command("Set mode=Daily stepsize=15m number=1")
    print("✅ OpenDSS 數位孿生模型已載入，等待 API 呼叫執行潮流計算...")

@app.get("/api/grid_status")
def get_grid_status():
    """動態 API 端點：每呼叫一次，OpenDSS 就推進 15 分鐘並回傳與更新狀態"""
    global current_step, is_island_mode,meters_history_data

    """
    提供給前端儀表板的即時狀態 API
    包含當前時間與 OpenDSS 中的電池即時數值
    """
    # 預設電池數值
    soc_percent = 0.0
    bess_kw = 0.0
    bess_amp = 0.0

    # 如果跑完一天，就重新從 00:00 開始
    if current_step >= MAX_STEPS:
        startup_event()
        current_step = 0
        is_island_mode = False  # 🌟 修正 1：換日記得把市電接回來

    # 1. 換算當下時間戳記
    current_minute = current_step * 15
    h = int(current_minute // 60)
    m = int(current_minute % 60)
    sim_time_str = f"{h:02d}:{m:02d}"   

    dss.Circuit.SetActiveElement("Storage.Battery_Sys")
    soc_str = dss.Properties.Value("%stored")
    decision_soc = float(soc_str) if soc_str else 0.0

    # 計算淨功率
    pv_kw = pv_w_list[current_step] / 1000.0   
    load_kw = total_load_w_list[current_step] / 1000.0
    net_kw = pv_kw - load_kw  

    # ==========================================
    # 🧠 3. HEMS 大腦控制邏輯 (與基準腳本完全一致)
    # ==========================================
    if decision_soc >= 99.9 and not is_island_mode:
        is_island_mode = True
        dss.Text.Command("Edit Line.ATS_to_EP enabled=no")
    elif decision_soc <= 20.0 and is_island_mode:
        is_island_mode = False
        dss.Text.Command("Edit Line.ATS_to_EP enabled=yes")

    if is_island_mode:
        dss.Text.Command("Edit Storage.Battery_Sys state=DISCHARGING kW=3.0")
    else:
        if net_kw > 0.05:
            if decision_soc >= 99.9:
                dss.Text.Command("Edit Storage.Battery_Sys state=IDLING")
            else:
                charge_kw = min(net_kw, 5.0)
                charge_pct = round((charge_kw / 5.0) * 100, 2)
                dss.Text.Command(f"Edit Storage.Battery_Sys state=CHARGING %Charge={charge_pct}")
        elif net_kw < -0.05:
            if decision_soc <= 20.0:
                dss.Text.Command("Edit Storage.Battery_Sys state=IDLING")
            else:
                discharge_kw = min(abs(net_kw), 5.0)
                dss.Text.Command(f"Edit Storage.Battery_Sys state=DISCHARGING kW={round(discharge_kw, 2)}")
        else:
            dss.Text.Command("Edit Storage.Battery_Sys state=IDLING")

    # 1. 推進 15 分鐘並解算潮流
    dss.Text.Command("Solve")  # 🌟 修正 2：務必使用 Text.Command 推進狀態機




    meter_targets = {
            "總電錶T": "KwhT",
            "PV電錶": "MeterPV",
            "A電錶": "KwhA",
            "B電錶": "KwhB",
            "C電錶": "KwhC",
            "D電錶": "KwhD"
        }
    
    current_minute = current_step * 15
    h = int(current_minute // 60)
    m = int(current_minute % 60)
    time_str = f"{h:02d}:{m:02d}"

        # 現在 Python 認得 time_str 了，可以安心放入字典
    meter_row = {"Time": time_str}
        
    for ch_name, dss_name in meter_targets.items():
        dss.Meters.Name(dss_name)
        regs = dss.Meters.RegisterValues()
        names = dss.Meters.RegisterNames()
        reg_map = dict(zip(names, regs)) if regs else {}
            
        # 抓取 kWh，保留小數點後 5 位
        kwh = round(reg_map.get('kWh', 0), 5)
        meter_row[f"{ch_name}_當前累積功率(kWh)"] = kwh

        # 將這一筆 15 分鐘的數據加入歷史陣列
    meters_history_data.append(meter_row)

        # 輸出 CSV 給網頁前端抓取
        # 注意這裡的路徑，如果是放在 data/sample 底下，可以依照您 server.py 的習慣路徑填寫
    meters_csv_path = r"C:\projects\hems-simulation-web\data\sample\All_meter.csv" 
    try:
        pd.DataFrame(meters_history_data).to_csv(meters_csv_path, index=False, encoding='utf-8-sig')
    except PermissionError:
        pass


    # ==========================================
    # 🌟 2. 抓取 1F 到 3F 設備的真實物理狀態，並覆寫 CSV 供網頁讀取
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


    

    # 自動確保資料夾存在
    os.makedirs(DATA_SAVE_DIR, exist_ok=True)

    # 批次處理每一層樓
    for floor in all_floors_config:
        device_data = []
        for dev in floor["load_list"]:
            dss.Circuit.SetActiveElement(dev["dss_name"])
            
            # 抓取總功率[cite: 2]
            total_powers = dss.CktElement.TotalPowers()
            kw = abs(total_powers[0]) if total_powers else 0.0
            watts = kw * 1000.0
            
            # 抓取電流[cite: 2]
            currents = dss.CktElement.CurrentsMagAng()
            amps = currents[0] if currents else 0.0
            
            # 狀態判定[cite: 2]
            status_code = 1 if watts > 1.0 else 0
            
            # 抓取額定電壓設定[cite: 2]
            rated_v = float(dss.Properties.Value("kV")) * 1000.0

            # 計算真實電壓[cite: 2]
            if status_code == 1 and amps > 0:
                real_v = watts / amps
            else:
                voltages = dss.CktElement.VoltagesMagAng()
                if rated_v >= 200 and len(voltages) >= 3:
                    real_v = voltages[0] + voltages[2]
                else:
                    real_v = voltages[0] if len(voltages) >= 1 else rated_v

            # 整理寫入資料陣列[cite: 2]
            device_data.append({
                "id": dev["id"],
                "name": dev["name"],
                "status_code": status_code,
                "power_w": round(watts, 1),
                "rated_v": int(rated_v),
                "real_v": round(real_v, 1),
                "current_a": round(amps, 2)
            })

        # 將該樓層的數據輸出為獨立的 CSV 檔，使用原本設定好的 DATA_SAVE_DIR 變數[cite: 2]
        csv_path = os.path.join(DATA_SAVE_DIR, f"{floor['floor_name']}_devices.csv")
        pd.DataFrame(device_data).to_csv(csv_path, index=False, encoding='utf-8-sig')
   
   
    
    dss.Circuit.SetActiveElement("Storage.Battery_Sys")
    total_powers = dss.CktElement.TotalPowers()
    bess_kw = abs(total_powers[0]) if total_powers else 0.0
    currents_mag = dss.CktElement.CurrentsMagAng()
    bess_amp = round(currents_mag[0], 2) if currents_mag else 0.0

    # 寫入 decision_soc (運算前狀態)，完美對齊您的第一張表格
    bess_data = [{
        "time": sim_time_str,
        "soc": round(decision_soc, 2),
        "power_kw": round(bess_kw, 2),
        "current_a": round(bess_amp, 2)
    }]
    
    bess_csv_path = os.path.join(DATA_SAVE_DIR, "bess_status.csv")
    try:
        pd.DataFrame(bess_data).to_csv(bess_csv_path, index=False, encoding='utf-8-sig')
    except PermissionError:
        pass # 略過 Excel 鎖定的錯誤











    current_step += 1 

    # 回傳 JSON 給前端時，因為已經改用 CSV 了，所以把 bess 拿掉，只留時間
    return {
        "status": "success",
        "timestamp": sim_time_str
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