import unittest

from scada_playground.water_distribution import WaterDistribution
from scada_playground.water_network import MineWaterNetwork


class WaterDistributionTests(unittest.TestCase):
    def setUp(self):
        self.model = WaterDistribution()
        self.state = self.model.initialize()

    def step(self, tick, events=()):
        request = self.model.prepare(self.state, tick, list(events), grid_power_fraction=1.0)
        self.model.advance(self.state, tick, list(events), treated_transfer_m3h=request,
                           source_chlorine_mg_l=0.45, source_turbidity_ntu=2.0)

    def test_elevated_tank_head_and_branch_losses_produce_distinct_pressures(self):
        pressures = {name: zone["pressure_kpa"]
                     for name, zone in self.state["zones"].items()}
        self.assertGreater(pressures["District zone A"], pressures["District zone B"])
        self.assertGreater(pressures["District zone B"], pressures["District zone C"])
        self.assertEqual(self.state["service_fraction"], 1.0)
        self.assertGreater(self.state["leak_flow_m3h"], 1.0)
        self.step(1)
        self.assertEqual(self.state["zones"]["District zone A"]["minimum_pressure_kpa"], 150.0)
        tags = {tag["tag"]: tag for tag in self.model.telemetry(self.state, seed=17)}
        self.assertIn("water_distribution.district_zone_c.line_pressure", tags)
        self.assertIn("water_distribution.leak_monitoring.non_revenue_water", tags)
        self.assertEqual(tags["water_distribution.district_zone_a.flow_rate"]["units"], "m³/h")
        self.assertGreater(self.state["zones"]["District zone C"]["water_age_h"], 0.0)
        self.assertLess(self.state["zones"]["District zone C"]["chlorine_residual_mg_l"],
                        self.state["chlorine_residual_mg_l"])
        self.assertIn("water_distribution.district_zone_a.water_age", tags)

    def test_empty_tank_limits_reported_delivery_to_available_inventory(self):
        state = self.model.initialize(initial_tank_volume_m3=0.0001)
        result = self.model.advance(state, 1, [], treated_transfer_m3h=0.0,
                                    source_chlorine_mg_l=0.45, source_turbidity_ntu=2.0)
        self.assertLessEqual(result["delivered_m3h"] / 3600.0, 0.0001 + 1e-9)
        self.assertAlmostEqual(state["customer_served_m3h"] + state["leak_flow_m3h"],
                               result["delivered_m3h"], places=6)
        self.assertGreater(state["unmet_demand_m3h"], 0.0)
        self.assertLessEqual(state["tank_volume_m3"], 1e-6)

    def test_tank_turbidity_mixes_source_quality_and_upset_resuspension(self):
        state = self.model.initialize(initial_tank_volume_m3=0.0)
        self.model.advance(state, 1, [], treated_transfer_m3h=100.0,
                           source_chlorine_mg_l=0.45, source_turbidity_ntu=20.0)
        self.assertGreater(state["turbidity_ntu"], 10.0)
        baseline = state["turbidity_ntu"]
        event = [{"name": "Process upset", "area": "Water distribution", "severity": "Large"}]
        self.model.advance(state, 2, event, treated_transfer_m3h=100.0,
                           source_chlorine_mg_l=0.45, source_turbidity_ntu=20.0)
        self.assertGreater(state["turbidity_ntu"], baseline)

    def test_local_feeder_outage_uses_tank_buffer_then_curbs_high_zone_service(self):
        initial_level = self.state["tank_level_pct"]
        initial_high_zone_pressure = self.state["zones"]["District zone C"]["pressure_kpa"]
        for tick in range(1, 18_301):
            events = []
            if tick <= 18_300:
                events.append({"name": "Utility loss", "area": "Water distribution",
                               "severity": "Large"})
            if 1_800 <= tick < 5_400:
                events.append({"name": "Process upset", "area": "Water distribution",
                               "severity": "Medium"})
            self.step(tick, events)

        self.assertLess(self.state["tank_level_pct"], initial_level * 0.1)
        self.assertLess(self.state["zones"]["District zone C"]["pressure_kpa"],
                        initial_high_zone_pressure)
        self.assertGreater(self.state["unmet_demand_m3h"], 0.0)
        self.assertGreater(self.state["cumulative_leakage_m3"], 0.0)

        tank_at_power_return = self.state["tank_level_pct"]
        for tick in range(18_301, 28_801):
            self.step(tick)
        self.assertGreater(self.state["tank_level_pct"], tank_at_power_return)
        self.assertAlmostEqual(self.state["service_fraction"], 1.0, places=3)
        self.assertEqual(self.state["pump_running"], True)

    def test_distribution_feeder_failure_does_not_trip_mine_water_or_treatment_pumps(self):
        model = MineWaterNetwork()
        state = model.initialize()
        event = [{"name": "Utility loss", "area": "Water distribution", "severity": "Large"}]
        for tick in range(1, 61):
            model.advance(state, tick, event, power_available_fraction=1.0)
        self.assertFalse(state["distribution"]["pump_running"])
        self.assertTrue(all(pump["running"] for pump in state["pumps"].values()))
        self.assertEqual(state["treatment"]["mode"], "FILTERING")
        self.assertLess(state["distribution"]["tank_volume_m3"],
                        model.DISTRIBUTION.TANK_CAPACITY_M3 * .65)

    def test_legacy_migration_adds_empty_tank_without_inventing_water(self):
        model = MineWaterNetwork()
        state = model.initialize()
        del state["distribution"]
        inventory_without_distribution = model.total_inventory_m3(state)
        model.migrate(state)
        self.assertEqual(state["distribution"]["tank_volume_m3"], 0.0)
        self.assertAlmostEqual(model.total_inventory_m3(state), inventory_without_distribution)


if __name__ == "__main__":
    unittest.main()
