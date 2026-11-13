import unittest
import math
from collections import deque

from scada_playground.water_network import MineWaterNetwork


class MineWaterNetworkTests(unittest.TestCase):
    def setUp(self):
        self.model = MineWaterNetwork()
        self.state = self.model.initialize()

    def step(self, count, events=None):
        for second in range(1, count + 1):
            self.model.advance(self.state, second, events or [])

    def test_default_run_respects_storage_bounds_and_mass_balance(self):
        initial_storage = self.model.total_inventory_m3(self.state)
        self.step(900)
        final_storage = self.model.total_inventory_m3(self.state)
        expected = (initial_storage + self.state["cumulative_concentrator_return_m3"]
                    - self.state["cumulative_process_consumption_m3"]
                    - self.state["cumulative_treated_delivery_m3"]
                    - self.state["total_spilled_m3"])
        self.assertAlmostEqual(final_storage, expected, places=6)
        for node in self.state["nodes"].values():
            self.assertGreaterEqual(node["volume_m3"], 0)
            self.assertLessEqual(node["volume_m3"], node["capacity_m3"])

    def test_filter_backwash_consumes_tank_water_and_preserves_network_balance(self):
        self.state["treatment"]["filter_solids_kg"] = self.model.TREATMENT.FILTER_CAPACITY_KG
        initial_storage = self.model.total_inventory_m3(self.state)
        for second in range(1, self.model.TREATMENT.FILTER_BACKWASH_SECONDS + 1):
            self.model.advance(self.state, second, [])
        self.assertGreater(self.state["cumulative_treatment_backwash_m3"], 0.5)
        self.assertEqual(self.state["treatment"]["mode"], "FILTERING")
        self.assertLess(self.state["treatment"]["filter_solids_kg"], 50)
        self.assertAlmostEqual(self.state["treatment"]["cumulative_backwash_solids_kg"],
                               self.model.TREATMENT.FILTER_CAPACITY_KG * .92, places=6)
        self.assertGreater(self.state["wastewater"]["equalization_tss_kg"], 300)
        expected = (initial_storage - self.state["cumulative_process_consumption_m3"]
                    - self.state["cumulative_treated_delivery_m3"]
                    - self.state["total_spilled_m3"])
        self.assertAlmostEqual(self.model.total_inventory_m3(self.state), expected, places=6)

    def test_loaded_filter_pressure_reduces_transfer_pump_flow(self):
        normal, _ = self.model._link_reading(
            self.state, "reclaim_transfer", self.model.LINKS["reclaim_transfer"])
        self.state["treatment"]["filter_differential_pressure_kpa"] = 100.0
        loaded, _ = self.model._link_reading(
            self.state, "reclaim_transfer", self.model.LINKS["reclaim_transfer"])
        self.assertLess(loaded, normal)

    def test_local_treatment_power_loss_holds_filters_without_tripping_mine_pumps(self):
        event = {"name": "Utility loss", "area": "Water treatment", "severity": "Large"}
        self.model.advance(self.state, 1, [event], power_available_fraction=1.0)
        self.assertEqual(self.state["treatment"]["mode"], "POWER_HOLD")
        self.assertEqual(self.state["treatment"]["treatment_power_fraction"], 0.0)
        self.assertTrue(all(pump["running"] for pump in self.state["pumps"].values()))

    def test_treatment_quality_tracks_source_upset_and_finite_chemical_inventory(self):
        treatment = self.state["treatment"]
        treatment["coagulant_inventory_kg"] = 0.0
        treatment["chlorine_inventory_kg"] = 0.0
        upset = {"name": "Process upset", "area": "Water treatment", "severity": "Large"}
        self.model.advance(self.state, 1, [upset])
        self.assertGreater(treatment["influent_turbidity_ntu"], 100)
        self.assertGreater(treatment["effluent_turbidity_ntu"], 20)
        self.assertEqual(treatment["chlorine_residual_mg_l"], 0.0)
        self.assertGreater(self.state["treatment"]["filter_differential_pressure_kpa"], 18.0)

    def test_treated_tank_quality_follows_delayed_quality_front_and_mixes(self):
        self.state["treatment"]["coagulant_inventory_kg"] = 0.0
        self.state["treatment"]["chlorine_inventory_kg"] = 0.0
        initial_turbidity = self.state["nodes"]["treated_tank"]["turbidity_ntu"]
        upset = {"name": "Process upset", "area": "Water treatment", "severity": "Large"}
        for second in range(1, 100):
            self.model.advance(self.state, second, [upset])
        self.assertAlmostEqual(self.state["nodes"]["treated_tank"]["turbidity_ntu"], initial_turbidity, places=2)
        self.assertGreater(self.state["treatment"]["effluent_turbidity_ntu"], 20)
        for second in range(100, 2_000):
            self.model.advance(self.state, second, [upset])
        self.assertGreater(self.state["nodes"]["treated_tank"]["turbidity_ntu"], initial_turbidity + 0.4)

    def test_utility_loss_propagates_to_flow_and_unmet_demand(self):
        self.step(60)
        normal_flow = self.state["links"]["process_feed"]["inlet_flow_m3h"]
        initial_process_level = self.state["nodes"]["process_tank"]["level_pct"]
        outage = {"name": "Utility loss", "area": "Water supply network", "severity": "Large"}
        self.step(600, [outage])
        self.assertTrue(all(not pump["running"] for pump in self.state["pumps"].values()))
        self.assertEqual(sum(pump["power_kw"] for pump in self.state["pumps"].values()), 0)
        self.assertLess(self.state["links"]["process_feed"]["inlet_flow_m3h"], normal_flow)
        # Water already in the full feed line continues reaching the tank after pump trip.
        self.assertGreater(self.state["links"]["process_feed"]["outlet_flow_m3h"], 0)
        self.step(1_800, [outage])
        self.assertLess(self.state["nodes"]["process_tank"]["level_pct"], initial_process_level)
        self.step(18_000, [outage])
        self.assertGreater(self.state["unmet_demand_m3h"], 0)

    def test_process_upset_increases_demand_and_changes_recovery(self):
        baseline = self.state["demand_m3h"]
        upset = {"name": "Process upset", "area": "Water supply network", "severity": "Medium"}
        self.model.advance(self.state, 1, [upset])
        self.assertGreater(self.state["demand_m3h"], baseline)
        self.assertLess(self.state["water_recovery_pct"], 68)

    def test_concentrator_gross_demand_and_reclaim_are_separate_network_flows(self):
        self.model.advance(self.state, 1, [], concentrator_demand_m3h=260,
                           concentrator_reclaim_m3h=190)
        self.assertAlmostEqual(self.state["demand_m3h"], self.state["base_demand_m3h"] + 260)
        self.assertEqual(self.state["concentrator_demand_m3h"], 260)
        self.assertEqual(self.state["concentrator_reclaim_m3h"], 190)
        self.assertAlmostEqual(self.state["concentrator_recovery_pct"], 190 / 260 * 100)
        self.assertLess(self.state["links"]["reclaim_return"]["inlet_flow_m3h"],
                        self.state["base_demand_m3h"] * self.state["water_recovery_pct"] / 100 + 1)

    def test_concentrator_reclaim_enters_sump_then_returns_through_delayed_pump(self):
        pump = self.state["pumps"]["Concentrator reclaim pump"]
        pump["enabled"] = False
        for second in range(1, 121):
            self.model.advance(self.state, second, [], concentrator_demand_m3h=260,
                               concentrator_reclaim_m3h=180)
        sump = self.state["nodes"]["concentrator_reclaim_sump"]
        # Rotor inertia produces a small return flow while the disabled pump coasts down.
        self.assertAlmostEqual(sump["volume_m3"], 6.0, delta=0.1)
        self.assertEqual(self.state["links"]["concentrator_reclaim"]["inlet_flow_m3h"], 0)
        pump["enabled"] = True
        start_volume = sump["volume_m3"]
        return_was_delivered = False
        for second in range(121, 2_000):
            self.model.advance(self.state, second, [], concentrator_demand_m3h=260,
                               concentrator_reclaim_m3h=0)
            return_was_delivered |= self.state["links"]["concentrator_reclaim"]["outlet_flow_m3h"] > 0
        self.assertLess(sump["volume_m3"], start_volume)
        self.assertTrue(return_was_delivered)

    def test_reclaim_return_preserves_network_mass_balance(self):
        initial_storage = self.model.total_inventory_m3(self.state)
        for second in range(1, 3_601):
            self.model.advance(self.state, second, [], concentrator_demand_m3h=260,
                               concentrator_reclaim_m3h=190)
        expected = (initial_storage + self.state["cumulative_concentrator_return_m3"]
                    - self.state["cumulative_process_consumption_m3"]
                    - self.state["cumulative_treated_delivery_m3"]
                    - self.state["total_spilled_m3"])
        self.assertAlmostEqual(self.model.total_inventory_m3(self.state), expected, places=5)

    def test_telemetry_includes_flow_and_storage_in_engineering_units(self):
        tags, diagram = self.model.telemetry(self.state)
        by_id = {tag["tag"]: tag for tag in tags}
        self.assertEqual(by_id["water_supply_network.raw_transfer.flow_rate"]["units"], "m³/h")
        self.assertEqual(by_id["water_supply_network.raw_tank.stored_volume"]["units"], "m³")
        self.assertIn("water_supply_network.intake_pump.pump_running", by_id)
        self.assertIn("water_supply_network.intake_pump.controller_process_value", by_id)
        self.assertIn("water_supply_network.raw_transfer_valve.valve_position", by_id)
        self.assertIn("water_treatment.filter_train.differential_pressure", by_id)
        self.assertIn("water_supply_network.treated_tank.turbidity", by_id)
        self.assertIn("water_supply_network.process_feed.wave_midpoint_peak_this_scan", by_id)
        self.assertIn("water_supply_network.process_feed.wave_destination_min_this_scan", by_id)
        self.assertEqual(len(diagram[0]["nodes"]), 6)
        self.assertEqual(len(diagram[0]["links"]), 5)
        self.assertGreater(diagram[0]["electrical_load_kw"], 0)

    def test_pump_affinity_and_pipe_transport_delay_are_observable(self):
        link = self.state["links"]["process_feed"]
        initial_flow = link["inlet_flow_m3h"]
        initial_delay = link["transport_delay_s"]
        pump = self.state["pumps"]["Process feed pump"]
        pump["speed_pct"] = 48.0
        lower_speed_flow, _ = self.model._link_reading(self.state, "process_feed", self.model.LINKS["process_feed"])
        self.assertLess(lower_speed_flow, initial_flow)
        outage = {"name": "Utility loss", "area": "Water supply network", "severity": "Large"}
        self.model.advance(self.state, 1, [outage])
        self.assertAlmostEqual(self.state["links"]["process_feed"]["outlet_flow_m3h"], initial_flow)
        self.assertLess(self.state["links"]["process_feed"]["inlet_flow_m3h"], 0.0)
        self.assertEqual(self.state["links"]["process_feed"]["check_valve_state"], "CLOSED")
        self.assertGreater(initial_delay, 1)

    def test_rapid_pump_trip_creates_a_damped_water_hammer_pressure_wave(self):
        outage = {"name": "Utility loss", "area": "Water supply network", "severity": "Large"}
        initial_inventory = self.model.total_inventory_m3(self.state)
        normal_velocity = self.state["links"]["process_feed"]["previous_velocity_m_s"]
        self.assertGreater(normal_velocity, 0.1)
        initial_flow = self.state["links"]["process_feed"]["inlet_flow_m3h"]
        initial_speed = self.state["pumps"]["Process feed pump"]["speed_pct"]

        self.model.advance(self.state, 1, [outage])
        link = self.state["links"]["process_feed"]
        pump = self.state["pumps"]["Process feed pump"]
        self.assertGreater(link["inlet_flow_m3h"], 0.0)
        self.assertLess(link["inlet_flow_m3h"], initial_flow)
        self.assertAlmostEqual(pump["speed_pct"], initial_speed * math.exp(-1.0 / 6.0))
        self.assertGreater(link["surge_peak_scan_kpa"], 50.0)
        self.assertLess(link["surge_min_scan_kpa"], 0.0)
        early_reflection_peak = link["surge_peak_scan_kpa"]

        self.model.advance(self.state, 2, [outage])
        self.assertGreater(link["surge_peak_scan_kpa"], 0.0)
        self.assertLess(link["inlet_flow_m3h"], initial_flow)
        tags, _ = self.model.telemetry(self.state)
        by_id = {tag["tag"]: tag for tag in tags}
        self.assertIn("water_supply_network.process_feed.water_hammer_surge", by_id)
        self.assertGreater(by_id["water_supply_network.process_feed.wave_peak_pressure_this_scan"]["value"], 0)
        self.assertIn("water_supply_network.process_feed.wave_destination_pressure", by_id)
        early_reflection_peak = max(early_reflection_peak, link["surge_peak_scan_kpa"])
        peak_check_valve_backflow = 0.0
        for second in range(3, 31):
            self.model.advance(self.state, second, [outage])
            if second <= 5:
                early_reflection_peak = max(early_reflection_peak,
                                            link["surge_peak_scan_kpa"])
            peak_check_valve_backflow = max(
                peak_check_valve_backflow, link["reverse_flow_m3h"])
            if (second > 3 and link["check_valve_state"] == "CLOSED"
                    and not link["check_valve_closure_complete_this_scan"]):
                self.assertEqual(link["inlet_flow_m3h"], 0.0)
                self.assertGreaterEqual(link["outlet_flow_m3h"], 0.0)
        self.assertGreater(peak_check_valve_backflow, 0.0)
        self.assertGreaterEqual(link["check_valve_closure_count"], 1)
        self.assertEqual(link["transport_direction"], 1)
        # The first reflected wave can exceed the launch peak before friction
        # attenuates the later pressure envelope.
        self.assertLess(link["surge_peak_scan_kpa"], early_reflection_peak)
        expected_inventory = (initial_inventory + self.state["cumulative_concentrator_return_m3"]
            - self.state["cumulative_process_consumption_m3"]
            - self.state["cumulative_treated_delivery_m3"] - self.state["total_spilled_m3"])
        self.assertAlmostEqual(self.model.total_inventory_m3(self.state), expected_inventory, places=5)

    def test_characteristic_wave_travels_reflects_and_reverses_sign(self):
        link = self.state["links"]["raw_transfer"]
        spec = self.model.LINKS["raw_transfer"]
        steady_velocity = link["previous_velocity_m_s"]
        changed_velocity = steady_velocity - 1.0
        cells = len(link["wave_right"])

        self.model._propagate_line_waves(link, spec, changed_velocity, 92.0, substeps=10)
        peak_cell = max(range(cells), key=lambda index: abs(link["wave_right"][index]))
        self.assertAlmostEqual(link["wave_right"][peak_cell],
                               max(link["wave_right"]), places=5)
        self.assertEqual(peak_cell, 9)
        self.assertEqual(link["wave_mid_peak_scan_kpa"], 0.0)

        self.model._propagate_line_waves(link, spec, changed_velocity, 92.0, substeps=18)
        self.assertGreater(link["wave_mid_peak_scan_kpa"], 0.0)

        self.model._propagate_line_waves(link, spec, changed_velocity, 92.0, substeps=cells - 28)
        self.assertGreater(link["wave_right"][-1], 0.0)
        self.model._propagate_line_waves(link, spec, changed_velocity, 92.0, substeps=1)
        self.assertLess(link["wave_left"][-1], 0.0)
        self.assertLess(link["wave_destination_min_scan_kpa"], 0.0)

        self.model._propagate_line_waves(link, spec, changed_velocity, 92.0, substeps=cells - 1)
        self.assertLess(link["wave_left"][0], 0.0)
        self.model._propagate_line_waves(link, spec, changed_velocity, 92.0, substeps=1)
        self.assertGreater(link["wave_right"][0], 0.0)

    def test_endpoint_wave_pressure_changes_the_next_hydraulic_flow_solve(self):
        key = "process_feed"
        link = self.state["links"][key]
        spec = self.model.LINKS[key]
        baseline_flow, _ = self.model._link_reading(self.state, key, spec)

        link["wave_source_pressure_kpa"] = 300.0
        link["wave_destination_pressure_kpa"] = 0.0
        source_pressurized_flow, _ = self.model._link_reading(self.state, key, spec)
        self.assertGreater(link["transient_differential_head_m"], 0.0)
        self.assertGreater(source_pressurized_flow, baseline_flow)

        link["wave_source_pressure_kpa"] = 0.0
        link["wave_destination_pressure_kpa"] = 300.0
        receiver_pressurized_flow, _ = self.model._link_reading(self.state, key, spec)
        self.assertLess(link["transient_differential_head_m"], 0.0)
        self.assertLess(receiver_pressurized_flow, baseline_flow)

    def test_wave_launch_matches_joukowsky_pressure_step(self):
        link = self.state["links"]["raw_transfer"]
        spec = self.model.LINKS["raw_transfer"]
        initial_velocity = link["previous_velocity_m_s"]
        changed_velocity = initial_velocity - 1.0

        self.model._propagate_line_waves(link, spec, changed_velocity, 92.0, substeps=1)

        measured_kpa = (link["wave_right"][0] * self.model.WATER_DENSITY_KG_M3
                        * 9.80665 / 1000.0)
        expected_kpa = (-self.model.WATER_DENSITY_KG_M3
                        * self.model.PIPE_WAVE_SPEED_M_S
                        * (changed_velocity - initial_velocity) / 1000.0)
        self.assertAlmostEqual(measured_kpa, expected_kpa, delta=2.0)

    def test_zero_flow_wave_uses_finite_laminar_friction_limit(self):
        key = "raw_transfer"
        link = self.state["links"][key]
        spec = self.model.LINKS[key]
        link["previous_velocity_m_s"] = 0.0
        link["wave_right"][0] = 1.0

        self.model._propagate_line_waves(link, spec, 0.0, 92.0, substeps=1)

        expected_attenuation = math.exp(
            -32.0 * 1.004e-6 / spec["diameter_m"]**2 * self.model.WAVE_TIME_STEP_S)
        self.assertAlmostEqual(link["wave_right"][1], expected_attenuation, places=9)

    def test_legacy_pipe_state_migrates_without_a_false_startup_surge(self):
        link = self.state["links"]["process_feed"]
        del link["previous_velocity_m_s"]
        self.model.migrate(self.state)
        self.assertAlmostEqual(link["previous_velocity_m_s"], link["inlet_flow_m3h"] / 3600 / (
            3.141592653589793 * self.model.LINKS["process_feed"]["diameter_m"] ** 2 / 4))
        self.assertEqual(link["surge_pressure_kpa"], 0.0)
        self.assertEqual(len(link["wave_right"]), round(
            self.model.LINKS["process_feed"]["length_m"]
            / (self.model.PIPE_WAVE_SPEED_M_S * self.model.WAVE_TIME_STEP_S)))

    def test_measurement_noise_is_seeded_and_repeatable(self):
        first, _ = self.model.telemetry(self.state)
        second, _ = self.model.telemetry(self.state)
        self.assertEqual(first, second)
        self.model.advance(self.state, 1, [], seed=99)
        third, _ = self.model.telemetry(self.state)
        tag = "water_supply_network.raw_tank.tank_level"
        self.assertNotEqual(next(t["value"] for t in first if t["tag"] == tag),
                            next(t["value"] for t in third if t["tag"] == tag))

    def test_drifted_level_transmitter_changes_its_auto_controller_and_water_flow(self):
        baseline_model = MineWaterNetwork()
        faulted_model = MineWaterNetwork()
        baseline = baseline_model.initialize()
        faulted = faulted_model.initialize()
        event = {"name": "Signal drift", "area": "Water supply network", "severity": "Large",
                 "started": 0, "duration": 1_000,
                 "target_tag": "water_supply_network.raw_tank.tank_level"}

        baseline_model.advance(baseline, 1, [], seed=17)
        faulted_model.advance(faulted, 1, [event], seed=17)

        baseline_controller = baseline["controllers"]["Intake pump"]
        faulted_controller = faulted["controllers"]["Intake pump"]
        self.assertGreater(faulted_controller["process_value_pct"],
                           baseline_controller["process_value_pct"])
        self.assertLess(faulted_controller["output_pct"], baseline_controller["output_pct"])
        self.assertLess(faulted["links"]["raw_transfer"]["inlet_flow_m3h"],
                        baseline["links"]["raw_transfer"]["inlet_flow_m3h"])

    def test_older_session_state_migrates_to_reclaim_circuit(self):
        legacy = self.model.initialize()
        del legacy["nodes"]["concentrator_reclaim_sump"]
        del legacy["pumps"]["Concentrator reclaim pump"]
        del legacy["controllers"]["Concentrator reclaim pump"]
        del legacy["valves"]["Concentrator reclaim valve"]
        del legacy["links"]["concentrator_reclaim"]
        self.model.migrate(legacy)
        self.assertEqual(legacy["nodes"]["concentrator_reclaim_sump"]["volume_m3"], 0)
        self.assertTrue(legacy["pumps"]["Concentrator reclaim pump"]["enabled"])
        self.assertIn("concentrator_reclaim", legacy["links"])

    def test_treatment_migration_from_live_session_advances_one_scan_only(self):
        legacy = self.model.initialize()
        legacy["sim_seconds"] = 50_000
        del legacy["treatment"]
        del legacy["nodes"]["treated_tank"]["turbidity_ntu"]
        del legacy["nodes"]["treated_tank"]["chlorine_residual_mg_l"]
        del legacy["links"]["reclaim_transfer"]["quality_transport_queue"]
        self.model.advance(legacy, 50_001, [])
        self.assertEqual(legacy["treatment"]["last_advance_seconds"], 50_001)
        self.assertLess(legacy["treatment"]["cumulative_treated_m3"], 0.2)

    def test_level_pi_loops_hold_the_process_tank_near_its_setpoint(self):
        self.step(10_000)
        level = self.state["nodes"]["process_tank"]["level_pct"]
        self.assertGreater(level, 45)
        self.assertLess(level, 75)
        self.assertGreater(self.state["pumps"]["Process feed pump"]["speed_pct"], 0)
        self.assertLess(self.state["pumps"]["Process feed pump"]["speed_pct"], 100)
        self.assertLess(self.state["unmet_demand_m3h"], 1)

    def test_factory_cip_uses_shared_water_and_is_shed_after_site_and_mine_process(self):
        state = self.model.initialize()
        process_tank = state["nodes"]["process_tank"]
        process_tank["volume_m3"] = 0.1
        process_tank["level_pct"] = 100 * 0.1 / process_tank["capacity_m3"]
        process_tank["level_m"] = 0.1 / process_tank["area_m2"]
        for pump in state["pumps"].values():
            pump.update(enabled=False, speed_pct=0.0, speed_command_pct=0.0)
        for link in state["links"].values():
            link["transport_queue"] = deque([0.0] * len(link["transport_queue"]))

        self.model.advance(state, 1, [], concentrator_demand_m3h=200.0,
                           factory_cip_demand_m3h=100.0,
                           factory_cip_effluent_m3h=80.0, power_available_fraction=0.0)
        self.assertAlmostEqual(state["demand_m3h"],
            state["base_demand_m3h"] + 200.0 + 100.0)
        self.assertGreater(state["concentrator_served_m3h"], 0.0)
        self.assertEqual(state["factory_cip_served_m3h"], 0.0)
        self.assertEqual(state["factory_water_available_fraction"], 0.0)
        self.assertEqual(state["cumulative_factory_cip_effluent_m3"], 0.0)


if __name__ == "__main__":
    unittest.main()
