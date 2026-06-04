# HEMS Simulation Web

## 專案簡介

本專案為智慧家庭能源管理系統（HEMS）模擬平台。

利用 Python 與 Streamlit 建立網頁介面，
模擬家庭負載、電池儲能系統以及 PSO 最佳化控制。

---

## 主要功能

- 家庭設備負載模擬
- PSO 最佳化前後比較
- 電池充放電控制
- 電池 SoC 顯示
- 電網購電狀態顯示

---

## 專案架構

app.py # Streamlit 主程式
hems_pso_core.py # PSO 與能源管理邏輯
daily_appliance_data_1min.csv # 家庭設備資料
requirements.txt # Python 套件需求

---

## 安裝方式

pip install -r requirements.txt

---

## 執行方式

streamlit run app.py

---

## 開發環境

Python 3.11
Streamlit
Plotly
Pandas
NumPy

```bash