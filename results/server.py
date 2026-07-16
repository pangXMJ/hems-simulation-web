import sys
import os
import datetime
import uvicorn
from fastapi import FastAPI
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
DATA_SAVE_DIR = r"C:\projects\hems-simulation-web\results\data"

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

# 全域變數：紀錄當前的模擬步數 (15分鐘為一步)
current_step = 0
MAX_STEPS = 96  # 一天 24 小時 * 4 

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
    global current_step

    # 如果跑完一天，就重新從 00:00 開始
    if current_step >= MAX_STEPS:
        startup_event()
        current_step = 0

    # 1. 推進 15 分鐘並解算潮流
    dss.Solution.Solve()

    # ==========================================
    # 🌟 2. 抓取 1F 設備的真實物理狀態，並覆寫 CSV 供網頁讀取
    # ==========================================
    floor1_loads = [
        {"id": "f1_dev_1",  "name": "1F 照明 1(EP)", "dss_name": "Load.ep_1f_lighting1_an"},
        {"id": "f1_dev_2",  "name": "1F 照明 2(EP)", "dss_name": "Load.ep_1f_lighting2_bn"},
        {"id": "f1_dev_3",  "name": "1F WiFi/插座(EP)", "dss_name": "Load.ep_1f_wifi_an"},
        {"id": "f1_dev_4",  "name": "1F 電熱水器(EP)", "dss_name": "Load.ep_1f_waterheater_bn"},
        {"id": "f1_dev_5",  "name": "1F 冰箱(EP)", "dss_name": "Load.ep_fridge_an"},
        {"id": "f1_dev_6",  "name": "1F 廚房專插(EP)", "dss_name": "Load.ep_kitchen_bn"},
        {"id": "f1_dev_7",  "name": "1F 照明 1(L1)", "dss_name": "Load.l1_lighting1_an"},
        {"id": "f1_dev_8",  "name": "1F 照明 2(L1)", "dss_name": "Load.l1_lighting2_bn"},
        {"id": "f1_dev_9",  "name": "1F 插座 1(L1)", "dss_name": "Load.l1_socket1_an"},
        {"id": "f1_dev_10", "name": "1F 插座 2(L1)", "dss_name": "Load.l1_socket2_bn"},
        {"id": "f1_dev_11", "name": "1F 插座 3(L1)", "dss_name": "Load.l1_socket3_an"},
        {"id": "f1_dev_12", "name": "1F 插座 4(L1)", "dss_name": "Load.l1_socket4_bn"},
        {"id": "f1_dev_13", "name": "1F 插座 5(L1)", "dss_name": "Load.l1_socket5_an"},
        {"id": "f1_dev_14", "name": "1F 插座 6(L1)", "dss_name": "Load.l1_socket6_bn"},
        {"id": "f1_dev_15", "name": "1F 電磁爐(L1)", "dss_name": "Load.l1_inductioncooktop_abn"},
        {"id": "f1_dev_16", "name": "1F 冷氣(L1)", "dss_name": "Load.l1_airc_abn"}
    ]

    f1_device_data = []
    for dev in floor1_loads:
        dss.Circuit.SetActiveElement(dev["dss_name"])
        
        # 抓取總功率
        total_powers = dss.CktElement.TotalPowers()
        kw = abs(total_powers[0]) if total_powers else 0.0
        watts = kw * 1000.0
        
        # 抓取電流
        currents = dss.CktElement.CurrentsMagAng()
        amps = currents[0] if currents else 0.0
        
        # 狀態判定
        status_code = 1 if watts > 1.0 else 0
        
        # 抓取額定電壓設定
        rated_v = float(dss.Properties.Value("kV")) * 1000.0

        # 計算真實電壓
        if status_code == 1 and amps > 0:
            real_v = watts / amps
        else:
            voltages = dss.CktElement.VoltagesMagAng()
            if rated_v >= 200 and len(voltages) >= 3:
                real_v = voltages[0] + voltages[2]
            else:
                real_v = voltages[0] if len(voltages) >= 1 else rated_v

        # 整理寫入資料陣列
        f1_device_data.append({
            "id": dev["id"],
            "name": dev["name"],
            "status_code": status_code,
            "power_w": round(watts, 1),
            "rated_v": int(rated_v),
            "real_v": round(real_v, 1),
            "current_a": round(amps, 2)
        })

    # 自動確保資料夾存在並存成 CSV
    os.makedirs(DATA_SAVE_DIR, exist_ok=True)
    csv_path = os.path.join(DATA_SAVE_DIR, "floor1_devices.csv")
    pd.DataFrame(f1_device_data).to_csv(csv_path, index=False, encoding='utf-8-sig')

    # ==========================================
    # 3. 抓取電池與主電網狀態 (給主面板使用)
    # ==========================================
    dss.Circuit.SetActiveElement("Storage.Battery_Sys")
    soc_str = dss.Properties.Value("%stored")
    soc = float(soc_str) if soc_str else 0.0

    dss.Circuit.SetActiveElement("Line.ToMeterT")
    grid_import_kw = dss.CktElement.TotalPowers()[0]

    # 4. 換算並回傳當下時間戳記
    current_minute = current_step * 15
    h = int(current_minute // 60)
    m = int(current_minute % 60)
    sim_time_str = f"{h:02d}:{m:02d}"
    
    current_step += 1 

    return {
        "status": "success",
        "timestamp": f"模擬時間 {sim_time_str}",
        "metrics": {
            "battery_soc": round(soc, 2),
            "grid_import_kw": round(grid_import_kw, 2),
            "total_load_kw": abs(round(grid_import_kw, 2)) 
        }
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