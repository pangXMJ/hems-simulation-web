# ============================================================
# pso_algorithm.py
# 粒子群演算法 PSO 核心
# ============================================================

import numpy as np

from .config import (
    NUM_PARTICLES,
    MAX_ITERATIONS,
    DIMENSIONS,
    BOUNDS
)

from .objective import hems_objective


def run_pso(p_critical, p_noncritical):
    """
    執行粒子群演算法 PSO。

    PSO 概念簡述：
    - 每個粒子代表一組 24 小時 BESS 排程
    - 粒子會記住自己的最佳位置 pbest
    - 整個群體會記住目前找到的最佳位置 gbest
    - 每次迭代時，粒子會同時參考：
      1. 自己過去最好的經驗
      2. 全體目前最好的經驗
    """
    # 初始化粒子位置：每個粒子都是一組 24 小時排程
    positions = np.random.uniform(
        BOUNDS[0],
        BOUNDS[1],
        (NUM_PARTICLES, DIMENSIONS)
    )

    # 初始化粒子速度
    velocities = np.random.uniform(
        -1,
        1,
        (NUM_PARTICLES, DIMENSIONS)
    )

    # 每個粒子自己的最佳位置
    pbest_positions = positions.copy()

    # 每個粒子自己的最佳 fitness
    pbest_fitness = np.array([
        hems_objective(p, p_critical, p_noncritical)
        for p in positions
    ])

    # 找出群體最佳粒子
    gbest_index = np.argmin(pbest_fitness)
    gbest_position = pbest_positions[gbest_index].copy()
    gbest_fitness = pbest_fitness[gbest_index]

    # 保存「迭代前」最佳結果
    before_position = gbest_position.copy()
    before_fitness = gbest_fitness

    # 用來記錄每次迭代的最佳 fitness，最後畫收斂曲線
    convergence_curve = []

    for i in range(MAX_ITERATIONS):
        # 動態慣性權重
        # 前期 w 較大：粒子比較敢探索
        # 後期 w 較小：粒子比較容易收斂
        w = 0.9 - (0.5 * (i / MAX_ITERATIONS))

        # c1：粒子相信自己經驗的程度
        # c2：粒子相信群體最佳經驗的程度
        c1 = 1.5
        c2 = 1.5

        for j in range(NUM_PARTICLES):
            r1 = np.random.rand(DIMENSIONS)
            r2 = np.random.rand(DIMENSIONS)

            # 更新速度
            velocities[j] = (
                w * velocities[j]
                + c1 * r1 * (pbest_positions[j] - positions[j])
                + c2 * r2 * (gbest_position - positions[j])
            )

            # 更新位置
            positions[j] = positions[j] + velocities[j]

            # 限制 BESS 充放電功率不可超過上下限
            positions[j] = np.clip(
                positions[j],
                BOUNDS[0],
                BOUNDS[1]
            )

            # 計算新的 fitness
            fitness = hems_objective(
                positions[j],
                p_critical,
                p_noncritical
            )

            # 如果目前位置比自己歷史最佳更好，就更新 pbest
            if fitness < pbest_fitness[j]:
                pbest_fitness[j] = fitness
                pbest_positions[j] = positions[j].copy()

                # 如果目前位置也比全體歷史最佳更好，就更新 gbest
                if fitness < gbest_fitness:
                    gbest_fitness = fitness
                    gbest_position = positions[j].copy()

        convergence_curve.append(gbest_fitness)

    # 保存「迭代後」最佳結果
    after_position = gbest_position.copy()
    after_fitness = gbest_fitness

    return {
        "before_position": before_position,
        "before_fitness": before_fitness,
        "after_position": after_position,
        "after_fitness": after_fitness,
        "convergence_curve": np.array(convergence_curve),
    }