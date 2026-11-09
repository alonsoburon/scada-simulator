import unittest

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

    def test_level_pi_loops_hold_the_process_tank_near_its_setpoint(self):
        self.step(10_000)
        level = self.state["nodes"]["process_tank"]["level_pct"]
        self.assertGreater(level, 45)
        self.assertLess(level, 75)
        self.assertGreater(self.state["pumps"]["Process feed pump"]["speed_pct"], 0)
        self.assertLess(self.state["pumps"]["Process feed pump"]["speed_pct"], 100)
        self.assertLess(self.state["unmet_demand_m3h"], 1)


if __name__ == "__main__":
    unittest.main()
