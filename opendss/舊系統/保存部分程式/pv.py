import pandas as pd
import numpy as np
import os
from datetime import datetime, timedelta

def generate_pv_curve():
    # 1. 建立 1440 分鐘的時間序列 (以 2017-11-08 為例)
    start_time = datetime(2017, 11, 8, 0, 0)
    times = [start_time + timedelta(minutes=i) for i in range(1440)]
    
    # 2. 建立標準晴天曲線 (基於正弦波)
    pv_kw = np.zeros(1440)
    for i in range(360, 1080): # 06:00 ~ 18:00
        x = (i - 360) / (1080 - 360) * np.pi
        pv_kw[i] = 5.0 * np.sin(x)
        
    # 3. 模擬 3 次短暫雲層遮蔽 (掉功率)
    for i in range(570, 585):   # 09:30
        pv_kw[i] *= np.random.uniform(0.3, 0.6)
    for i in range(735, 755):   # 12:15
        pv_kw[i] *= np.random.uniform(0.2, 0.5)
    for i in range(900, 910):   # 15:00
        pv_kw[i] *= np.random.uniform(0.4, 0.7)
        
    # 4. 加入極微小的自然波動
    for i in range(360, 1080):
        if not ((570 <= i < 585) or (735 <= i < 755) or (900 <= i < 910)):
            noise = np.random.normal(0, 0.03) 
            pv_kw[i] = max(0, pv_kw[i] + noise)
            
    # 5. 建立 DataFrame
    df = pd.DataFrame({
        'time': [t.strftime('%Y-%m-%dT%H:%M:%S') for t in times],
        'pv_kw': np.round(pv_kw, 3) 
    })
    
    # 6. 指定存檔到桌面 (注意：前面加了 r 避免斜線跳脫字元錯誤)
    save_path = r"C:\Users\User\Desktop"
    filename = "pv_curve_1min_simulated.csv"
    
    # 將資料夾路徑與檔名組合起來
    full_path = os.path.join(save_path, filename)
    
    # 輸出 CSV
    df.to_csv(full_path, index=False)
    print(f"✅ 生成完畢！檔案已經熱騰騰地送到你的桌面了：\n{full_path}")

if __name__ == "__main__":
    generate_pv_curve()