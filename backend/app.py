import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import matplotlib.pyplot as plt
from pathlib import Path

# 匯入您的核心演算法包
import hems_pso_core

# ============================================================
# 1. 網頁基本設定與資料定義
# ============================================================
st.set_page_config(page_title="智慧家庭系統", layout="wide", page_icon="⚡")

# ============================================================
# Sidebar 可滑動設定
# ============================================================
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

# 設備清單定義 (保留您的設定)
APPLIANCES = {
    'fridge': {'name': '變頻冰箱', 'color': '#06b6d4'},
    'ac': {'name': '客廳變頻冷氣', 'color': '#3b82f6'},
    'heater': {'name': '儲熱式電熱水器', 'color': '#ea580c'},
    'dehumidifier': {'name': '除濕機 (自動)', 'color': '#22c55e'},
    'dryer': {'name': '熱泵烘衣機', 'color': '#84cc16'},
    'lighting': {'name': '全棟照明', 'color': '#facc15'},
    'washer': {'name': '洗衣機', 'color': '#a855f7'},
    'transferPump': {'name': '揚水馬達', 'color': '#0ea5e9'},
    'boosterPump': {'name': '加壓馬達', 'color': '#f59e0b'},
}

BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = BASE_DIR / "daily_appliance_data_1min.csv"

# ============================================================
# 2. 資料讀取與快取處理
# ============================================================
# 為了避免每次點擊開關都重新讀取大檔案，使用 @st.cache_data
@st.cache_data
def load_base_data(path):
    """讀取基礎的 1 分鐘解析度資料 (假設您的原始資料是長這樣)"""
    try:
        # 這裡請根據您實際的資料格式進行調整
        df = pd.read_csv(path)
        # 確保有 Time 欄位，如果沒有，這裡我們模擬一個作為範例展示
        if 'Time' not in df.columns:
             # 模擬 24小時，1分鐘一筆 = 1440 筆
            df['Time'] = pd.date_range("2026-05-17", periods=len(df), freq="min")
        return df
    except Exception as e:
        st.error(f"無法讀取資料檔 {path}。錯誤訊息: {e}")
        return None

# ============================================================
# 3. 側邊欄：執行狀態看板
# ============================================================
st.sidebar.title("⚡ 系統大腦核心")

with st.sidebar.expander(
    "🎛️ 設備控制開關",
    expanded=True
):
    active_items = {
        k: st.checkbox(
            v['name'],
            value=True
        )
        for k, v in APPLIANCES.items()
    }

st.sidebar.markdown("本區監控 PSO 粒子群演算法的即時運算狀態。")

status_placeholder = st.sidebar.empty()

st.sidebar.markdown("---")

view_mode = st.sidebar.radio(
    "📌 顯示狀態切換",
    ["PSO 最佳化迭代前", "PSO 最佳化迭代後"],
    index=1
)

show_after = view_mode == "PSO 最佳化迭代後"

# ============================================================
# 4. 主畫面 - 上半部：智慧負載模擬區
# ============================================================
st.title("智慧家庭負載模擬與 BESS 最佳化排程")
st.markdown("---")

df_base = load_base_data(DATA_PATH)

if df_base is not None:
    st.subheader("🏠 步驟一：調整家庭負載情境")

    df_plot = df_base.copy()
    active_keys = [k for k, v in active_items.items() if v and k in df_plot.columns]

    # 將沒勾選的設備用電歸零
    for key in APPLIANCES.keys():
        if key in df_plot.columns and not active_items[key]:
            df_plot[key] = 0

    # 計算新的總用電
    available_keys = [k for k in APPLIANCES.keys() if k in df_plot.columns]
    df_plot['Total_W'] = df_plot[available_keys].sum(axis=1)

    # 繪製 Plotly 長條圖
    if active_keys:
        # 確保 Time 欄位是 datetime 格式
        df_plot['Time'] = pd.to_datetime(df_plot['Time'])

        # 將 Time 設為 index，方便做 15 分鐘重取樣
        df_15min = df_plot.set_index('Time')

        # 將 1 分鐘資料轉成 15 分鐘資料，使用平均值
        df_15min = df_15min[available_keys + ['Total_W']].resample('15min').mean().reset_index()

        # 只畫目前有勾選的設備
        fig = px.bar(
            df_15min,
            x='Time',
            y=active_keys,
            color_discrete_map={k: v['color'] for k, v in APPLIANCES.items()},
            barmode='stack'
        )

        # 調整長條圖外觀，避免 X 軸標籤重疊
        fig.update_layout(
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

        st.plotly_chart(fig, use_container_width=True)

    else:
        st.info("請至少開啟一項設備。")


    # ============================================================
    # 5. 銜接 PSO 演算法與資料預處理 (修改後)
    # ============================================================
    st.markdown("---")
    st.subheader("🤖 步驟二：PSO 儲能排程最佳化")

    try:
        # 將 1 分鐘的總負載轉為 24 小時陣列
        hourly_load = df_plot['Total_W'].values.reshape(-1, 60).mean(axis=1) / 1000.0
    except:
        st.warning("資料格式非標準 1440 筆，使用簡化重取樣。")
        hourly_load = df_plot['Total_W'].values[:24] / 1000.0 

    status_placeholder.info("🔄 偵測到負載變更，PSO 重新計算中...")
    with st.spinner("PSO 演算法運算中..."):
        # 直接將 hourly_load 陣列傳入核心演算法，不需經過 CSV
        data_package = hems_pso_core.run_optimization_pipeline(hourly_load)
    
    status_placeholder.success("✅ 計算完成！")


    # ============================================================
    # 6. 主畫面 - 下半部：成果展示
    # ============================================================

    before_result = data_package["before_result"]
    after_result = data_package["after_result"]
    convergence_curve = data_package["convergence_curve"]

    st.sidebar.markdown("---")
    st.sidebar.subheader("📊 經濟效益評估")

    before_cost = before_result.get("total_cost", 100)
    after_cost = after_result.get("total_cost", 80)

    if show_after:
        display_cost = after_cost
        display_label = "優化後日電費"
        display_delta = before_cost - after_cost
        display_delta_percent = display_delta / max(before_cost, 1) * 100

        st.sidebar.metric(
            label=display_label,
            value=f"NT$ {display_cost:.2f}",
            delta=f"- NT$ {display_delta:.2f} ({display_delta_percent:.1f}%)",
            delta_color="inverse"
        )

    else:
        display_cost = before_cost
        display_label = "優化前日電費"

        st.sidebar.metric(
            label=display_label,
            value=f"NT$ {display_cost:.2f}",
            delta="尚未套用 PSO 最佳化",
            delta_color="off"
        )


    # 圖表分頁
    tab1, tab2 = st.tabs(["🔋 儲能系統 (BESS) 狀態", "📉 演算法收斂歷程"])

    with tab1:
        st.write("#### 電池充放電功率與電網購電狀態")
        # 這裡請替換成您實際想畫的內容
        fig_bess, ax1 = plt.subplots(figsize=(10, 4))

        # 假設 after_result 裡有 p_grid 陣列
    with tab1:
        if show_after:
            st.write("#### PSO 最佳化後：電池充放電功率與電網購電狀態")
            selected_result = after_result
            chart_title = "After PSO Optimization"
        else:
            st.write("#### PSO 最佳化前：原始電網購電狀態")
            selected_result = before_result
            chart_title = "Before PSO Optimization"

        fig_bess, ax1 = plt.subplots(figsize=(10, 4))

        if 'p_grid_curve' in selected_result:
            ax1.plot(
                selected_result['p_grid_curve'],
                label='Grid Power',
                linewidth=2
            )
        else:
            st.warning("目前結果資料中沒有 p_grid_curve，請確認 hems_pso_core.py 是否有回傳此欄位。")

        if show_after and 'bess_power' in selected_result:
            ax1.bar(
                range(len(selected_result['bess_power'])),
                selected_result['bess_power'],
                alpha=0.5,
                label='BESS Power'
            )

        ax1.set_title(chart_title)
        ax1.set_xlabel("Hour")
        ax1.set_ylabel("Power (kW)")
        ax1.grid(True)
        ax1.legend()

        st.pyplot(fig_bess)

        # 假設 after_result 裡有 bess 功率陣列
        # ax1.bar(range(24), after_result['bess_power'], label='BESS Power', color='green')

        ax1.set_xlabel("Hour")
        ax1.set_ylabel("Power (kW)")
        ax1.grid(True)
        ax1.legend()
        st.pyplot(fig_bess)

    with tab2:
        st.write("#### PSO 最佳化過程")
        fig_conv, ax_conv = plt.subplots(figsize=(10, 4))
        ax_conv.plot(convergence_curve, color='blue', linewidth=2, label='Best Fitness')
        ax_conv.set_title("PSO 收斂曲線")
        ax_conv.set_xlabel("Iteration")
        ax_conv.set_ylabel("Fitness Value")
        ax_conv.grid(True)
        st.pyplot(fig_conv)