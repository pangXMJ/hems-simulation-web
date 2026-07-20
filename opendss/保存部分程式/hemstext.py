import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

# 1. 修正變數標籤並輸入完整 22 個題項數據
data = {
    "In (資訊透明度)": [
        0.838,
        0.933,
        0.945,
        0.928,
        0.813,  # In1-In5
        0.670,
        0.646,
        0.679,
        0.655,
        0.674,
        0.700,
        0.672,  # Kn1-Kn7
        0.835,
        0.752,
        0.687,
        0.729,
        0.742,  # Or1-Or5
        0.755,
        0.617,
        0.728,
        0.760,
        0.694,
    ],  # Pe1-Pe5
    "Kn (知覺價值)": [
        0.561,
        0.711,
        0.664,
        0.654,
        0.640,  # In1-In5
        0.890,
        0.951,
        0.870,
        0.969,
        0.971,
        0.951,
        0.871,  # Kn1-Kn7
        0.882,
        0.802,
        0.794,
        0.818,
        0.827,  # Or1-Or5
        0.707,
        0.678,
        0.793,
        0.759,
        0.793,
    ],  # Pe1-Pe5
    "Or (組織信任)": [
        0.673,
        0.780,
        0.760,
        0.695,
        0.608,  # In1-In5
        0.763,
        0.763,
        0.819,
        0.824,
        0.836,
        0.862,
        0.726,  # Kn1-Kn7
        0.922,
        0.905,
        0.945,
        0.960,
        0.961,  # Or1-Or5
        0.778,
        0.686,
        0.818,
        0.773,
        0.846,
    ],  # Pe1-Pe5
    "Pe (知識分享意願)": [
        0.699,
        0.747,
        0.753,
        0.753,
        0.628,  # In1-In5
        0.785,
        0.785,
        0.758,
        0.744,
        0.791,
        0.814,
        0.734,  # Kn1-Kn7
        0.891,
        0.807,
        0.834,
        0.813,
        0.805,  # Or1-Or5
        0.858,
        0.896,
        0.904,
        0.908,
        0.906,
    ],  # Pe1-Pe5
}

index = [
    "In1",
    "In2",
    "In3",
    "In4",
    "In5",
    "Kn1",
    "Kn2",
    "Kn3",
    "Kn4",
    "Kn5",
    "Kn6",
    "Kn7",
    "Or1",
    "Or2",
    "Or3",
    "Or4",
    "Or5",
    "Pe1",
    "Pe2",
    "Pe3",
    "Pe4",
    "Pe5",
]

df = pd.DataFrame(data, index=index)

# 2. 設定繪圖風格與中文字型（防止不顯示例題中文字）
plt.figure(figsize=(10, 12), dpi=300)
sns.set_theme(style="white")
plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "Arial Unicode MS", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

# 3. 繪製熱力圖 (使用具有學術美感的 'GnBu' 綠藍漸層色調)
# vmin 與 vmax 設定在 0.4 到 1.0 之間，能拉大主要負荷量與交叉負荷量的顏色深淺視覺差距
ax = sns.heatmap(
    df,
    annot=True,
    fmt=".3f",
    cmap="GnBu",
    linewidths=0.5,
    vmin=0.4,
    vmax=1.0,
    cbar_kws={"label": "負荷量數值 (Loading)"},
    annot_kws={"size": 10},
)

# 4. 優化外觀排版
plt.title(
    "表 4-2 反映性測量模型交叉負荷量熱力圖\n(Cross-Loadings Matrix Heatmap)",
    fontsize=14,
    fontweight="bold",
    pad=20,
)
plt.xlabel("潛在構面 (Constructs)", fontsize=12, labelpad=10)
plt.ylabel("測量指標 (Indicators)", fontsize=12, labelpad=10)

# 將 X 軸標籤放到上方，更符合論文表格習慣
ax.xaxis.tick_top()
ax.xaxis.set_label_position("top")
plt.xticks(fontsize=11)
plt.yticks(rotation=0, fontsize=11)

# 5. 自動調整佈局並儲存高畫質圖片
plt.tight_layout()
plt.savefig("cross_loadings_heatmap.png", bbox_inches="tight")
plt.show()