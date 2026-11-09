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
        self.assertNotIn("mine_power_network.concentrator_substation.active_power", ids)
        self.assertIn("water_supply_network.concentrator_process.water_demand", ids)
        self.assertIn("open-pit_mining.pit_sector_a.rom_stockpile", ids)

    def test_same_seed_produces_replayable_process_samples(self):
        first, second = self.model.initialize(), self.model.initialize()
        for tick in range(1, 901):
            self.model.advance(first, tick, [], seed=123)
            self.model.advance(second, tick, [], seed=123)
        self.assertEqual(first, second)

    def test_equipment_status_tags_follow_plc_availability(self):
        unavailable = {name: False for name in (
            "crusher", "conveyors", "sag_mill", "ball_mill", "flotation", "tailings_pump")}
        self.model.advance(self.state, 1, [], equipment_available=unavailable)
        readings = {tag["tag"]: tag["value"] for tag in self.model.telemetry(self.state)}
        for tag in ("crushing_and_screening.primary_crusher.equipment_running",
                    "crushing_and_screening.ore_conveyors.equipment_running",
                    "grinding_and_flotation.sag_mill.equipment_running",
                    "grinding_and_flotation.ball_mill_01.equipment_running",
                    "grinding_and_flotation.flotation_cells.equipment_running",
                    "tailings_and_reclaim.tailings_pump_station_a.equipment_running"):
            self.assertEqual(readings[tag], 0.0, tag)

    def test_concentrate_and_tailings_solids_balances_close(self):
        initial_concentrate = (self.state["concentrate_thickener_inventory_t"]
                               + self.state["concentrate_cake_inventory_t"])
        initial_tailings = self.state["tailings_thickener_inventory_t"]
        self.advance(1_800)
        concentrate_accounted = (self.state["concentrate_thickener_inventory_t"]
                                 + self.state["filter_press_batch_t"]
                                 + self.state["concentrate_cake_inventory_t"]
                                 + self.state["cumulative_concentrate_shipped_t"])
        self.assertAlmostEqual(initial_concentrate + self.state["cumulative_concentrate_produced_t"],
                               concentrate_accounted, places=5)
        tailings_accounted = (self.state["tailings_thickener_inventory_t"]
                              + self.state["cumulative_tailings_pumped_t"])
        self.assertAlmostEqual(initial_tailings + self.state["cumulative_tailings_received_t"],
                               tailings_accounted, places=5)
        self.assertGreater(self.state["filter_press_cycle_count"], 0)

    def test_filter_press_trip_fills_concentrate_buffer_then_curbs_flotation(self):
        self.state["concentrate_thickener_inventory_t"] = 100.0
        unavailable = {name: True for name in (
            "crusher", "conveyors", "sag_mill", "ball_mill", "flotation",
            "tailings_pump", "tailings_thickener", "concentrate_thickener", "filter_press")}
        unavailable["filter_press"] = False
        baseline_feed = self.state["plant_feed_tph"]
        self.advance(300, equipment_available=unavailable)
        self.assertGreater(self.state["concentrate_thickener_inventory_t"], 100.0)
        self.assertLess(self.state["plant_feed_tph"], baseline_feed * 0.9)
        self.assertEqual(self.state["filter_press_feed_tph"], 0.0)

    def test_discrete_truck_payloads_preserve_rom_inventory_mass_balance(self):
        initial = self.state["rom_stockpile_t"]
        for tick in range(1, 901):
            payload = 205.0 if tick in (210, 510, 810) else 0.0
            self.model.advance(self.state, tick, [], mine_delivery_t=payload)
        expected = (initial + self.state["cumulative_rom_received_t"]
                    - self.state["cumulative_rom_discharge_t"])
        self.assertAlmostEqual(self.state["rom_stockpile_t"], expected, places=6)
        self.assertGreater(self.state["rom_feed_tph"], 0)


if __name__ == "__main__":
    unittest.main()
