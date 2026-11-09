import unittest

from scada_playground.concentrator import CopperConcentrator
from scada_playground.plant_controller import PlantController


class PlantControllerTests(unittest.TestCase):
    def setUp(self):
        self.controller = PlantController()
        self.state = self.controller.initialize()
        self.events = []
        self.utilities = {"process_power_fraction": 1.0, "water_power_fraction": 1.0,
                          "process_water_level_pct": 70.0, "voltage_pu": 1.0,
                          "frequency_hz": 50.0}

    def advance(self, seconds, events=None, **overrides):
        values = {**self.utilities, **overrides}
        for tick in range(1, seconds + 1):
            self.controller.advance(self.state, tick, self.events if events is None else events, **values)

    def test_start_and_stop_follow_ordered_sequences(self):
        self.controller.request(self.state, "STOP")
        self.advance(80)
        self.assertEqual(self.state["mode"], "STOPPED")
        self.assertTrue(all(item["state"] == "STOPPED" for item in self.state["equipment"].values()))

        self.controller.request(self.state, "START")
        self.advance(80)
        self.assertEqual(self.state["mode"], "RUNNING")
        self.assertTrue(all(item["state"] == "RUNNING" for item in self.state["equipment"].values()))
        self.assertEqual(self.state["equipment"]["tailings_pump"]["starts"], 2)

    def test_startup_waits_for_water_and_power_permissives(self):
        self.controller.request(self.state, "STOP")
        self.advance(80)
        self.controller.request(self.state, "START")
        self.advance(30, process_water_level_pct=10.0)
        self.assertEqual(self.state["equipment"]["tailings_pump"]["state"], "RUNNING")
        self.assertEqual(self.state["equipment"]["flotation"]["state"], "STOPPED")
        self.assertIn("water", self.state["permissive_reason"].lower())
        self.advance(60)
        self.assertEqual(self.state["mode"], "RUNNING")

    def test_orderly_stop_keeps_equipment_available_during_coastdown(self):
        self.controller.request(self.state, "STOP")
        self.controller.advance(self.state, 1, [], **self.utilities)
        self.assertEqual(self.state["equipment"]["crusher"]["state"], "STOPPING")
        self.assertTrue(self.controller.availability(self.state)["crusher"])

    def test_orderly_stop_propagates_into_concentrator_rates_and_status_tags(self):
        concentrator = CopperConcentrator()
        process = concentrator.initialize()
        baseline = process["plant_feed_tph"]
        self.controller.request(self.state, "STOP")
        for tick in range(1, 100):
            self.controller.advance(self.state, tick, [], **self.utilities)
            concentrator.advance(process, tick, [], equipment_available=self.controller.availability(self.state))
        self.assertEqual(self.state["mode"], "STOPPED")
        self.assertLess(process["plant_feed_tph"], baseline * 0.5)
        statuses = {tag["tag"]: tag["value"] for tag in concentrator.telemetry(process)
                    if tag["sensor"] == "equipment_running"
                    and any(asset in tag["tag"] for asset in
                            ("primary_crusher", "ore_conveyors", "sag_mill", "ball_mill_01",
                             "flotation_cells", "tailings_pump_station_a"))}
        self.assertEqual(len(statuses), 6)
        self.assertEqual(set(statuses.values()), {0.0})

    def test_jam_latches_trip_and_requires_cause_clear_and_manual_reset(self):
        jam = [{"name": "Conveyor jam", "area": "Crushing and screening", "severity": "Large"}]
        self.advance(3, events=jam)
        self.assertEqual(self.state["equipment"]["conveyors"]["state"], "TRIPPED")
        self.assertEqual(self.state["equipment"]["crusher"]["state"], "TRIPPED")
        with self.assertRaisesRegex(ValueError, "clear the active jam"):
            self.controller.reset_trips(self.state, events=jam, power_fraction=1, voltage_pu=1, frequency_hz=50)
        self.controller.reset_trips(self.state, events=[], power_fraction=1, voltage_pu=1, frequency_hz=50)
        self.assertEqual(self.state["mode"], "STOPPED")
        self.controller.request(self.state, "START")
        self.advance(80)
        self.assertEqual(self.state["mode"], "RUNNING")

    def test_filter_press_fault_trips_upstream_feed_and_requires_manual_reset(self):
        fault = [{"name": "Mechanical degradation", "area": "Concentrate handling", "severity": "Large"}]
        self.advance(3, events=fault)
        for name in ("filter_press", "concentrate_thickener", "flotation", "ball_mill", "sag_mill"):
            self.assertEqual(self.state["equipment"][name]["state"], "TRIPPED")
        with self.assertRaisesRegex(ValueError, "clear the active jam or severe mechanical"):
            self.controller.reset_trips(self.state, events=fault, power_fraction=1,
                                        voltage_pu=1, frequency_hz=50)
        self.controller.reset_trips(self.state, events=[], power_fraction=1,
                                    voltage_pu=1, frequency_hz=50)
        self.assertEqual(self.state["trip_count"], 0)
        self.controller.request(self.state, "START")
        self.advance(80)
        self.assertEqual(self.state["mode"], "RUNNING")

    def test_electrical_protection_is_delayed_and_latched(self):
        self.advance(2, process_power_fraction=0.5)
        self.assertEqual(self.state["trip_count"], 0)
        self.advance(1, process_power_fraction=0.5)
        self.assertGreater(self.state["trip_count"], 0)
        with self.assertRaisesRegex(ValueError, "electrical supply"):
            self.controller.reset_trips(self.state, events=[], power_fraction=0.5,
                                        voltage_pu=0.75, frequency_hz=47)

    def test_live_session_migration_adds_downstream_equipment_as_running(self):
        legacy = self.controller.initialize()
        for name in ("tailings_thickener", "filter_press", "concentrate_thickener"):
            legacy["equipment"].pop(name)
        legacy["sequence_index"] = 6
        self.controller.migrate(legacy)
        self.assertEqual(len(legacy["equipment"]), len(self.controller.START_ORDER))
        self.assertEqual(legacy["sequence_index"], len(self.controller.START_ORDER))
        self.assertTrue(all(item["state"] == "RUNNING" for item in legacy["equipment"].values()))


if __name__ == "__main__":
    unittest.main()
