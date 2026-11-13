import unittest
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
        self.assertAlmostEqual(sump["volume_m3"], 6.0, places=5)
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
        self.assertIn("water_supply_network.raw_transfer_valve.valve_position", by_id)
        self.assertIn("water_treatment.filter_train.differential_pressure", by_id)
        self.assertIn("water_supply_network.treated_tank.turbidity", by_id)
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
        self.assertEqual(self.state["links"]["process_feed"]["outlet_flow_m3h"], initial_flow)
        self.assertGreater(initial_delay, 1)

    def test_rapid_pump_trip_creates_a_damped_water_hammer_pressure_wave(self):
        outage = {"name": "Utility loss", "area": "Water supply network", "severity": "Large"}
        normal_velocity = self.state["links"]["process_feed"]["previous_velocity_m_s"]
        self.assertGreater(normal_velocity, 0.1)

        self.model.advance(self.state, 1, [outage])
        link = self.state["links"]["process_feed"]
        self.assertEqual(link["inlet_flow_m3h"], 0.0)
        self.assertGreater(link["surge_pressure_kpa"], 100.0)
        initial_surge = link["surge_pressure_kpa"]

        self.model.advance(self.state, 2, [outage])
        self.assertGreater(link["surge_pressure_kpa"], 0.0)
        self.assertLess(link["surge_pressure_kpa"], initial_surge)
        tags, _ = self.model.telemetry(self.state)
        by_id = {tag["tag"]: tag for tag in tags}
        self.assertIn("water_supply_network.process_feed.water_hammer_surge", by_id)
        self.assertGreater(by_id["water_supply_network.process_feed.transient_gauge_pressure"]["value"], 0)

    def test_legacy_pipe_state_migrates_without_a_false_startup_surge(self):
        link = self.state["links"]["process_feed"]
        del link["previous_velocity_m_s"]
        self.model.migrate(self.state)
        self.assertAlmostEqual(link["previous_velocity_m_s"], link["inlet_flow_m3h"] / 3600 / (
            3.141592653589793 * self.model.LINKS["process_feed"]["diameter_m"] ** 2 / 4))
        self.assertEqual(link["surge_pressure_kpa"], 0.0)

    def test_measurement_noise_is_seeded_and_repeatable(self):
        first, _ = self.model.telemetry(self.state)
        second, _ = self.model.telemetry(self.state)
        self.assertEqual(first, second)
        self.model.advance(self.state, 1, [], seed=99)
        third, _ = self.model.telemetry(self.state)
        tag = "water_supply_network.raw_tank.tank_level"
        self.assertNotEqual(next(t["value"] for t in first if t["tag"] == tag),
                            next(t["value"] for t in third if t["tag"] == tag))

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
