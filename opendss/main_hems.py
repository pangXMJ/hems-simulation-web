import opendssdirect as dss
import os
from hems_basecontrol import run_simulation # 注意：從 hems_basecontrol 引入的函數名稱已更新為 run_simulation

def time_to_step(time_str):
    """將 HH:MM 格式轉換為 15 分鐘步進的步數 (0-95)"""
    try:
        h, m = map(int, time_str.split(':'))
        step = (h * 60 + m) // 15
        if 0 <= step <= 95:
            return step
        else:
            return -1
    except ValueError:
        return -1

def save_history_and_warning(df_history, df_warning, mode_prefix):
    """統一處理存檔與印出結果的副程式，自動加上前綴避免檔案覆蓋"""
    base_path = r".\data\sample"
    os.makedirs(base_path, exist_ok=True)
    
    history_path = os.path.join(base_path, f"{mode_prefix}_History.csv")
    warning_path = os.path.join(base_path, f"{mode_prefix}_Warning_Report.csv")
    
    try:
        df_history.to_csv(history_path, index=False, encoding='utf-8-sig')
        print(f"✅ [{mode_prefix}] 歷史總表已成功存檔至：{history_path}")

        if not df_warning.empty:
            df_warning.to_csv(warning_path, index=False, encoding='utf-8-sig')
            print(f"⚠️ [警告] {mode_prefix} 模式發現系統過載或電壓異常！共有 {len(df_warning)} 筆紀錄。")
            print(f"✅ 警報報表已存檔至：{warning_path}")
        else:
            print(f"🎉 [恭喜] {mode_prefix} 模式系統體檢通過！全天無任何設備過載或電壓越限。")     
            
    except Exception as e:
        print(f"❌ {mode_prefix} 模式存檔失敗！電腦給的錯誤原因：{e}")


def main():
    print("="*60)
    print("🚀 啟動 HEMS 智慧微電網數位孿生 - 三階段系統")
    print("="*60 + "\n")

    # ==========================================
    # 階段一：Baseline 模擬 (傳統淨功率邏輯)
    # ==========================================
    print("▶️ [階段一] 開始執行 (Baseline) 未經最佳化基準模擬...")
    df_base_history, df_base_warning = run_simulation(mode='baseline')
    save_history_and_warning(df_base_history, df_base_warning, "Baseline")
    
    print("\n" + "-"*60 + "\n")

    # ==========================================
    # 階段二：PSO 最佳化模擬 (讀取排程，正常併網)
    # ==========================================
    print("▶️ [階段二] 開始執行 (PSO) 最佳化排程模擬...")
    df_pso_history, df_pso_warning = run_simulation(mode='pso')
    save_history_and_warning(df_pso_history, df_pso_warning, "Pso")

    print("\n" + "-"*60 + "\n")

    # ==========================================
    # 階段三：動態突發停電模擬 (PSO + Island Mode)
    # ==========================================
    print("▶️ [階段三] 進入 (Island) 突發停電模擬情境設定")
    print("💡 請設定預計的停電時間 (格式為 HH:MM，例如 13:00。輸入 x 則跳過此階段)")
    
    start_time_str = input("👉 請輸入【停電開始】時間: ")
    if start_time_str.lower() != 'x':
        end_time_str = input("👉 請輸入【市電恢復】時間: ")
        
        start_step = time_to_step(start_time_str)
        end_step = time_to_step(end_time_str)
        
        if start_step != -1 and end_step != -1 and start_step < end_step:
            print(f"\n⚠️ 已鎖定停電區間：{start_time_str} (第 {start_step} 步) 至 {end_time_str} (第 {end_step} 步)")
            df_island_history, df_island_warning = run_simulation(mode='island', outage_start_step=start_step, outage_end_step=end_step)
            save_history_and_warning(df_island_history, df_island_warning, "Island")
        else:
            print("\n❌ 輸入的時間格式有誤，或是開始時間大於結束時間，已跳過停電模擬。")
    else:
        print("\n⏭️ 已跳過停電模擬階段。")
        
    print("\n" + "="*60)
    print("🏆 三階段兵推系統執行完畢！所有歷史數據與 01~06 設備資料已備妥。")
    print("="*60 + "\n")

if __name__ == "__main__":
    main()
    

    