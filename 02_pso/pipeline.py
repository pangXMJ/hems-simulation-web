# ============================================================
# pipeline.py
# PSO 最佳化流程整合入口
# ============================================================

from .pso_algorithm import run_pso
from .simulation import simulate_schedule


def split_load_into_critical_and_noncritical(p_fixed):
    """
    將總負載切成：
    - 關鍵負載：停電時仍希望供電的負載
    - 非關鍵負載：停電時可以暫停的負載

    目前假設：
    - 關鍵負載 = 40%
    - 非關鍵負載 = 60%
    """
    p_critical = 0.4 * p_fixed
    p_noncritical = 0.6 * p_fixed

    return p_critical, p_noncritical


def run_optimization_pipeline(p_fixed):
    """
    執行完整的最佳化與模擬流程。
    直接接收長度為 24 的負載陣列 (p_fixed)，不再讀寫實體 CSV。
    """
    # 1. 切分關鍵負載與非關鍵負載
    p_critical, p_noncritical = split_load_into_critical_and_noncritical(
        p_fixed
    )

    # 2. 執行 PSO 最佳化
    pso_result = run_pso(
        p_critical,
        p_noncritical
    )

    # 3. 取得迭代前與迭代後的排程
    before_position = pso_result["before_position"]
    after_position = pso_result["after_position"]
    convergence_curve = pso_result.get("convergence_curve", [])

    # 4. 將排程轉成詳細的模擬圖表資料
    before_result = simulate_schedule(
        before_position,
        p_critical,
        p_noncritical
    )

    after_result = simulate_schedule(
        after_position,
        p_critical,
        p_noncritical
    )

    # 5. 整理成 app.py 可以直接使用的資料包
    data_package = {
        "before_position": before_position,
        "after_position": after_position,
        "before_result": before_result,
        "after_result": after_result,
        "convergence_curve": convergence_curve
    }

    return data_package