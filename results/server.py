import sys
import os
import uvicorn

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import opendssdirect as dss
import pandas as pd

# ==========================================
# 🔍 系統路徑與資料夾設定
# 🆕 只有這一行 PROJECT_ROOT 需要依每個人電腦上的實際路徑調整，
#    其餘路徑都從這裡推導出來，不用每個地方各自改一次。
#    資料夾結構對齊實際規劃：PROJECT_ROOT/data/01_raw、/04_optimized_pso、/sample
#
# 🆕 這段要放在 battery_control_logic / scenario_switch 這兩個 import 之前！
#    因為 server.py 現在放在 results 資料夾（跟 opendss 資料夾是分開的），
#    battery_control_logic.py、scenario_switch.py、hems_circuit.py 都放在 opendss，
#    要先把 opendss 資料夾加進 sys.path，Python 才找得到這些模組。
# ==========================================
PROJECT_ROOT = r"C:\projects\hems-simulation-web"

OPENDSS_DIR = os.path.join(PROJECT_ROOT, "opendss")
SERVER_DIR = os.path.join(PROJECT_ROOT, "results")

RAW_DATA_DIR = os.path.join(PROJECT_ROOT, "data", "01_raw")
PSO_OUTPUT_DIR = os.path.join(PROJECT_ROOT, "data", "04_optimized_pso")
CURRENT_SCENARIO = {"pricing": "two_stage", "outage": None}
CURRENT_COST_SUMMARY = {"pso_cost": None, "baseline_cost": None, "savings": None}
SAMPLE_DIR = os.path.join(PROJECT_ROOT, "data", "sample")

if OPENDSS_DIR not in sys.path:
    sys.path.append(OPENDSS_DIR)

from battery_control_logic import decide_battery_action  # 🆕 共用電池決策模組（在 opendss 資料夾）
import scenario_switch  # 🆕 第4階段：設備控制/停電控制頁面確認後觸發的切換流程（在 opendss 資料夾）
from hems_circuit import build_circuit

# ==========================================
# 🚀 FastAPI 伺服器設定
# ==========================================
app = FastAPI(
    title="HEMS Smart Switchboard API",
    description="Backend server for Microgrid EMS and ESP32 IoT integration"
)

current_step = 0
MAX_STEPS = 96  # 一天 24 小時 * 4
is_island_mode = False  # 紀錄是否處於斷網獨立供電模式

BATTERY_KWRATED = 5.0
DUMP_LOAD_MAX_KW = 2.0

# ==========================================
# 啟動時一次讀取全部 CSV 進記憶體（不是每次 API 呼叫都重讀）
# ==========================================
df_pv_brain = pd.read_csv(os.path.join(RAW_DATA_DIR, "pv_curve_15min.csv"))
pv_w_list = df_pv_brain['pv_kw'].tolist()

df_loads_brain = pd.read_csv(os.path.join(RAW_DATA_DIR, "LoadShapes_All_Nodes_15min.csv"))
load_cols = [c for c in df_loads_brain.columns if c not in ['Time', 'Hour', 'Minute']]
total_load_w_list = df_loads_brain[load_cols].sum(axis=1).tolist()

try:
    df_pso = pd.read_csv(os.path.join(PSO_OUTPUT_DIR, "battery_usage_two_stage_summer_weekday_15min.csv"))
    pso_kw_list = df_pso['battery_power_kw'].tolist()  # 🆕 改用欄位名稱讀取，不用位置索引，比較不怕欄位順序變動
except Exception as e:
    print(f"⚠️ PSO 讀取失敗，預設為全天待機。原因: {e}")
    pso_kw_list = [0.0] * 96


@app.on_event("startup")
def startup_event():
    """伺服器啟動時，載入並初始化 OpenDSS 實體電路"""
    commands = build_circuit()
    for cmd in commands:
        dss.Text.Command(cmd)

    dss.Text.Command("Set mode=Daily stepsize=15m number=1")
    dss.Text.Command("Edit Storage.Battery_Sys DispMode=External")

    print("✅ OpenDSS 數位孿生模型已載入，等待 API 呼叫執行潮流計算...")


# ==========================================
# 🆕 第4階段：設備控制與停電控制頁面按下確認後觸發（做法 A，同步處理）
# ==========================================
@app.post("/api/switch_scenario")
def switch_scenario(config: dict):
    """
    config 範例：
    {
        "pricing": "two_stage",
        "outage": {"start_time": "13:00", "end_time": "15:00"},   # 沒勾選就傳 null
        "device_schedules": [
            {"column": "l1_airc_abn", "start_time": "14:00", "end_time": "16:00", "on_power_kw": 1.2}
        ]
    }
    這是同步處理：前端打這支 API 會等到整個「寫CSV → PSO → 離線驗證(PSO版+baseline對比)
    → 重置即時引擎」流程跑完才收到回應，期間前端應顯示等待畫面。
    """
    current_module = sys.modules[__name__]  # 把 server.py 自己當模組傳進去，讓 scenario_switch 改它的全域變數
    result = scenario_switch.switch_scenario(config, current_module)
    return result


@app.get("/api/daily_summary")
def get_daily_summary(pricing: str = "two_stage"):
    schedule_filename = (
        "battery_usage_two_stage_summer_weekday_15min.csv" if pricing == "two_stage"
        else "battery_usage_three_stage_summer_weekday_15min.csv"
    )
    df_pso_history = pd.read_csv(os.path.join(SAMPLE_DIR, "Pso_History.csv"))
    df_pso_bess = pd.read_csv(os.path.join(SAMPLE_DIR, "Pso_bess_status.csv"))
    df_baseline_bess = pd.read_csv(os.path.join(SAMPLE_DIR, "Baseline_bess_status.csv"))
    df_ideal_schedule = pd.read_csv(os.path.join(PSO_OUTPUT_DIR, schedule_filename))
    df_pv = pd.read_csv(os.path.join(RAW_DATA_DIR, "pv_curve_15min.csv"))
    df_meter = pd.read_csv(os.path.join(SAMPLE_DIR, "Pso_All_meter.csv"))

    floor_devices = {}
    for floor in ["floor1", "floor2", "floor3"]:
        floor_devices[floor] = pd.read_csv(
            os.path.join(SAMPLE_DIR, f"Pso_{floor}_devices.csv")
        ).to_dict(orient="records")

    line_loss_kw = (
        df_pso_history["線路損失(kW)"].tolist()
        if "線路損失(kW)" in df_pso_history.columns else []
    )

    grid_kwh = df_meter["總電錶T_當前累積功率(kWh)"].to_numpy()
    grid_kwh_diff = [grid_kwh[0]] + list(grid_kwh[1:] - grid_kwh[:-1])
    grid_power_kw = [round(v / 0.25, 4) for v in grid_kwh_diff]

    return {
        "time_labels": df_pso_history["Time"].tolist(),
        "floor_devices": floor_devices,
        "line_loss_kw": line_loss_kw,
        "pv_kw": (df_pv["pv_kw"] / 1000.0).tolist(),
        "grid_power_kw": grid_power_kw,
        "ideal_schedule": df_ideal_schedule[
            ["Time", "battery_power_kw", "battery_status",
             "battery_energy_change_kwh", "battery_energy_kwh", "soc_percent"]
        ].to_dict(orient="list"),
        "pso_actual": df_pso_bess[["Time", "battery_power_kw", "soc"]].to_dict(orient="list"),
        "baseline_actual": df_baseline_bess[["Time", "battery_power_kw", "soc"]].to_dict(orient="list"),
    }

@app.get("/api/current_scenario")
def get_current_scenario():
    return {**CURRENT_SCENARIO, **CURRENT_COST_SUMMARY}


outage_start_step = -1
outage_end_step = -1


@app.get("/api/set_outage")
def set_outage(start_time: str, end_time: str):
    """
    接收前端傳來的停電時間 (格式 "HH:MM")
    並重設 OpenDSS 模擬，從 00:00 重新開始跑
    """
    global outage_start_step, outage_end_step, current_step

    def time_to_step(t_str):
        h, m = map(int, t_str.split(':'))
        return h * 4 + (m // 15)

    outage_start_step = time_to_step(start_time)
    outage_end_step = time_to_step(end_time)

    startup_event()
    current_step = 0

    return {
        "status": "success",
        "msg": f"已排程停電區間: 步數 {outage_start_step} 到 {outage_end_step}，模擬已重置"
    }


# 🌟 API 參數：web_island_mode_active（前端手動開關）、operation_mode（AUTO / PSO）
@app.get("/api/grid_status")
def get_grid_status(web_island_mode_active: bool = False, operation_mode: str = "PSO"):
    """動態 API 端點：每呼叫一次，OpenDSS 就推進 15 分鐘並回傳與更新狀態"""
    global current_step, is_island_mode, outage_start_step, outage_end_step

    is_island_mode = web_island_mode_active

    # 如果跑完一天，就重新從 00:00 開始
    if current_step >= MAX_STEPS:
        startup_event()
        current_step = 0

    # 換算當下時間戳記
    current_minute = current_step * 15
    h = int(current_minute // 60)
    m = int(current_minute % 60)
    sim_time_str = f"{h:02d}:{m:02d}"

    if outage_start_step <= current_step < outage_end_step:
        is_island_mode = True
    else:
        is_island_mode = False

    # ==========================================
    # ATS 物理開關切換與孤島電壓源
    # ==========================================
    if is_island_mode:
        dss.Text.Command("Edit Line.ATS_to_EP enabled=no")
        dss.Text.Command("Edit Line.home1F enabled=no")   # 🆕 切斷 L1
        dss.Text.Command("Edit Line.home2F enabled=no")   # 🆕 切斷 L2
        dss.Text.Command("Edit Line.home3F enabled=no")   # 🆕 切斷 L3
        dss.Text.Command("Edit Vsource.BESS_GFM_L1 phases=1 bus1=EP_panel.1 basekv=0.11 pu=1.0 angle=0 enabled=yes")
        dss.Text.Command("Edit Vsource.BESS_GFM_L2 phases=1 bus1=EP_panel.2 basekv=0.11 pu=1.0 angle=180 enabled=yes")
    else:
        dss.Text.Command("Edit Line.ATS_to_EP enabled=yes")
        dss.Text.Command("Edit Line.home1F enabled=yes")   # 🆕 恢復 L1
        dss.Text.Command("Edit Line.home2F enabled=yes")   # 🆕 恢復 L2
        dss.Text.Command("Edit Line.home3F enabled=yes")   # 🆕 恢復 L3
        dss.Text.Command("Edit Vsource.BESS_GFM_L1 enabled=no")
        dss.Text.Command("Edit Vsource.BESS_GFM_L2 enabled=no")

    # 每次呼叫都強制奪取外部控制權
    dss.Text.Command("Edit Storage.Battery_Sys DispMode=External")

    # 抓取運算前的電池 SOC（決策用）
    dss.Circuit.SetActiveElement("Storage.Battery_Sys")
    soc_str = dss.Properties.Value("%stored")
    decision_soc = float(soc_str.replace('%', '').strip()) if soc_str else 0.0

    # 計算這一步的 PV / Load / net_kw
    pv_kw = pv_w_list[current_step] / 1000.0
    load_kw = total_load_w_list[current_step] / 1000.0
    net_kw = pv_kw - load_kw

    # 計算 PV 當日累積發電量
    current_pv_w_sum = sum(pv_w_list[:current_step + 1])
    pv_cumulative_kwh = (current_pv_w_sum / 1000.0) * 0.25

    current_pso_kw = float(pso_kw_list[current_step])

    # ==========================================
    # 🧠 呼叫共用電池決策模組，取代原本寫死在這裡的 if/elif
    # ==========================================
    action = decide_battery_action(
        is_island_mode=is_island_mode,
        operation_mode=operation_mode,
        net_kw=net_kw,
        soc=decision_soc,
        current_pso_kw=current_pso_kw,
        load_kw=load_kw,
        battery_kwrated=BATTERY_KWRATED,
        dump_load_max_kw=DUMP_LOAD_MAX_KW,
        time_str=sim_time_str,
    )

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

    current_bess_state = action["battery_state"]

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
    # 抓取電錶資料
    # ==========================================
    meter_row = {"Time": sim_time_str}
    for ch_name, dss_name in meter_targets.items():
        dss.Meters.Name(dss_name)
        regs = dss.Meters.RegisterValues()
        names = dss.Meters.RegisterNames()
        reg_map = dict(zip(names, regs)) if regs else {}
        kwh = round(reg_map.get('kWh', 0), 5)
        meter_row[f"{ch_name}_當前累積功率(kWh)"] = kwh

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

    all_floors_config = [
        {"floor_name": "floor1", "load_list": floor1_loads},
        {"floor_name": "floor2", "load_list": floor2_loads},
        {"floor_name": "floor3", "load_list": floor3_loads}
    ]
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

        all_floors_results[floor['floor_name']] = device_data

    # ==========================================
    # 電壓與電流抓取
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
    # 🌟 最終回傳 JSON
    # ==========================================
    return {
        "status": "success",
        "timestamp": sim_time_str,
        "source": source_status,
        "pv_active": bool(pv_kw > 0),
        "bess_state": current_bess_state,

        "pv": {
            "power_kw": round(pv_kw, 2),
            "cumulative_kwh": round(pv_cumulative_kwh, 2)
        },

        "bess": {
            "soc": round(current_soc, 2),
            "power_kw": round(bess_kw, 2),
            "current_a": round(bess_amp, 2)
        },

        "meters": meter_row,

        "ep": {"v1": ep_v1, "a1": ep_a1, "v2": ep_v2, "a2": ep_a2},
        "l1": {"v1": l1_v1, "a1": l1_a1, "v2": l1_v2, "a2": l1_a2},
        "l2": {"v1": l2_v1, "a1": l2_a1, "v2": l2_v2, "a2": l2_a2},
        "l3": {"v1": l3_v1, "a1": l3_a1, "v2": l3_v2, "a2": l3_a2},

        "floor1_devices": all_floors_results["floor1"],
        "floor2_devices": all_floors_results["floor2"],
        "floor3_devices": all_floors_results["floor3"]
    }


# ==========================================
# 🌐 靜態網頁伺服器
# ==========================================
app.mount("/", StaticFiles(directory=SERVER_DIR, html=True), name="static")

if __name__ == "__main__":
    print("🚀 FastAPI 伺服器啟動中...")
    print("👉 測試 API 端點: http://127.0.0.1:8000/api/grid_status")
    print("👉 觀看儀表板: http://127.0.0.1:8000/hems_index.html")
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)