import math
import unittest

from scada_playground.haulage import HaulTruckFleet
from scada_playground.concentrator import CopperConcentrator


ROUTES = {"Haul truck": [
    ("Pit loading", -24.2910, -69.0810, 32),
    ("Pit ramp bend", -24.2870, -69.0765, 0),
    ("Haul road junction", -24.2815, -69.0730, 0),
    ("Crusher tip", -24.2780, -69.0670, 24),
    ("East return bend", -24.2810, -69.0605, 0),
    ("South return road", -24.2870, -69.0640, 0),
]}


def distance_km(a, b):
    lat_scale = 111.1
    lon_scale = 111.1 * math.cos(math.radians(-24.28))
    return math.hypot((b[0] - a[0]) * lat_scale, (b[1] - a[1]) * lon_scale)


class HaulTruckFleetTests(unittest.TestCase):
    def setUp(self):
        self.model = HaulTruckFleet(ROUTES, distance_km)
        self.state = self.model.initialize()

    def run_ticks(self, state, count, events=None, rom=18_000):
        deliveries = 0.0
        for tick in range(1, count + 1):
            deliveries += self.model.advance(state, tick, events or [], rom)
        return deliveries

    def test_truck_cycles_produce_discrete_payload_deliveries_and_real_tags(self):
        delivered = self.run_ticks(self.state, 3_600)
        self.assertGreater(delivered, 2_000)
        self.assertEqual(len(self.state["trucks"]), 12)
        frames = self.model.positions(self.state)
        self.assertTrue(any(frame["state"].startswith("Loaded") for frame in frames))
        self.assertTrue(any(frame["state"].startswith("Empty") for frame in frames))
        self.assertTrue(all(0 <= frame["payload_t"] <= 216 for frame in frames))
        tags = self.model.telemetry(self.state, seed=42)
        ids = {tag["tag"] for tag in tags}
        self.assertIn("mobile_fleet.haul_truck_01.engine_load", ids)
        self.assertIn("mobile_fleet.haul_truck_01.gps_latitude", ids)
        self.assertIn("mobile_fleet.haul_truck_01.cycle_state", ids)

    def test_mechanical_fault_slows_haul_cycles_without_teleporting_trucks(self):
        baseline_state = self.model.initialize()
        fault_state = self.model.initialize()
        normal_delivered = self.run_ticks(baseline_state, 1_800)
        fault = [{"name": "Mechanical degradation", "area": "Mobile fleet", "severity": "Large"}]
        fault_delivered = self.run_ticks(fault_state, 1_800, fault)
        self.assertLess(fault_delivered, normal_delivered)
        for unit in self.model.truck_names:
            self.assertGreaterEqual(fault_state["trucks"][unit]["frame"]["mobility_factor"], 0.46)
            self.assertLessEqual(fault_state["trucks"][unit]["frame"]["mobility_factor"], 0.48)

    def test_full_rom_stockpile_holds_trucks_at_shovel_and_resumes_with_headroom(self):
        first_hour = self.run_ticks(self.state, 3_600, rom=40_500)
        self.assertTrue(self.state["rom_dispatch_paused"])
        late_deliveries = 0.0
        for tick in range(3_601, 5_401):
            late_deliveries += self.model.advance(self.state, tick, [], 40_500)
        self.assertLess(late_deliveries, first_hour)
        resumed = 0.0
        for tick in range(5_401, 7_201):
            resumed += self.model.advance(self.state, tick, [], 30_000)
        self.assertGreater(resumed, 0)
        self.assertFalse(self.state["rom_dispatch_paused"])

    def test_replay_is_deterministic_for_same_routes_and_events(self):
        first, second = self.model.initialize(), self.model.initialize()
        incident = [{"name": "Mechanical degradation", "area": "Mobile fleet", "severity": "Medium"}]
        for tick in range(1, 600):
            self.model.advance(first, tick, incident, 18_000)
            self.model.advance(second, tick, incident, 18_000)
        self.assertEqual(first, second)

    def test_route_grade_changes_travel_speed_and_loaded_fuel_rate(self):
        cycle = self.model.cycles[self.model.truck_names[0]]
        loaded_climb = next(segment for segment in cycle
                            if segment["kind"] == "travel_loaded" and segment["road_grade_pct"] > 0)
        loaded_flat = next(segment for segment in cycle
                           if segment["kind"] == "travel_loaded" and abs(segment["road_grade_pct"]) < 1e-9)
        loaded_descent = next(segment for segment in cycle
                              if segment["kind"] == "travel_loaded" and segment["road_grade_pct"] < 0)
        empty_climb = next(segment for segment in cycle
                           if segment["kind"] == "travel_empty" and segment["road_grade_pct"] > 0)
        empty_flat = next(segment for segment in cycle
                          if segment["kind"] == "travel_empty" and abs(segment["road_grade_pct"]) < 1e-9)

        def at_midpoint(segment):
            return self.model._frame(self.model.truck_names[0],
                                     segment["start"] + segment["duration"] / 2)

        climb_frame, flat_frame, descent_frame = map(
            at_midpoint, (loaded_climb, loaded_flat, loaded_descent))
        self.assertGreater(climb_frame["road_grade_pct"], 0.0)
        self.assertLess(climb_frame["speed_kmh"], flat_frame["speed_kmh"])
        self.assertLess(descent_frame["speed_kmh"], flat_frame["speed_kmh"])
        self.assertGreater(self.model._fuel_rate_lph(climb_frame),
                           self.model._fuel_rate_lph(flat_frame))
        self.assertLess(at_midpoint(empty_climb)["speed_kmh"],
                        at_midpoint(empty_flat)["speed_kmh"])
        tags = {tag["tag"]: tag for tag in self.model.telemetry(self.state)}
        self.assertIn("mobile_fleet.haul_truck_01.road_grade", tags)

    def test_trucks_refuel_at_pit_bay_before_running_dry(self):
        # At the modeled duty cycle a 4,500 L tank reaches the 85% service
        # threshold after about 27 h, then needs ten minutes at the pit bay.
        self.run_ticks(self.state, 108_000)
        completed = [truck["refuels_completed"] for truck in self.state["trucks"].values()]
        self.assertGreater(sum(completed), 0)
        tags = {tag["tag"]: tag for tag in self.model.telemetry(self.state)}
        self.assertIn("mobile_fleet.haul_truck_01.refuel_status", tags)
        self.assertIn("mobile_fleet.haul_truck_01.fuel_level", tags)
        self.assertIn("mobile_fleet.mine_fuel_bay.tank_inventory", tags)

    def test_shared_pit_fuel_bay_dispenses_finite_inventory_in_fifo_order(self):
        first, second = "Haul truck 01", "Haul truck 02"
        for index, unit in enumerate((first, second)):
            truck = self.state["trucks"][unit]
            pit_queue = next(segment for segment in self.model.cycles[unit]
                             if segment["kind"] == "pit_queue")
            truck["phase_s"] = pit_queue["start"]
            truck["frame"] = self.model._frame(unit, pit_queue["start"])
            truck["fuel_used_l"] = truck["fuel_capacity_l"] * 0.85
            truck["cumulative_fuel_consumed_l"] = truck["fuel_used_l"]
            truck["shovel_arrival_s"] = index + 1
        self.state["fuel_bay_inventory_l"] = 10_000.0
        initial_fuel_inventory = (self.state["fuel_bay_inventory_l"]
                                  + sum(truck["fuel_capacity_l"] - truck["fuel_used_l"]
                                        for truck in self.state["trucks"].values())
                                  + sum(truck["cumulative_fuel_consumed_l"]
                                        for truck in self.state["trucks"].values()))

        self.model.advance(self.state, 1, [], 18_000.0)
        self.assertEqual(self.state["fuel_bay_active_truck"], first)
        self.assertEqual(self.state["fuel_bay_queue"], [second])
        self.assertEqual(self.state["trucks"][second]["refuels_completed"], 0)
        for tick in range(2, 511):
            self.model.advance(self.state, tick, [], 18_000.0)
        self.assertEqual(self.state["trucks"][first]["refuels_completed"], 1)
        self.assertIsNone(self.state["fuel_bay_active_truck"])
        self.model.advance(self.state, 511, [], 18_000.0)
        self.assertEqual(self.state["fuel_bay_active_truck"], second)

        final_fuel_inventory = (self.state["fuel_bay_inventory_l"]
                                + sum(truck["fuel_capacity_l"] - truck["fuel_used_l"]
                                      for truck in self.state["trucks"].values())
                                + sum(truck["cumulative_fuel_consumed_l"]
                                      for truck in self.state["trucks"].values()))
        self.assertAlmostEqual(final_fuel_inventory, initial_fuel_inventory, places=6)
        tags = {tag["tag"]: tag for tag in self.model.telemetry(self.state)}
        self.assertIn("mobile_fleet.mine_fuel_bay.tank_inventory", tags)
        self.assertEqual(tags["mobile_fleet.mine_fuel_bay.queue_length"]["value"], 0)

    def test_empty_truck_tank_immobilizes_vehicle_until_towed_to_fuel_bay(self):
        unit = "Haul truck 01"
        truck = self.state["trucks"][unit]
        loaded_travel = next(segment for segment in self.model.cycles[unit]
                             if segment["kind"] == "travel_loaded")
        truck["phase_s"] = loaded_travel["start"] + 5.0
        truck["frame"] = self.model._frame(unit, truck["phase_s"])
        truck["fuel_used_l"] = truck["fuel_capacity_l"] - 1e-6
        self.model.advance(self.state, 1, [], 18_000.0)
        self.assertTrue(truck["fuel_starved"])
        stopped_phase = truck["phase_s"]
        self.assertIn("out of fuel", truck["frame"]["state"].lower())
        self.model.advance(self.state, 2, [], 18_000.0)
        self.assertEqual(truck["phase_s"], stopped_phase)
        self.assertEqual(truck["frame"]["speed_kmh"], 0.0)

    def test_empty_site_fuel_tank_holds_truck_at_bay_without_fabricating_flow(self):
        unit = "Haul truck 01"
        truck = self.state["trucks"][unit]
        pit_queue = next(segment for segment in self.model.cycles[unit]
                         if segment["kind"] == "pit_queue")
        truck["phase_s"] = pit_queue["start"]
        truck["frame"] = self.model._frame(unit, pit_queue["start"])
        truck["fuel_used_l"] = truck["fuel_capacity_l"] * 0.85
        self.state["fuel_bay_inventory_l"] = 0.0
        self.model.advance(self.state, 1, [], 18_000.0)
        self.assertEqual(self.state["fuel_bay_active_truck"], unit)
        self.assertEqual(truck["frame"]["state"], "Fuel bay empty · awaiting delivery")
        tags = {tag["tag"]: tag for tag in self.model.telemetry(self.state)}
        self.assertEqual(tags["mobile_fleet.mine_fuel_bay.dispensing_flow"]["value"], 0.0)
        self.assertEqual(tags[f"mobile_fleet.haul_truck_01.fuel_rate"]["value"], 0.0)

    def test_old_fleet_snapshot_migrates_to_finite_fuel_bay(self):
        legacy = self.model.initialize()
        for key in ("fuel_bay_capacity_l", "fuel_bay_inventory_l", "fuel_bay_total_dispensed_l",
                    "fuel_bay_active_truck", "fuel_bay_queue"):
            legacy.pop(key)
        for truck in legacy["trucks"].values():
            truck.pop("fuel_starved")
            truck.pop("cumulative_fuel_consumed_l")
        self.model.migrate(legacy)
        self.assertEqual(legacy["fuel_bay_inventory_l"], self.model.FUEL_BAY_CAPACITY_L)
        self.assertTrue(all("fuel_starved" in truck for truck in legacy["trucks"].values()))

    def test_shared_shovel_and_tip_queues_serve_one_truck_and_separate_gps_positions(self):
        for unit in self.model.truck_names:
            loading = next(segment for segment in self.model.cycles[unit]
                           if segment["kind"] == "loading")
            truck = self.state["trucks"][unit]
            truck["phase_s"] = loading["start"] + 20
            truck["frame"] = self.model._frame(unit, truck["phase_s"])
            truck["position_latitude"] = truck["frame"]["latitude"]
            truck["position_longitude"] = truck["frame"]["longitude"]
            truck["shovel_arrival_s"] = 0
            truck["loading_queue_since_s"] = int(unit[-2:])

        before = {unit: truck["phase_s"] for unit, truck in self.state["trucks"].items()}
        self.model.advance(self.state, 1, [], 18_000)
        moved = [unit for unit, truck in self.state["trucks"].items()
                 if truck["phase_s"] != before[unit]]
        self.assertEqual(moved, [self.model.truck_names[0]])
        self.assertEqual(self.state["active_shovel_truck"], self.model.truck_names[0])
        self.assertEqual(self.state["shovel_queue_length"], self.model.TRUCK_COUNT - 1)
        shovel_tags = {tag["tag"]: tag for tag in self.model.telemetry(self.state)}
        self.assertEqual(shovel_tags["mobile_fleet.shovel_dispatch.queue_length"]["value"], 11)
        self.assertTrue(all(truck["frame"]["speed_kmh"] <= self.model.QUEUE_CREEP_SPEED_KMH
                            for truck in self.state["trucks"].values()))
        for tick in range(2, 1_502):
            self.model.advance(self.state, tick, [], 18_000)
        shovel_positions = [(self.state["trucks"][unit]["frame"]["latitude"],
                             self.state["trucks"][unit]["frame"]["longitude"])
                            for unit in self.model.truck_names
                            if self.state["trucks"][unit]["frame"]["cycle_state"] in ("pit_queue", "loading")]
        self.assertGreater(len(shovel_positions), 1)
        self.assertEqual(len(set(shovel_positions)), len(shovel_positions))

        for unit in self.model.truck_names:
            tip_queue = next(segment for segment in self.model.cycles[unit]
                             if segment["kind"] == "tip_queue")
            truck = self.state["trucks"][unit]
            truck["phase_s"] = tip_queue["end"]
            truck["frame"] = self.model._frame(unit, truck["phase_s"])
            truck["position_latitude"] = truck["frame"]["latitude"]
            truck["position_longitude"] = truck["frame"]["longitude"]
            self.assertAlmostEqual(truck["frame"]["payload_t"], truck["frame"]["payload_target_t"])
            truck["tip_arrival_s"] = 0
            truck["dump_queue_since_s"] = int(unit[-2:])

        before = {unit: truck["phase_s"] for unit, truck in self.state["trucks"].items()}
        self.model.advance(self.state, 1_502, [], 18_000)
        moved = [unit for unit, truck in self.state["trucks"].items()
                 if truck["phase_s"] != before[unit]]
        self.assertEqual(moved, [self.model.truck_names[0]])
        self.assertEqual(self.state["active_tip_truck"], self.model.truck_names[0])
        self.assertEqual(self.state["tip_queue_length"], self.model.TRUCK_COUNT - 1)
        map_positions = {item["unit"]: item for item in self.model.positions(self.state)}
        self.assertEqual(sorted(map_positions[unit]["queue_position"]
                                for unit in self.model.truck_names),
                         list(range(1, self.model.TRUCK_COUNT + 1)))
        self.assertTrue(all(self.state["trucks"][unit]["frame"]["speed_kmh"]
                            <= self.model.QUEUE_CREEP_SPEED_KMH
                            for unit in self.model.truck_names))
        tags = {tag["tag"]: tag for tag in self.model.telemetry(self.state)}
        self.assertEqual(tags["mobile_fleet.shovel_dispatch.queue_length"]["value"], 0)
        self.assertEqual(tags["mobile_fleet.crusher_tip_dispatch.queue_length"]["value"], 11)

    def test_truck_tips_feed_concentrator_rom_with_exact_inventory_balance(self):
        concentrator = CopperConcentrator()
        process = concentrator.initialize()
        initial_rom = process["rom_stockpile_t"]
        for tick in range(1, 3_601):
            delivery_t = self.model.advance(self.state, tick, [], process["rom_stockpile_t"])
            concentrator.advance(process, tick, [], seed=17, mine_delivery_t=delivery_t)
        expected_rom = (initial_rom + self.state["cumulative_delivery_t"]
                        - process["cumulative_rom_discharge_t"])
        self.assertAlmostEqual(process["rom_stockpile_t"], expected_rom, places=5)
        self.assertAlmostEqual(process["cumulative_rom_received_t"],
                               self.state["cumulative_delivery_t"], places=5)
        self.assertGreater(process["rom_feed_tph"], 1_000)

    def test_long_crusher_stoppage_fills_rom_then_dispatch_resumes_after_recovery(self):
        concentrator = CopperConcentrator()
        process = concentrator.initialize()
        initial_rom = process["rom_stockpile_t"]
        rom_high_watermark = initial_rom
        projected_rom_high_watermark = initial_rom
        dispatch_paused = False
        resumed_after_pause = False
        for tick in range(1, 72_001):
            events = []
            if 300 <= tick < 21_900:
                events.append({"name": "Conveyor jam", "area": "Crushing and screening", "severity": "Large"})
            if 900 <= tick < 3_300:
                events.append({"name": "Mechanical degradation", "area": "Mobile fleet", "severity": "Medium"})
            delivery = self.model.advance(self.state, tick, events, process["rom_stockpile_t"])
            concentrator.advance(process, tick, events, mine_delivery_t=delivery)
            rom_high_watermark = max(rom_high_watermark, process["rom_stockpile_t"])
            projected_rom_high_watermark = max(
                projected_rom_high_watermark, self.state["projected_rom_inventory_t"])
            if self.state["rom_dispatch_paused"]:
                dispatch_paused = True
            if dispatch_paused and not self.state["rom_dispatch_paused"]:
                resumed_after_pause = True
        expected_rom = initial_rom + process["cumulative_rom_received_t"] - process["cumulative_rom_discharge_t"]
        self.assertGreaterEqual(projected_rom_high_watermark, HaulTruckFleet.ROM_PAUSE_AT_T)
        self.assertLess(rom_high_watermark, HaulTruckFleet.ROM_PAUSE_AT_T)
        self.assertTrue(dispatch_paused)
        self.assertTrue(resumed_after_pause)
        self.assertGreater(process["plant_feed_tph"], 800)
        self.assertAlmostEqual(process["rom_stockpile_t"], expected_rom, places=5)


if __name__ == "__main__":
    unittest.main()
