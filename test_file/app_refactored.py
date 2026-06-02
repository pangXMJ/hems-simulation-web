import streamlit as st
import pandas as pd
import plotly.express as px
import matplotlib.pyplot as plt

# 匯入核心演算法模組
import hems_pso_core

# ============================================================
# 0. 全域設定與基礎資料定義
# ============================================================
st.set_page_config(
    page_title="智慧家庭系統",
    layout="wide",
    page_icon="⚡"
)

DATA_PATH = "daily_appliance_data_1min.csv"

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


# ============================================================
# 1. CSS 樣式設定
# ============================================================
def apply_custom_css():
    """設定側邊欄可滑動，避免控制項過多時畫面卡住。"""
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
        unsafe_allow_html=True,
    )


# ============================================================
# 2. 資料處理與演算法邏輯
#    這一區只負責資料讀取、資料轉換、PSO 計算
# ============================================================
@st.cache_data
def load_appliance_data(file_path: str) -> pd.DataFrame | None:
    """讀取 1 分鐘解析度的家庭設備用電資料。"""
    try:
        data = pd.read_csv(file_path)

        # 若原始資料沒有 Time 欄位，則自動建立 1 分鐘時間軸
        if "Time" not in data.columns:
            data["Time"] = pd.date_range(
                "2026-05-17",
                periods=len(data),
                freq="min"
            )

        data["Time"] = pd.to_datetime(data["Time"])
        return data

    except Exception as error:
        st.error(f"無法讀取資料檔 {file_path}。錯誤訊息：{error}")
        return None


def get_available_appliance_keys(data: pd.DataFrame) -> list[str]:
    """取得目前資料中實際存在的設備欄位。"""
    return [key for key in APPLIANCES.keys() if key in data.columns]


def apply_appliance_switches(
    base_data: pd.DataFrame,
    appliance_switches: dict[str, bool]
) -> tuple[pd.DataFrame, list[str], list[str]]:
    """依照側邊欄開關狀態，產生新的負載資料。"""
    load_data = base_data.copy()
    available_keys = get_available_appliance_keys(load_data)

    active_keys = [
        key for key in available_keys
        if appliance_switches.get(key, False)
    ]

    # 關閉未勾選設備的功率
    for key in available_keys:
        if not appliance_switches.get(key, False):
            load_data[key] = 0

    # 計算家庭總負載，單位 W
    load_data["Total_W"] = load_data[available_keys].sum(axis=1)

    return load_data, active_keys, available_keys


def resample_load_to_15min(
    load_data: pd.DataFrame,
    appliance_columns: list[str]
) -> pd.DataFrame:
    """將 1 分鐘負載資料轉換為 15 分鐘平均資料。"""
    columns_to_resample = appliance_columns + ["Total_W"]

    data_15min = (
        load_data
        .set_index("Time")[columns_to_resample]
        .resample("15min")
        .mean()
        .reset_index()
    )

    return data_15min


def convert_total_load_to_hourly_kw(load_data: pd.DataFrame) -> pd.Series:
    """將 1 分鐘總負載轉為 24 小時平均負載，單位由 W 轉為 kW。"""
    total_load_w = load_data["Total_W"].values

    try:
        hourly_load_kw = total_load_w.reshape(-1, 60).mean(axis=1) / 1000.0
    except ValueError:
        st.warning("資料筆數不是標準 1440 筆，改用簡化方式取前 24 筆資料。")
        hourly_load_kw = total_load_w[:24] / 1000.0

    return hourly_load_kw


def run_pso_optimization(hourly_load_kw):
    """執行 PSO 儲能最佳化排程。"""
    return hems_pso_core.run_optimization_pipeline(hourly_load_kw)


def calculate_cost_summary(before_result: dict, after_result: dict) -> dict:
    """整理優化前後的電費與節省比例。"""
    before_cost = before_result.get("total_cost", 0)
    after_cost = after_result.get("total_cost", 0)
    saved_cost = before_cost - after_cost
    saved_percent = saved_cost / max(before_cost, 1) * 100

    return {
        "before_cost": before_cost,
        "after_cost": after_cost,
        "saved_cost": saved_cost,
        "saved_percent": saved_percent,
    }


# ============================================================
# 3. UI 元件函式
#    這一區只負責畫 Streamlit 畫面
# ============================================================
def render_sidebar() -> tuple[dict[str, bool], str, bool, st.delta_generator.DeltaGenerator]:
    """左側邊欄：設備控制、顯示模式切換、演算法狀態。"""
    st.sidebar.title("⚡ 系統大腦核心")
    st.sidebar.caption("控制家庭設備負載，並監控 PSO 粒子群演算法狀態。")

    st.sidebar.markdown("---")

    # 狀態切換
    view_mode = st.sidebar.radio(
        "📌 顯示狀態切換",
        ["PSO 最佳化迭代前", "PSO 最佳化迭代後"],
        index=1,
    )
    show_after = view_mode == "PSO 最佳化迭代後"

    st.sidebar.markdown("---")

    # 設備控制開關
    st.sidebar.subheader("🎛️ 設備控制開關")
    st.sidebar.caption("勾選代表該設備納入家庭負載模擬。")

    appliance_switches = {}
    for key, appliance_info in APPLIANCES.items():
        appliance_switches[key] = st.sidebar.checkbox(
            appliance_info["name"],
            value=True,
            key=f"switch_{key}",
        )

    st.sidebar.markdown("---")
    st.sidebar.subheader("🧠 PSO 運算狀態")
    status_placeholder = st.sidebar.empty()

    return appliance_switches, view_mode, show_after, status_placeholder


def render_header(view_mode: str):
    """上方標題區：系統名稱與目前顯示狀態。"""
    with st.container():
        st.title("智慧家庭負載模擬與 BESS 最佳化排程")
        st.caption("Home Energy Management System, HEMS")

        if view_mode == "PSO 最佳化迭代後":
            st.success("目前狀態：顯示 PSO 最佳化迭代後結果")
        else:
            st.info("目前狀態：顯示 PSO 最佳化迭代前結果")

        st.markdown("---")


def render_load_bar_chart(
    data_15min: pd.DataFrame,
    active_keys: list[str]
):
    """核心展示區：繪製 15 分鐘解析度的家庭負載堆疊長條圖。"""
    st.subheader("🏠 家庭負載情境長條圖")

    if not active_keys:
        st.info("請至少在左側邊欄開啟一項設備。")
        return

    figure = px.bar(
        data_15min,
        x="Time",
        y=active_keys,
        color_discrete_map={key: value["color"] for key, value in APPLIANCES.items()},
        barmode="stack",
    )

    figure.update_layout(
        hovermode="x unified",
        margin=dict(l=0, r=0, t=30, b=0),
        height=390,
        bargap=0.15,
        xaxis=dict(
            tickformat="%H:%M",
            tickangle=-45,
            dtick=60 * 60 * 1000,
        ),
        xaxis_title="Time",
        yaxis_title="Power (W)",
        legend_title_text="設備",
    )

    st.plotly_chart(figure, use_container_width=True)


def render_bess_result_chart(
    selected_result: dict,
    show_after: bool
):
    """核心展示區：繪製優化前或優化後的電網購電 / BESS 狀態圖。"""
    if show_after:
        st.write("#### PSO 最佳化後：電網購電與 BESS 充放電狀態")
        chart_title = "After PSO Optimization"
    else:
        st.write("#### PSO 最佳化前：原始電網購電狀態")
        chart_title = "Before PSO Optimization"

    fig_bess, ax_bess = plt.subplots(figsize=(10, 4))

    if "p_grid_curve" in selected_result:
        ax_bess.plot(
            selected_result["p_grid_curve"],
            label="Grid Power",
            linewidth=2,
        )
    else:
        st.warning("目前結果資料中沒有 p_grid_curve，請確認 hems_pso_core.py 是否有回傳此欄位。")

    if show_after and "bess_power" in selected_result:
        ax_bess.bar(
            range(len(selected_result["bess_power"])),
            selected_result["bess_power"],
            alpha=0.5,
            label="BESS Power",
        )

    ax_bess.set_title(chart_title)
    ax_bess.set_xlabel("Hour")
    ax_bess.set_ylabel("Power (kW)")
    ax_bess.grid(True)
    ax_bess.legend()

    st.pyplot(fig_bess)


def render_convergence_chart(convergence_curve):
    """核心展示區：繪製 PSO 最佳化收斂曲線。"""
    st.write("#### PSO 最佳化收斂歷程")

    fig_conv, ax_conv = plt.subplots(figsize=(10, 4))
    ax_conv.plot(
        convergence_curve,
        linewidth=2,
        label="Best Fitness",
    )
    ax_conv.set_title("PSO Convergence Curve")
    ax_conv.set_xlabel("Iteration")
    ax_conv.set_ylabel("Fitness Value")
    ax_conv.grid(True)
    ax_conv.legend()

    st.pyplot(fig_conv)


def render_sidebar_cost_metric(cost_summary: dict, show_after: bool):
    """左側邊欄：顯示目前切換狀態對應的電費指標。"""
    st.sidebar.markdown("---")
    st.sidebar.subheader("📊 經濟效益評估")

    if show_after:
        st.sidebar.metric(
            label="優化後日電費",
            value=f"NT$ {cost_summary['after_cost']:.2f}",
            delta=(
                f"- NT$ {cost_summary['saved_cost']:.2f} "
                f"({cost_summary['saved_percent']:.1f}%)"
            ),
            delta_color="inverse",
        )
    else:
        st.sidebar.metric(
            label="優化前日電費",
            value=f"NT$ {cost_summary['before_cost']:.2f}",
            delta="尚未套用 PSO 最佳化",
            delta_color="off",
        )


def render_bottom_summary(cost_summary: dict):
    """底部總結區：顯示優化前後電費與節省結果。"""
    st.markdown("---")
    st.subheader("📌 底部總結：經濟效益與電費試算")

    col_before, col_after, col_saved = st.columns(3)

    with col_before:
        st.metric(
            label="優化前日電費",
            value=f"NT$ {cost_summary['before_cost']:.2f}",
        )

    with col_after:
        st.metric(
            label="優化後日電費",
            value=f"NT$ {cost_summary['after_cost']:.2f}",
        )

    with col_saved:
        st.metric(
            label="預估節省金額",
            value=f"NT$ {cost_summary['saved_cost']:.2f}",
            delta=f"{cost_summary['saved_percent']:.1f}%",
            delta_color="inverse",
        )


# ============================================================
# 4. 主程式流程
#    用簡潔的流程串接：讀資料 → UI 控制 → 資料處理 → 演算法 → 畫面輸出
# ============================================================
def main():
    apply_custom_css()

    # ---------- 左側邊欄：控制開關與狀態切換 ----------
    appliance_switches, view_mode, show_after, status_placeholder = render_sidebar()

    # ---------- 上方標題區：系統名稱與目前狀態 ----------
    render_header(view_mode)

    # ---------- 資料讀取 ----------
    base_data = load_appliance_data(DATA_PATH)
    if base_data is None:
        st.stop()

    # ---------- 資料處理：依設備開關產生負載資料 ----------
    load_data, active_keys, available_keys = apply_appliance_switches(
        base_data,
        appliance_switches,
    )
    load_data_15min = resample_load_to_15min(load_data, available_keys)
    hourly_load_kw = convert_total_load_to_hourly_kw(load_data)

    # ---------- 核心展示區：主要負載長條圖 ----------
    with st.container():
        st.subheader("📊 核心展示區")
        render_load_bar_chart(load_data_15min, active_keys)

    # ---------- 演算法運算 ----------
    status_placeholder.info("🔄 偵測到負載情境變更，PSO 重新計算中...")
    with st.spinner("PSO 演算法運算中..."):
        optimization_data = run_pso_optimization(hourly_load_kw)
    status_placeholder.success("✅ 計算完成！")

    before_result = optimization_data["before_result"]
    after_result = optimization_data["after_result"]
    convergence_curve = optimization_data["convergence_curve"]

    selected_result = after_result if show_after else before_result
    cost_summary = calculate_cost_summary(before_result, after_result)

    # ---------- 左側邊欄：經濟效益評估 ----------
    render_sidebar_cost_metric(cost_summary, show_after)

    # ---------- 核心展示區：BESS 狀態與 PSO 收斂分頁 ----------
    with st.container():
        tab_bess, tab_convergence = st.tabs([
            "🔋 儲能系統 BESS 狀態",
            "📉 PSO 收斂歷程",
        ])

        with tab_bess:
            render_bess_result_chart(selected_result, show_after)

        with tab_convergence:
            render_convergence_chart(convergence_curve)

    # ---------- 底部總結區：電費試算 ----------
    render_bottom_summary(cost_summary)


if __name__ == "__main__":
    main()
