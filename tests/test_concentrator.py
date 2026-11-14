import unittest

from scada_playground.concentrator import CopperConcentrator


class CopperConcentratorTests(unittest.TestCase):
    def setUp(self):
        self.model = CopperConcentrator()
        self.state = self.model.initialize()

    def advance(self, seconds, events=None, **kwargs):
        first_tick = self.state.get("sim_seconds", 0) + 1
        for tick in range(first_tick, first_tick + seconds):
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

    def test_flotation_reagent_stockout_reduces_dose_and_copper_recovery(self):
        self.advance(1_800)
        stocked_recovery = self.state["copper_recovery_pct"]
        stocked_dose = self.state["reagent_dose_l_h"]
        self.state["reagent_inventory_l"] = 0.0
        self.advance(600)
        self.assertEqual(self.state["reagent_dose_l_h"], 0.0)
        self.assertEqual(self.state["reagent_pump_flow_factor"], 0.0)
        self.assertLess(self.state["copper_recovery_pct"], stocked_recovery - 5.0)
        self.assertGreater(stocked_dose, 200.0)

    def test_reagent_delivery_restores_dose_before_recovery_fully_recovers(self):
        self.advance(300)
        self.state["reagent_inventory_l"] = 10.0
        self.advance(1_200)
        stockout_recovery = self.state["copper_recovery_pct"]
        self.assertEqual(self.state["reagent_dose_l_h"], 0.0)
        starved_collector = self.state["flotation_cell_collector_factor"][:]
        self.model.receive_flotation_reagent(self.state, 5_000.0)
        self.advance(1)
        self.assertGreater(self.state["reagent_dose_l_h"], 200.0)
        self.assertLess(self.state["copper_recovery_pct"], 88.0)
        self.assertGreater(self.state["flotation_cell_collector_factor"][0], starved_collector[0])
        self.assertGreater(self.state["flotation_cell_collector_factor"][-1],
                           self.state["flotation_cell_collector_factor"][0])
        self.advance(900)
        self.assertGreater(self.state["copper_recovery_pct"], stockout_recovery + 20.0)

    def test_collector_starvation_front_moves_through_cells_with_residence_lag(self):
        self.state["reagent_inventory_l"] = 0.0
        self.advance(60)
        collector = self.state["flotation_cell_collector_factor"]
        self.assertEqual(len(collector), self.model.FLOTATION_CELL_COUNT)
        self.assertLess(collector[0], 0.85)
        self.assertGreater(collector[-1], 0.98)
        self.assertLess(collector[0], collector[-1])
        self.assertAlmostEqual(self.state["history"][-1]["collector_availability_cell_01"],
                               100.0 * collector[0])
        tags = {row["sensor"]: row for row in self.model.telemetry(self.state)}
        self.assertAlmostEqual(tags["collector_availability_cell_01"]["value"],
                               100.0 * collector[0], delta=0.15)

    def test_flotation_reagent_inventory_conserves_stock_and_delivery_is_bounded(self):
        initial = self.state["reagent_inventory_l"]
        self.advance(3_600)
        consumed = self.state["reagent_consumed_l"]
        self.assertAlmostEqual(initial - consumed, self.state["reagent_inventory_l"], places=6)
        headroom = self.state["reagent_tank_capacity_l"] - self.state["reagent_inventory_l"]
        self.model.receive_flotation_reagent(self.state, headroom)
        self.assertAlmostEqual(self.state["reagent_inventory_l"],
                               self.state["reagent_tank_capacity_l"])
        with self.assertRaises(ValueError):
            self.model.receive_flotation_reagent(self.state, 1.0)
        with self.assertRaises(ValueError):
            self.model.receive_flotation_reagent(self.state, float("nan"))

    def test_flotation_reagent_dose_is_limited_by_dosing_pump_capacity(self):
        fault = {"name": "Mechanical degradation", "area": "Grinding and flotation",
                 "severity": "Large"}
        self.advance(1, [fault])
        self.assertLess(self.state["reagent_dose_l_h"], self.state["reagent_requested_l_h"])
        self.assertAlmostEqual(self.state["reagent_pump_flow_factor"],
                               self.state["reagent_dose_l_h"]
                               / self.state["reagent_requested_l_h"])

    def test_flotation_reagent_pump_loses_flow_with_process_power(self):
        self.advance(1, power_available_fraction=0.0)
        self.assertEqual(self.state["reagent_dose_l_h"], 0.0)
        self.assertEqual(self.state["reagent_inventory_l"], self.model.FLOTATION_REAGENT_INITIAL_L)

    def test_legacy_concentrator_state_adds_finite_reagent_inventory(self):
        legacy = self.model.initialize()
        legacy["history"] = [{"time": 15}]
        for key in ("reagent_inventory_l", "reagent_tank_capacity_l", "reagent_consumed_l",
                    "reagent_delivery_total_l", "reagent_dose_l_h", "reagent_requested_l_h",
                    "reagent_pump_condition", "reagent_pump_flow_factor"):
            legacy.pop(key)
        legacy.pop("flotation_cell_collector_factor")
        self.model.migrate(legacy)
        self.assertEqual(legacy["reagent_inventory_l"], self.model.FLOTATION_REAGENT_INITIAL_L)
        self.assertEqual(legacy["reagent_tank_capacity_l"], self.model.FLOTATION_REAGENT_TANK_CAPACITY_L)
        self.assertEqual(legacy["flotation_cell_collector_factor"], [1.0] * self.model.FLOTATION_CELL_COUNT)
        self.assertEqual(legacy["history"][0]["collector_availability_cell_06"], 100.0)

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

    def test_process_water_balance_uses_solids_and_underflow_moisture(self):
        # Remove pond inventory so this checks the steady reclaim circuit rather
        # than crediting a pre-filled pond as process recovery.
        self.state["tailings_pond_inventory_m3"] = 0.0
        self.advance(1_200)
        state = self.state
        self.assertAlmostEqual(state["water_specific_m3_t"], 0.90)
        self.assertAlmostEqual(state["gross_process_water_m3h"],
                               state["plant_feed_tph"] * state["water_specific_m3_t"], delta=2)
        self.assertAlmostEqual(state["gross_process_water_m3h"],
                               state["water_makeup_m3h"] + state["concentrator_reclaim_m3h"], places=6)
        self.assertAlmostEqual(state["gross_process_water_m3h"],
                               state["tailings_water_feed_m3h"] + state["concentrate_water_feed_m3h"], places=6)
        self.assertAlmostEqual(state["concentrator_reclaim_m3h"],
                               state["tailings_reclaim_water_m3h"]
                               + state["concentrate_thickener_overflow_m3h"]
                               + state["filter_press_filtrate_m3h"], places=6)
        self.assertGreater(state["water_recovery_pct"], 35)
        self.assertLess(state["water_recovery_pct"], 65)

    def test_tailings_thickener_liquid_inventory_conserves_water(self):
        initial_water = self.state["tailings_thickener_water_inventory_m3"]
        self.advance(1_800)
        accounted_water = (initial_water + self.state["cumulative_tailings_water_feed_m3"]
                           - self.state["cumulative_tailings_underflow_water_m3"]
                           - self.state["cumulative_tailings_overflow_water_m3"]
                           - self.state["cumulative_tailings_water_spill_m3"])
        self.assertAlmostEqual(self.state["tailings_thickener_water_inventory_m3"],
                               accounted_water, places=6)

    def test_thickener_outage_holds_water_then_flushes_overflow_on_restart(self):
        self.advance(600)
        normal_return = self.state["tailings_reclaim_water_m3h"]
        normal_overflow = self.state["tailings_thickener_overflow_water_m3h"]
        initial_water = self.state["tailings_thickener_water_inventory_m3"]
        unavailable = {name: True for name in (
            "crusher", "conveyors", "sag_mill", "ball_mill", "flotation", "tailings_pump",
            "tailings_thickener", "concentrate_thickener", "filter_press")}
        unavailable["tailings_thickener"] = False
        for tick in range(601, 1_501):
            self.model.advance(self.state, tick, [], equipment_available=unavailable)
        # The thickener overflow stops, but the independent pond decant pump
        # continues recovering stored water until the pond level controller slows it.
        self.assertEqual(self.state["tailings_thickener_overflow_water_m3h"], 0.0)
        self.assertGreater(self.state["tailings_decant_water_m3h"], 0.0)
        self.assertAlmostEqual(self.state["tailings_reclaim_water_m3h"],
                               self.state["tailings_decant_water_m3h"])
        self.assertGreater(self.state["tailings_thickener_water_inventory_m3"], initial_water)
        self.model.advance(self.state, 1_501, [])
        self.assertGreater(self.state["tailings_thickener_overflow_water_m3h"], normal_overflow)
        self.assertGreater(self.state["tailings_reclaim_water_m3h"], normal_return * 0.8)

    def test_legacy_state_recalculates_water_metrics_for_new_model_revision(self):
        legacy = self.model.initialize()
        legacy["water_model_revision"] = 1
        legacy.update(water_specific_m3_t=0.30, gross_process_water_m3h=261.0,
                      concentrator_reclaim_m3h=193.0, water_makeup_m3h=68.0,
                      water_recovery_pct=74.0)
        self.model.migrate(legacy)
        self.assertEqual(legacy["water_model_revision"], 3)
        self.assertAlmostEqual(legacy["water_specific_m3_t"], 0.90)
        self.assertAlmostEqual(legacy["gross_process_water_m3h"],
                               legacy["plant_feed_tph"] * 0.90)
        self.assertAlmostEqual(legacy["water_makeup_m3h"] + legacy["concentrator_reclaim_m3h"],
                               legacy["gross_process_water_m3h"])

    def test_tailings_thickener_trip_removes_its_reclaim_stream(self):
        self.advance(600)
        normal_return = self.state["concentrator_reclaim_m3h"]
        unavailable = {name: True for name in (
            "crusher", "conveyors", "sag_mill", "ball_mill", "flotation", "tailings_pump",
            "tailings_thickener", "concentrate_thickener", "filter_press")}
        unavailable["tailings_thickener"] = False
        self.advance(3, equipment_available=unavailable)
        self.assertEqual(self.state["tailings_thickener_overflow_water_m3h"], 0.0)
        self.assertGreater(self.state["tailings_decant_water_m3h"], 0.0)
        self.assertAlmostEqual(self.state["tailings_reclaim_water_m3h"],
                               self.state["tailings_decant_water_m3h"])
        self.assertLess(self.state["concentrator_reclaim_m3h"], normal_return * 0.8)
        self.assertGreater(self.state["water_makeup_m3h"], 0.0)

    def test_press_filtrate_is_a_squeeze_phase_batch_release(self):
        peak_filtrate_m3h = 0.0
        squeeze_observed = False
        for tick in range(1, 1_601):
            self.model.advance(self.state, tick, [])
            if self.state["filter_press_phase"] == "SQUEEZING":
                squeeze_observed = True
                peak_filtrate_m3h = max(peak_filtrate_m3h, self.state["filter_press_filtrate_m3h"])
        self.assertGreater(self.state["filter_press_cycle_count"], 0)
        self.assertTrue(squeeze_observed)
        self.assertGreater(peak_filtrate_m3h, 0.0)
        if self.state["filter_press_phase"] != "SQUEEZING":
            self.assertEqual(self.state["filter_press_filtrate_m3h"], 0.0)
        self.assertGreaterEqual(self.state["filter_press_batch_filtrate_inventory_m3"], 0.0)

    def test_telemetry_ids_are_unique_and_seeded(self):
        first = self.model.telemetry(self.state)
        second = self.model.telemetry(self.state)
        self.assertEqual(first, second)
        ids = [tag["tag"] for tag in first]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("grinding_and_flotation.sag_mill.motor_power", ids)
        self.assertNotIn("mine_power_network.concentrator_substation.active_power", ids)
        self.assertIn("water_supply_network.concentrator_process.water_demand", ids)
        self.assertIn("tailings_and_reclaim.tailings_thickener.water_inventory", ids)
        self.assertIn("tailings_and_reclaim.tailings_thickener.overflow_flow", ids)
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
