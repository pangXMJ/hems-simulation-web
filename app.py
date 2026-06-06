import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import matplotlib.pyplot as plt
import os
from pathlib import Path
from matplotlib import font_manager

# 匯入 PSO 核心演算法檔案。
# 你的資料夾裡面需要有 hems_pso_core.py，否則這行會出錯。
from pso import hems_pso_core

# ============================================================
# 0. 設定 Matplotlib 支援中文
# ============================================================
def setup_chinese_font():
    """設定 Matplotlib 支援繁體中文，避免圖表中文變成方框。"""

    # Windows 常見繁體中文字型
    font_path = r"C:\Windows\Fonts\msjh.ttc"   # 微軟正黑體

    if os.path.exists(font_path):
        font_manager.fontManager.addfont(font_path)
        plt.rcParams["font.family"] = "Microsoft JhengHei"
    else:
        # 如果不是 Windows，嘗試用其他常見中文字型
        plt.rcParams["font.family"] = [
            "Microsoft JhengHei",
            "Microsoft YaHei",
            "SimHei",
            "Noto Sans CJK TC",
            "Arial Unicode MS"
        ]

    # 避免負號 '-' 也變成亂碼
    plt.rcParams["axes.unicode_minus"] = False


setup_chinese_font()

# ============================================================
# 1. 網頁基本設定
# ============================================================

# 設定 Streamlit 網頁的標題、版面寬度、左上角 icon。
st.set_page_config(
    page_title="智慧家庭系統",
    layout="wide",
    page_icon="⚡"
)

# 讓左側 sidebar 可以滑動，避免設備太多時看不到下面的內容。
st.markdown(
    """
    <style>
    section[data-testid="stSidebar"] {
        overflow-y: auto;
    }

    section[data-testid="stSidebar"] > div {
        overflow-y: auto;
        max-height: 100vh;
    }
    </style>
    """,
    unsafe_allow_html=True
)


# ============================================================
# 2. 資料與設備清單設定
# ============================================================

# APPLIANCES 是設備清單。
# 左邊的 key，例如 fridge、ac，是 CSV 欄位名稱。
# name 是顯示在網頁上的中文名稱。
# color 是畫圖時使用的顏色。
APPLIANCES = {
    "fridge": {"name": "變頻冰箱", "color": "#06b6d4"},
    "ac": {"name": "客廳變頻冷氣", "color": "#3b82f6"},
    "heater": {"name": "儲熱式電熱水器", "color": "#ea580c"},
    "dehumidifier": {"name": "除濕機 (自動)", "color": "#22c55e"},
    "dryer": {"name": "熱泵烘衣機", "color": "#84cc16"},
    "lighting": {"name": "全棟照明", "color": "#facc15"},
    "washer": {"name": "洗衣機", "color": "#a855f7"},
    "transferPump": {"name": "揚水馬達", "color": "#0ea5e9"},
    "boosterPump": {"name": "加壓馬達", "color": "#f59e0b"},
}

# 取得目前 app.py 所在的資料夾位置。
# 這樣不管你從哪個 terminal 位置執行，程式都會從 app.py 同一層去找 CSV。
BASE_DIR = Path(__file__).resolve().parent

# CSV 檔案路徑。
DATA_PATH = BASE_DIR / "data" / "raw" / "daily_appliance_data_1min.csv"


# ============================================================
# 3. Function：讀取 CSV 資料
# ============================================================

@st.cache_data
def load_base_data(path):
    """
    讀取家庭設備用電資料。

    path：CSV 檔案路徑。
    回傳值：pandas DataFrame，也就是表格資料。
    """
    try:
        # 讀取 CSV 檔案。
        df = pd.read_csv(path)

        # 檢查 CSV 裡面有沒有 Time 欄位。
        # 如果沒有，程式就自己產生一個時間欄位。
        if "Time" not in df.columns:
            # 假設資料是 1 分鐘一筆。
            # periods=len(df) 代表產生和資料列數一樣多的時間點。
            df["Time"] = pd.date_range(
                "2026-05-17",
                periods=len(df),
                freq="min"
            )

        return df

    except Exception as e:
        # 如果讀不到 CSV，就在網頁上顯示錯誤訊息。
        st.error(f"無法讀取資料檔 {path}。錯誤訊息: {e}")
        return None


# ============================================================
# 4. Function：計算一天總用電量
# ============================================================

def calculate_daily_energy_kwh(data, power_column="Total_W", interval_minutes=1):
    """
    計算一天用了多少度電。

    data：表格資料。
    power_column：功率欄位名稱，這裡通常是 Total_W。
    interval_minutes：每一筆資料相隔幾分鐘。你的 CSV 是 1 分鐘一筆，所以預設是 1。

    計算公式：
    kWh = W × 小時 ÷ 1000
    """
    # 1 分鐘 = 1 / 60 小時。
    interval_hours = interval_minutes / 60

    # 把一天所有時間點的功率加總。
    total_power_sum_w = data[power_column].sum()

    # 將 W 轉成 kW，並乘上每筆資料代表的時間。
    total_energy_kwh = total_power_sum_w * interval_hours / 1000

    return total_energy_kwh


# ============================================================
# 5. Function：把 1 分鐘資料轉成 15 分鐘資料
# ============================================================

def resample_to_15min(data, columns_to_keep):
    """
    將 1 分鐘一筆的資料轉成 15 分鐘一筆。
    轉換方式使用平均值 mean()。
    """
    # 確保 Time 欄位是時間格式，不然 resample 不能使用。
    data["Time"] = pd.to_datetime(data["Time"], format="%H:%M")

    # 將 Time 設成 index，pandas 才能根據時間重新取樣。
    data_indexed_by_time = data.set_index("Time")

    # 只保留要畫圖或計算的欄位，然後每 15 分鐘取平均。
    data_15min = (
        data_indexed_by_time[columns_to_keep]
        .resample("15min")
        .mean()
        .reset_index()
    )

    return data_15min


# ============================================================
# 6. Function：將總負載轉成 PSO 需要的 24 小時陣列
# ============================================================

def build_hourly_load_for_pso(data):
    """
    PSO 演算法需要 24 筆資料，代表 24 小時的平均負載。
    原始 CSV 是 1 分鐘一筆，所以一天正常會有 1440 筆。
    1440 筆可以分成 24 組，每組 60 分鐘。
    """
    try:
        # 取出 Total_W 欄位，轉成 numpy 陣列。
        total_w_array = data["Total_W"].values

        # reshape(-1, 60) 的意思：每 60 筆分成一組。
        # 因為一小時有 60 分鐘，所以每組就是一小時。
        load_grouped_by_hour = total_w_array.reshape(-1, 60)

        # 每一小時取平均，得到 24 筆小時平均功率。
        hourly_load_w = load_grouped_by_hour.mean(axis=1)

        # PSO 使用 kW，所以 W 要除以 1000。
        hourly_load_kw = hourly_load_w / 1000.0

        return hourly_load_kw

    except Exception:
        # 如果資料不是標準 1440 筆，就用比較簡化的方式處理。
        st.warning("資料格式非標準 1440 筆，使用簡化重取樣。")

        # 直接取前 24 筆，並且從 W 轉成 kW。
        hourly_load_kw = data["Total_W"].values[:24] / 1000.0

        return hourly_load_kw


# ============================================================
# 7. 側邊欄：設備控制與畫面切換
# ============================================================

st.sidebar.title("⚡ 系統大腦核心")

# active_items 用來記錄每個設備目前有沒有被勾選。
# 例如：
# active_items["fridge"] = True 代表冰箱開啟。
# active_items["washer"] = False 代表洗衣機關閉。
active_items = {}

with st.sidebar.expander("🎛️ 設備控制開關", expanded=True):
    # 一個一個設備建立 checkbox。
    for appliance_key, appliance_info in APPLIANCES.items():
        # 取得設備中文名稱。
        appliance_name = appliance_info["name"]

        # 建立 checkbox。
        # value=True 代表一開始預設是勾選狀態。
        is_checked = st.checkbox(
            appliance_name,
            value=True
        )

        # 把使用者勾選結果存進 active_items。
        active_items[appliance_key] = is_checked

# 使用 radio 讓使用者切換「最佳化前」或「最佳化後」。
view_mode = st.sidebar.radio(
    "📌 顯示狀態切換",
    ["PSO 最佳化迭代前", "PSO 最佳化迭代後"],
    index=1
)

st.sidebar.markdown("本區監控 PSO 粒子群演算法的即時運算狀態。")

# 建立一個空位置，之後用來顯示「計算中」或「計算完成」。
status_placeholder = st.sidebar.empty()

# 如果目前選到「PSO 最佳化迭代後」，show_after 就會是 True。
show_after = view_mode == "PSO 最佳化迭代後"


# ============================================================
# 8. 主畫面標題
# ============================================================

st.title("智慧家庭負載模擬與 BESS 最佳化排程")
st.markdown("---")


# ============================================================
# 9. 讀取 CSV 資料
# ============================================================

# 從 CSV 讀取原始家庭用電資料。
df_base = load_base_data(DATA_PATH)

# 如果資料讀取失敗，df_base 會是 None，後面的程式就不執行。
if df_base is not None:

    # ============================================================
    # 10. 步驟一：根據設備開關調整家庭負載
    # ============================================================

    st.subheader("🏠 步驟一：調整家庭負載情境")

    # 複製一份資料。
    # 這樣可以保留原始 df_base，不會直接改到原始資料。
    df_plot = df_base.copy()

    # ------------------------------------------------------------
    # 10-1. 找出目前有勾選，而且 CSV 裡真的存在的設備欄位
    # ------------------------------------------------------------

    # 原本比較短的寫法是：
    # active_keys = [k for k, v in active_items.items() if v and k in df_plot.columns]
    # 下面是展開後比較好懂的版本。

    active_keys = []

    for appliance_key, is_active in active_items.items():
        # appliance_key 是設備欄位名稱，例如 fridge、ac。
        # is_active 是 True 或 False，代表 checkbox 有沒有勾選。

        # 先確認使用者有勾選這個設備。
        if is_active:
            # 再確認 CSV 裡面真的有這個欄位。
            if appliance_key in df_plot.columns:
                # 兩個條件都成立，才把它加入 active_keys。
                active_keys.append(appliance_key)

    # ------------------------------------------------------------
    # 10-2. 將沒有勾選的設備功率歸零
    # ------------------------------------------------------------

    for appliance_key in APPLIANCES.keys():
        # 先確認 CSV 裡面有這個設備欄位。
        if appliance_key in df_plot.columns:
            # 如果這個設備沒有被勾選，就把它整天的功率變成 0。
            if active_items[appliance_key] == False:
                df_plot[appliance_key] = 0

    # ------------------------------------------------------------
    # 10-3. 找出 CSV 裡面真的存在的設備欄位
    # ------------------------------------------------------------

    # 原本比較短的寫法是：
    # available_keys = [k for k in APPLIANCES.keys() if k in df_plot.columns]
    # 下面是展開後比較好懂的版本。

    available_keys = []

    for appliance_key in APPLIANCES.keys():
        # 只有 CSV 裡面有的欄位，才加入 available_keys。
        if appliance_key in df_plot.columns:
            available_keys.append(appliance_key)

    # ------------------------------------------------------------
    # 10-4. 計算每一分鐘的家庭總功率 Total_W
    # ------------------------------------------------------------

    # axis=1 代表「橫向加總」。
    # 也就是同一列裡面，把冰箱、冷氣、熱水器...等設備功率加起來。
    df_plot["Total_W"] = df_plot[available_keys].sum(axis=1)

    # ------------------------------------------------------------
    # 10-5. 計算今日總用電量 kWh，也就是幾度電
    # ------------------------------------------------------------

    daily_energy_kwh = calculate_daily_energy_kwh(
        data=df_plot,
        power_column="Total_W",
        interval_minutes=1
    )

    # 顯示今日用電量和目前啟用設備數。
    metric_col1, metric_col2 = st.columns(2)

    with metric_col1:
        st.metric(
            label="今日家庭總用電量",
            value=f"{daily_energy_kwh:.2f} 度"
        )

    with metric_col2:
        st.metric(
            label="目前啟用設備數",
            value=f"{len(active_keys)} 台"
        )

    # ------------------------------------------------------------
    # 10-6. 繪製家庭設備負載長條圖
    # ------------------------------------------------------------

    if len(active_keys) > 0:
        # 圖表需要使用的欄位：所有設備欄位 + Total_W。
        columns_to_keep = []

        for appliance_key in available_keys:
            columns_to_keep.append(appliance_key)

        columns_to_keep.append("Total_W")

        # 將 1 分鐘資料轉成 15 分鐘資料，避免圖太密集。
        df_15min = resample_to_15min(
            data=df_plot,
            columns_to_keep=columns_to_keep
        )

        # 建立顏色對應表。
        color_map = {}

        for appliance_key, appliance_info in APPLIANCES.items():
            color_map[appliance_key] = appliance_info["color"]

        # 使用 Plotly 畫堆疊長條圖。
        fig_load = px.bar(
            df_15min,
            x="Time",
            y=active_keys,
            color_discrete_map=color_map,
            barmode="stack"
        )

        # 調整圖表外觀。
        fig_load.update_layout(
            hovermode="x unified",
            margin=dict(l=0, r=0, t=30, b=0),
            height=380,
            bargap=0.15,
            xaxis=dict(
                tickformat="%H:%M",
                tickangle=-45,
                dtick=60 * 60 * 1000
            ),
            yaxis_title="Power (W)",
            xaxis_title="Time"
        )

        st.plotly_chart(fig_load, use_container_width=True)

    else:
        st.info("請至少開啟一項設備。")

    # ============================================================
    # 11. 步驟二：把負載資料送進 PSO 演算法
    # ============================================================

    st.markdown("---")
    st.subheader("🤖 步驟二：PSO 儲能排程最佳化")

    # 將每分鐘的 Total_W 轉成 24 小時平均負載，單位是 kW。
    hourly_load = build_hourly_load_for_pso(df_plot)

    # 更新 sidebar 狀態。
    status_placeholder.info("🔄 偵測到負載變更，PSO 重新計算中...")

    with st.spinner("PSO 演算法運算中..."):
        # 將 24 小時負載送進 PSO 核心演算法。
        data_package = hems_pso_core.run_optimization_pipeline(hourly_load)

    status_placeholder.success("✅ 計算完成！")

    # ============================================================
    # 12. 取得 PSO 回傳結果
    # ============================================================

    # 最佳化前的結果。
    before_result = data_package["before_result"]

    # 最佳化後的結果。
    after_result = data_package["after_result"]

    # PSO 收斂曲線。
    convergence_curve = data_package["convergence_curve"]

    # ============================================================
    # 13. 側邊欄：經濟效益評估
    # ============================================================

    st.sidebar.markdown("---")
    st.sidebar.subheader("📊 經濟效益評估")

    # .get("total_cost", 100) 的意思：
    # 如果 before_result 有 total_cost，就拿 total_cost。
    # 如果沒有 total_cost，就暫時用 100 當預設值。
    before_cost = before_result.get("total_cost", 100)
    after_cost = after_result.get("total_cost", 80)

    if show_after:
        # 顯示最佳化後的日電費。
        display_cost = after_cost

        # 計算省下多少錢。
        saved_money = before_cost - after_cost

        # 計算省下百分比。
        saved_percent = saved_money / max(before_cost, 1) * 100

        st.sidebar.metric(
            label="優化後日電費",
            value=f"NT$ {display_cost:.2f}",
            delta=f"- NT$ {saved_money:.2f} ({saved_percent:.1f}%)",
            delta_color="inverse"
        )

    else:
        # 顯示最佳化前的日電費。
        st.sidebar.metric(
            label="優化前日電費",
            value=f"NT$ {before_cost:.2f}",
            delta="尚未套用 PSO 最佳化",
            delta_color="off"
        )

    # ============================================================
    # 14. 主畫面分頁：BESS 狀態與 PSO 收斂歷程
    # ============================================================

    tab_bess, tab_convergence, tab_soc = st.tabs(["🔋 儲能系統 (BESS) 狀態","📉 演算法收斂歷程", "🔋 電池目前 SOC (%)"])

    # ------------------------------------------------------------
    # 14-1. 分頁一：BESS 與電網功率圖
    # ------------------------------------------------------------

    with tab_bess:
        if show_after:
            st.write("#### PSO 最佳化後：電網購電與電池充放電狀態")
            selected_result = after_result
        else:
            st.write("#### PSO 最佳化前：電網購電與電池充放電狀態")
            selected_result = before_result

        hours = range(24)

        # ============================================================
        # 第一張圖：向電網購電功率
        # ============================================================
        grid_df = pd.DataFrame({
            "Hour": list(range(24)),
            "Grid Power": selected_result["p_grid_curve"]
        })

        fig_grid = px.bar(
            grid_df,
            x="Hour",
            y="Grid Power",
            title="向電網購電功率"
        )

        fig_grid.update_layout(
            xaxis_title="Hour",
            yaxis_title="Power (kW)",
            height=500
        )

        st.plotly_chart(fig_grid, use_container_width=True)
        # ============================================================
        # 第二張圖：電池充放電功率
        # ============================================================
        bess_df = pd.DataFrame({
            "Hour": list(range(24)),
            "BESS Power": selected_result["actual_bess_curve"]
        })

        fig_bess = px.bar(
            bess_df,
            x="Hour",
            y="BESS Power",
            title="電池充放電功率"
        )

        fig_bess.update_layout(
            xaxis_title="Hour",
            yaxis_title="Power (kW)",
            height=500
        )

        st.plotly_chart(fig_bess, use_container_width=True)
    # ------------------------------------------------------------
    # 14-2. 分頁二：PSO 收斂歷程
    # ------------------------------------------------------------

    with tab_convergence:
        st.write("#### PSO 最佳化過程")

        conv_df = pd.DataFrame({
            "Iteration": list(range(len(convergence_curve))),
            "Best Fitness": convergence_curve
        })

        fig_conv = px.line(
            conv_df,
            x="Iteration",
            y="Best Fitness",
            title="PSO 收斂曲線"
        )

        fig_conv.update_layout(
            xaxis_title="Iteration",
            yaxis_title="Fitness Value",
            height=500
        )

        st.plotly_chart(fig_conv, use_container_width=True)

    with tab_soc:
        st.write("#### 電池目前 SOC (%)")
        hours = range(24)  # X 軸小時 0~23

        # 選擇最佳化後的資料
        selected_result = after_result if show_after else before_result

        # 建立 DataFrame
        soc_df = pd.DataFrame({
            "Hour": hours,
            "SOC (%)": selected_result["soc_curve"] * 100  # 轉成百分比
        })

        # 畫圖
        fig_soc = px.line(
            soc_df,
            x="Hour",
            y="SOC (%)",
            title="電池 SOC 變化",
            markers=True
        )

        fig_soc.update_layout(
            xaxis_title="Hour",
            yaxis_title="SOC (%)",
            yaxis_range=[0, 100],
            height=500
        )

        st.plotly_chart(fig_soc, use_container_width=True)