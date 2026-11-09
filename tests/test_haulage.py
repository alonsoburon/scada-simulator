import math
import unittest

from scada_playground.haulage import HaulTruckFleet
from scada_playground.concentrator import CopperConcentrator


ROUTES = {"Haul truck": [
    ("Pit loading", -24.2910, -69.0810, 0),
    ("Pit ramp bend", -24.2870, -69.0765, 0),
    ("Haul road junction", -24.2815, -69.0730, 0),
    ("Crusher tip", -24.2780, -69.0670, 0),
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

    def test_trucks_refuel_at_the_shovel_queue_before_running_dry(self):
        self.run_ticks(self.state, 43_200)
        completed = [truck["refuels_completed"] for truck in self.state["trucks"].values()]
        self.assertGreater(sum(completed), 0)
        tags = {tag["tag"]: tag for tag in self.model.telemetry(self.state)}
        self.assertIn("mobile_fleet.haul_truck_01.refuel_status", tags)
        self.assertIn("mobile_fleet.haul_truck_01.fuel_level", tags)

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
        dispatch_paused = False
        resumed_after_pause = False
        for tick in range(1, 46_801):
            events = []
            if 300 <= tick < 21_900:
                events.append({"name": "Conveyor jam", "area": "Crushing and screening", "severity": "Large"})
            if 900 <= tick < 3_300:
                events.append({"name": "Mechanical degradation", "area": "Mobile fleet", "severity": "Medium"})
            delivery = self.model.advance(self.state, tick, events, process["rom_stockpile_t"])
            concentrator.advance(process, tick, events, mine_delivery_t=delivery)
            rom_high_watermark = max(rom_high_watermark, process["rom_stockpile_t"])
            if self.state["rom_dispatch_paused"]:
                dispatch_paused = True
            if dispatch_paused and not self.state["rom_dispatch_paused"]:
                resumed_after_pause = True
        expected_rom = initial_rom + process["cumulative_rom_received_t"] - process["cumulative_rom_discharge_t"]
        self.assertGreaterEqual(rom_high_watermark, HaulTruckFleet.ROM_PAUSE_AT_T)
        self.assertTrue(dispatch_paused)
        self.assertTrue(resumed_after_pause)
        self.assertGreater(process["plant_feed_tph"], 800)
        self.assertAlmostEqual(process["rom_stockpile_t"], expected_rom, places=5)


if __name__ == "__main__":
    unittest.main()
