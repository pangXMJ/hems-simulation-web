import streamlit as st
import pandas as pd
import plotly.express as px
import os

# 1. 基礎設定
st.set_page_config(page_title="家庭日負載智慧模擬器", layout="wide", page_icon="⚡")

APPLIANCES = {
    'fridge': {'name': ' 變頻冰箱', 'color': '#06b6d4'},
    'ac': {'name': ' 客廳變頻冷氣', 'color': '#3b82f6'},
    'heater': {'name': ' 儲熱式電熱水器', 'color': '#ea580c'},
    'dehumidifier': {'name': ' 除濕機 (自動)', 'color': '#22c55e'},
    'dryer': {'name': ' 熱泵烘衣機', 'color': '#84cc16'},
    'lighting': {'name': ' 全棟照明 (1F-3F)', 'color': '#facc15'},
    'washer': {'name': ' 洗衣機 (冷水)', 'color': '#a855f7'},
    'transferPump': {'name': '⚡ 揚水馬達', 'color': '#0ea5e9'},
    'boosterPump': {'name': '🔋 加壓馬達', 'color': '#f59e0b'},
}

# 2. 讀取同一個資料夾下的 CSV 資料
@st.cache_data
def load_data():
    if os.path.exists('daily_appliance_data.csv'):
        return pd.read_csv('daily_appliance_data.csv')
    else:
        return None

st.title("⚡ 家庭日用電負載智慧模擬器 (雲端部署版)")
df_1min = load_data()

if df_1min is None:
    st.error("❌ 找不到資料檔 (daily_appliance_data.csv)！請確認檔案有正確上傳到 GitHub。")
else:
    # --- 側邊欄與運算邏輯 ---
    st.sidebar.header("🎛️ 設備控制開關")
    active_items = {k: st.sidebar.checkbox(v['name'], value=True) for k, v in APPLIANCES.items()}
    
    df_plot = df_1min.copy()
    active_keys = [k for k, v in active_items.items() if v]
    for key in APPLIANCES.keys():
        if not active_items[key]: df_plot[key] = 0
    df_plot['Total_W'] = df_plot[list(APPLIANCES.keys())].sum(axis=1)

    # --- 顯示統計面板 ---
    col1, col2 = st.columns(2)
    col1.metric("🔋 預估日總用電量", f"{(df_plot['Total_W'].sum() * (1 / 60)) / 1000:.2f} 度 (kWh)")
    col2.metric("📈 最高瞬間峰值", f"{df_plot['Total_W'].max():,.0f} W")

    # --- 繪製圖表 ---
    if active_keys:
        fig = px.area(df_plot, x='Time', y=active_keys, color_discrete_map={k: v['color'] for k, v in APPLIANCES.items()})
        fig.update_layout(hovermode="x unified")
        fig.for_each_trace(lambda t: t.update(name=APPLIANCES[t.name]['name']))
        st.plotly_chart(fig, use_container_width=True)

    # --- 匯出功能 ---
    st.divider()
    df_export = df_plot.copy()
    df_export['Time_20m'] = df_export.apply(lambda row: f"{int(row['Hour']):02d}:{(int(row['Minute']) // 20) * 20:02d}", axis=1)
    df_20m = df_export.groupby('Time_20m')[list(APPLIANCES.keys()) + ['Total_W']].mean().reset_index().round(1)
    st.download_button("💾 下載 20分鐘一筆 CSV", data=df_20m.to_csv(index=False).encode('utf-8-sig'), file_name='cloud_data_export.csv')
    st.dataframe(df_20m, height=200)
