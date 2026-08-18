"""PSO 電池 SOC 動態下限的回歸測試。"""

import unittest

import numpy as np
import pandas as pd

from config import (
    BESS_CAPACITY_KWH,
    CSV_ENCODING,
    LOAD_CSV_PATH,
    NUM_INTERVALS,
    OUTAGE_END_INDEX,
    OUTAGE_SOC_MIN,
    OUTAGE_START_INDEX,
    P_BESS_MAX_KW,
    PV_CSV_PATH,
    RESULT_DIR,
    SOC_MIN,
    TARGET_FINAL_SOC,
)
from main import read_input_data, simulate_best_schedule
from pso import battery_energy_change_kwh, limit_bess_power_by_soc


TEST_OUTPUT_DIR = RESULT_DIR / "test"
TEST_OUTPUT_PATH = (
    TEST_OUTPUT_DIR
    / "battery_usage_outage_5pct_test_summer_weekday_15min.csv"
)


def generate_current_data_outage_test_csv():
    """使用目前負載與 PV 產生停電 SOC 降至 5% 的測試排程。"""
    input_data = read_input_data(LOAD_CSV_PATH, PV_CSV_PATH)
    requested_schedule_kw = np.zeros(NUM_INTERVALS)

    # 停電前盡量放電到正常下限 20%，讓目前的關鍵負載能測到緊急下限 5%。
    requested_schedule_kw[:OUTAGE_START_INDEX] = P_BESS_MAX_KW
    result = simulate_best_schedule(input_data, requested_schedule_kw)

    TEST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    result.to_csv(
        TEST_OUTPUT_PATH,
        index=False,
        encoding=CSV_ENCODING,
        float_format="%.6f",
    )
    return result


class DynamicSocLimitTests(unittest.TestCase):
    def test_normal_operation_keeps_twenty_percent_floor(self):
        energy_kwh = SOC_MIN * BESS_CAPACITY_KWH

        actual_power_kw = limit_bess_power_by_soc(energy_kwh, 1.0)

        self.assertAlmostEqual(actual_power_kw, 0.0)

    def test_outage_can_discharge_below_twenty_percent(self):
        energy_kwh = SOC_MIN * BESS_CAPACITY_KWH

        actual_power_kw = limit_bess_power_by_soc(
            energy_kwh,
            5.0,
            outage=True,
        )
        remaining_energy_kwh = (
            energy_kwh + battery_energy_change_kwh(actual_power_kw)
        )

        self.assertGreater(actual_power_kw, 0.0)
        self.assertLess(remaining_energy_kwh, energy_kwh)
        self.assertGreaterEqual(
            remaining_energy_kwh,
            OUTAGE_SOC_MIN * BESS_CAPACITY_KWH,
        )

    def test_outage_stops_discharge_at_five_percent(self):
        energy_kwh = OUTAGE_SOC_MIN * BESS_CAPACITY_KWH

        actual_power_kw = limit_bess_power_by_soc(
            energy_kwh,
            1.0,
            outage=True,
        )

        self.assertAlmostEqual(actual_power_kw, 0.0)

    def test_noncritical_load_is_shed_during_outage(self):
        times = [
            f"{minute // 60:02d}:{minute % 60:02d}"
            for minute in range(0, 24 * 60, 15)
        ]
        input_data = pd.DataFrame(
            {
                "Time": times,
                "load_kw": np.full(NUM_INTERVALS, 5.0),
                "critical_load_kw": np.zeros(NUM_INTERVALS),
                "pv_kw": np.zeros(NUM_INTERVALS),
            }
        )

        result = simulate_best_schedule(
            input_data,
            np.zeros(NUM_INTERVALS),
        )

        outage_power = result.loc[
            OUTAGE_START_INDEX : OUTAGE_END_INDEX - 1,
            "battery_power_kw",
        ]
        self.assertTrue(np.allclose(outage_power, 0.0))

    def test_schedule_recovers_from_five_to_fifty_percent_by_midnight(self):
        times = [
            f"{minute // 60:02d}:{minute % 60:02d}"
            for minute in range(0, 24 * 60, 15)
        ]
        input_data = pd.DataFrame(
            {
                "Time": times,
                "load_kw": np.zeros(NUM_INTERVALS),
                "critical_load_kw": np.zeros(NUM_INTERVALS),
                "pv_kw": np.zeros(NUM_INTERVALS),
            }
        )
        input_data.loc[
            OUTAGE_START_INDEX : OUTAGE_END_INDEX - 1,
            ["load_kw", "critical_load_kw"],
        ] = 5.0

        result = simulate_best_schedule(
            input_data,
            np.zeros(NUM_INTERVALS),
        )

        self.assertAlmostEqual(
            result.loc[OUTAGE_END_INDEX - 1, "soc_percent"],
            OUTAGE_SOC_MIN * 100.0,
        )
        self.assertGreater(
            result.loc[OUTAGE_END_INDEX, "soc_percent"],
            OUTAGE_SOC_MIN * 100.0,
        )
        self.assertAlmostEqual(
            result.iloc[-1]["soc_percent"],
            TARGET_FINAL_SOC * 100.0,
        )

    def test_current_data_generates_five_percent_outage_csv(self):
        result = generate_current_data_outage_test_csv()

        self.assertTrue(TEST_OUTPUT_PATH.exists())
        self.assertEqual(len(result), NUM_INTERVALS)
        self.assertAlmostEqual(
            result.loc[OUTAGE_START_INDEX - 1, "soc_percent"],
            SOC_MIN * 100.0,
        )
        self.assertAlmostEqual(
            result["soc_percent"].min(),
            OUTAGE_SOC_MIN * 100.0,
        )
        self.assertAlmostEqual(
            result.iloc[-1]["soc_percent"],
            TARGET_FINAL_SOC * 100.0,
        )


if __name__ == "__main__":
    unittest.main()
