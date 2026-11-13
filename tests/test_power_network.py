import unittest

from scada_playground.power_network import MinePowerNetwork


class MinePowerNetworkTests(unittest.TestCase):
    def setUp(self):
        self.model = MinePowerNetwork()
        self.state = self.model.initialize()

    def advance(self, seconds, events=None, process_load_mw=28.0, water_load_mw=0.5,
                utility_load_mw=0.0):
        for tick in range(1, seconds + 1):
            self.model.advance(self.state, tick, events or [], process_load_mw=process_load_mw,
                               water_load_mw=water_load_mw, utility_load_mw=utility_load_mw, seed=17)

    def test_normal_dispatch_balances_load_without_unnecessary_storage_discharge(self):
        battery_energy_start = self.state["battery_energy_mwh"]
        battery_energy_delta_mwh = 0.0
        for tick in range(1, 301):
            self.model.advance(self.state, tick, [], process_load_mw=28.0,
                               water_load_mw=0.5, seed=17)
            battery_energy_delta_mwh -= self.state["battery_power_mw"] / 3600.0
        self.assertAlmostEqual(self.state["process_power_fraction"], 1.0)
        self.assertAlmostEqual(self.state["water_power_fraction"], 1.0)
        self.assertGreater(self.state["grid_import_mw"], 20)
        self.assertLessEqual(self.state["battery_power_mw"], 0)
        self.assertGreater(self.state["battery_energy_mwh"], 16.4)
        self.assertAlmostEqual(self.state["battery_energy_mwh"] - battery_energy_start,
                               battery_energy_delta_mwh, places=9)
        self.assertAlmostEqual(self.state["total_generation_mw"],
            self.state["grid_import_mw"] + self.state["solar_mw"] + self.state["diesel_mw"]
            + max(0.0, self.state["battery_power_mw"]), places=9)
        self.assertGreater(self.state["frequency_hz"], 49.8)
        self.assertLess(self.state["frequency_hz"], 50.2)
        self.assertAlmostEqual(self.state["requested_load_mw"], self.state["served_load_mw"], places=6)
        self.assertAlmostEqual(self.state["requested_load_mw"],
                               self.state["served_load_mw"] + self.state["unserved_load_mw"], places=6)
        self.assertAlmostEqual(sum(flow["active_power_mw"]
                                   for flow in self.state["distribution_feeders"].values()),
                               self.state["served_load_mw"] + self.state["distribution_losses_mw"],
                               places=6)

    def test_initial_dispatch_can_supply_a_stopped_dashboard_snapshot(self):
        for key in ("factory_utilities_requested_mw", "factory_utilities_load_mw",
                    "factory_utilities_power_fraction"):
            del self.state[key]
        self.model.advance(self.state, 0, [], process_load_mw=26.9, water_load_mw=0.4,
                           utility_load_mw=0.6, seed=17)
        self.assertGreater(self.state["grid_import_mw"], 20)
        self.assertAlmostEqual(self.state["process_power_fraction"], 1.0)
        self.assertAlmostEqual(self.state["water_power_fraction"], 1.0)
        self.assertAlmostEqual(self.state["factory_utilities_power_fraction"], 1.0)
        self.assertAlmostEqual(self.state["factory_utilities_load_mw"], 0.6)
        self.assertAlmostEqual(self.state["total_generation_mw"],
            self.state["served_load_mw"] + max(0.0, -self.state["battery_power_mw"])
            + self.state["distribution_losses_mw"], delta=0.02)

    def test_old_power_snapshot_migrates_to_feeder_protection_state(self):
        for feeder in self.state["distribution_feeders"].values():
            for key in ("breaker_closed", "relay_pickup", "relay_current_multiple", "relay_time_to_trip_s",
                        "relay_timer_s", "trip_count", "reclose_count", "reclose_timer_s", "lockout",
                        "last_trip_reason"):
                feeder.pop(key, None)
        self.model.advance(self.state, 1, [], process_load_mw=28.0,
                           water_load_mw=0.5, seed=17)
        self.assertTrue(all(feeder["breaker_closed"]
                            for feeder in self.state["distribution_feeders"].values()))

    def test_transmission_outage_dispatches_reserves_and_derates_process_load(self):
        event = {"name": "Utility loss", "area": "Grid transmission", "severity": "Large"}
        initial_energy = self.state["battery_energy_mwh"]
        self.advance(180, [event])
        self.assertEqual(self.state["tie_capacity_mw"], 0.0)
        self.assertEqual(self.state["grid_import_mw"], 0.0)
        self.assertTrue(self.state["diesel_started"])
        self.assertGreater(self.state["diesel_mw"], 5)
        self.assertLess(self.state["process_power_fraction"], 1)
        self.assertLess(self.state["battery_energy_mwh"], initial_energy)
        self.assertGreater(self.state["load_shed_mw"], 0)
        self.assertGreaterEqual(self.state["site_load_mw"], 3.0)
        self.assertLessEqual(self.state["site_load_mw"], 6.0)
        self.assertGreaterEqual(self.state["frequency_hz"], 47)

    def test_feeder_trip_is_local_and_publishes_coherent_dynamic_tags(self):
        event = {"name": "Utility loss", "area": "Grid distribution", "severity": "Large"}
        self.advance(60, [event])
        self.assertLess(self.state["process_power_fraction"], 0.3)
        self.assertGreater(self.state["water_power_fraction"], self.state["process_power_fraction"])
        tags = self.model.telemetry(self.state)
        by_id = {tag["tag"]: tag for tag in tags}
        self.assertEqual(len(by_id), len(tags))
        self.assertIn("grid_generation.battery_storage.state_of_charge", by_id)
        self.assertIn("grid_transmission.intertie.active_power", by_id)
        self.assertIn("mine_power_network.concentrator_substation.active_power", by_id)
        self.assertTrue(all(tag["quality"] == "GOOD" for tag in tags))

    def test_overcurrent_relay_times_trip_and_automatically_recloses(self):
        fault = {"name": "Electrical fault", "area": "Grid distribution",
                 "target_asset": "Feeder north", "severity": "Large"}
        self.model.advance(self.state, 1, [fault], process_load_mw=28.0,
                           water_load_mw=0.5, seed=17)
        north = self.state["distribution_feeders"]["north"]
        self.assertTrue(north["breaker_closed"])
        self.assertTrue(north["relay_pickup"])
        self.assertEqual(north["relay_current_multiple"], 8.0)
        self.assertGreater(north["current_a"], north["pickup_current_a"] * 7.9)
        self.assertLess(north["voltage_pu"], 0.80)
        self.model.advance(self.state, 2, [fault], process_load_mw=28.0,
                           water_load_mw=0.5, seed=17)
        north = self.state["distribution_feeders"]["north"]
        self.assertFalse(north["breaker_closed"])
        self.assertEqual(north["trip_count"], 1)
        self.assertEqual(north["current_a"], 0.0)
        self.assertEqual(north["voltage_pu"], 0.0)
        self.assertGreater(self.state["voltage_pu"], 0.90)
        self.assertAlmostEqual(sum(flow["active_power_mw"]
                                   for flow in self.state["distribution_feeders"].values()),
                               self.state["served_load_mw"] + self.state["distribution_losses_mw"],
                               places=6)
        self.assertLess(self.state["process_power_fraction"], 1.0)
        self.assertTrue(self.state["distribution_feeders"]["south"]["breaker_closed"])
        for tick in range(3, 17):
            self.model.advance(self.state, tick, [], process_load_mw=28.0,
                               water_load_mw=0.5, seed=17)
        self.assertFalse(north["breaker_closed"])
        self.model.advance(self.state, 17, [], process_load_mw=28.0,
                           water_load_mw=0.5, seed=17)
        north = self.state["distribution_feeders"]["north"]
        self.assertTrue(north["breaker_closed"])
        self.assertEqual(north["reclose_count"], 1)
        tags = {tag["tag"]: tag for tag in self.model.telemetry(self.state)}
        self.assertEqual(tags["grid_distribution.feeder_north.breaker_state"]["value"], 1.0)
        self.assertIn("grid_distribution.feeder_north.trip_count", tags)

    def test_persistent_fault_retrips_and_locks_out_after_one_reclose(self):
        fault = {"name": "Electrical fault", "area": "Grid distribution",
                 "target_asset": "Feeder east", "severity": "Small"}
        self.advance(40, [fault])
        east = self.state["distribution_feeders"]["east"]
        self.assertFalse(east["breaker_closed"])
        self.assertTrue(east["lockout"])
        self.assertEqual(east["trip_count"], 2)
        self.assertEqual(east["reclose_count"], 1)
        with self.assertRaisesRegex(ValueError, "Clear the feeder fault"):
            self.model.reset_feeder_protection(self.state, "east", fault_active=True)
        self.model.reset_feeder_protection(self.state, "east", fault_active=False)
        east = self.state["distribution_feeders"]["east"]
        self.assertTrue(east["breaker_closed"])
        self.assertFalse(east["lockout"])
        self.assertEqual(east["trip_count"], 2)

    def test_seeded_measurement_noise_replays(self):
        self.advance(25)
        self.assertEqual(self.model.telemetry(self.state), self.model.telemetry(self.state))

    def test_power_deficit_preserves_water_and_factory_utilities_before_concentrator(self):
        outage = [{"name": "Utility loss", "area": "Grid transmission", "severity": "Large"}]
        self.advance(180, outage, process_load_mw=28.0, water_load_mw=0.5, utility_load_mw=15.0)
        self.assertAlmostEqual(self.state["water_power_fraction"], 1.0)
        self.assertGreater(self.state["factory_utilities_load_mw"], self.state["process_load_mw"])
        self.assertLess(self.state["factory_utilities_power_fraction"], 1.0)
        self.assertAlmostEqual(self.state["process_power_fraction"], 0.0)
        self.assertGreater(self.state["unserved_load_mw"], 0.0)
        self.assertAlmostEqual(self.state["requested_load_mw"],
                               self.state["served_load_mw"] + self.state["unserved_load_mw"], places=6)
        tags = {tag["tag"]: tag for tag in self.model.telemetry(self.state)}
        self.assertLess(abs(tags["mine_power_network.factory_utility_feeder.available_fraction"]["value"]
                            - 100 * self.state["factory_utilities_power_fraction"]), 0.2)


if __name__ == "__main__":
    unittest.main()
