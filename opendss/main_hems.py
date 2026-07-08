# step1定義頻率跟電源，60HZ跟 台電過來的電=11.4kv，接上變壓器
# step2定義一個變壓器從電源端過來，邊壓器一次側接電源的兩端
# step3統一建立線徑，給line用
# step4設定連接到電錶的系線，並將電表放在變壓器後面
# step5定義負載
# step5定義負載
# step6設定基準並使用solve去執行

from hems_basecontrol import run_basecontrol_simulation

def main():
    print("系統啟動 HEMS 模擬系統\n")

    # ==========================================
    # 執行 basecontrol 模擬 (未經最佳化 / 太陽能自用)
    # ==========================================
    print("開始執行對照組 (basecontrol) 模擬...")
    df_basecontrol = run_basecontrol_simulation()
    
    # 統一由主程式負責存檔
    basecontrol_save_path = r".\data\sample\Panel_Common_Nodes_15m_History.csv"
    
    try:
        df_basecontrol.to_csv(basecontrol_save_path, index=False, encoding='utf-8-sig')
        print(f"\n✅ 檔案已成功存檔至：{basecontrol_save_path}")
    except PermissionError:
        print(f"\n❌ 存檔失敗！請檢查是否正在使用 Excel 開啟該 CSV 檔案，關閉後再執行一次。")

    print("\n=== basecontrol 模擬完成！前 5 筆資料預覽 ===")
    print(df_basecontrol.head(5).to_string(index=False))

if __name__ == "__main__":
    main()