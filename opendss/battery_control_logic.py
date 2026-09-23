#battery_control_logic.py
#網站與離線共用的『電池決策大腦』。

#設計原則：
#這裡的函式只負責『算出應該下達什麼指令』，不直接呼叫 dss.Text.Command
#也不知道自己是被離線批次模擬(hems_basecontrol.py)還是即時網站(server.py)呼叫。
#呼叫端拿到回傳的 dict 之後，自己決定怎麼組成 OpenDSS 指令字串並執行
#之後如果要修電池控制邏輯（SOC 保護、三道防線、功率上限...）
#只要改這一份檔案，離線驗證跟即時網站會同時套用到，不會再兩邊不同步。



BATTERY_KWRATED_DEFAULT = 5 #電池的預設 額定充放電功率上限 用在 decide_battery_action() 函式的參數列表裡第25行
DUMP_LOAD_MAX_KW_DEFAULT = 2.0 #假負載（洩載電阻）的最大吸收功率上限，單位 kW

#函數宣告
#Type Hints 型態提示 的寫法
def decide_battery_action(
    is_island_mode: bool,          # 類型：布林值（True 或 False）
    operation_mode: str,           # "AUTO" 或 "PSO"（併網時才有意義，孤島時忽略這個參數）
    net_kw: float,                 # net_kw = pv_kw - load_kw
    soc: float,                    # 決策當下的電池 SOC（百分比，0~100）
    current_pso_kw: float,         # 這一步的 PSO 排程功率（正值=放電，負值=充電）
    load_kw: float = 0.0,          # 這一步的負載功率，孤島模式 PV 降載計算需要用到
    battery_kwrated: float = BATTERY_KWRATED_DEFAULT,
    dump_load_max_kw: float = DUMP_LOAD_MAX_KW_DEFAULT,
    time_str: str = "",
):#剩下全部都是decide_battery_action的內容
    """
    回傳一個 dict，描述電池/假負載/PV 應該被設定成什麼狀態：
    {
        "battery_state": "CHARGING" / "DISCHARGING" / "IDLING",
        "battery_command_kw": float | None,   # 目前已不再使用，保留欄位供未來需要絕對kW時使用
        "battery_command_pct": float | None,  # 所有模式（含孤島）統一用 %Charge / %Discharge 下指令
        "dump_load_kw": float,                # 假負載（只有孤島模式會用到，其餘固定 0）
        "pv_pmpp": float,                      # PV 額定功率上限（孤島降載時會 < 5.0，其餘固定 5.0）
        "logs": [str, ...]                     # 這一步想印出來的除錯訊息，呼叫端自行 print
    }
    """
    result = {
        "battery_state": "IDLING",#電池狀態 "CHARGING"（充電）、"DISCHARGING"（放電）、"IDLING"（待機不動作）這個值會被拿去組成 OpenDSS 指令的 state= 那部分,也會被拿去印 log、判斷要用 %Charge 還是 %Discharge。
        "battery_command_kw": None,#電池的功率指令 這個欄位目前已經停用（我們這一路除錯時,發現 OpenDSS 的 kW 屬性本身自帶方向性,跟 state= 文字衝突,曾經導致充電被誤執行成放電，後來全面改用下面那個 battery_command_pct 取代),現在永遠是 None,保留這個欄位只是為了以後萬一真的需要用絕對 kW 下指令時還有位置可以用，目前不會被任何呼叫端實際使用。
        "battery_command_pct": None,#電池百分比控制指令 目前實際在用的指令欄位——存放「要用幾 % 的充放電速度」這個數字（0~100之間)。呼叫端會用這個數字組成 %Charge=XX 或 %Discharge=XX 這種 OpenDSS 指令。之所以用「百分比」而不是絕對瓦數
        "dump_load_kw": 0.0,#假負載消耗功率
        "pv_pmpp": None,#太陽能最大功率點上限 若維持 None（或主程式判定後給 5.0），代表不限制發電，讓 PV 自由追隨日照發電（MPPT 模式）。
        "logs": [], #（除錯日誌清單）
    }

    # ==========================================
    # mode 1 ：先檢查是不是在孤島模式 網站模擬不會用到
    # ==========================================
    if is_island_mode:#從server.py 或 hems_basecontrol.py）在呼叫這個函式的當下傳進來
        if net_kw > 0.05: #net_kw 在basecontrol的232行有算
            # 太陽能發電過剩 如果電池當前的電量（SOC）小於 99.9%，則「可充電空間」就等於電池的額定最大功率（battery_kwrated）；否則，可充電空間為 0.0
            available_charge_space = battery_kwrated if soc < 99.9 else 0.0 
            charge_need = abs(net_kw)#取絕對值（abs）將淨功率（net_kw）取絕對值（abs），當作 系統的充電量

            # --- 電池最大化吸收 ---
            actual_charge_kw = min(charge_need, available_charge_space) #判斷當前是  電池需要充電的值比較大 還是  available_charge_space = battery_kwrated=battery_kwrated: float = BATTERY_KWRATED_DEFAULT=5
            if actual_charge_kw > 0:#只要大於 0 kW，就代表需要充電
                result["battery_state"] = "CHARGING"#修改上面result裡面的battery_state 變成充電
                result["battery_command_pct"] = round((actual_charge_kw / battery_kwrated) * 100, 2)  # 🆕 改用百分比，跟平常模式一致
            else:
                result["battery_state"] = "IDLING"

            remaining_excess = charge_need - actual_charge_kw #charge_need是淨功率 太陽能過剩多少電（需要被吸收掉的量）56行，actual_charge_kw = 電池實際吸收了多少

            # --- 洩載電阻（假負載）吸收 ---
            actual_dump_kw = min(remaining_excess, dump_load_max_kw)#min(a, b) 取兩個數值中較小的那個
            #actual_dump_kw 是「假負載（洩載電阻）這一步實際要消耗掉多少 kW 的電」
            #remaining_excess：電池充飽/充到極限後，還剩下多少過剩的太陽能電力沒地方去（這是上一行 remaining_excess = charge_need - actual_charge_kw 算出來的）
            #dump_load_max_kw：假負載這個硬體最多能吃多少電（預設 2.0 kW，是這顆洩載電阻的物理上限）
            result["dump_load_kw"] = round(actual_dump_kw, 2)
            remaining_excess -= actual_dump_kw# 這一步結束後 remaining_excess -= actual_dump_kw 例如變成 1 - 1 = 0，代表電已經全部被消化掉，不用進到PV降載

            #如果多出來的電力 洩壓電阻無法處理 就會讓PV降載

            # --- PV 主動降載 ---
            if remaining_excess > 0.05:#在第66行算的 原本過剩電量 − 電池吸收的 − 假負載燒的（前兩道防線都用完後還剩下的量）
                curtailed_pv_kw = load_kw + actual_charge_kw + actual_dump_kw#把三個變數相加，結果存進新變數 curtailed_pv_kw（curtailed = 被削減/限制的）。
                #資料：這行在算「PV 接下來只准發多少電」。邏輯是能量守恆——系統裡的電，發出來多少就要有地方去多少：
                result["pv_pmpp"] = round(curtailed_pv_kw, 2)#把算好的值四捨五入到小數點後2位，寫進 result 這個字典的 "pv_pmpp" 欄位。 語法：round(number, ndigits)參數：number：要處理的數字。ndigits：要取到的小數位數（省略時預設為 0，會傳回整數）。
                result["logs"].append(
                    f" [{time_str}] PV過剩！充 {round(actual_charge_kw,1)}kW，"
                    f"假負載燒 {round(actual_dump_kw,1)}kW，需要降載 PV"
                )
                #語法：result["logs"] 是一個 list（陣列），.append(...) 是 list 的方法，把新元素加到最後面。
                #這裡有兩個 f-string（f"..." 開頭的字串）緊接著寫在一起，Python 會自動把相鄰的字串常值（string literal）拼接成一個字串，不需要用 + 號連接。
                #{time_str}、{round(actual_charge_kw,1)} 這種 {} 內是運算式，Python 會先算出結果再嵌入字串。
            else:#不需要降載的情況
                result["pv_pmpp"] = None #前面（電池+假負載）就已經把過剩電力完全吸收掉了，不需要限制 PV。pv_pmpp = None 代表「不設上限」，PV 可以照原本的額定功率正常發電。
                if actual_dump_kw > 0:
                    result["logs"].append(
                        f"🔥 [{time_str}] 防線作動！充 {round(actual_charge_kw,1)}kW，"
                        f"假負載消耗 {round(actual_dump_kw,1)}kW。"#記錄進去
                    )

        elif net_kw < -0.05:
            # 【太陽能不足，需電池放電】net_kw = pv_kw - load_kw。當這個值小於 -0.05，代表 PV 發的電不夠負載用，孤島系統這時只能靠電池補上，不然負載會斷電。
            # 初始化不要用的資料
            result["dump_load_kw"] = 0.0
            result["pv_pmpp"] = None
            ISLAND_SOC_FLOOR = 5.0 #ISLAND_SOC_FLOOR = 1.0 是宣告一個區域變數（local variable），只在這個函式呼叫的這一次執行中存在，不是存進 result 字典
            #ISLAND_SOC_FLOOR = 1.0 是電池的最低電量 如果變成1%就不會再放電了


            if soc <= ISLAND_SOC_FLOOR: #檢查電池是否已經見底
                result["battery_state"] = "IDLING"
                result["logs"].append(f" [{time_str}] 電池耗盡！無法支撐負載，微電網崩潰。")
            else:
                actual_deficit = abs(net_kw)#電池還有電決定要放多少電
                
                if operation_mode == "PSO" and current_pso_kw > 0:
                    discharge_need = max(current_pso_kw, actual_deficit)
                    result["logs"].append(
                        f" [{time_str}] 孤島+PSO：依排程放電 {round(current_pso_kw,1)}kW（真實缺口 {round(actual_deficit,1)}kW）"
            )
            #為什麼取較大值而不是直接用排程值？
            #因為孤島模式下，供電穩定是第一優先——如果實際缺口比排程要求的還大（例如排程只安排放2kW，但這一刻負載突然暴增缺口變成3kW），系統仍必須多放電來補足，
            #不能死守排程量，不然負載會斷電。所以邏輯是：「至少要滿足排程要求，但如果現實缺口更大，就以現實缺口為準」。
                else:#非 PSO 排程放電的情況
                # baseline/AUTO：沒有規劃能力，維持原本的即時反應
                    discharge_need = actual_deficit # discharge_need（需要放電的量）直接等於 actual_deficit（實際缺口）
                    
                actual_discharge_kw = min(discharge_need, battery_kwrated)#不管需求（discharge_need）是多少，實際放電量都不能超過電池的額定功率 battery_kwrated（預設5kW）

                if discharge_need > battery_kwrated:
                    result["logs"].append(
                        f" [{time_str}] 過載！缺口 ({round(discharge_need,1)}kW) 超過極限 ({battery_kwrated}kW)"
                    ) #如果「需要的放電量」大於「電池能給的上限」，代表這一步電池扛不住，會有電力缺口沒被補上（負載端電壓/頻率可能會不穩）。這裡只是記錄警告 log，程式不會中斷或報錯
                result["battery_state"] = "DISCHARGING"#正式把這一步的決策寫進 result：電池狀態設為「放電中」，並且算出對應的 %Discharge 指令值，供呼叫端（server.py/hems_basecontrol.py）拿去組 OpenDSS 指令字串執行。
                result["battery_command_pct"] = round((actual_discharge_kw / battery_kwrated) * 100.0, 2)  # 🆕 同樣改用百分比
        else:#net_kw 落在平衡區間（|net_kw| ≤ 0.05）既不過剩也不短缺 當 PV 發電跟負載用電幾乎打平（差距在 ±0.05kW 以內），系統不需要做任何動作：電池不充不放（IDLING）、假負載不啟動、PV不受限制。這是「供需平衡」的穩態情況。
            result["battery_state"] = "IDLING"
            result["dump_load_kw"] = 0.0
            result["pv_pmpp"] = None

        return result


        



    # ==========================================
    # mode 2 併網 + AUTO（自發自用，削峰填谷）沒有PSO排程表
    # ==========================================
    if operation_mode == "AUTO":#進入這個模式的條件
        result["dump_load_kw"] = 0.0
        result["pv_pmpp"] = None

        if net_kw > 0.05:#發電過剩（net_kw > 0.05）→ 充電
            if soc >= 99.9:#先判斷「是否過剩」，再判斷「電池滿了沒」
                result["battery_state"] = "IDLING"
            else:
                charge_kw = min(net_kw, battery_kwrated)
                result["battery_state"] = "CHARGING"
                result["battery_command_pct"] = round((charge_kw / battery_kwrated) * 100, 2)

        elif net_kw < -0.05:#發電不足（net_kw < -0.05）→ 放電
            if soc <= 20.0:#併網模式下不需要把電池放到見底，因為就算電池不夠力，還可以跟電網買電補足負載，不會斷電。所以這裡保留 20% 當緩衝
                result["battery_state"] = "IDLING"
            else:
                discharge_kw = min(abs(net_kw), battery_kwrated)
                result["battery_state"] = "DISCHARGING"
                result["battery_command_pct"] = round((discharge_kw / battery_kwrated) * 100, 2)
        else:
            result["battery_state"] = "IDLING"

        return result

    # ==========================================
    # mode 3 ：併網 + PSO（排程模式，含 SOC 保護 + 功率上限保護）
    # ==========================================
    if operation_mode == "PSO":
        result["dump_load_kw"] = 0.0
        result["pv_pmpp"] =  None

        if current_pso_kw > 0.05:
            if soc <= 20.0:
                result["battery_state"] = "IDLING"
            else:
                #useful_discharge_kw 的意思就是：這一步放電，最多能被家裡負載實際用掉多少 kW
                useful_discharge_kw = max(-net_kw, 0.0)#如果 net_kw 是負的（PV不足以應付負載，家裡本身就缺電），-net_kw 就是正的，代表「家裡實際還缺多少電」——這部分放電是有用的，因為放出來的電會被負載直接吃掉。
                #如果 net_kw 是正的（PV發電還有剩，家裡根本不缺電），-net_kw 就是負的，這時 max(-net_kw, 0.0) 會取 0.0——代表「家裡不缺電，放電也沒有負載可以消耗」。
                actual_discharge_kw = min(current_pso_kw, battery_kwrated, useful_discharge_kw)
                #實際放電量同時要滿足三個限制，缺一不可：
                #current_pso_kw：排程要求放多少
                #battery_kwrated：電池硬體能放多少（額定功率上限）
                #useful_discharge_kw：家裡實際能用掉多少（不做超過負載需求的無效放電）
                if current_pso_kw > battery_kwrated:
                    result["logs"].append(
                f" [{time_str}] PSO排程過載！要求 {round(current_pso_kw,1)}kW 超過額定 {battery_kwrated}kW"
            )
                result["battery_state"] = "DISCHARGING"
                result["battery_command_pct"] = round((actual_discharge_kw / battery_kwrated) * 100.0, 2)

        elif current_pso_kw < -0.05:
            if soc >= 99.9:
                result["battery_state"] = "IDLING"
            else:
                actual_charge_kw = min(abs(current_pso_kw), battery_kwrated)
                if abs(current_pso_kw) > battery_kwrated:
                    result["logs"].append(
                        f" [{time_str}] PSO排程過載！要求 {round(abs(current_pso_kw),1)}kW 超過額定 {battery_kwrated}kW"
                    )
                result["battery_state"] = "CHARGING"
                result["battery_command_pct"] = round((actual_charge_kw / battery_kwrated) * 100.0, 2)
        else:
            result["battery_state"] = "IDLING"

        return result

    # 保底：未知的 operation_mode，維持待機，避免整個 API 出錯
    result["logs"].append(f"⚠️ [{time_str}] 未知的 operation_mode='{operation_mode}'，電池維持 IDLING")
    return result