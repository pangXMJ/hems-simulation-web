import pandas as pd
import matplotlib.pyplot as plt
import os

# 解決 matplotlib 中文字體與負號顯示問題
plt.rcParams['font.sans-serif'] = ['Microsoft JhengHei']  # 微軟正黑體 (Windows 預設)
plt.rcParams['axes.unicode_minus'] = False

def plot_bess_comparison():
    base_path = r".\data\sample"
    
    print("📊 正在讀取三階段模擬數據...")
    try:
        df_base = pd.read_csv(os.path.join(base_path, "Baseline_bess_status.csv"))
        df_pso = pd.read_csv(os.path.join(base_path, "Pso_bess_status.csv"))
        df_island = pd.read_csv(os.path.join(base_path, "Island_bess_status.csv"))
    except FileNotFoundError as e:
        print(f"❌ 找不到檔案！請確認您已經先執行過 main_hems.py 產生資料了。\n錯誤細節: {e}")
        return

    # 抓取 X 軸時間標籤
    time_labels = df_base['Time'].tolist()
    x_ticks = range(len(time_labels))

    # 建立畫布 (上下兩張圖)
    plt.figure(figsize=(15, 10))

    # ==========================================
    # 上半部：SOC (%) 變化比較圖
    # ==========================================
    plt.subplot(2, 1, 1)
    plt.plot(df_base['soc'], label='Baseline (淨功率)', linewidth=2.5, alpha=0.8)
    plt.plot(df_pso['soc'], label='PSO (最佳化排程)', linewidth=2.5, linestyle='--')
    plt.plot(df_island['soc'], label='Island (突發停電)', linewidth=2.5, linestyle=':', color='red')
    
    plt.title('🔋 三階段兵推：電池 SOC (%) 變化比較', fontsize=16, fontweight='bold')
    plt.ylabel('SOC (%)', fontsize=14)
    # 畫出 20% 與 100% 的保護紅線
    plt.axhline(y=20, color='gray', linestyle='-.', alpha=0.7, label='20% 保護底線')
    plt.axhline(y=100, color='gray', linestyle='-.', alpha=0.7)
    
    # 設定 X 軸標籤 (每 8 步顯示一次，避免太擠)
    plt.xticks(x_ticks[::8], time_labels[::8], rotation=45)
    plt.legend(fontsize=12, loc='upper right')
    plt.grid(True, alpha=0.3)

    # ==========================================
    # 下半部：電池充放電功率 (kW) 比較圖
    # ==========================================
    plt.subplot(2, 1, 2)
    plt.plot(df_base['power_kw'], label='Baseline (淨功率)', linewidth=2.5, alpha=0.8)
    plt.plot(df_pso['power_kw'], label='PSO (最佳化排程)', linewidth=2.5, linestyle='--')
    plt.plot(df_island['power_kw'], label='Island (突發停電強制 3.0kW)', linewidth=3, linestyle=':', color='red')
    
    plt.title('⚡ 三階段兵推：電池充放電功率 (kW) 比較', fontsize=16, fontweight='bold')
    plt.ylabel('功率 (kW) [>0 放電 / <0 充電]', fontsize=14)
    # 畫出 0kW 的基準線 (區分充放電)
    plt.axhline(y=0, color='black', linewidth=1.5)
    
    plt.xticks(x_ticks[::8], time_labels[::8], rotation=45)
    plt.legend(fontsize=12, loc='upper right')
    plt.grid(True, alpha=0.3)

    plt.tight_layout()
    print("✅ 圖表渲染完成！請查看彈出的視窗。")
    plt.show()

if __name__ == "__main__":
    plot_bess_comparison()