import unittest

from scada_playground.cip_system import CleaningInPlaceSystem
from scada_playground.plant_utilities import FactoryUtilities


class CleaningInPlaceTests(unittest.TestCase):
    def setUp(self):
        self.model = CleaningInPlaceSystem()
        self.state = self.model.initialize()

    def advance(self, start, end, *, water=1.0, steam=1.0):
        for second in range(start, end + 1):
            self.model.advance(self.state, second, steam_available_fraction=steam,
                               water_available_fraction=water, power_available_fraction=1.0)

    def test_water_shortage_holds_phase_timer_then_cycle_completes(self):
        self.assertTrue(self.model.start(self.state, "pasteurizer_01"))
        self.advance(1, 46)
        self.assertEqual(self.state["phase"], "PRE_RINSE")
        elapsed_before_shortage = self.state["phase_elapsed_s"]
        self.advance(47, 107, water=0.2)
        self.assertEqual(self.state["phase"], "PRE_RINSE")
        self.assertEqual(self.state["phase_elapsed_s"], elapsed_before_shortage)
        self.assertGreater(self.state["water_starved_seconds"], 30)
        for second in range(108, 1_800):
            self.model.advance(self.state, second, steam_available_fraction=1.0,
                               water_available_fraction=1.0, power_available_fraction=1.0)
        self.assertFalse(self.state["active"])
        self.assertEqual(self.state["cycles_completed"], 1)

    def test_wastewater_headroom_stalls_water_phases_and_effluent(self):
        self.model.start(self.state, "pasteurizer_01", sim_seconds=0)
        self.advance(1, 46)
        self.assertEqual(self.state["phase"], "PRE_RINSE")
        elapsed = self.state["phase_elapsed_s"]
        self.model.advance(self.state, 47, steam_available_fraction=1.0,
                           water_available_fraction=1.0, power_available_fraction=1.0,
                           wastewater_available_fraction=0.0)
        self.assertEqual(self.state["phase_elapsed_s"], elapsed)
        self.assertEqual(self.state["water_demand_m3h"], 0.0)
        self.assertEqual(self.state["effluent_m3h"], 0.0)

    def test_hot_sanitation_waits_for_steam_and_temperature(self):
        self.model.start(self.state, "pasteurizer_02")
        self.advance(1, 226)
        self.assertEqual(self.state["phase"], "CAUSTIC_WASH")
        # No heat means no caustic-wash time can be credited even with water available.
        self.advance(227, 300, steam=0.0)
        self.assertEqual(self.state["phase"], "CAUSTIC_WASH")
        self.assertEqual(self.state["phase_elapsed_s"], 0)
        hot_sanitize_started = False
        safe_hot_phase_credited = False
        for second in range(301, 1_400):
            self.model.advance(self.state, second, steam_available_fraction=1.0,
                               water_available_fraction=1.0, power_available_fraction=1.0)
            if self.state["phase"] == "HOT_SANITIZE":
                hot_sanitize_started = True
                safe_hot_phase_credited |= (self.state["phase_elapsed_s"] > 0
                                            and self.state["temperature_c"] >= 72.0)
        self.assertTrue(hot_sanitize_started)
        self.assertTrue(safe_hot_phase_credited)

    def test_chemical_permissives_and_target_validation(self):
        with self.assertRaises(ValueError):
            self.model.start(self.state, "pasteurizer_03")
        self.state["caustic_inventory_l"] = 20
        self.assertFalse(self.model.start(self.state))
        self.assertIn("Caustic", self.state["start_block_reason"])

    def test_cip_reduces_one_pasteurizer_while_parallel_line_runs(self):
        model = FactoryUtilities()
        state = model.initialize()
        self.model.start(state["cip"], "pasteurizer_01")
        for second in range(1, 47):
            model.advance(state, second, [], water_available_fraction=1.0)
        self.assertEqual(state["pasteurizers"]["pasteurizer_01"]["process_rate"], 0.0)
        self.assertGreater(state["pasteurizers"]["pasteurizer_02"]["process_rate"], 80.0)
        self.assertGreater(state["cip_water_demand_m3h"], 0.0)


if __name__ == "__main__":
    unittest.main()
