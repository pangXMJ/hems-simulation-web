import streamlit as st

# 設定網頁標題與寬度
st.set_page_config(page_title="HEMS 模擬系統", layout="wide")

st.title("⚡ HEMS 家庭能源312313121312331231231管理系統")
st.write("這是我用 Streamlit 打造的23123123123123123123123第一個網頁介面！")

# 模擬一個簡單的互動按鈕
if st.button("執行 PSO 排程模擬"):
    st.success("模擬執行中...（這裡之後會串接你的 hems_pso_core 運算結果）")