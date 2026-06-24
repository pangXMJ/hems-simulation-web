import opendssdirect as dss
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import os


# step1定義頻率跟電源，60HZ跟 台電過來的電=11.4kv，接上變壓器
# step2定義一個變壓器從電源端過來，邊壓器一次側接電源的兩端
# step3統一建立線徑，給line用
# step4設定連接到電錶的系線，並將電表放在變壓器後面
# step5定義負載
# step5定義負載
# step6設定基準並使用solve去執行

def build_circuit():

    dss.Basic.ClearAll()
    # 1. 讀取 CSV
    df_loads = pd.read_csv(r'C:\Users\User\Desktop\project\dssdata\LoadShapes_All_Nodes_15min.csv')
    #csv檔案的路徑
    df_pv = pd.read_csv(r'C:\Users\User\Desktop\project\dssdata\pv_curve.csv')

    # 轉成 OpenDSS 要求的清單格式串列
    # 假設 CSV 裡的數值是瓦特 (W)，我們除以 1000 轉成 kW
    # 如果你的資料已經是 kW，請把 / 1000.0 拿掉
    # 組裝成 OpenDSS 看得懂的字串
    # 在 OpenDSS 裡，要輸入陣列數值，必須是一長串用逗號隔開、並用中括號包起來的純文字
    # ==========================================
    # 定義所有的用電曲線
    # ==========================================
    loadshape_commands = []
    for col in df_loads.columns:
        # 排除時間欄位，只抓取設備名稱
        if col not in ['Time', 'Hour', 'Minute']:
            mult_list = (df_loads[col] / 1000.0).tolist()
            mult_str = "[" + ",".join(map(str, mult_list)) + "]"
            # npts=96 (總共96筆), minterval=15 (間隔15分鐘)
            cmd = f"New LoadShape.Shape_{col} npts=96 minterval=15 mult={mult_str}"
            loadshape_commands.append(cmd)

    pv_mult_list = df_pv['pv_kw'].tolist()
    pv_mult_str = "[" + ",".join(map(str, pv_mult_list)) + "]"
    # 注意！這裡是 npts=24 (24筆) interval=1 (間隔1小時)
    pv_cmd = f"New LoadShape.PV_Shape npts=24 interval=1 mult={pv_mult_str}"
    loadshape_commands.append(pv_cmd)


    commands=[
        "Clear",
# ==========================================
# 定義電源
# ==========================================
        "Set DefaultBaseFrequency=60",
        # 台電 11.4kV 三相電源
        "New Circuit.PPC basekv=11.4 pu=1.0 phases=3 bus1=SourceBus",
    ]
    commands.extend(loadshape_commands)

    commands.extend(
    [
# ==========================================
# 定義桿上變壓器 桿上變壓器(HomeTransFMR)：11.4kV → 110/220V 單相三線  一次測叫做BusH.1.0 二次側名稱是MeterABN
# ==========================================

        "New Transformer.HomeTransFMR phases=1 windings=3 "  # 定義出單相供電，但它有一個一次側（高壓進線）與二個二次側（低壓出線），windings =3，所以有三組線圈
        "buses=[BusH.1.0, MeterABN.1.0, MeterABN.0.2] "  # buses=[winding1,winding2,winding3]，BusH.1.0：一次側接到高壓的第 1 相（L1）與大地（0），MeterAN.1.0，第一個二次側繞組，一端接火線 1，一端接大地（中性線）。這提供 110V，MeterBN.0.2第一個二次側繞組，一端接火線 2，一端接大地（中性線）。這提供 110V。
        "conns=[wye, wye, wye] "  # 設定windings是用什麼方式連接，wye是星型， Delta三角接法 是(conn=Delta)
        "kVs=[6.6, 0.11, 0.11] "  # 根據變壓器的3個線圈，bush.1.0=6.6kv,busAN.1.0=110V,busBN=.0.2=110V
        "kVAs=[25, 25, 25] "  # 為每個繞組定義容量，=25kv
        "%rs=[0.6, 1.2, 1.2] "  # 變壓器各個繞組的 電阻百分比指銅損 參數來自 IEEE Std C57，會影響變壓器的電壓調整率
        "xhl=2.04 xht=2.04 xlt=1.56",  # 設定繞組間的漏電抗百分比

        # 高壓側連接電源到變壓器
        "New Line.高壓進線 Bus1=SourceBus.1.0 Bus2=BusH.1.0 "  # SourceBus.1.0這條線的起點是接在台電高壓電網的第一相上
        "r1=0.1 x1=0.1 length=0.01 units=km",#約10公尺

# ==================== 建立線徑規格 (LineCode)  ====================

        "New LineCode.wire_100mm2    nphases=2 r1=0.18  x1=0.07 r0=0.18  x0=0.07 units=km",
        # 總進屋線的線徑規格 100mm平方的正序的電阻,電抗,
        "New LineCode.wire_30mm2     nphases=2 r1=0.60  x1=0.08 r0=0.60  x0=0.08 units=km",  # 電錶到各盤的線徑規格
        "New LineCode.wire_8mm2_2p   nphases=2 r1=2.30  x1=0.08 r0=2.30  x0=0.08 units=km",  # EP→LW 幹線
        "New LineCode.wire_2mm2_1p   nphases=1 r1=9.18  x1=0.09 r0=9.18  x0=0.09 units=km",  # 110V 照明
        "New LineCode.wire_5p5mm2_1p nphases=1 r1=3.30  x1=0.08 r0=3.30  x0=0.08 units=km",  # 110V
        "New LineCode.wire_5p5mm2_2p nphases=2 r1=3.30  x1=0.08 r0=3.30  x0=0.08 units=km",  # 220V



# ==========================================
# 接電錶 A
# ==========================================

        # 建立一條極短的「錶前引接線」(Service Drop)
        # 說明：從變壓器 (MeterBus) 拉到電表
        # 為什麼 phases=2？ 因為我們要同時把 L1(.1) 跟 L2(.2) 兩條火線拉進家裡，而 N(.0) 是共用地線。
        # 因為電錶只是感測器，所以不存在接點，使用名為 MeterA,B,C,D 的接點待替
        # dss.Text.Command("New Line.[名稱] Phases=[相數] Bus1=[起點] Bus2=[終點] R1=[正序電阻] X1=[正序電抗] Length=[長度] Units=[單位]")
        # 掛上電錶語法：New EnergyMeter.名稱 Element=元件名稱 Terminal=端子編號

        # 第一組電錶 KwhA，創造一條線到電錶A 叫做 ToMeterA=進屋線，指定從變壓器2次側 MeterABN，拉線過來接，因為電錶不具有實體接點，所以幫他創造一個叫做 MeterA，將電錶 KwhA 掛在線上 ToMeterA
        "New Line.ToMeterA Bus1=MeterABN.1.2 Bus2=MeterA.1.2 LineCode=wire_30mm2 Length=0.002 units=km",  # 2公尺
        "New EnergyMeter.KwhA element=Line.ToMeterA terminal=1",

        # 建立逆變器交流端匯流排 (Inv_AC)，並與 KwhA 連接 (雙向併網路徑)
        "New Line.Inv_AC_Main Bus1=MeterA.1.2 Bus2=Inv_AC.1.2 LineCode=wire_30mm2 Length=0.015 units=km",#如果逆變器裝在頂樓，交流電線要拉到一樓總盤，大約需要 15 公尺


# ==========================================
# 逆變器 DC 端：太陽能與儲能系統 (專門測量發電量的電錶)
# ==========================================

        # 1. 建立一條極短的線，連接 PV 專屬節點與 Inv_AC 匯流排
        "New Line"
        ".ToMeterPV Bus1=PV_Node.1.2 Bus2=Inv_AC.1.2 LineCode=wire_30mm2 Length=0.005 units=km",

        # 2. 將新增的電錶 (MeterPV) 掛在這條短線上，Terminal=1 量測從 PV 流出的電力
        "New EnergyMeter.MeterPV element=Line.ToMeterPV terminal=1",

        # 3. 修正 PVSystem 的連接點，將其接在新建的 PV_Node 上 (原本是直接接 Inv_AC)
        "New PVSystem.PV_Array phases=1 bus1=PV_Node.1.2 kV=0.22 kVA=5.0 pmpp=4.8 pf=1.0 daily=PV_Shape",



# ==========================================
# 儲能電池
# ==========================================

        # kVA: 儲能逆變器的額定容量。 kWrated: 電池連續輸出的最大功率限制。
        # kWhrated 電池的總能量容量（電量），即 20 度電。pf=1.0:
        # 預設功因為 1.0。state=IDLING:
        # 初始狀態設定。IDLING: 待機中，既不充電也不放電。後續可透過指令改為 CHARGING（充電）或 DISCHARGING（放電）。
        "New Storage.Battery_Sys phases=1 bus1=Inv_AC.1.2 kV=0.22 kVA=5.0 kWrated=5.0 kWhrated=20.0 pf=1.0 state=IDLING",

# ==========================================
# ATS 自動轉換開關與 EP 配電盤 (停電備援邏輯)
#===========================================
        # 建立 EP 配電盤的匯流排 EP_Panel.1.2
        # [ATS 切換路徑 1] 平常由市電 (MeterA) 供電給 EP 盤，預設為開啟 (enabled=yes)
        "New Line.ATS_to_EP Bus1=MeterA.1.2 Bus2=EP_panel.1.2 LineCode=wire_30mm2 Length=0.01 units=km enabled=yes",

        # [ATS 切換路徑 2] 停電時由逆變器 (Inv_AC) 供電給 EP 盤，預設為關閉 (enabled=no)
        "New Line.Inv_to_EP Bus1=Inv_AC.1.2 Bus2=EP_Panel.1.2 LineCode=wire_30mm2 Length=0.01 units=km enabled=no",

# ==========================================
# A電錶接負載 EP PANEL (Loads) 設定
# 設定線從電錶 A 出來，接入 EP 的匯流盤
# ==========================================

        # 1F 照明 線路 1，創造一條線叫 EP_Br1，指定從EP panel  拉線過來接，源頭接在 EP_Panel ，尾端幫 負載做一個 做一個接口 叫做 EP_a
        "New Line.EP_Br1 Bus1=EP_Panel.1.0 Bus2=EP_a.1.0 LineCode=wire_2mm2_1p Length=0.008 units=km",#盤體拉到天花板並串聯 8公尺
        "New Load.EP_1F_Lighting1_AN phases=1 Bus1=EP_a.1.0 kV=0.11 kW=1 pf=1 model=1 Daily=Shape_ep_1f_lighting1_an",

        # 1F 照明 線路 2
        "New Line.EP_Br2 Bus1=EP_Panel.2.0 Bus2=EP_b.2.0 LineCode=wire_2mm2_1p Length=0.008 units=km",
        "New Load.EP_1F_Lighting2_BN phases=1 Bus1=EP_b.2.0 kV=0.11 kW=1 pf=1 model=1 Daily=Shape_ep_1f_lighting2_bn",

        # 1F 插座/WiFi 線路 3
        "New Line.EP_Br3 Bus1=EP_Panel.1.0 Bus2=EP_c.1.0 LineCode=wire_5p5mm2_1p Length=0.006 units=km",#盤體拉到牆壁插座6公尺
        "New Load.EP_1F_WiFi_AN phases=1 Bus1=EP_c.1.0 kV=0.11 kW=1 pf=1 model=1 Daily=Shape_ep_1f_wifi_an",

        # 1F 插座  線路 4
        "New Line.EP_Br4 Bus1=EP_Panel.2.0 Bus2=EP_d.2.0 LineCode=wire_5p5mm2_1p Length=0.006 units=km",
        "New Load.EP_1F_socket_BN phases=1 Bus1=EP_d.2.0 kV=0.11 kW=1 pf=1 model=1 Daily=Shape_ep_1f_socket_bn",

        # 1F 廚房專插/冰箱 線路 5
        "New Line.EP_Br5 Bus1=EP_Panel.1.0 Bus2=EP_e.1.0 LineCode=wire_5p5mm2_1p Length=0.01 units=km",#通常在 1F 後方或靠近陽台，距離 1F 盤較遠大概10公尺
        "New Load.EP_Fridge_AN phases=1 Bus1=EP_e.1.0 kV=0.11 kW=1 pf=1 model=1 Daily=Shape_ep_fridge_an",

        # 1F 廚房專插 線路 6
        "New Line.EP_Br6 Bus1=EP_Panel.2.0 Bus2=EP_f.2.0 LineCode=wire_5p5mm2_1p Length=0.01 units=km",
        "New Load.EP_Kitchen_BN phases=1 Bus1=EP_f.2.0 kV=0.11 kW=1 pf=1 model=1 Daily=Shape_ep_kitchen_bn",

        #  EPtoLW 線路 7，創造一條線叫 ToLW，指定從EP panel  拉線過來接，源頭接在 EP_Panel ，尾端幫 配電盤做一個 做一個接口 叫做 LW_panel
        "New Line.ToLW Bus1=EP_Panel.1.2 Bus2=LW_panel.1.2 LineCode=wire_8mm2_2p Length=0.009 units=km",#從一樓拉到三樓大概 9公尺
        #  LW 線路 1，創造一條線叫 Topump，指定從 LW_panel  拉線過來接，源頭接在 LW_panel ，尾端幫 配電盤做一個 做一個接口 叫做 LW_a
        "New Line.Topump Bus1=LW_panel.1.2 Bus2=LW_a.1.2 LineCode=wire_5p5mm2_2p Length=0.002 units=km",#通常很近 2公尺
        "New Load.BoosterPump_ABN phases=1 Bus1=LW_a.1.2 kV=0.22 kW=1 pf=1 model=1 Daily=Shape_boosterpump_abn",

        # 2F 插座  線路 8
        "New Line.EP_Br8 Bus1=EP_Panel.1.0 Bus2=EP_h.1.0 LineCode=wire_5p5mm2_1p Length=0.015 units=km",#從一樓拉到 二樓大概10公尺以上
        "New Load.EP_2F_socket1_AN phases=1 Bus1=EP_h.1.0 kV=0.11 kW=1 pf=1 model=1 Daily=Shape_ep_2f_socket1_an",

        # 2F 插座  線路 9
        "New Line.EP_Br9 Bus1=EP_Panel.2.0 Bus2=EP_i.2.0 LineCode=wire_5p5mm2_1p Length=0.015 units=km",
        "New Load.EP_2F_socket2_BN phases=1 Bus1=EP_i.2.0 kV=0.11 kW=1 pf=1 model=1 Daily=Shape_ep_2f_socket2_bn",

        # 2F 照明  線路 10
        "New Line.EP_Br10 Bus1=EP_Panel.1.0 Bus2=EP_j.1.0 LineCode=wire_2mm2_1p Length=0.02 units=km",#從一樓拉到二樓又需要覆蓋 二樓電燈 20公尺
        "New Load.EP_2F_Lighting1_AN phases=1 Bus1=EP_j.1.0 kV=0.11 kW=1 pf=1 model=1 Daily=Shape_ep_2f_lighting1_an",

        # 2F 插座  線路 11
        "New Line.EP_Br11 Bus1=EP_Panel.2.0 Bus2=EP_k.2.0 LineCode=wire_2mm2_1p Length=0.02 units=km",
        "New Load.EP_2F_Lighting2_BN phases=1 Bus1=EP_k.2.0 kV=0.11 kW=1 pf=1 model=1 Daily=Shape_ep_2f_lighting2_bn",

        # 3F 照明  線路 12
        "New Line.EP_Br12 Bus1=EP_Panel.1.0 Bus2=EP_l.1.0 LineCode=wire_2mm2_1p Length=0.025 units=km",#從一樓拉到三樓又需要覆蓋 三樓電燈 25公尺
        "New Load.EP_3F_Lighting1_AN phases=1 Bus1=EP_l.1.0 kV=0.11 kW=1 pf=1 model=1 Daily=Shape_ep_3f_lighting1_an",

        # 3F 照明  線路 13
        "New Line.EP_Br13 Bus1=EP_Panel.2.0 Bus2=EP_m.2.0 LineCode=wire_2mm2_1p Length=0.025 units=km",
        "New Load.EP_3F_Lighting2_BN phases=1 Bus1=EP_m.2.0 kV=0.11 kW=1 pf=1 model=1 Daily=Shape_ep_3f_lighting2_bn",

        # 3F 加壓馬達 線路14  220V 1/2hp (約 0.4kW)
        "New Line.EP_Br14 Bus1=EP_Panel.1.2 Bus2=EP_n.1.2 LineCode=wire_5p5mm2_2p Length=0.03 units=km",
        "New Load.EP_Pump phases=1 Bus1=EP_n.1.2 kV=0.22 kW=1 pf=1 model=1 Daily=Shape_ep_pump",

        # 3F 插座/洗衣機  線路15
        "New Line.EP_Br15 Bus1=EP_Panel.1.0 Bus2=EP_o.1.0 LineCode=wire_5p5mm2_1p Length=0.03 units=km",
        "New Load.EP_Washer_AN phases=1 Bus1=EP_o.1.0 kV=0.11 kW=1 pf=1 model=1 Daily=Shape_ep_washer_an",

        # 3F 插座/烘衣機  線路16
        "New Line.EP_Br16 Bus1=EP_Panel.2.0 Bus2=EP_p.2.0 LineCode=wire_5p5mm2_1p Length=0.03 units=km",
        "New Load.EP_Dryer_BN phases=1 Bus1=EP_p.2.0 kV=0.11 kW=1 pf=1 model=1 Daily=Shape_ep_dryer_bn",


# ==========================================
# 接電錶 B,C,D
# ==========================================

        # 第二組電錶 KwhB
        # B，創造一條線到電錶B 叫做 ToMeterB=進屋線，指定從變壓器2次側 MeterABN，拉線過來接，因為電錶不具有實體接點，所以幫他創造一個叫做 MeterB，將電錶 KwhB 掛在線上 ToMeterB
        "New Line.ToMeterB Bus1=MeterABN.1.2 Bus2=MeterB.1.2 LineCode=wire_30mm2 Length=0.005 units=km",
        "New EnergyMeter.KwhB element=Line.ToMeterB terminal=1",

        # 第三組電錶 KwhC，創造一條線到電錶C叫做 ToMeterC=進屋線，指定從變壓器2次側 MeterABN，拉線過來接，因為電錶不具有實體接點，所以幫他創造一個叫做 MeterC，將電錶 KwhC 掛在線上 ToMeterC
        "New Line.ToMeterC Bus1=MeterABN.1.2 Bus2=MeterC.1.2 LineCode=wire_30mm2 Length=0.005 units=km",
        "New EnergyMeter.KwhC element=Line.ToMeterC terminal=1",

        # 第四組電錶 KwhD，創造一條線到電錶D叫做 ToMeterD=進屋線，指定從變壓器2次側 MeterABN，拉線過來接，因為電錶不具有實體接點，所以幫他創造一個叫做 MeterD，將電錶 KwhD 掛在線上 ToMeterD
        "New Line.ToMeterD Bus1=MeterABN.1.2 Bus2=MeterD.1.2 LineCode=wire_30mm2 Length=0.005 units=km",
        "New EnergyMeter.KwhD element=Line.ToMeterD terminal=1",

        # 掛在各電錶匯流排對應的進線上，Terminal=1 量測進入方向

# ==========================================
# 設定其他電錶接配電盤的  PANEL 設定
# 設定線從電錶B,C,D出來，接入 PANEL 的匯流盤
# ==========================================

        # KwhB > L1 創造一條線叫 home1F，指定從電錶 KwhB 拉線過來接 源頭接在 MeterB，尾端幫 L1 PANEL 做一個接口 叫做 panel1F
        "New Line.home1F Bus1=MeterB.1.2 Bus2=panel1F.1.2 LineCode=wire_30mm2 Length=0.01 units=km",
        # KwhC > L2 創造一條線叫 home2F，指定從電錶 KwhC 拉線過來接 源頭接在 MeterC，尾端幫 L2 PANEL 做一個接口 叫做 panel2F
        "New Line.home2F Bus1=MeterC.1.2 Bus2=panel2F.1.2 LineCode=wire_30mm2 Length=0.01 units=km",
        # KwhD > L3 創造一條線叫 home3F，指定從電錶 KwhD 拉線過來接 源頭接在 MeterD，尾端幫 L3 PANEL 做一個接口 叫做 panel3F
        "New Line.home3F Bus1=MeterD.1.2 Bus2=panel3F.1.2 LineCode=wire_30mm2 Length=0.01 units=km",

# ==========================================
# B電錶接負載 L1 PANEL (Loads) 設定
# 設定線從電錶 B 出來，接入 L1 的匯流盤
# ==========================================

        # 照明 線路 1，創造一條線叫 L1_Br1，指定從 L1panel  拉線過來接，源頭接在 panel1F ，尾端幫 負載做一個 做一個接口 叫做 L1_a
        "New Line.L1_Br1 Bus1=panel1F.1.0 Bus2=L1_a.1.0 LineCode=wire_2mm2_1p Length=0.01 units=km", # 110,10公尺

        # 創造負載叫 Lighting1_AN 從 L1_a  拉線過來接，接在 L1_a
        # dss.Text.Command("New Load.[負載名稱] phases=[相數] Bus1=[匯流排.節點] kV=[電壓] kW=[功率] pf=[功率因數] model=[模型類型]")
        "New Load.L1_Lighting1_AN  phases=1  Bus1=L1_a.1.0   kV=0.11 kW=1 pf=1 model=1 Daily=Shape_l1_lighting1_an", # 消耗功率 =0.1kw * cvs檔的 mult值

        # 照明 線路 2,L1_Br2, L1_b
        "New Line.L1_Br2 Bus1=panel1F.2.0 Bus2=L1_b.2.0 LineCode=wire_2mm2_1p Length=0.01 units=km",  # 110
        "New Load.L1_Lighting2_BN  phases=1  Bus1=L1_b.2.0   kV=0.11 kW=1 pf=1 model=1 Daily=Shape_l1_lighting2_bn",

        # 插座 線路 3,L1_Br3, L1_c
        "New Line.L1_Br3 Bus1=panel1F.1.0 Bus2=L1_c.1.0 LineCode=wire_5p5mm2_1p Length=0.006 units=km",  # 110,6公尺
        "New Load.L1_socket1_AN  phases=1  Bus1=L1_c.1.0   kV=0.11 kW=1 pf=1 model=1 Daily=Shape_l1_socket1_an",

        # 插座 線路 4,L1_Br4, L1_d
        "New Line.L1_Br4 Bus1=panel1F.2.0 Bus2=L1_d.2.0 LineCode=wire_5p5mm2_1p Length=0.006 units=km",  # 110
        "New Load.L1_socket2_BN  phases=1  Bus1=L1_d.2.0   kV=0.11 kW=1 pf=1 model=1 Daily=Shape_l1_socket2_bn",

        # 插座 線路 5,L1_Br5, L1_e
        "New Line.L1_Br5 Bus1=panel1F.1.0 Bus2=L1_e.1.0 LineCode=wire_5p5mm2_1p Length=0.012 units=km",  # 110
        "New Load.L1_socket3_AN  phases=1  Bus1=L1_e.1.0   kV=0.11 kW=1 pf=1 model=1 Daily=Shape_l1_socket3_an",

        # 插座 線路 6,L1_Br6, L1_f
        "New Line.L1_Br6 Bus1=panel1F.2.0 Bus2=L1_f.2.0 LineCode=wire_5p5mm2_1p Length=0.012 units=km",  # 110
        "New Load.L1_socket4_BN  phases=1  Bus1=L1_f.2.0   kV=0.11 kW=1 pf=1 model=1 Daily=Shape_l1_socket4_bn",

        # 插座 線路 7,L1_Br7, L1_g
        "New Line.L1_Br7 Bus1=panel1F.1.0 Bus2=L1_g.1.0 LineCode=wire_5p5mm2_1p Length=0.08 units=km",  # 110
        "New Load.L1_socket5_AN  phases=1  Bus1=L1_g.1.0   kV=0.11 kW=1 pf=1 model=1 Daily=Shape_l1_socket5_an",

        # 插座 線路 8,L1_Br8, L1_h
        "New Line.L1_Br8 Bus1=panel1F.2.0 Bus2=L1_h.2.0 LineCode=wire_5p5mm2_1p Length=0.08 units=km",  # 110
        "New Load.L1_socket6_BN  phases=1  Bus1=L1_h.2.0   kV=0.11 kW=1 pf=1 model=1 Daily=Shape_l1_socket6_bn",

        # 插座 廚房線路 9,L1_Br9, L1_i
        "New Line.L1_Br9 Bus1=panel1F.1.2 Bus2=L1_i.1.2 LineCode=wire_5p5mm2_2p Length=0.012 units=km",  # 110
        "New Load.L1_Kitchen_ABN  phases=1  Bus1=L1_i.1.2   kV=0.22 kW=1 pf=1 model=1 Daily=Shape_l1_kitchen_abn",

        # 冷氣 線路 10,L1_Br10, L1_j
        "New Line.L1_Br10 Bus1=panel1F.1.2 Bus2=L1_j.1.2 LineCode=wire_5p5mm2_2p Length=0.012 units=km",  # 110
        "New Load.L1_Airc_ABN  phases=1  Bus1=L1_j.1.2   kV=0.22 kW=1 pf=1 model=1 Daily=Shape_l1_airc_abn",



# ==========================================
# C電錶接負載 L2 PANEL (Loads) 設定
# 設定線從電錶 C 出來，接入 L2 的匯流盤
# ==========================================

        # 照明 線路 1，創造一條線叫 L2_Br1，指定從 L2panel  拉線過來接，源頭接在 panel2F ，尾端幫 負載做一個 做一個接口 叫做 L2_a
        "New Line.L2_Br1 Bus1=panel2F.1.0 Bus2=L2_a.1.0 LineCode=wire_2mm2_1p Length=0.01 units=km", #110
        "New Load.L2_Lighting1_AN  phases=1  Bus1=L2_a.1.0   kV=0.11 kW=1 pf=1 model=1 Daily=Shape_l2_lighting1_an",#假設消耗功率 100w


        # 照明 線路 2,L2_Br2, L2_b
        "New Line.L2_Br2 Bus1=panel2F.2.0 Bus2=L2_b.2.0 LineCode=wire_2mm2_1p Length=0.01 units=km", #110
        "New Load.L2_Lighting2_BN  phases=1  Bus1=L2_b.2.0   kV=0.11 kW=1 pf=1 model=1 Daily=Shape_l2_lighting2_bn",

        # 插座 線路 3,L2_Br3, L2_c
        "New Line.L2_Br3 Bus1=panel2F.1.0 Bus2=L2_c.1.0 LineCode=wire_5p5mm2_1p Length=0.006 units=km", #110
        "New Load.L2_socket1_AN  phases=1  Bus1=L2_c.1.0   kV=0.11 kW=1 pf=1 model=1 Daily=Shape_l2_socket1_an",

        # 插座 線路 4,L2_Br4, L2_d
        "New Line.L2_Br4 Bus1=panel2F.2.0 Bus2=L2_d.2.0 LineCode=wire_5p5mm2_1p Length=0.006 units=km", #110
        "New Load.L2_socket2_BN  phases=1  Bus1=L2_d.2.0   kV=0.11 kW=1 pf=1 model=1  Daily=Shape_l2_socket2_bn",

        # 插座 線路 5,L2_Br5, L2_e
        "New Line.L2_Br5 Bus1=panel2F.1.0 Bus2=L2_e.1.0 LineCode=wire_5p5mm2_1p Length=0.006 units=km", #110
        "New Load.L2_socket3_AN  phases=1  Bus1=L2_e.1.0   kV=0.11 kW=1 pf=1 model=1 Daily=Shape_l2_socket3_an",

        # 插座 線路 6,L2_Br6, L2_f
        "New Line.L2_Br6 Bus1=panel2F.2.0 Bus2=L2_f.2.0 LineCode=wire_5p5mm2_1p Length=0.006 units=km", #110
        "New Load.L2_socket4_BN  phases=1  Bus1=L2_f.2.0   kV=0.11 kW=1 pf=1 model=1 Daily=Shape_l2_socket4_bn",

        # 插座 線路 7,L2_Br7, L2_g
        "New Line.L2_Br7 Bus1=panel2F.1.0 Bus2=L2_g.1.0 LineCode=wire_5p5mm2_1p Length=0.006 units=km", #110
        "New Load.L2_socket5_AN  phases=1  Bus1=L2_g.1.0   kV=0.11 kW=1 pf=1 model=1 Daily=Shape_l2_socket5_an",

        # 插座 線路 8,L2_Br8, L2_h
        "New Line.L2_Br8 Bus1=panel2F.2.0 Bus2=L2_h.2.0 LineCode=wire_5p5mm2_1p Length=0.006 units=km", #110
        "New Load.L2_socket6_BN  phases=1  Bus1=L2_h.2.0   kV=0.11 kW=1 pf=1 model=1 Daily=Shape_l2_socket6_bn",

        # 插座 線路 9,L2_Br9, L2_i
        "New Line.L2_Br9 Bus1=panel2F.1.0 Bus2=L2_i.1.0 LineCode=wire_5p5mm2_2p Length=0.006 units=km", #110
        "New Load.L2_socket7_AN  phases=1  Bus1=L2_i.1.0   kV=0.11 kW=1 pf=1 model=1 Daily=Shape_l2_socket7_an",

        # 插座 線路 10,L2_Br10, L2_j
        "New Line.L2_Br10 Bus1=panel2F.2.0 Bus2=L2_j.2.0 LineCode=wire_5p5mm2_2p Length=0.006 units=km", #110
        "New Load.L2_socket8_BN  phases=1  Bus1=L2_j.2.0   kV=0.11 kW=1 pf=1 model=1 Daily=Shape_l2_socket8_bn",


        # 浴室插座 線路 11,L2_Br11, L2_k
        "New Line.L2_Br11 Bus1=panel2F.1.0 Bus2=L2_k.1.0 LineCode=wire_5p5mm2_1p Length=0.01 units=km",
        "New Load.L2_bathroom1_AN  phases=1  Bus1=L2_k.1.0   kV=0.11 kW=1 pf=1 model=1 Daily=Shape_l2_bathroom1_an", # 假設消耗功率 110V 100w

        # 浴室插座 線路 12,L2_Br12, L2_l
        "New Line.L2_Br12 Bus1=panel2F.2.0 Bus2=L2_l.2.0 LineCode=wire_5p5mm2_1p Length=0.01 units=km",
        "New Load.L2_bathroom2_BN  phases=1  Bus1=L2_l.2.0   kV=0.11 kW=1 pf=1 model=1 Daily=Shape_l2_bathroom2_bn", # 假設消耗功率 110V 100w


        # 冷氣 線路 13，創造一條線叫 L2_Br13，指定從 L2panel  拉線過來接，源頭接在 panel2F ，尾端幫 負載做一個 做一個接口 叫做 L2_m
        "New Line.L2_Br13 Bus1=panel2F.1.2 Bus2=L2_m.1.2 LineCode=wire_5p5mm2_2p Length=0.015 units=km",
        "New Load.L2_Airc1_ABN  phases=1  Bus1=L2_m.1.2   kV=0.22 kW=1 pf=1 model=1 Daily=Shape_l2_airc1_abn",# 假設消耗功率 220V 1500w


        # 冷氣 線路 14，創造一條線叫 L2_Br14，指定從 L2panel  拉線過來接，源頭接在 panel2F ，尾端幫 負載做一個 做一個接口 叫做 L2_n
        "New Line.L2_Br14 Bus1=panel2F.1.2 Bus2=L2_n.1.2 LineCode=wire_5p5mm2_2p Length=0.015 units=km",
        "New Load.L2_Airc2_ABN  phases=1  Bus1=L2_n.1.2   kV=0.22 kW=1 pf=1 model=1 Daily=Shape_l2_airc2_abn",# 假設消耗功率 220V 1500w



# ==========================================
# D電錶接負載 L3 PANEL (Loads) 設定
 # 設定線從電錶 D 出來，接入 L3 的匯流盤
# ==========================================


        # 照明 線路 1 ，創造一條線叫 L3_Br1，指定從 L3panel  拉線過來接，源頭接在 panel3F ，尾端幫 負載做一個 做一個接口 叫做 L3_a
        "New Line.L3_Br1 Bus1=panel3F.1.0 Bus2=L3_a.1.0 LineCode=wire_2mm2_1p Length=0.008 units=km",
        "New Load.L3_Lighting1_AN phases=1 Bus1=L3_a.1.0 kV=0.11 kW=1 pf=1 model=1 Daily=Shape_l3_lighting1_an",

        # 照明 線路 2 ，創造一條線叫 L3_Br2，指定從 L3panel  拉線過來接，源頭接在 panel3F ，尾端幫 負載做一個 做一個接口 叫做 L3_b
        "New Line.L3_Br2 Bus1=panel3F.2.0 Bus2=L3_b.2.0 LineCode=wire_2mm2_1p Length=0.008 units=km",
        "New Load.L3_Lighting2_BN phases=1 Bus1=L3_b.2.0 kV=0.11 kW=1 pf=1 model=1 Daily=Shape_l3_lighting2_bn",


        # 插座 線路 3 ，創造一條線叫 L3_Br3，指定從 L3panel  拉線過來接，源頭接在 panel3F ，尾端幫 負載做一個 做一個接口 叫做 L3_c
        "New Line.L3_Br3 Bus1=panel3F.1.0 Bus2=L3_c.1.0 LineCode=wire_5p5mm2_1p Length=0.006 units=km",
        "New Load.L3_socket1_AN phases=1 Bus1=L3_c.1.0 kV=0.11 kW=1 pf=1 model=1 Daily=Shape_l3_socket1_an",

        # 插座 線路 4 ，創造一條線叫 L3_Br4，指定從 L3panel  拉線過來接，源頭接在 panel3F ，尾端幫 負載做一個 做一個接口 叫做 L3_d
        "New Line.L3_Br4 Bus1=panel3F.2.0 Bus2=L3_d.2.0 LineCode=wire_5p5mm2_1p Length=0.006 units=km",
        "New Load.L3_socket2_BN phases=1 Bus1=L3_d.2.0 kV=0.11 kW=1 pf=1 model=1 Daily=Shape_l3_socket2_bn",

        # 插座 線路 5 ，創造一條線叫 L3_Br4，指定從 L3panel  拉線過來接，源頭接在 panel3F ，尾端幫 負載做一個 做一個接口 叫做 L3_e
        "New Line.L3_Br5 Bus1=panel3F.1.0 Bus2=L3_e.1.0 LineCode=wire_5p5mm2_1p Length=0.006 units=km",
        "New Load.L3_socket3_AN phases=1 Bus1=L3_e.1.0 kV=0.11 kW=1 pf=1 model=1 Daily=Shape_l3_socket3_an",

        # 插座 線路 6 ，創造一條線叫 L3_Br5，指定從 L3panel  拉線過來接，源頭接在 panel3F ，尾端幫 負載做一個 做一個接口 叫做 L3_f
        "New Line.L3_Br6 Bus1=panel3F.2.0 Bus2=L3_f.2.0 LineCode=wire_5p5mm2_1p Length=0.006 units=km",
        "New Load.L3_socket4_BN phases=1 Bus1=L3_f.2.0 kV=0.11 kW=1 pf=1 model=1 Daily=Shape_l3_socket4_bn",

        # 插座 線路 7 ，創造一條線叫 L3_Br6，指定從 L3panel  拉線過來接，源頭接在 panel3F ，尾端幫 負載做一個 做一個接口 叫做 L3_g
        "New Line.L3_Br7 Bus1=panel3F.1.0 Bus2=L3_g.1.0 LineCode=wire_5p5mm2_1p Length=0.006 units=km",
        "New Load.L3_socket5_AN phases=1 Bus1=L3_g.1.0 kV=0.11 kW=1 pf=1 model=1 Daily=Shape_l3_socket5_an",

        # 插座 線路 8 ，創造一條線叫 L3_Br7，指定從 L3panel  拉線過來接，源頭接在 panel3F ，尾端幫 負載做一個 做一個接口 叫做 L3_h
        "New Line.L3_Br8 Bus1=panel3F.2.0 Bus2=L3_h.2.0 LineCode=wire_5p5mm2_1p Length=0.006 units=km",
        "New Load.L3_socket6_BN phases=1 Bus1=L3_h.2.0 kV=0.11 kW=1 pf=1 model=1 Daily=Shape_l3_socket6_bn",

        # 插座 線路 9 ，創造一條線叫 L3_Br8，指定從 L3panel  拉線過來接，源頭接在 panel3F ，尾端幫 負載做一個 做一個接口 叫做 L3_i
        "New Line.L3_Br9 Bus1=panel3F.1.0 Bus2=L3_i.1.0 LineCode=wire_5p5mm2_1p Length=0.006 units=km",
        "New Load.L3_socket7_AN phases=1 Bus1=L3_i.1.0 kV=0.11 kW=1 pf=1 model=1 Daily=Shape_l3_socket7_an",

        # 插座 線路 10 ，創造一條線叫 L3_Br9，指定從 L3panel  拉線過來接，源頭接在 panel3F ，尾端幫 負載做一個 做一個接口 叫做 L3_j
        "New Line.L3_Br10 Bus1=panel3F.2.0 Bus2=L3_j.2.0 LineCode=wire_5p5mm2_1p Length=0.006 units=km",
        "New Load.L3_socket8_BN phases=1 Bus1=L3_j.2.0 kV=0.11 kW=1 pf=1 model=1 Daily=Shape_l3_socket8_bn",

        # 冷氣 線路 11 ，創造一條線叫 L3_Br10，指定從 L3panel  拉線過來接，源頭接在 panel3F ，尾端幫 負載做一個 做一個接口 叫做 L3_k
        "New Line.L3_Br11 Bus1=panel3F.1.2 Bus2=L3_k.1.2 LineCode=wire_5p5mm2_2p Length=0.015 units=km",
        "New Load.L3_Airc_ABN phases=1 Bus1=L3_k.1.2 kV=0.22 kW=1 pf=1 model=1 Daily=Shape_l3_airc_abn",



        "Set Voltagebases=[11.4, 6.6, 0.22, 0.11]",
        "CalcVoltageBases",
        "Set mode=Daily stepsize=15m number=1",

        #"Solve",將solve刪除進入迴圈控制
    ])

    return commands


def run_simulation():
    """執行電路建構與步進求解，並以寬表格(Wide Format)整理所有狀態與電錶累積值。"""
    commands = build_circuit()

    for cmd in commands:
        dss.Text.Command(cmd)

    print("✅ 電路建置完成！開始進行 24 小時步進模擬 (Step-by-Step Simulation)...\n")

    dss.Text.Command("Set mode=Daily stepsize=15m number=1")

    total_steps = 96
    history_data = []  # 準備收集寬表格資料的陣列

    # 配電盤定義
    panel_configs = {
        "EP": {"bus": "EP_panel", "line": "Line.ATS_to_EP"},
        "L1": {"bus": "panel1F", "line": "Line.home1F"},
        "L2": {"bus": "panel2F", "line": "Line.home2F"},
        "L3": {"bus": "panel3F", "line": "Line.home3F"}
    }

    # 電錶定義 (對應你 OpenDSS 裡面的名稱)
    meter_targets = {
        "PV電錶": "MeterPV",
        "A電錶": "KwhA",
        "B電錶": "KwhB",
        "C電錶": "KwhC",
        "D電錶": "KwhD"
    }

    for step in range(total_steps):
        dss.Text.Command("Solve")

        # 1. 處理時間字串 (將小數小時 0.25, 0.5 轉換為 0:15, 0:30 等格式)
        current_hour = dss.Solution.DblHour()
        h = int(current_hour)
        m = int(round((current_hour - h) * 60))
        if m == 60:
            h += 1
            m = 0
        time_str = f"{h}:{m:02d}"

        # 建立這一筆時間的資料字典 (這就是未來 CSV 的一列)
        row_dict = {"時間": time_str}

        # 2. 抓取各配電盤的電壓與電流
        for p_name, config in panel_configs.items():

            # [電壓]
            dss.Circuit.SetActiveBus(config["bus"])
            nodes = dss.Bus.Nodes()
            v_mag_ang = dss.Bus.VMagAngle()
            v_dict = {node: v_mag_ang[i * 2] for i, node in enumerate(nodes)}
            v1 = round(v_dict.get(1, 0), 3)
            v2 = round(v_dict.get(2, 0), 3)

            # [電流]
            line_name = config["line"]
            if p_name == "EP":
                dss.Circuit.SetActiveElement("Line.ATS_to_EP")
                if not dss.CktElement.Enabled():
                    line_name = "Line.Inv_to_EP"

            dss.Circuit.SetActiveElement(line_name)
            curr_mag_ang = dss.CktElement.CurrentsMagAng()
            i1 = round(curr_mag_ang[0], 2) if len(curr_mag_ang) > 0 else 0
            i2 = round(curr_mag_ang[2], 2) if len(curr_mag_ang) > 2 else 0

            # 寫入字典 (區分 L1 與 L2)
            row_dict[f"{p_name}_匯流排電壓_L1(V)"] = v1
            row_dict[f"{p_name}_匯流排電壓_L2(V)"] = v2
            row_dict[f"{p_name}_總電流_L1(A)"] = i1
            row_dict[f"{p_name}_總電流_L2(A)"] = i2

        # 3. 抓取各電錶當前的累積用電量 (kWh)
        for ch_name, dss_name in meter_targets.items():
            dss.Meters.Name(dss_name)
            regs = dss.Meters.RegisterValues()
            names = dss.Meters.RegisterNames()
            reg_map = dict(zip(names, regs)) if regs else {}
            # 抓取 kWh，保留小數點後 2 位
            kwh = round(reg_map.get('kWh', 0), 2)
            row_dict[f"{ch_name}_當前累積功率(kWh)"] = kwh

        # 將這一列整合完畢的資料放入總表中
        history_data.append(row_dict)

    # 迴圈結束，轉換為 DataFrame
    df_history = pd.DataFrame(history_data)

    print("=== 模擬完成！匯出寬表格摘要 (前 5 筆) ===")
    print(df_history.head(5).to_string(index=False))

    return df_history


if __name__ == "__main__":
    df_history = run_simulation()
    #show_results()

    # 【修改這裡】指定你要儲存的完整路徑
    save_path = r"C:\Users\User\Desktop\project\dssdata\Panel_Common_Nodes_History.csv"

    # 存檔，並加入 encoding='utf-8-sig' 確保 Excel 開啟不亂碼
    df_history.to_csv(save_path, index=False, encoding='utf-8-sig')

    # 印出提示訊息確認
    print(f"\n檔案已成功存檔至：{save_path}")
