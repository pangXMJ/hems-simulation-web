"""
build_pv_from_codis.py
放置位置：C:\\projects\\hems-simulation-web\\data\\processed\\

用途（前處理，手動執行）：
  把中央氣象署 CODiS 臺中站 2026 年 6 月「逐時全天空日射量」實測資料，
  換算成 5 kW 太陽光電系統每 15 分鐘的發電功率，產出系統使用的三個 PV 檔：
    晴天 pv_curve_15min.csv、陰天 cloudy_day_pv_curve_15min.csv、雨天 rain_day_pv_curve_15min.csv

挑選代表日（條件固定，每類取最接近該類平均日射量的一天）：
  晴天：降雨 0 mm、日照 ≥ 10 h      → 6/19（28.11 MJ/m²）
  陰天：降雨 ≤ 0.2 mm、日照 1~4 h   → 6/17（17.19 MJ/m²，降雨 0、日照 3.0 h）
  雨天：降雨 ≥ 10 mm、日照 0 h      → 6/9 （3.96 MJ/m²，降雨 18.3 mm；排除颱風日 6/27）

換算：
  1. CODiS 第 h 欄 = (h-1):00 ~ h:00 這一小時的累積日射量（MJ/m²）
  2. 小時平均日射強度 G (kW/m²) = MJ/m² ÷ 3.6
  3. 小時平均發電 P = 額定 5 kW × G ÷ 1 kW/m² × 系統效率 PR（0.8，含逆變器、線路、溫度損失）
  4. 以小時中點做線性內插到 15 分鐘，再逐小時縮放，使每小時發電量與實測一致（能量守恆）
  假設面板水平安裝（台灣夏季太陽高度角大，傾角影響小）。

輸出格式與原檔相同：Time, pv_kw（⚠️ 數值單位是 W，沿用專案既有慣例）

執行：
  python build_pv_from_codis.py
  （測試用：python build_pv_from_codis.py --root D:\\測試資料夾）
執行後到 07 頁按確認，系統才會使用新的 PV 曲線。
"""

import argparse
import shutil
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = r"C:\projects\hems-simulation-web"
IRRADIANCE_FILENAME = "CODiS_Taichung_202606_irradiance.csv"   # 放在 data\01_raw

PV_RATED_KW = 5.0
PERFORMANCE_RATIO = 0.8
DAYS = {  # 天氣: (日期, 輸出檔名)
    "sunny": (19, "pv_curve_15min.csv"),
    "cloudy": (17, "cloudy_day_pv_curve_15min.csv"),
    "rainy": (9, "rain_day_pv_curve_15min.csv"),
}


def read_codis(path: Path) -> pd.DataFrame:
    """回傳 index=日、columns=1..24（小時）的日射量表（MJ/m²）。"""
    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    df = df[df["日/時"].str.strip() != "總和"]
    df.index = df["日/時"].astype(int)
    hours = [str(h) for h in range(1, 25)]
    return df[hours].astype(float).rename(columns=lambda c: int(c))


def hourly_to_15min_kw(hourly_mj: np.ndarray) -> np.ndarray:
    """24 個小時累積日射量 → 96 個 15 分鐘平均發電功率（kW），每小時能量守恆。"""
    hourly_kw = PV_RATED_KW * (hourly_mj / 3.6) * PERFORMANCE_RATIO   # 小時平均功率
    centers_h = np.arange(24) + 0.5                                     # 第 h 欄對應 (h-1)~h，中點 h-0.5
    slot_mid = np.arange(96) / 4 + 0.125
    kw = np.clip(np.interp(slot_mid, centers_h, hourly_kw, left=0.0, right=0.0), 0, None)
    for h in range(24):
        sl = slice(h * 4, h * 4 + 4)
        target = hourly_kw[h]                       # 該小時平均功率（kW）
        if target <= 0:
            kw[sl] = 0.0
        elif kw[sl].sum() > 0:
            kw[sl] *= target * 4 / kw[sl].sum()     # 4 格平均 = 小時平均
        else:
            kw[sl] = target
    return kw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=PROJECT_ROOT)
    args = ap.parse_args()
    root = Path(args.root)
    raw_dir, proc_dir = root / "data" / "01_raw", root / "data" / "processed"
    proc_dir.mkdir(parents=True, exist_ok=True)

    irr = read_codis(raw_dir / IRRADIANCE_FILENAME)
    times = [f"{i // 4:02d}:{(i % 4) * 15:02d}" for i in range(96)]
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    curves = {}

    for weather, (day, fname) in DAYS.items():
        kw = hourly_to_15min_kw(irr.loc[day].to_numpy())
        curves[weather] = kw
        out_path = raw_dir / fname
        if out_path.exists():
            backup = proc_dir / f"{out_path.stem}_backup_{stamp}.csv"
            shutil.copy2(out_path, backup)
        pd.DataFrame({"Time": times, "pv_kw": np.round(kw * 1000, 2)}).to_csv(out_path, index=False)
        print(f"{weather:7s} 6/{day:<2d} 一天 {kw.sum() * 0.25:5.2f} 度，尖峰 {kw.max():.2f} kW → {out_path}")

    print(f"舊檔已備份到 {proc_dir}（檔名含 backup_{stamp}）")
    print("提醒：到 07 頁按確認，系統才會使用新的 PV 曲線。")


if __name__ == "__main__":
    main()
