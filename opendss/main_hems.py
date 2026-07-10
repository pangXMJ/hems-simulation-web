# step1定義頻率跟電源，60HZ跟 台電過來的電=11.4kv，接上變壓器
# step2定義一個變壓器從電源端過來，邊壓器一次側接電源的兩端
# step3統一建立線徑，給line用
# step4設定連接到電錶的系線，並將電表放在變壓器後面
# step5定義負載
# step5定義負載
# step6設定基準並使用solve去執行

from hems_basecontrol import run_basecontrol_simulation# 從 hems_basecontrol 這個檔案中，引入 run_basecontrol_simulation 函式

def main():
    print("系統啟動 HEMS 模擬系統\n")

    # ==========================================
    # 執行 basecontrol 模擬 (未經最佳化 / 太陽能自用)
    # ==========================================
    print("開始執行對照組 (basecontrol) 模擬...")
    df_basecontrol = run_basecontrol_simulation()#呼叫base
    
    # 統一由主程式負責存檔
    basecontrol_save_path = r".\data\sample\Panel_Common_Nodes_15m_History.csv"
    
    try:#不用try以外的方法是為了在存檔時遇到錯誤卻不知道原因時，能夠捕捉到錯誤訊息，並且給出提示，避免程式崩潰。
        df_basecontrol.to_csv(basecontrol_save_path, index=False, encoding='utf-8-sig')#我的檔案是用pandas套件存成csv檔案，所以可以直接寫.to_csv()，並且指定index=False，避免存檔時多出一個索引欄位，encoding='utf-8-sig'是為了讓Excel能夠正確顯示中文。
        print(f"\n✅ 檔案已成功存檔至：{basecontrol_save_path}")
        print("\n=== basecontrol 模擬完成！前 5 筆資料預覽 ===")
        print(df_basecontrol.head(5).to_string(index=False))
        
    except Exception as e:
        print(f"\n❌ 存檔失敗！電腦給的錯誤原因：{e}")#用except Exception as e:來捕捉所有的錯誤，並且將錯誤訊息存到變數e中，方便後續使用。

    

if __name__ == "__main__":
    main()