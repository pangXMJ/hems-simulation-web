 #step1定義頻率跟電源，60HZ跟 台電過來的電=11.4kv，接上變壓器
# step2定義一個變壓器從電源端過來，邊壓器一次側接電源的兩端
# step3統一建立線徑，給line用
# step4設定連接到電錶的系線，並將電表放在變壓器後面
# step5定義負載
# step5定義負載
# step6設定基準並使用solve去執行
import opendssdirect as dss
from hems_basecontrol import run_basecontrol_simulation# 從 hems_basecontrol 這個檔案中，引入 run_basecontrol_simulation 函式
import os

def main():
    print("系統啟動 HEMS 模擬系統\n")

    # ==========================================
    # 執行 basecontrol 模擬 (未經最佳化 / 太陽能自用)
    # ==========================================
    print("開始執行 (basecontrol) 模擬...")
    df_basecontrol,df_warning = run_basecontrol_simulation()#呼叫base

    
    # 統一由主程式負責存檔
    basecontrol_save_path = r".\data\sample\Panel_Common_Nodes_15m_History.csv"
    warning_save_path=r".\data\sample\Panel_Wraning_Report_15m_History.csv"
    try:#不用try以外的方法是為了在存檔時遇到錯誤卻不知道原因時，能夠捕捉到錯誤訊息，並且給出提示，避免程式崩潰。
        df_basecontrol.to_csv(basecontrol_save_path, index=False, encoding='utf-8-sig')#我的檔案是用pandas套件存成csv檔案，所以可以直接寫.to_csv()，並且指定index=False，避免存檔時多出一個索引欄位，encoding='utf-8-sig'是為了讓Excel能夠正確顯示中文。
        print(f"\n✅ 檔案已成功存檔至：{basecontrol_save_path}")
        print("\n=== basecontrol 模擬完成！前 5 筆資料預覽 ===")
        print(df_basecontrol.head(5).to_string(index=False))

        if not df_warning.empty:
            df_warning.to_csv(warning_save_path,index=False,encoding='utf-8-sig')
            print(f"⚠️ [警告] 發現系統過載或電壓異常！共有 {len(df_warning)} 筆紀錄。")
            print(f"✅ 警報報表已存檔至：{warning_save_path}")
            print("\n=== 警報預覽 ===")
            print(df_warning.head(10).to_string(index=False))  
        else:
                print("🎉 [恭喜] 系統體檢通過！全天無任何設備過載或電壓越限。")     
        
    except Exception as e:
        print(f"\n❌ 存檔失敗！電腦給的錯誤原因：{e}")#用except Exception as e:來捕捉所有的錯誤，並且將錯誤訊息存到變數e中，方便後續使用。

    
#印出所有節點的電壓，方便檢查電路是否正確
if __name__ == "__main__":
    main()
    # 取得所有節點名稱與實際電壓
    #node_names = dss.Circuit.AllNodeNames()
    #act_voltages = dss.Circuit.AllBusVMag()
    
    #print("\n=== 🕵️ 變壓器源頭電壓大揭密 ===")
    # 我們只印出前 3 個節點（通常第一個就是電源或變壓器二次側）
    #for i in range(min(60, len(node_names))):
     #   print(f"節點名稱: {node_names[i]:<15} | 真實物理電壓: {act_voltages[i]:.2f} V")
    #print("==================================\n")
