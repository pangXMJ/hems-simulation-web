import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path

# 匯入您的核心演算法包
import hems_pso_core

# ============================================================
# 1. 網頁基本設定與資料定義
# ============================================================
st.set_page_config(page_title="智慧家庭系統", layout="wide", page_icon="⚡")

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
    .block-container {
        padding-top: 2rem;
    }
    </style>
    """,
    unsafe_allow_html=True
)

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

BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = BASE_DIR / "daily_appliance_data_1min.csv"


# ============================================================
# 2. 資料讀取與資料處理 Function
# ============================================================
@st.cache_data
def load_base_data(path: str) -> pd.DataFrame | None:
    """讀取 1 分鐘家庭負載資料。若沒有 Time 欄位，會自動補一組 24 小時時間軸。"""
    try:
        df = pd.read_csv(path)

        if "Time" not in df.columns:
            df["Time"] = pd.date_range("2026-05-17", periods=len(df), freq="min")

        df["Time"] = pd.to_datetime(df["Time"])
        return df

    except Exception as e:
        st.error(f"無法讀取資料檔 {path}。錯誤訊息：{e}")
        return None


def build_load_dataframe(df_base: pd.DataFrame, active_items: dict[str, bool]) -> pd.DataFrame:
    """依照側邊欄設備開關，重新計算目前家庭總負載。"""
    df_plot = df_base.copy()

    for key in APPLIANCES:
        if key in df_plot.columns and not active_items.get(key, False):
            df_plot[key] = 0

    available_keys = [key for key in APPLIANCES if key in df_plot.columns]
    df_plot["Total_W"] = df_plot[available_keys].sum(axis=1)

    return df_plot


def resample_load_to_15min(df_plot: pd.DataFrame) -> pd.DataFrame:
    """將 1 分鐘資料轉成 15 分鐘平均資料，讓圖表比較好閱讀。"""
    available_keys = [key for key in APPLIANCES if key in df_plot.columns]
    columns_to_resample = available_keys + ["Total_W"]

    return (
        df_plot
        .set_index("Time")[columns_to_resample]
        .resample("15min")
        .mean()
        .reset_index()
    )


def get_hourly_load_kw(df_plot: pd.DataFrame) -> np.ndarray:
    """將總負載轉成 24 筆小時平均 kW，提供給 PSO 演算法。"""
    total_w = df_plot["Total_W"].to_numpy()

    if len(total_w) >= 1440:
        return total_w[:1440].reshape(24, 60).mean(axis=1) / 1000.0

    st.warning("資料長度不是標準 1440 筆，已用重取樣方式轉成 24 小時資料。")
    temp = df_plot.set_index("Time")["Total_W"].resample("1h").mean().head(24) / 1000.0
    return temp.reindex(range(24), fill_value=temp.mean()).to_numpy()


def result_array(result: dict, possible_keys: list[str], fallback=None) -> np.ndarray:
    """從演算法回傳結果中，用多種可能欄位名稱取得陣列。"""
    for key in possible_keys:
        if key in result and result[key] is not None:
            return np.asarray(result[key], dtype=float)

    if fallback is None:
        return np.array([], dtype=float)

    return np.asarray(fallback, dtype=float)


def normalize_to_24(values: np.ndarray, fallback: np.ndarray | None = None) -> np.ndarray:
    """確保圖表資料是 24 筆。若欄位不存在，使用 fallback。"""
    values = np.asarray(values, dtype=float)

    if values.size == 24:
        return values

    if values.size > 24:
        return values[:24]

    if values.size > 0:
        return np.pad(values, (0, 24 - values.size), mode="edge")

    if fallback is not None:
        fallback = np.asarray(fallback, dtype=float)
        if fallback.size >= 24:
            return fallback[:24]
        if fallback.size > 0:
            return np.pad(fallback, (0, 24 - fallback.size), mode="edge")

    return np.zeros(24)


def calculate_grid_load(total_load_kw: np.ndarray, battery_power_kw: np.ndarray) -> np.ndarray:
    """
    從電表角度看到的家庭負載。

    Battery Power > 0：電池放電，電表看到的用電下降
    Battery Power < 0：電池充電，電表看到的用電上升
    """
    return total_load_kw - battery_power_kw


def calculate_energy_kwh(power_kw: np.ndarray) -> float:
    """24 筆小時功率資料轉成日用電量 kWh。"""
    return float(np.sum(power_kw))


def calculate_battery_usage_kwh(battery_power_kw: np.ndarray) -> tuple[float, float]:
    """分別計算電池總放電量與總充電量。"""
    discharge_kwh = float(np.sum(np.clip(battery_power_kw, 0, None)))
    charge_kwh = float(abs(np.sum(np.clip(battery_power_kw, None, 0))))
    return discharge_kwh, charge_kwh


def calculate_battery_utilization(battery_power_kw: np.ndarray, battery_capacity_kwh: float) -> float:
    """粗略估算電池使用率。"""
    throughput = float(np.sum(np.abs(battery_power_kw)))
    if battery_capacity_kwh <= 0:
        return 0.0
    return throughput / battery_capacity_kwh * 100


# ============================================================
# 3. 圖表 Function
# ============================================================
def plot_appliance_stack(df_15min: pd.DataFrame, active_keys: list[str]):
    """設備堆疊長條圖。"""
    fig = px.bar(
        df_15min,
        x="Time",
        y=active_keys,
        color_discrete_map={key: value["color"] for key, value in APPLIANCES.items()},
        barmode="stack",
    )

    fig.update_layout(
        hovermode="x unified",
        margin=dict(l=0, r=0, t=30, b=0),
        height=380,
        bargap=0.15,
        xaxis=dict(tickformat="%H:%M", tickangle=-45, dtick=60 * 60 * 1000),
        yaxis_title="Power (W)",
        xaxis_title="Time",
        legend_title_text="設備",
    )

    return fig


def plot_battery_usage(hours: np.ndarray, battery_power_kw: np.ndarray, title: str):
    """電池充放電功率圖。正值代表放電，負值代表充電。"""
    fig = go.Figure()

    fig.add_trace(
        go.Bar(
            x=hours,
            y=battery_power_kw,
            name="Battery Power",
            hovertemplate="Hour %{x}<br>Battery Power=%{y:.2f} kW<extra></extra>",
        )
    )

    fig.add_hline(y=0, line_dash="dash")

    fig.update_layout(
        title=title,
        height=360,
        margin=dict(l=0, r=0, t=50, b=0),
        xaxis_title="Hour",
        yaxis_title="Battery Power (kW)",
        hovermode="x unified",
    )

    return fig


def plot_grid_load(hours: np.ndarray, total_load_kw: np.ndarray, grid_load_kw: np.ndarray, title: str):
    """電表視角負載圖：同時顯示原始總負載與電表實際看到的負載。"""
    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=hours,
            y=total_load_kw,
            mode="lines+markers",
            name="Total Load",
            hovertemplate="Hour %{x}<br>Total Load=%{y:.2f} kW<extra></extra>",
        )
    )

    fig.add_trace(
        go.Scatter(
            x=hours,
            y=grid_load_kw,
            mode="lines+markers",
            name="Grid Load",
            hovertemplate="Hour %{x}<br>Grid Load=%{y:.2f} kW<extra></extra>",
        )
    )

    fig.update_layout(
        title=title,
        height=380,
        margin=dict(l=0, r=0, t=50, b=0),
        xaxis_title="Hour",
        yaxis_title="Power (kW)",
        hovermode="x unified",
    )

    return fig


def plot_soc(hours: np.ndarray, soc_percent: np.ndarray):
    """電池電量 SOC 圖。"""
    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=hours,
            y=soc_percent,
            mode="lines+markers",
            name="SOC",
            hovertemplate="Hour %{x}<br>SOC=%{y:.1f}%<extra></extra>",
        )
    )

    fig.update_layout(
        title="電池電量變化（SOC）",
        height=360,
        margin=dict(l=0, r=0, t=50, b=0),
        xaxis_title="Hour",
        yaxis_title="SOC (%)",
        yaxis=dict(range=[0, 100]),
        hovermode="x unified",
    )

    return fig


def plot_optimized_comparison(hours: np.ndarray, original_grid_kw: np.ndarray, optimized_grid_kw: np.ndarray):
    """比較 PSO 前後的電表視角負載。"""
    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=hours,
            y=original_grid_kw,
            mode="lines+markers",
            name="Original Grid Load",
        )
    )

    fig.add_trace(
        go.Scatter(
            x=hours,
            y=optimized_grid_kw,
            mode="lines+markers",
            name="Optimized Grid Load",
        )
    )

    fig.update_layout(
        title="PSO 最佳化前後電表負載比較",
        height=380,
        margin=dict(l=0, r=0, t=50, b=0),
        xaxis_title="Hour",
        yaxis_title="Power (kW)",
        hovermode="x unified",
    )

    return fig


def plot_convergence_curve(convergence_curve):
    """PSO 收斂曲線。"""
    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            y=convergence_curve,
            mode="lines",
            name="Best Fitness",
        )
    )

    fig.update_layout(
        title="PSO 收斂歷程",
        height=360,
        margin=dict(l=0, r=0, t=50, b=0),
        xaxis_title="Iteration",
        yaxis_title="Fitness Value",
        hovermode="x unified",
    )

    return fig


# ============================================================
# 4. 頁面 Function
# ============================================================
def show_before_page(
    hours: np.ndarray,
    total_load_kw: np.ndarray,
    before_battery_kw: np.ndarray,
    before_grid_kw: np.ndarray,
    before_result: dict,
):
    """顯示 PSO 迭代前頁面。"""
    st.header("🔵 迭代前：原始家庭能源使用情況")

    total_kwh = calculate_energy_kwh(total_load_kw)
    discharge_kwh, charge_kwh = calculate_battery_usage_kwh(before_battery_kw)
    before_cost = before_result.get("total_cost", None)
    original_peak = float(np.max(before_grid_kw))

    col1, col2, col3, col4 = st.columns(4)

    col1.metric("總負載耗電量", f"{total_kwh:.2f} kWh")
    col2.metric("電池放電量（正值）", f"{discharge_kwh:.2f} kWh")
    col3.metric("電池充電量（負值）", f"{charge_kwh:.2f} kWh")
    col4.metric("原始尖峰負載", f"{original_peak:.2f} kW")

    if before_cost is not None:
        st.metric("優化前日電費", f"NT$ {before_cost:.2f}")

    st.info("電池功率定義：正值代表放電，負值代表充電。")

    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        st.plotly_chart(
            plot_battery_usage(hours, before_battery_kw, "迭代前電池使用情況"),
            use_container_width=True,
        )

    with chart_col2:
        st.plotly_chart(
            plot_grid_load(hours, total_load_kw, before_grid_kw, "迭代前：從電表看到的家庭負載"),
            use_container_width=True,
        )


def show_after_page(
    hours: np.ndarray,
    total_load_kw: np.ndarray,
    before_grid_kw: np.ndarray,
    after_battery_kw: np.ndarray,
    after_grid_kw: np.ndarray,
    soc_percent: np.ndarray,
    before_result: dict,
    after_result: dict,
    battery_capacity_kwh: float,
):
    """顯示 PSO 迭代後頁面。"""
    st.header("🟢 迭代後：PSO 最佳化調度結果")

    total_kwh = calculate_energy_kwh(total_load_kw)
    before_cost = float(before_result.get("total_cost", 0))
    after_cost = float(after_result.get("total_cost", 0))
    cost_saving = before_cost - after_cost

    original_peak = float(np.max(before_grid_kw))
    optimized_peak = float(np.max(after_grid_kw))
    peak_reduction = original_peak - optimized_peak
    peak_reduction_percent = peak_reduction / max(original_peak, 1e-6) * 100

    battery_utilization = calculate_battery_utilization(after_battery_kw, battery_capacity_kwh)

    col1, col2, col3, col4 = st.columns(4)

    col1.metric("總負載耗電量", f"{total_kwh:.2f} kWh")
    col2.metric("最佳化後日電費", f"NT$ {after_cost:.2f}")
    col3.metric("電費節省", f"NT$ {cost_saving:.2f}")
    col4.metric("尖峰削減", f"{peak_reduction:.2f} kW", f"{peak_reduction_percent:.1f}%")

    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        st.plotly_chart(
            plot_battery_usage(hours, after_battery_kw, "PSO 最佳化後電池充放電功率"),
            use_container_width=True,
        )

    with chart_col2:
        st.plotly_chart(
            plot_soc(hours, soc_percent),
            use_container_width=True,
        )

    st.plotly_chart(
        plot_optimized_comparison(hours, before_grid_kw, after_grid_kw),
        use_container_width=True,
    )

    st.subheader("📌 最佳化成效總結")

    sum_col1, sum_col2, sum_col3, sum_col4 = st.columns(4)
    sum_col1.metric("原始尖峰負載", f"{original_peak:.2f} kW")
    sum_col2.metric("最佳化後尖峰負載", f"{optimized_peak:.2f} kW")
    sum_col3.metric("電池使用率", f"{battery_utilization:.1f}%")
    sum_col4.metric("削峰比例", f"{peak_reduction_percent:.1f}%")


# ============================================================
# 5. Sidebar
# ============================================================
st.sidebar.title("⚡ HEMS 控制面板")

view_mode = st.sidebar.radio(
    "📌 顯示模式切換",
    ["迭代前", "迭代後"],
    index=1,
)

show_after = view_mode == "迭代後"

st.sidebar.markdown("---")
st.sidebar.subheader("🔋 電池參數")
battery_capacity_kwh = st.sidebar.number_input("電池容量（kWh）", min_value=1.0, max_value=100.0, value=10.0, step=1.0)

status_placeholder = st.sidebar.empty()

st.sidebar.markdown("---")
st.sidebar.subheader("🎛️ 設備控制開關")
st.sidebar.caption("勾選以模擬設備運轉")
active_items = {
    key: st.sidebar.checkbox(value["name"], value=True)
    for key, value in APPLIANCES.items()
}


# ============================================================
# 6. 主程式
# ============================================================
st.title("智慧家庭能源管理系統（HEMS）")
st.caption("Home Energy Management System Dashboard：比較 PSO 最佳化前後的家庭負載、電池調度、SOC 與電表視角用電。")
st.markdown("---")

df_base = load_base_data(DATA_PATH)

if df_base is None:
    st.stop()

df_plot = build_load_dataframe(df_base, active_items)
df_15min = resample_load_to_15min(df_plot)

active_keys = [key for key, is_active in active_items.items() if is_active and key in df_plot.columns]

with st.container():
    st.subheader("🏠 家庭負載情境")

    if active_keys:
        st.plotly_chart(
            plot_appliance_stack(df_15min, active_keys),
            use_container_width=True,
        )
    else:
        st.info("請至少開啟一項設備。")
        st.stop()

st.markdown("---")

hourly_load_kw = get_hourly_load_kw(df_plot)

status_placeholder.info("🔄 負載資料已更新，PSO 正在重新計算...")
with st.spinner("PSO 演算法運算中..."):
    data_package = hems_pso_core.run_optimization_pipeline(hourly_load_kw)
status_placeholder.success("✅ PSO 計算完成")

before_result = data_package.get("before_result", {})
after_result = data_package.get("after_result", {})
convergence_curve = data_package.get("convergence_curve", [])

hours = np.arange(24)
total_load_kw = normalize_to_24(hourly_load_kw)

# 迭代前的電池功率：若核心程式沒有回傳，預設為 0，代表尚未調度電池。
before_battery_kw = normalize_to_24(
    result_array(
        before_result,
        ["bess_power", "battery_power", "battery_power_curve", "Battery_Power"],
        fallback=np.zeros(24),
    )
)

# 迭代後的 PSO 電池功率
after_battery_kw = normalize_to_24(
    result_array(
        after_result,
        ["bess_power", "battery_power", "battery_power_curve", "Battery_Power_Optimized"],
        fallback=np.zeros(24),
    )
)

# 若核心程式有回傳 p_grid_curve，優先使用；否則用 Total_Load - Battery_Power 重新計算。
before_grid_from_core = result_array(
    before_result,
    ["p_grid_curve", "grid_power", "grid_load", "Grid_Load"],
)
after_grid_from_core = result_array(
    after_result,
    ["p_grid_curve", "grid_power", "grid_load", "Grid_Load_Optimized"],
)

before_grid_kw = (
    normalize_to_24(before_grid_from_core)
    if before_grid_from_core.size > 0
    else calculate_grid_load(total_load_kw, before_battery_kw)
)

after_grid_kw = (
    normalize_to_24(after_grid_from_core)
    if after_grid_from_core.size > 0
    else calculate_grid_load(total_load_kw, after_battery_kw)
)

soc_percent = normalize_to_24(
    result_array(
        after_result,
        ["soc", "SOC", "soc_curve", "battery_soc"],
        fallback=np.full(24, 50.0),
    )
)

# 如果 SOC 是 0~1 小數，轉成百分比。
if np.nanmax(soc_percent) <= 1.0:
    soc_percent = soc_percent * 100

soc_percent = np.clip(soc_percent, 0, 100)

# Sidebar 經濟效益
st.sidebar.markdown("---")
st.sidebar.subheader("📊 經濟效益評估")

before_cost = float(before_result.get("total_cost", 0))
after_cost = float(after_result.get("total_cost", 0))

if show_after:
    saving = before_cost - after_cost
    saving_percent = saving / max(before_cost, 1e-6) * 100
    st.sidebar.metric(
        label="優化後日電費",
        value=f"NT$ {after_cost:.2f}",
        delta=f"- NT$ {saving:.2f} ({saving_percent:.1f}%)",
        delta_color="inverse",
    )
else:
    st.sidebar.metric(
        label="優化前日電費",
        value=f"NT$ {before_cost:.2f}",
        delta="尚未套用 PSO 最佳化",
        delta_color="off",
    )

st.markdown("---")

if show_after:
    show_after_page(
        hours=hours,
        total_load_kw=total_load_kw,
        before_grid_kw=before_grid_kw,
        after_battery_kw=after_battery_kw,
        after_grid_kw=after_grid_kw,
        soc_percent=soc_percent,
        before_result=before_result,
        after_result=after_result,
        battery_capacity_kwh=battery_capacity_kwh,
    )
else:
    show_before_page(
        hours=hours,
        total_load_kw=total_load_kw,
        before_battery_kw=before_battery_kw,
        before_grid_kw=before_grid_kw,
        before_result=before_result,
    )

if len(convergence_curve) > 0:
    with st.expander("📉 查看 PSO 收斂歷程"):
        st.plotly_chart(plot_convergence_curve(convergence_curve), use_container_width=True)
else:
    st.caption("目前 PSO 核心程式沒有回傳 convergence_curve，因此未顯示收斂曲線。")
