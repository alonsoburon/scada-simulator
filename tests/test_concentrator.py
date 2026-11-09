import unittest

from scada_playground.concentrator import CopperConcentrator


class CopperConcentratorTests(unittest.TestCase):
    def setUp(self):
        self.model = CopperConcentrator()
        self.state = self.model.initialize()

    def advance(self, seconds, events=None, **kwargs):
        for tick in range(1, seconds + 1):
            self.model.advance(self.state, tick, events or [], **kwargs)

    def test_normal_operation_respects_capacity_and_metal_balance(self):
        self.advance(1_800)
        state = self.state
        self.assertGreater(state["plant_feed_tph"], 700)
        self.assertLess(state["plant_feed_tph"], 1_000)
        self.assertGreater(state["copper_recovery_pct"], 80)
        self.assertLess(state["copper_recovery_pct"], 95)
        contained_copper_tph = state["plant_feed_tph"] * state["copper_feed_grade_pct"] / 100
        recovered_copper_tph = state["concentrate_tph"] * state["concentrate_grade_pct"] / 100
        self.assertAlmostEqual(recovered_copper_tph / contained_copper_tph * 100,
                               state["copper_recovery_pct"], delta=8)
        self.assertGreaterEqual(state["crushed_stockpile_t"], 0)
        self.assertLessEqual(state["crushed_stockpile_t"], 8_000)

    def test_grinding_degradation_propagates_to_throughput_and_recovery(self):
        self.advance(1_200)
        baseline = self.state["plant_feed_tph"]
        fault = {"name": "Mechanical degradation", "area": "Grinding and flotation", "severity": "Large"}
        self.advance(900, [fault])
        self.assertLess(self.state["plant_feed_tph"], baseline * 0.9)
        self.assertGreater(self.state["p80_microns"], 150)
        self.assertLess(self.state["copper_recovery_pct"], 88)
        self.assertGreater(self.state["sag_vibration_mm_s"], 8)

    def test_conveyor_jam_is_buffered_before_reaching_mill_feed(self):
        self.advance(300)
        baseline_feed = self.state["plant_feed_tph"]
        baseline_crusher = self.state["crushed_ore_tph"]
        fault = {"name": "Conveyor jam", "area": "Crushing and screening", "severity": "Large"}
        self.advance(1_800, [fault])
        self.assertLess(self.state["crushed_ore_tph"], baseline_crusher * 0.4)
        self.assertGreater(self.state["plant_feed_tph"], baseline_feed * 0.85)
        self.assertLess(self.state["crushed_stockpile_t"], 3_200)

    def test_water_shortage_derates_feed_and_float_recovery(self):
        self.advance(900)
        normal = self.state["plant_feed_tph"]
        self.advance(900, water_shortage_m3h=250)
        self.assertLess(self.state["plant_feed_tph"], normal * 0.2)
        self.assertEqual(self.state["fresh_water_shortage_m3h"], 250)

    def test_telemetry_ids_are_unique_and_seeded(self):
        first = self.model.telemetry(self.state)
        second = self.model.telemetry(self.state)
        self.assertEqual(first, second)
        ids = [tag["tag"] for tag in first]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("grinding_and_flotation.sag_mill.motor_power", ids)
        self.assertIn("mine_power_network.concentrator_substation.active_power", ids)
        self.assertIn("water_supply_network.concentrator_process.water_demand", ids)
        self.assertIn("open-pit_mining.pit_sector_a.rom_stockpile", ids)

    def test_same_seed_produces_replayable_process_samples(self):
        first, second = self.model.initialize(), self.model.initialize()
        for tick in range(1, 901):
            self.model.advance(first, tick, [], seed=123)
            self.model.advance(second, tick, [], seed=123)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
