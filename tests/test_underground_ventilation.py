import unittest

from scada_playground.underground_ventilation import UndergroundVentilation


class UndergroundVentilationTests(unittest.TestCase):
    def setUp(self):
        self.model = UndergroundVentilation()
        self.state = self.model.initialize()
        self.model.advance(self.state, 0, [], seed=17)

    def test_normal_fan_curve_serves_design_airflow_and_replay_is_deterministic(self):
        other = self.model.initialize()
        self.model.advance(other, 0, [], seed=17)
        self.assertGreater(self.state["total_airflow_m3s"],
                           self.state["required_airflow_m3s"] * 0.90)
        self.assertGreater(self.state["network_pressure_pa"], 500)
        self.assertGreater(self.state["fan_power_kw"], 100)
        self.assertEqual(self.model.telemetry(self.state, 17),
                         self.model.telemetry(other, 17))

    def test_feeder_loss_drops_airflow_before_gas_and_temperature_drift(self):
        temperatures = [zone["temperature_c"]
                        for zone in self.state["branches"].values()]
        methane_start = max(zone["methane_pct"]
                            for zone in self.state["branches"].values())
        outage = [{"name": "Utility loss", "area": "Underground mining",
                   "severity": "Large"}]
        for tick in range(1, 1_801):
            self.model.advance(self.state, tick, outage, seed=17,
                               haulage_activity=0.8)
        self.assertLess(self.state["total_airflow_m3s"], 1.0)
        self.assertTrue(all(not fan["running"] for fan in self.state["fans"].values()))
        self.assertGreater(max(zone["methane_pct"]
                               for zone in self.state["branches"].values()), methane_start)
        self.assertNotEqual([zone["temperature_c"]
                             for zone in self.state["branches"].values()], temperatures)

    def test_restored_power_ramps_fans_and_dilutes_gas_over_time(self):
        outage = [{"name": "Utility loss", "area": "Underground mining",
                   "severity": "Large"}]
        for tick in range(1, 1_201):
            self.model.advance(self.state, tick, outage, seed=17)
        methane_at_recovery = max(zone["methane_pct"]
                                  for zone in self.state["branches"].values())
        self.model.advance(self.state, 1_201, [], seed=17)
        self.assertTrue(all(fan["running"] for fan in self.state["fans"].values()))
        self.assertLess(self.state["total_airflow_m3s"],
                        self.state["required_airflow_m3s"])
        for tick in range(1_202, 3_002):
            self.model.advance(self.state, tick, [], seed=17)
        self.assertGreater(self.state["total_airflow_m3s"],
                           self.state["required_airflow_m3s"] * 0.90)
        self.assertLess(max(zone["methane_pct"]
                            for zone in self.state["branches"].values()), methane_at_recovery)

    def test_mine_power_derates_vfd_speed_and_motor_demand(self):
        constrained = self.model.initialize()
        for tick in range(61):
            self.model.advance(constrained, tick, [], seed=17,
                               power_available_fraction=0.36)
        self.assertLess(constrained["total_airflow_m3s"], self.state["total_airflow_m3s"])
        self.assertLess(constrained["fan_power_kw"], self.state["fan_power_kw"])
        self.assertGreater(constrained["fan_power_kw"], 0)

    def test_fan_vfd_tracks_diesel_air_demand_and_saves_power_at_low_activity(self):
        low_activity = self.model.initialize()
        for tick in range(601):
            self.model.advance(low_activity, tick, [], haulage_activity=0.15)
        full_activity = self.model.initialize()
        for tick in range(601):
            self.model.advance(full_activity, tick, [], haulage_activity=1.0)

        self.assertLess(low_activity["required_airflow_m3s"],
                        full_activity["required_airflow_m3s"])
        self.assertLess(low_activity["total_airflow_m3s"],
                        full_activity["total_airflow_m3s"])
        self.assertLess(low_activity["fan_power_kw"], full_activity["fan_power_kw"])
        self.assertAlmostEqual(low_activity["total_airflow_m3s"],
                               low_activity["required_airflow_m3s"] *
                               self.model.FAN_FLOW_CONTROL_MARGIN, delta=1.5)
        self.assertTrue(all(fan["running"] for fan in low_activity["fans"].values()))

    def test_gas_upset_increases_air_requirement_before_fan_capacity_is_exceeded(self):
        normal = self.model.initialize()
        upset = self.model.initialize()
        event = [{"name": "Process upset", "area": "Underground mining",
                  "severity": "Large"}]
        for tick in range(601):
            self.model.advance(normal, tick, [], haulage_activity=0.8)
            self.model.advance(upset, tick, event, haulage_activity=0.8)
        self.assertGreater(upset["required_airflow_m3s"],
                           normal["required_airflow_m3s"])
        self.assertGreater(upset["total_airflow_m3s"], normal["total_airflow_m3s"])
        self.assertGreater(max(fan["speed_pct"] for fan in upset["fans"].values()), 99.0)
        self.assertLess(upset["total_airflow_m3s"], upset["required_airflow_m3s"])

    def test_process_upset_increases_gas_source_and_telemetry_is_engineering_tagged(self):
        normal = self.model.initialize()
        upset = self.model.initialize()
        for state in (normal, upset):
            self.model.advance(state, 0, [], seed=17)
        event = [{"name": "Process upset", "area": "Underground mining",
                  "severity": "Large"}]
        for tick in range(1, 1_801):
            self.model.advance(normal, tick, [], seed=17)
            self.model.advance(upset, tick, event, seed=17)
        normal_methane = normal["branches"]["Decline level 2"]["methane_pct"]
        upset_methane = upset["branches"]["Decline level 2"]["methane_pct"]
        self.assertGreater(upset_methane, normal_methane)
        tags = {tag["tag"]: tag for tag in self.model.telemetry(upset, seed=17)}
        self.assertIn("underground_mining.decline_level_2.methane", tags)
        self.assertEqual(tags["underground_mining.decline_level_2.methane"]["units"], "% vol")


if __name__ == "__main__":
    unittest.main()
