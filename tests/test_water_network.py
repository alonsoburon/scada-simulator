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
        initial_storage = sum(node["volume_m3"] for node in self.state["nodes"].values())
        self.step(900)
        final_storage = sum(node["volume_m3"] for node in self.state["nodes"].values())
        expected = (initial_storage - self.state["cumulative_process_consumption_m3"]
                    - self.state["total_spilled_m3"])
        self.assertAlmostEqual(final_storage, expected, places=6)
        for node in self.state["nodes"].values():
            self.assertGreaterEqual(node["volume_m3"], 0)
            self.assertLessEqual(node["volume_m3"], node["capacity_m3"])

    def test_utility_loss_propagates_to_flow_and_unmet_demand(self):
        self.step(60)
        normal_flow = self.state["links"]["process_feed"]["flow_m3h"]
        initial_process_level = self.state["nodes"]["process_tank"]["level_pct"]
        outage = {"name": "Utility loss", "area": "Water supply network", "severity": "Large"}
        self.step(600, [outage])
        self.assertTrue(all(not pump["running"] for pump in self.state["pumps"].values()))
        self.assertLess(self.state["links"]["process_feed"]["flow_m3h"], normal_flow)
        self.assertLess(self.state["nodes"]["process_tank"]["level_pct"], initial_process_level)
        self.step(18_000, [outage])
        self.assertGreater(self.state["unmet_demand_m3h"], 0)

    def test_process_upset_increases_demand_and_changes_recovery(self):
        baseline = self.state["demand_m3h"]
        upset = {"name": "Process upset", "area": "Water supply network", "severity": "Medium"}
        self.model.advance(self.state, 1, [upset])
        self.assertGreater(self.state["demand_m3h"], baseline)
        self.assertLess(self.state["water_recovery_pct"], 68)

    def test_telemetry_includes_flow_and_storage_in_engineering_units(self):
        tags, diagram = self.model.telemetry(self.state)
        by_id = {tag["tag"]: tag for tag in tags}
        self.assertEqual(by_id["water_supply_network.raw_transfer.flow_rate"]["units"], "m³/h")
        self.assertEqual(by_id["water_supply_network.raw_tank.stored_volume"]["units"], "m³")
        self.assertIn("water_supply_network.intake_pump.pump_running", by_id)
        self.assertIn("water_supply_network.raw_transfer_valve.valve_position", by_id)
        self.assertEqual(len(diagram[0]["nodes"]), 5)
        self.assertEqual(len(diagram[0]["links"]), 4)


if __name__ == "__main__":
    unittest.main()
