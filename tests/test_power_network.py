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
            battery_energy_delta_mwh -= self.state["battery_scan_average_power_mw"] / 3600.0
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

    def test_source_voltage_falls_with_higher_active_and_reactive_load(self):
        light_load = self.model.initialize()
        heavy_load = self.model.initialize()
        self.model.advance(light_load, 1, [], process_load_mw=8.0,
                           water_load_mw=0.5, seed=17)
        self.model.advance(heavy_load, 1, [], process_load_mw=36.0,
                           water_load_mw=0.5, seed=17)

        self.assertLess(heavy_load["source_voltage_pu"], light_load["source_voltage_pu"])
        self.assertGreater(heavy_load["distribution_losses_mw"],
                           light_load["distribution_losses_mw"])

    def test_motor_start_transient_is_included_in_grid_demand_and_telemetry(self):
        baseline = self.model.initialize()
        starting = self.model.initialize()
        self.model.advance(baseline, 1, [], process_load_mw=10.0,
                           water_load_mw=0.5, seed=17)
        self.model.advance(starting, 1, [], process_load_mw=10.0,
                           water_load_mw=0.5, motor_starting_load_mw=3.0, seed=17)

        self.assertAlmostEqual(starting["motor_starting_load_mw"], 3.0)
        self.assertAlmostEqual(starting["process_requested_mw"]
                               - baseline["process_requested_mw"], 3.0)
        self.assertLess(starting["source_voltage_pu"], baseline["source_voltage_pu"])
        tags = {tag["tag"]: tag for tag in self.model.telemetry(starting)}
        self.assertAlmostEqual(
            tags["mine_power_network.process_motor_starts.starting_load"]["value"], 3.0)

    def test_capacitor_bank_waits_for_persistent_low_voltage(self):
        outage = {"name": "Utility loss", "area": "Grid transmission", "severity": "Large"}
        for tick in range(1, self.model.CAPACITOR_SWITCH_DELAY_S):
            self.model.advance(self.state, tick, [outage], process_load_mw=28.0,
                               water_load_mw=0.5, seed=17)
        self.assertEqual(self.state["capacitor_bank_stage"], 1)
        self.assertEqual(self.state["capacitor_control_timer_s"],
                         self.model.CAPACITOR_SWITCH_DELAY_S - 1)

        self.model.advance(self.state, self.model.CAPACITOR_SWITCH_DELAY_S, [outage],
                           process_load_mw=28.0, water_load_mw=0.5, seed=17)
        self.assertEqual(self.state["capacitor_bank_stage"], 2)
        self.assertEqual(self.state["capacitor_bank_mvar"], 3.5)
        tags = {tag["tag"]: tag for tag in self.model.telemetry(self.state)}
        self.assertAlmostEqual(tags["grid_distribution.capacitor_bank.stage"]["value"],
                               2.0, delta=0.02)

    def test_battery_frequency_droop_reduces_charging_when_frequency_is_low(self):
        nominal = self.model.initialize()
        low_frequency = self.model.initialize()
        low_frequency["frequency_hz"] = 49.0
        self.model.advance(nominal, 1, [], process_load_mw=8.0,
                           water_load_mw=0.5, seed=17)
        self.model.advance(low_frequency, 1, [], process_load_mw=8.0,
                           water_load_mw=0.5, seed=17)

        self.assertGreater(low_frequency["battery_frequency_response_mw"],
                           nominal["battery_frequency_response_mw"])
        tags = {tag["tag"]: tag for tag in self.model.telemetry(low_frequency)}
        self.assertAlmostEqual(tags["grid_generation.battery_storage.frequency_response"]["value"],
                               low_frequency["battery_frequency_response_mw"], delta=0.02)

    def test_diesel_governor_increases_output_below_nominal_frequency(self):
        nominal = self.model.initialize()
        low_frequency = self.model.initialize()
        for state, frequency in ((nominal, 50.0), (low_frequency, 49.0)):
            state["frequency_hz"] = frequency
            state["diesel_started"] = True
            state["diesel_start_timer_s"] = 30
            state["diesel_mw"] = 1.0
            self.model.advance(state, 1, [], process_load_mw=28.0,
                               water_load_mw=0.5, seed=17)

        self.assertGreater(low_frequency["diesel_mw"], nominal["diesel_mw"])
        self.assertGreater(low_frequency["diesel_governor_bias_mw"], 0.0)
        tags = {tag["tag"]: tag for tag in self.model.telemetry(low_frequency)}
        self.assertAlmostEqual(
            tags["grid_generation.turbine_generator_01.governor_bias"]["value"],
            low_frequency["diesel_governor_bias_mw"] * 0.6, delta=0.02)

    def test_underfrequency_sheds_load_in_stages_and_restores_after_stability_delay(self):
        interval = 1 / self.model.FREQUENCY_SUBSTEPS_PER_SCAN
        pickup_steps = round(self.model.UFLS_PICKUP_DELAY_S / interval)
        for stage, frequency in ((1, 49.1), (2, 48.7), (3, 48.3)):
            self.state["frequency_hz"] = frequency
            for _ in range(pickup_steps):
                self.model._advance_ufls_relay(self.state, 28.0, interval)
            self.assertEqual(self.state["ufls_stage"], stage)

        self.assertAlmostEqual(self.state["ufls_shed_mw"], 9.8)
        self.assertAlmostEqual(self.state["process_after_ufls_mw"], 18.2)
        tags = {tag["tag"]: tag for tag in self.model.telemetry(self.state)}
        self.assertAlmostEqual(
            tags["grid_distribution.distribution_incomer.underfrequency_stage"]["value"],
            3.0, delta=0.02)

        restore_steps = round(self.model.UFLS_RESTORE_DELAY_S / interval)
        for _ in range(restore_steps - 1):
            self.state["frequency_hz"] = 49.8
            self.model._advance_ufls_relay(self.state, 28.0, interval)
        self.assertEqual(self.state["ufls_stage"], 3)
        self.assertAlmostEqual(self.state["ufls_restore_timer_s"],
                               self.model.UFLS_RESTORE_DELAY_S - interval)

        self.state["frequency_hz"] = 49.8
        self.model._advance_ufls_relay(self.state, 28.0, interval)
        self.assertEqual(self.state["ufls_stage"], 2)
        self.assertEqual(self.state["ufls_restore_timer_s"], 0)

    def test_island_frequency_resolves_within_scan_and_trips_ufls(self):
        outage = {"name": "Utility loss", "area": "Grid transmission", "severity": "Large"}
        self.model.advance(self.state, 1, [outage], process_load_mw=28.0,
                           water_load_mw=0.5, seed=17)
        first_scan_frequency = self.state["frequency_hz"]
        self.assertLess(first_scan_frequency, 49.2)
        self.assertEqual(self.state["ufls_stage"], 1)
        self.assertGreater(self.state["ufls_operated_this_scan_mw"], 0.0)

        self.model.advance(self.state, 2, [outage], process_load_mw=28.0,
                           water_load_mw=0.5, seed=17)
        self.assertEqual(self.state["ufls_stage"], 1)
        self.assertEqual(self.state["ufls_operated_this_scan_mw"], 0.0)
        self.assertGreater(self.state["frequency_hz"], first_scan_frequency)
        self.assertLessEqual(self.state["frequency_nadir_hz"], first_scan_frequency)

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
        self.assertGreater(north["relay_current_multiple"], 1.0)
        self.assertGreater(north["current_a"], north["pickup_current_a"] * 5.0)
        self.assertLess(north["voltage_pu"], 0.80)
        self.model.advance(self.state, 2, [fault], process_load_mw=28.0,
                           water_load_mw=0.5, seed=17)
        self.assertTrue(self.state["distribution_feeders"]["north"]["breaker_closed"])
        self.model.advance(self.state, 3, [fault], process_load_mw=28.0,
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
        self.assertEqual(self.state["distribution_incomer"]["trip_count"], 0)
        for tick in range(4, 17):
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

    def test_fault_current_depends_on_source_strength_and_fault_location(self):
        fault = {"name": "Electrical fault", "area": "Grid distribution",
                 "target_asset": "Feeder north", "severity": "Large",
                 "fault_location_pct": 0.0}
        source_end = self.model.initialize()
        self.model.advance(source_end, 1, [fault], process_load_mw=28.0,
                           water_load_mw=0.5, seed=17)
        source_end_current = source_end["distribution_feeders"]["north"]["fault_current_a"]
        far_end = self.model.initialize()
        remote_fault = {**fault, "fault_location_pct": 100.0}
        self.model.advance(far_end, 1, [remote_fault], process_load_mw=28.0,
                           water_load_mw=0.5, seed=17)
        far_end_current = far_end["distribution_feeders"]["north"]["fault_current_a"]
        self.assertGreater(source_end_current, far_end_current * 1.5)
        islanded = self.model.initialize()
        islanded["tie_capacity_mw"] = 0.0
        self.model.advance(islanded, 1, [remote_fault], process_load_mw=28.0,
                           water_load_mw=0.5, seed=17)
        island_fault_current = islanded["distribution_feeders"]["north"]["fault_current_a"]
        self.assertGreater(far_end_current, island_fault_current)
        resistive_fault = {**remote_fault, "severity": "Small"}
        high_resistance = self.model.initialize()
        self.model.advance(high_resistance, 1, [resistive_fault], process_load_mw=28.0,
                           water_load_mw=0.5, seed=17)
        self.assertGreater(far_end_current,
                           high_resistance["distribution_feeders"]["north"]["fault_current_a"])

    def test_failed_feeder_breaker_trips_coordinated_incomer_backup(self):
        fault = {"name": "Electrical fault", "area": "Grid distribution",
                 "target_asset": "Feeder north", "severity": "Large",
                 "fault_location_pct": 90.0, "primary_breaker_failure": True}
        primary_operated_at = None
        incomer_trip_at = None
        coordinated_primary_time = None
        coordinated_backup_time = None
        for tick in range(1, 11):
            self.model.advance(self.state, tick, [fault], process_load_mw=28.0,
                               water_load_mw=0.5, seed=17)
            if self.state["distribution_feeders"]["north"]["relay_operated"] and primary_operated_at is None:
                primary_operated_at = tick
                coordinated_primary_time = self.state["distribution_feeders"]["north"]["relay_time_to_trip_s"]
                coordinated_backup_time = self.state["distribution_incomer"]["relay_time_to_trip_s"]
            if self.state["distribution_incomer"]["trip_count"] and incomer_trip_at is None:
                incomer_trip_at = tick
        self.assertIsNotNone(primary_operated_at)
        self.assertIsNotNone(incomer_trip_at)
        self.assertGreater(incomer_trip_at, primary_operated_at)
        north = self.state["distribution_feeders"]["north"]
        incomer = self.state["distribution_incomer"]
        self.assertTrue(north["relay_operated"])
        self.assertEqual(north["breaker_failure_count"], 1)
        self.assertTrue(north["breaker_closed"])
        self.assertFalse(incomer["breaker_closed"])
        self.assertTrue(incomer["lockout"])
        self.assertEqual(incomer["trip_count"], 1)
        self.assertGreater(coordinated_backup_time, coordinated_primary_time)
        self.assertTrue(all(not feeder["energized"]
                            for feeder in self.state["distribution_feeders"].values()))
        self.assertEqual(self.state["voltage_pu"], 0.0)
        with self.assertRaisesRegex(ValueError, "Clear the feeder fault"):
            self.model.reset_feeder_protection(self.state, "distribution_incomer", fault_active=True)
        self.model.reset_feeder_protection(self.state, "distribution_incomer", fault_active=False)
        self.assertTrue(incomer["breaker_closed"])
        self.assertFalse(incomer["lockout"])
        self.model.advance(self.state, 11, [], process_load_mw=28.0,
                           water_load_mw=0.5, seed=17)
        self.assertTrue(all(feeder["energized"]
                            for feeder in self.state["distribution_feeders"].values()))

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
