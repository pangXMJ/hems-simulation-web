"""
build_taiwan_loadshape.py
放置位置：C:\\projects\\hems-simulation-web\\data\\processed\\

用途（前處理，手動執行，不會被 server.py / scenario_switch.py 自動呼叫）：
  把 NREL 參考住宅 13985（邁阿密）的逐 15 分鐘用電，轉成「台灣平均戶、夏月平日」的設備日負載 pattern，
  輸出成 scenario_switch.py 讀的 data/01_raw/LoadShapes_All_pattern.csv（Time/Hour/Minute + 51 個設備欄位，0~1）。

  系統原本的做法：負載(W) = template(額定W) × pattern
  本程式保證：template × pattern 全天加總 = 2025 臺灣用電百科換算出的台灣平均戶每日用電（夏月 14.27 度）

資料分工：
  NREL 13985      → 每個用途「幾點用電」的形狀（夏月 6~9 月所有平日，同時段取平均）
  2025 用電百科   → 每類電器「一天用幾度」（每戶月用電 × 用電占比 ÷ 30）
  template        → 每個迴路的額定功率（pattern = 功率 ÷ 額定）

流程：
  1. NREL：timestamp 是區間結束時間 → 往前 15 分鐘對齊；取夏月平日，96 個時段各自平均。
     洗衣/烘衣/熱水/烹飪一年只用一兩百次，平均後仍鋸齒 → 75 分鐘移動平均（總量不變）。
  2. 台灣化作息（平日）：白天不在家時段保留 15%，其餘依原曲線比例搬到各設備指定時段（總量不變）；
     出門 07:30~08:30、回家 17:00~19:00 用漸變。
  3. 縮放成用電百科的每日度數（形狀不變、面積換成台灣的數字）。
  4. 依「這個時段人在哪一層」把電量分給各迴路（17~21 點客廳、21~22 點逐步回房、22 點後臥室）。
  5. pattern = 每時段平均功率(W) ÷ template 額定功率(W)。

執行：
  python build_taiwan_loadshape.py
  （要在別的資料夾測試：python build_taiwan_loadshape.py --root D:\\測試資料夾）

輸出：
  data/01_raw/LoadShapes_All_pattern.csv                       ← 系統讀的（覆蓋前會先備份）
  data/processed/LoadShapes_All_pattern_backup_YYYYMMDD_HHMM.csv ← 舊 pattern 備份
  data/processed/loadshape_check.xlsx                          ← 檢查用（參數、各類度數、各迴路度數、曲線、驗證）
  data/processed/loadshape_check.png                           ← 驗證圖

執行後要讓系統用新負載：重新啟動 server.py，或在 07 頁按一次確認。
"""

import argparse
import shutil
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# ============================================================
# 0. 路徑（只有 PROJECT_ROOT 需要依電腦調整，跟其他程式一致）
# ============================================================
PROJECT_ROOT = r"C:\projects\hems-simulation-web"
NREL_FILENAME = "NREL_RefHouse_13985_Miami.csv"
TEMPLATE_FILENAME = "LoadShapes_All_Nodes_15min_template.csv"
PATTERN_FILENAME = "LoadShapes_All_pattern.csv"

# ============================================================
# 1. 時間與季節
# ============================================================
N = 96                          # 一天 96 個 15 分鐘時段
DT_H = 0.25                     # 每時段 0.25 小時
SUMMER_MONTHS = (6, 7, 8, 9)    # 台電夏月 6/1~9/30
DAYS_PER_MONTH = 30.0

# ============================================================
# 2. 2025 臺灣用電百科
# ============================================================
SUMMER_MONTHLY_KWH = 428.0      # 夏月每戶平均月用電（第四章 圖25）
NONSUMMER_MONTHLY_KWH = 305.0   # 非夏月（用來由全年占比換算「只在全年圖出現」的電器）

# 圖13 家庭夏月用電消費流向（%）
SHARE_SUMMER = {
    "冷氣機": 50.42, "電冰箱": 8.91, "電熱水器": 6.78, "照明": 6.49, "電視機": 3.26,
    "電鍋": 3.21, "電熱水瓶": 2.20, "開飲機": 2.02, "除濕機": 1.45, "電腦": 1.38, "其他": 13.89,
}
# 圖12 家庭全年用電消費流向（%）：只取夏月圖沒有、被併入「其他」的四項，假設全年每月用量相同
SHARE_ANNUAL_ONLY = {"吸塵器": 1.74, "數位機上盒": 1.65, "飲水機": 1.34, "吹風機": 1.32}

# 用電百科沒有細分、在「其他」裡的設備：先用「功率 × 時間」估算（⚠️ 假設值，報告要註明）
# 估算總和若超過「其他」剩餘額度，全部按同一比例縮小，讓全屋總量仍等於用電百科
FIXED_DAILY_KWH = {"WiFi": 18 * 24 / 1000 * 0.55}   # template 18 W，路由器平均約 10 W，24 小時，不縮放
ESTIMATED_DAILY_KWH = {
    "洗衣機":   0.25 * 6 / 7,           # 每次約 0.25 度，每週 6 次
    "烘衣機":   1.0 * 1.0 * 1 / 7,      # 1 kW × 1 小時，每週 1 次（台灣烘衣機使用不普遍）
    "電磁爐":   1.5 * 10 / 60,          # 1.5 kW × 每天 10 分鐘
    "揚水馬達": 0.375 * 20 / 60,        # template 375 W × 每天 20 分鐘
    "加壓馬達": 0.375 * 20 / 60,        # template 375 W × 每天 20 分鐘
    "電扇等插座": 0.030 * 8 * 2,        # 電扇 30 W × 8 小時 × 2 台（用電百科：每戶 3.81 台電扇）
    "手機充電": 0.010 * 2.5 * 2,        # 10 W × 2.5 小時 × 2 支
}

# ============================================================
# 3. 台灣化作息參數（平日）
# ============================================================
RETAIN = 0.15                   # 白天不在家時段保留比例（代表白天有人在家的家庭）
LEAVE_RAMP = (7.5, 8.5)         # 07:30~08:30 陸續出門
RETURN_RAMP = (17.0, 19.0)      # 17:00~19:00 陸續回家

# 每類電器：(NREL 形狀來源, 白天移走的電量要搬到的時段)；時段可跨午夜；None = 不移動
CATEGORY_RULES = {
    "冷氣機":     ("cooling",   [(22, 24), (0, 7)]),
    "照明":       ("lighting",  [(18, 23)]),
    "電視機":     ("tv",        [(19, 23)]),
    "數位機上盒": ("tv",        [(19, 23)]),
    "電腦":       ("plug",      [(19, 23)]),
    "洗衣機":     ("washer",    [(20, 23)]),
    "烘衣機":     ("dryer",     [(20, 23)]),
    "電熱水器":   ("hot_water", [(19, 23)]),
    "吹風機":     ("hot_water", [(19, 23)]),     # 洗完澡吹頭髮，借熱水曲線
    "揚水馬達":   ("hot_water", [(19, 23)]),     # 用水時間代理
    "加壓馬達":   ("hot_water", [(19, 23)]),
    "電鍋":       ("cooking",   [(17.5, 19.5)]),
    "電磁爐":     ("cooking",   [(17.5, 19.5)]),
    "電扇等插座": ("plug",      [(18, 23)]),
    "吸塵器":     ("plug",      [(19, 23)]),
    "電冰箱":     ("fridge",    None),
    "電熱水瓶":   ("flat",      None),
    "開飲機":     ("flat",      None),
    "飲水機":     ("flat",      None),
    "除濕機":     ("flat",      None),           # ⚠️ 無資料，假設全天平均運轉
    "WiFi":       ("flat",      None),
    "手機充電":   ("night",     None),           # ⚠️ 假設 22:00~07:00 睡前充電
}

# NREL 欄位（檔案裡是 "..kwh"，讀檔時統一成 ".kwh"）
NREL_USES = {
    "cooling":   ["out.electricity.cooling.energy_consumption.kwh",
                  "out.electricity.cooling_fans_pumps.energy_consumption.kwh"],
    "fridge":    ["out.electricity.refrigerator.energy_consumption.kwh"],
    "hot_water": ["out.electricity.hot_water.energy_consumption.kwh"],
    "lighting":  ["out.electricity.lighting_interior.energy_consumption.kwh"],
    "plug":      ["out.electricity.plug_loads.energy_consumption.kwh"],
    "tv":        ["out.electricity.television.energy_consumption.kwh"],
    "cooking":   ["out.electricity.range_oven.energy_consumption.kwh"],
    "washer":    ["out.electricity.clothes_washer.energy_consumption.kwh"],
    "dryer":     ["out.electricity.clothes_dryer.energy_consumption.kwh"],
}
EVENT_USES_TO_SMOOTH = ("washer", "dryer", "cooking", "hot_water")

# ============================================================
# 4. 人在哪一層（1F 比例，其餘在 2F/3F）
#    (起, 迄, 起點1F比例, 終點1F比例)，中間線性變化
# ============================================================
LOCATION_1F = [
    (0, 6, 0.0, 0.0),       # 睡覺
    (6, 8, 0.3, 0.3),       # 起床準備：以臥室、浴室為主
    (8, 17, 0.5, 0.5),      # 白天留在家的人
    (17, 21, 1.0, 1.0),     # 吃飯休息：客廳
    (21, 22, 1.0, 0.0),     # 陸續回房
    (22, 24, 0.0, 0.0),     # 睡覺
]
LOCATION_1F_LIGHTING = [    # 照明：17~21 點 1F 70%，其他樓層（浴室、樓梯、房間）30%
    (0, 6, 0.0, 0.0),
    (6, 8, 0.3, 0.3),
    (8, 17, 0.5, 0.5),
    (17, 21, 0.7, 0.7),
    (21, 22, 0.7, 0.0),
    (22, 24, 0.0, 0.0),
]
UPSTAIRS_SPLIT = {"2F": 2 / 3, "3F": 1 / 3}   # 2F 兩間臥室、3F 一間 → 2:1

# 照明迴路：每層 EP 一半、一般一半，各半裡依額定功率比例分
LIGHTING_BY_FLOOR = {
    "1F": {"EP": ["ep_1f_lighting1_an", "ep_1f_lighting2_bn"], "L": ["l1_lighting1_an", "l1_lighting2_bn"]},
    "2F": {"EP": ["ep_2f_lighting1_an", "ep_2f_lighting2_bn"], "L": ["l2_lighting1_an", "l2_lighting2_bn"]},
    "3F": {"EP": ["ep_3f_lighting1_an", "ep_3f_lighting2_bn"], "L": ["l3_lighting1_an", "l3_lighting2_bn"]},
}
BEDROOM_AIRC = ["l2_airc1_abn", "l2_airc2_abn", "l3_airc_abn"]   # 睡覺時段三間平均


# ============================================================
# 工具函式
# ============================================================
def slot_mid_hours() -> np.ndarray:
    return np.arange(N) / 4 + 0.125


def window_mask(windows) -> np.ndarray:
    t = slot_mid_hours()
    m = np.zeros(N, dtype=bool)
    for a, b in windows:
        m |= (t >= a) & (t < b)
    return m


def piecewise(schedule) -> np.ndarray:
    t = slot_mid_hours()
    out = np.zeros(N)
    for a, b, va, vb in schedule:
        sel = (t >= a) & (t < b)
        out[sel] = va + (vb - va) * (t[sel] - a) / (b - a)
    return out


def circular_smooth(x: np.ndarray, width: int = 5) -> np.ndarray:
    pad = width // 2
    return np.convolve(np.r_[x[-pad:], x, x[:pad]], np.ones(width) / width, mode="valid")


# ============================================================
# 步驟 1：NREL → 夏月平日 96 點平均功率（kW）
# ============================================================
def load_nrel_profiles(path: Path) -> tuple[dict, int]:
    df = pd.read_csv(path, encoding="utf-8-sig")
    df.columns = [c.replace("..kwh", ".kwh").replace("..c", ".c") for c in df.columns]
    t = pd.to_datetime(df["timestamp"], format="%Y/%m/%d %H:%M") - pd.Timedelta(minutes=15)
    keep = t.dt.month.isin(SUMMER_MONTHS) & (t.dt.dayofweek < 5)
    slot = (t.dt.hour * 4 + t.dt.minute // 15)[keep].to_numpy()
    n_days = t[keep].dt.date.nunique()

    profiles = {}
    for use, cols in NREL_USES.items():
        missing = [c for c in cols if c not in df.columns]
        if missing:
            raise KeyError(f"NREL 檔案缺少欄位：{missing}")
        kw = df.loc[keep, cols].sum(axis=1).to_numpy() / DT_H
        prof = pd.Series(kw).groupby(slot).mean().reindex(range(N), fill_value=0).to_numpy()
        profiles[use] = circular_smooth(prof) if use in EVENT_USES_TO_SMOOTH else prof
    profiles["flat"] = np.ones(N)
    profiles["night"] = window_mask([(22, 24), (0, 7)]).astype(float)
    return profiles, n_days


# ============================================================
# 步驟 2：用電百科 → 台灣平均戶夏月每日各類 kWh
# ============================================================
def taiwan_daily_kwh() -> tuple[dict, dict]:
    daily = {k: SUMMER_MONTHLY_KWH * v / 100 / DAYS_PER_MONTH for k, v in SHARE_SUMMER.items() if k != "其他"}
    annual_total = SUMMER_MONTHLY_KWH * len(SUMMER_MONTHS) + NONSUMMER_MONTHLY_KWH * (12 - len(SUMMER_MONTHS))
    for k, v in SHARE_ANNUAL_ONLY.items():
        daily[k] = annual_total * v / 100 / 12 / DAYS_PER_MONTH

    # 「其他」用「每日總量 - 已列出類別」算，吸收圖13 占比四捨五入的誤差（加總為 100.01%）
    other_total = SUMMER_MONTHLY_KWH / DAYS_PER_MONTH - sum(
        daily[k] for k in SHARE_SUMMER if k != "其他")
    pool = other_total - sum(daily[k] for k in SHARE_ANNUAL_ONLY) - sum(FIXED_DAILY_KWH.values())
    est = sum(ESTIMATED_DAILY_KWH.values())
    scale = min(1.0, pool / est)
    daily.update(FIXED_DAILY_KWH)
    for k, v in ESTIMATED_DAILY_KWH.items():
        daily[k] = v * scale
    leftover = pool - est * scale       # 估算不足額度時，剩餘併入「電扇等插座」
    daily["電扇等插座"] += max(leftover, 0.0)

    target = SUMMER_MONTHLY_KWH / DAYS_PER_MONTH
    assert abs(sum(daily.values()) - target) < 1e-9, "類別加總不等於用電百科每日用電"
    info = {"其他_總額度": other_total, "其他_可分配額度": pool, "假設值估算合計": est, "假設值縮放比例": scale}
    return daily, info


# ============================================================
# 步驟 3：台灣化（平日白天保留 RETAIN，其餘依比例搬到目標時段）
# ============================================================
def presence_factor() -> np.ndarray:
    (l0, l1), (r0, r1) = LEAVE_RAMP, RETURN_RAMP
    return piecewise([
        (0, l0, 1, 1), (l0, l1, 1, RETAIN), (l1, r0, RETAIN, RETAIN), (r0, r1, RETAIN, 1), (r1, 24, 1, 1),
    ])


def taiwanize(shape: np.ndarray, windows) -> np.ndarray:
    shape = shape.astype(float)
    if windows is None:
        return shape.copy()
    f = presence_factor()
    removed = (shape * (1 - f)).sum()
    kept = shape * f
    w = kept * window_mask(windows)
    if w.sum() <= 0:                    # 目標時段原本沒有用電 → 平均灑
        w = window_mask(windows).astype(float)
    return kept + circular_smooth(removed * w / w.sum())


# ============================================================
# 步驟 4：分配到迴路（回傳 {欄位: 96 點 W}）
# ============================================================
def allocate(cat_w: dict, rated: dict, columns: list) -> dict:
    out = {c: np.zeros(N) for c in columns}
    loc1f = piecewise(LOCATION_1F)
    loc1f_light = piecewise(LOCATION_1F_LIGHTING)

    def add(col, arr):
        if col not in out:
            raise KeyError(f"template 沒有欄位 {col}")
        out[col] += arr

    # 冷氣：1F 份額給客廳，其餘三間臥室平均
    ac = cat_w["冷氣機"]
    add("l1_airc_abn", ac * loc1f)
    for c in BEDROOM_AIRC:
        add(c, ac * (1 - loc1f) / len(BEDROOM_AIRC))

    # 照明：樓層 → EP/一般各半 → 各半依額定比例
    light = cat_w["照明"]
    floor_share = {"1F": loc1f_light,
                   "2F": (1 - loc1f_light) * UPSTAIRS_SPLIT["2F"],
                   "3F": (1 - loc1f_light) * UPSTAIRS_SPLIT["3F"]}
    for floor, groups in LIGHTING_BY_FLOOR.items():
        for cols in groups.values():
            tot = sum(rated[c] for c in cols)
            for c in cols:
                add(c, light * floor_share[floor] * 0.5 * rated[c] / tot)

    # 插座
    add("l1_socket1_an", cat_w["電視機"] + cat_w["數位機上盒"])
    add("l2_socket1_an", cat_w["電腦"] / 2)
    add("l3_socket1_an", cat_w["電腦"] / 2)
    fan = cat_w["電扇等插座"]
    add("l1_socket2_bn", fan * loc1f)
    add("l2_socket2_bn", fan * (1 - loc1f) * UPSTAIRS_SPLIT["2F"])
    add("l3_socket2_bn", fan * (1 - loc1f) * UPSTAIRS_SPLIT["3F"])
    add("ep_2f_socket1_an", cat_w["手機充電"])
    add("l1_socket3_an", cat_w["吸塵器"])

    # 固定迴路
    add("ep_fridge_an", cat_w["電冰箱"])
    add("ep_kitchen_bn", cat_w["電鍋"] + cat_w["電熱水瓶"] + cat_w["開飲機"] + cat_w["飲水機"])
    add("l1_InductionCooktop_abn", cat_w["電磁爐"])
    add("ep_1f_WaterHeater_abn", cat_w["電熱水器"])
    add("ep_1f_wifi_an", cat_w["WiFi"])
    add("ep_washer_an", cat_w["洗衣機"])
    add("ep_dryer_bn", cat_w["烘衣機"])
    add("ep_pump", cat_w["揚水馬達"])
    add("ep_boosterpump_abn", cat_w["加壓馬達"])
    add("l2_Dehumidifier_bn", cat_w["除濕機"])
    add("l2_bathroom2_bn", cat_w["吹風機"])
    return out


# ============================================================
# 主程式
# ============================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=PROJECT_ROOT, help="專案根目錄（預設 PROJECT_ROOT）")
    args = ap.parse_args()
    root = Path(args.root)
    raw_dir = root / "data" / "01_raw"
    proc_dir = root / "data" / "processed"
    proc_dir.mkdir(parents=True, exist_ok=True)

    # --- 讀 template ---
    tpl = pd.read_csv(raw_dir / TEMPLATE_FILENAME)
    columns = [c for c in tpl.columns if c not in ("Time", "Hour", "Minute")]
    rated = {c: float(tpl[c].max()) for c in columns}

    # --- 步驟 1~3 ---
    profiles, n_days = load_nrel_profiles(raw_dir / NREL_FILENAME)
    daily_kwh, other_info = taiwan_daily_kwh()
    print(f"NREL：夏月平日 {n_days} 天取平均")

    cat_w, curve_rows = {}, {}
    for cat, (src, windows) in CATEGORY_RULES.items():
        raw = profiles[src]
        adj = taiwanize(raw, windows)
        cat_w[cat] = adj / adj.sum() * daily_kwh[cat] / DT_H * 1000           # W
        curve_rows[f"{cat}_原始W"] = raw / raw.sum() * daily_kwh[cat] / DT_H * 1000
        curve_rows[f"{cat}_台灣化W"] = cat_w[cat]

    # --- 步驟 4~5 ---
    dev_w = allocate(cat_w, rated, columns)
    df_w = pd.DataFrame(dev_w)[columns]
    df_pattern = df_w / pd.Series(rated)[columns]

    # --- 驗證 ---
    total_w = df_w.sum(axis=1)
    total_kwh = total_w.sum() * DT_H / 1000
    target_kwh = SUMMER_MONTHLY_KWH / DAYS_PER_MONTH
    peak_slot = int(total_w.idxmax())
    peak_hour = peak_slot / 4
    checks = [
        ("全屋每日用電 = 用電百科", f"{total_kwh:.3f} 度 / 目標 {target_kwh:.3f} 度",
         abs(total_kwh - target_kwh) < 1e-6),
        ("各類電器每日用電 = 用電百科換算",
         f"最大誤差 {max(abs(cat_w[c].sum() * DT_H / 1000 - daily_kwh[c]) for c in cat_w):.2e} 度",
         all(abs(cat_w[c].sum() * DT_H / 1000 - daily_kwh[c]) < 1e-9 for c in cat_w)),
        ("pattern 全部 ≥ 0", f"最小 {df_pattern.min().min():.6f}", bool((df_pattern >= 0).all().all())),
        ("pattern 全部 ≤ 1", f"最大 {df_pattern.max().max():.3f}（{df_pattern.max().idxmax()}）",
         bool((df_pattern <= 1).all().all())),
        ("全屋尖峰落在 18:00~23:00", f"尖峰 {tpl['Time'].iloc[peak_slot]}，{total_w.max() / 1000:.2f} kW",
         18 <= peak_hour < 23),
    ]
    ok = all(c[2] for c in checks)
    for name, val, passed in checks:
        print(f"[{'通過' if passed else '失敗'}] {name}：{val}")
    if not ok:
        raise SystemExit("❌ 驗證未通過，沒有寫出 pattern，請依上面訊息調整參數")

    # --- 輸出 pattern（先備份舊檔）---
    pattern_path = raw_dir / PATTERN_FILENAME
    if pattern_path.exists():
        backup = proc_dir / f"LoadShapes_All_pattern_backup_{datetime.now():%Y%m%d_%H%M}.csv"
        shutil.copy2(pattern_path, backup)
        print(f"舊 pattern 已備份：{backup}")
    out = pd.concat([tpl[["Time", "Hour", "Minute"]], df_pattern.round(6)], axis=1)
    out.to_csv(pattern_path, index=False)
    print(f"✅ 已寫出：{pattern_path}")

    # --- 檢查用 Excel ---
    write_check_excel(proc_dir / "loadshape_check.xlsx", tpl, columns, rated, df_w, df_pattern,
                      daily_kwh, other_info, curve_rows, checks, n_days)
    write_check_png(proc_dir / "loadshape_check.png", tpl, cat_w, curve_rows, total_w)
    print(f"檢查檔：{proc_dir / 'loadshape_check.xlsx'}、{proc_dir / 'loadshape_check.png'}")
    print("提醒：重新啟動 server.py 或在 07 頁按一次確認，系統才會用新的負載。")


# ============================================================
# 檢查檔
# ============================================================
def write_check_excel(path, tpl, columns, rated, df_w, df_pattern, daily_kwh, other_info, curve_rows, checks, n_days):
    params = pd.DataFrame([
        ("NREL 資料", NREL_FILENAME), ("取樣", f"夏月（6~9 月）平日 {n_days} 天平均"),
        ("台灣平均戶夏月月用電", f"{SUMMER_MONTHLY_KWH} 度（用電百科）"),
        ("每日目標", f"{SUMMER_MONTHLY_KWH / DAYS_PER_MONTH:.3f} 度"),
        ("平日白天保留比例", RETAIN), ("出門漸變", f"{LEAVE_RAMP[0]}~{LEAVE_RAMP[1]} 時"),
        ("回家漸變", f"{RETURN_RAMP[0]}~{RETURN_RAMP[1]} 時"),
        ("「其他」總額度(度/日)", round(other_info["其他_總額度"], 4)),
        ("「其他」扣除吸塵器等後可分配(度/日)", round(other_info["其他_可分配額度"], 4)),
        ("假設值估算合計(度/日)", round(other_info["假設值估算合計"], 4)),
        ("假設值縮放比例", round(other_info["假設值縮放比例"], 4)),
    ], columns=["項目", "值"])

    src = {k: "用電百科 夏月占比" for k in SHARE_SUMMER}
    src.update({k: "用電百科 全年占比（假設各月相同）" for k in SHARE_ANNUAL_ONLY})
    src.update({k: "假設值（固定）" for k in FIXED_DAILY_KWH})
    src.update({k: "假設值（功率×時間，已縮放）" for k in ESTIMATED_DAILY_KWH})
    cat_table = pd.DataFrame([{
        "類別": k, "每日度數": round(v, 4), "來源": src.get(k, ""),
        "NREL 形狀": CATEGORY_RULES[k][0],
        "白天電量移到": "不移動" if CATEGORY_RULES[k][1] is None else
                    "、".join(f"{a:g}~{b:g}" for a, b in CATEGORY_RULES[k][1]),
    } for k, v in sorted(daily_kwh.items(), key=lambda x: -x[1])])

    dev_table = pd.DataFrame([{
        "欄位": c, "額定W(template)": rated[c], "每日度數": round(df_w[c].sum() * DT_H / 1000, 4),
        "平均W": round(df_w[c].mean(), 2), "最大W": round(df_w[c].max(), 2),
        "pattern最大值": round(df_pattern[c].max(), 4), "是否使用": "是" if df_w[c].sum() > 0 else "給 0",
    } for c in columns])

    head = tpl[["Time"]].reset_index(drop=True)
    curves = pd.concat([head, pd.DataFrame(curve_rows).round(2)], axis=1)
    circuit_w = pd.concat([head, df_w.round(2), pd.Series(df_w.sum(axis=1).round(2), name="全屋W")], axis=1)
    pattern = pd.concat([head, df_pattern.round(6)], axis=1)
    check_df = pd.DataFrame([(n, v, "通過" if p else "失敗") for n, v, p in checks], columns=["項目", "結果", "判定"])

    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, d in [("驗證", check_df), ("參數", params), ("各類電器度數", cat_table),
                        ("各迴路度數", dev_table), ("台灣化前後曲線", curves),
                        ("各迴路功率W", circuit_w), ("pattern", pattern)]:
            d.to_excel(xw, sheet_name=name, index=False)
            ws = xw.sheets[name]
            ws.freeze_panes = "B2"
            for col in ws.columns:
                width = max(len(str(col[0].value)), *(len(str(c.value)) for c in col[1:20])) + 2
                ws.column_dimensions[col[0].column_letter].width = min(width * 1.2, 45)


def write_check_png(path, tpl, cat_w, curve_rows, total_w):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    for f in ("Microsoft JhengHei", "Noto Sans CJK TC", "Noto Sans CJK JP", "PingFang TC"):
        if any(f in x.name for x in font_manager.fontManager.ttflist):
            plt.rcParams["font.sans-serif"] = [f]
            break
    plt.rcParams["axes.unicode_minus"] = False
    h = np.arange(N) / 4
    fig, axes = plt.subplots(2, 3, figsize=(15, 8))
    for ax, cat in zip(axes.flat[:5], ["冷氣機", "照明", "電熱水器", "電視機", "電鍋"]):
        ax.plot(h, curve_rows[f"{cat}_原始W"] / 1000, color="#9a9a9a", lw=1.5, label="NREL 原始形狀")
        ax.plot(h, curve_rows[f"{cat}_台灣化W"] / 1000, color="#1f6feb", lw=2, label="台灣化後")
        ax.set_title(f"{cat}")
    ax = axes.flat[5]
    ax.plot(h, total_w / 1000, color="#d1242f", lw=2.2)
    ax.axvspan(18, 23, color="#d1242f", alpha=0.08, lw=0)
    ax.set_title("全屋總負載（紅底 = 晚間尖峰 18~23 時）")
    for ax in axes.flat:
        ax.axvspan(8, 17, color="#f0b429", alpha=0.10, lw=0)
        ax.set_xticks(range(0, 25, 3))
        ax.set_xlim(0, 24)
        ax.set_ylabel("kW")
        ax.grid(alpha=0.25)
    axes.flat[0].legend(fontsize=9)
    fig.suptitle(f"台灣平均戶 夏月平日（每日 {total_w.sum() * DT_H / 1000:.2f} 度，平日白天保留 {RETAIN:.0%}，黃底 = 08~17 時）")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    main()
