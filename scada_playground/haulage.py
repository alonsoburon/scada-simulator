"""Discrete truck-cycle model for a simulated open-pit haul road."""
from __future__ import annotations

import math
import random


class HaulTruckFleet:
    """Route trucks through shovel loading, loaded haul, dump, and empty return.

    Cycle progress is stateful, while positions are interpolated from simple road
    segments. Every dump delivers a finite payload to the concentrator's ROM bin.
    """

    AREA = "Mobile fleet"
    TRUCK_COUNT = 12
    PAYLOAD_T = 210.0
    ROM_CAPACITY_T = 45_000.0
    ROM_PAUSE_AT_T = 40_000.0
    ROM_RESUME_AT_T = 34_000.0
    STATE_CODE = {"pit_queue": 1, "loading": 2, "travel_loaded": 3,
                  "tip_queue": 4, "dumping": 5, "travel_empty": 6}

    def __init__(self, routes: dict, distance_km):
        self.routes = routes
        self.distance_km = distance_km
        self.truck_names = [f"Haul truck {number:02}" for number in range(1, self.TRUCK_COUNT + 1)]
        self.cycles = {name: self._build_cycle(name) for name in self.truck_names}

    def _build_cycle(self, unit: str) -> list[dict]:
        route = self.routes["Haul truck"]
        truck_no = int(unit.rsplit(" ", 1)[1])
        payload = self.PAYLOAD_T + ((truck_no * 17) % 13) - 6
        loaded_speed = 17.0 + (truck_no % 4) * 0.7
        empty_speed = 25.0 + (truck_no % 3) * 1.2
        cycle = []

        def dwell(kind: str, duration: float, point_index: int, label: str) -> None:
            cycle.append({"kind": kind, "duration": duration, "point": route[point_index], "label": label})

        # Queue plus shovel loading and dump queue plus tipping are explicit stops.
        dwell("pit_queue", 30 + (truck_no * 19) % 55, 0, "Waiting at shovel")
        dwell("loading", 270 + (truck_no * 13) % 70, 0, "Loading at shovel")
        for index in range(3):
            start, end = route[index], route[index + 1]
            distance = self.distance_km((start[1], start[2]), (end[1], end[2]))
            speed = loaded_speed * (0.78 if "ramp" in end[0].casefold() else 1.0)
            travel = max(10.0, distance / speed * 3600.0 * 1.5)
            cycle.append({"kind": "travel_loaded", "duration": travel, "from": start, "to": end,
                          "speed_limit_kmh": speed, "distance_km": distance,
                          "label": f"Loaded · {start[0]} to {end[0]}"})
        dwell("tip_queue", 20 + (truck_no * 11) % 40, 3, "Queue at crusher tip")
        dwell("dumping", 75 + (truck_no * 7) % 25, 3, "Dumping at crusher")
        for index in range(3, len(route)):
            start, end = route[index], route[(index + 1) % len(route)]
            distance = self.distance_km((start[1], start[2]), (end[1], end[2]))
            speed = empty_speed * (0.82 if "bend" in end[0].casefold() else 1.0)
            travel = max(10.0, distance / speed * 3600.0 * 1.5)
            cycle.append({"kind": "travel_empty", "duration": travel, "from": start, "to": end,
                          "speed_limit_kmh": speed, "distance_km": distance,
                          "label": f"Empty · {start[0]} to {end[0]}"})
        cursor = 0.0
        for segment in cycle:
            segment["start"] = cursor
            cursor += segment["duration"]
            segment["end"] = cursor
        return cycle

    @staticmethod
    def _smoothstep(value: float) -> tuple[float, float]:
        fraction = min(1.0, max(0.0, value))
        eased = fraction * fraction * (3 - 2 * fraction)
        derivative = 6 * fraction * (1 - fraction)
        return eased, derivative

    @staticmethod
    def _payload_t(unit: str) -> float:
        truck_no = int(unit.rsplit(" ", 1)[1])
        return 210.0 + ((truck_no * 17) % 13) - 6

    def _segment_at(self, unit: str, phase: float) -> tuple[dict, float]:
        cycle = self.cycles[unit]
        duration = cycle[-1]["end"]
        phase %= duration
        for segment in cycle:
            if phase < segment["end"]:
                return segment, phase - segment["start"]
        return cycle[-1], cycle[-1]["duration"]

    def _frame(self, unit: str, phase: float, mobility: float = 1.0,
               paused_for_rom: bool = False) -> dict:
        segment, elapsed = self._segment_at(unit, phase)
        kind = segment["kind"]
        payload_limit = self._payload_t(unit)
        payload = 0.0
        speed = 0.0
        heading = 0.0
        if kind in ("travel_loaded", "travel_empty"):
            fraction = elapsed / segment["duration"]
            eased, derivative = self._smoothstep(fraction)
            lat0, lon0 = segment["from"][1:3]
            lat1, lon1 = segment["to"][1:3]
            latitude = lat0 + eased * (lat1 - lat0)
            longitude = lon0 + eased * (lon1 - lon0)
            speed = segment["distance_km"] / segment["duration"] * 3_600.0 * derivative * mobility
            heading = math.degrees(math.atan2(lon1 - lon0, lat1 - lat0)) % 360.0
            if kind == "travel_loaded":
                payload = payload_limit
            return {"unit": unit, "vehicle_type": "Haul truck", "latitude": latitude,
                    "longitude": longitude, "speed_kmh": speed, "heading": heading,
                    "payload_t": payload, "state": segment["label"], "cycle_state": kind,
                    "payload_target_t": payload_limit, "cycle_elapsed_s": phase,
                    "mobile": True, "mobility_factor": mobility}
        else:
            point = segment["point"]
            latitude, longitude = point[1], point[2]
            if kind == "loading":
                payload = payload_limit * elapsed / segment["duration"]
            elif kind == "dumping":
                payload = payload_limit * (1.0 - elapsed / segment["duration"])
        state_label = "Standby · ROM stockpile high" if paused_for_rom and kind in ("pit_queue", "loading") else segment["label"]
        return {"unit": unit, "vehicle_type": "Haul truck", "latitude": latitude,
                "longitude": longitude, "speed_kmh": speed, "heading": heading,
                "payload_t": max(0.0, payload), "state": state_label, "cycle_state": kind,
                "payload_target_t": payload_limit, "cycle_elapsed_s": phase,
                "mobile": kind not in ("pit_queue", "loading", "tip_queue", "dumping"),
                "mobility_factor": mobility}

    def initialize(self) -> dict:
        truck_states = {}
        for index, unit in enumerate(self.truck_names):
            period = self.cycles[unit][-1]["end"]
            phase = (period * index / self.TRUCK_COUNT) % period
            frame = self._frame(unit, phase)
            truck_states[unit] = {"phase_s": phase, "frame": frame, "total_delivered_t": 0.0,
                                  "engine_hours": 0.0, "fuel_used_l": 0.0,
                                  "fuel_capacity_l": 4_500.0, "refuel_remaining_s": 0,
                                  "refuels_completed": 0,
                                  "tire_temperature_c": 60.0 + (index % 4) * 1.5,
                                  "brake_temperature_c": 65.0 + (index % 5) * 2.0}
        return {"sim_seconds": 0, "trucks": truck_states, "last_delivery_t": 0.0,
                "cumulative_delivery_t": 0.0, "recent_deliveries_t": [], "delivery_rate_tph": 0.0,
                "rom_dispatch_paused": False, "measured_mobility_factor": 1.0}

    @staticmethod
    def _severity(events: list[dict], incident: str, area: str) -> float:
        severity_factor = {"Small": 0.18, "Medium": 0.42, "Large": 0.78}
        return max((severity_factor[event["severity"]] for event in events
                    if event["name"] == incident and event["area"] in (area, "Whole factory")), default=0.0)

    @staticmethod
    def _dumped_within_cycle(unit: str, cycle: list[dict], phase: float) -> float:
        payload = HaulTruckFleet._payload_t(unit)
        for segment in cycle:
            if segment["kind"] == "dumping":
                return payload * min(1.0, max(0.0, (phase - segment["start"]) / segment["duration"]))
            if phase >= segment["end"]:
                continue
            if phase < segment["start"]:
                return 0.0
            return 0.0
        return payload

    def advance(self, state: dict, sim_seconds: int, events: list[dict], rom_inventory_t: float) -> float:
        """Advance trucks one simulated second and return this tick's ROM delivery tonnes."""
        state["sim_seconds"] = sim_seconds
        fleet_fault = self._severity(events, "Mechanical degradation", "Mobile fleet")
        pit_fault = self._severity(events, "Mechanical degradation", "Open-pit mining")
        utility_fault = self._severity(events, "Utility loss", "Mine power network")
        mobility = max(0.0, 1.0 - fleet_fault * 0.68)
        shovel_rate = max(0.0, 1.0 - pit_fault * 0.72 - utility_fault * 0.55)
        paused_rom = state["rom_dispatch_paused"]
        if paused_rom and rom_inventory_t < self.ROM_RESUME_AT_T:
            paused_rom = False
        elif not paused_rom and rom_inventory_t >= self.ROM_PAUSE_AT_T:
            paused_rom = True
        state["rom_dispatch_paused"] = paused_rom
        state["measured_mobility_factor"] = mobility

        deliveries = 0.0
        for unit, truck in state["trucks"].items():
            old_phase = truck["phase_s"]
            old_frame = truck["frame"]
            at_shovel = old_frame["cycle_state"] in ("pit_queue", "loading")
            refueling = truck["refuel_remaining_s"] > 0
            if not refueling and old_frame["cycle_state"] == "pit_queue" and truck["fuel_used_l"] >= truck["fuel_capacity_l"] * 0.85:
                truck["refuel_remaining_s"] = 600
                refueling = True
            progress = 0.0 if refueling or (paused_rom and at_shovel) else shovel_rate if old_frame["cycle_state"] == "loading" else 1.0
            if old_frame["cycle_state"] in ("travel_loaded", "travel_empty"):
                progress *= mobility
            if refueling:
                truck["refuel_remaining_s"] = max(0, truck["refuel_remaining_s"] - 1)
                if truck["refuel_remaining_s"] == 0:
                    truck["fuel_used_l"] = 0.0
                    truck["refuels_completed"] += 1
            cycle = self.cycles[unit]
            period = cycle[-1]["end"]
            new_unwrapped = old_phase + progress
            new_phase = new_unwrapped % period
            old_dumped = self._dumped_within_cycle(unit, cycle, old_phase)
            new_dumped = self._dumped_within_cycle(unit, cycle, new_phase)
            if new_unwrapped >= period:
                delta = self._payload_t(unit) - old_dumped + new_dumped
            else:
                delta = max(0.0, new_dumped - old_dumped)
            deliveries += delta
            truck["phase_s"] = new_phase
            truck["frame"] = self._frame(unit, new_phase, mobility, paused_rom)
            if refueling:
                truck["frame"]["state"] = "Refueling at mine fuel bay"
                truck["frame"]["speed_kmh"] = 0.0
                truck["frame"]["payload_t"] = 0.0
            truck["total_delivered_t"] += delta
            cycle_state = truck["frame"]["cycle_state"]
            engine_running = (cycle_state != "pit_queue" or not paused_rom) and not refueling
            if engine_running:
                truck["engine_hours"] += 1 / 3600
                fuel_rate_lph = 95.0 if truck["frame"]["speed_kmh"] <= 2 else 370.0 + truck["frame"]["speed_kmh"] * 4.8
                truck["fuel_used_l"] += fuel_rate_lph / 3600.0
            speed = truck["frame"]["speed_kmh"]
            truck["tire_temperature_c"] += (58 + speed * .24 - truck["tire_temperature_c"]) / 300.0
            if old_frame["cycle_state"] == "dumping" or truck["frame"]["cycle_state"] == "dumping":
                truck["brake_temperature_c"] = min(260.0, truck["brake_temperature_c"] + .03)
            else:
                truck["brake_temperature_c"] += (65.0 - truck["brake_temperature_c"]) / 600.0

        state["last_delivery_t"] = deliveries
        state["cumulative_delivery_t"] += deliveries
        state["recent_deliveries_t"].append(deliveries)
        state["recent_deliveries_t"] = state["recent_deliveries_t"][-600:]
        sample_count = max(1, len(state["recent_deliveries_t"]))
        state["delivery_rate_tph"] = sum(state["recent_deliveries_t"]) * 3_600.0 / sample_count
        return deliveries

    def telemetry(self, state: dict, seed: int = 17) -> list[dict]:
        rows = []
        for unit, truck in state["trucks"].items():
            frame = truck["frame"]
            number = int(unit.rsplit(" ", 1)[1])
            fuel_capacity = truck["fuel_capacity_l"]
            readings = {
                "gps_latitude": (frame["latitude"], "°", -24.4, -24.2),
                "gps_longitude": (frame["longitude"], "°", -69.2, -68.9),
                "gps_speed": (frame["speed_kmh"], "km/h", 0, 60),
                "heading": (frame["heading"], "°", 0, 360),
                "payload": (frame["payload_t"], "t", 0, 250),
                "payload_target": (frame["payload_target_t"], "t", 0, 250),
                "engine_load": (min(100.0, 34.0 + frame["speed_kmh"] * 1.45 + frame["payload_t"] * .08), "%", 0, 100),
                "fuel_rate": ((0.0 if frame["state"].startswith(("Standby", "Refueling")) else
                               95.0 if frame["speed_kmh"] <= 1 else 370.0 + frame["speed_kmh"] * 5.0), "L/h", 0, 700),
                "fuel_level": (max(0.0, 100.0 - truck["fuel_used_l"] / fuel_capacity * 100), "%", 0, 100),
                "refuel_status": (float(truck["refuel_remaining_s"] > 0), "bool", 0, 1),
                "refuels_completed": (truck["refuels_completed"], "count", 0, 100_000),
                "engine_hours": (truck["engine_hours"], "h", 0, 100_000),
                "tire_temperature": (truck["tire_temperature_c"], "°C", 0, 180),
                "brake_temperature": (truck["brake_temperature_c"], "°C", 0, 400),
                "cycle_state": (float(self.STATE_CODE[frame["cycle_state"]]), "state_code", 1, 6),
                "loads_completed": (truck["total_delivered_t"] / frame["payload_target_t"], "loads", 0, 1_000_000),
            }
            for sensor, (truth, units, low, high) in readings.items():
                tag_id = f"mobile_fleet.{unit.lower().replace(' ', '_')}.{sensor}"
                if sensor not in {"gps_latitude", "gps_longitude", "heading", "cycle_state"}:
                    stable_id = sum((i + 1) * ord(char) for i, char in enumerate(tag_id))
                    rng = random.Random(seed * 1_000_003 + state["sim_seconds"] * 9_176 + stable_id)
                    truth += rng.gauss(0.0, max(high - low, 1e-9) * 0.0015)
                rows.append({"tag": tag_id, "area": self.AREA, "unit": unit, "sensor": sensor,
                    "units": units, "value": round(min(high, max(low, truth)), 3), "quality": "GOOD",
                    "low": low, "high": high, "base": (low + high) / 2, "phase": 0.0,
                    "last_change": frame["state"]})
        return rows

    @staticmethod
    def positions(state: dict) -> list[dict]:
        return [{key: value for key, value in truck["frame"].items()
                 if key in {"unit", "vehicle_type", "latitude", "longitude", "speed_kmh", "heading", "payload_t", "state"}}
                for truck in state["trucks"].values()]

    @staticmethod
    def summary(state: dict) -> dict:
        trucks = list(state["trucks"].values())
        active = sum(truck["frame"]["cycle_state"] not in ("pit_queue", "loading") or
                     not state["rom_dispatch_paused"] for truck in trucks)
        return {"sim_seconds": state["sim_seconds"], "truck_count": len(trucks), "active_trucks": active,
                "rom_dispatch_paused": state["rom_dispatch_paused"],
                "delivery_rate_tph": state["delivery_rate_tph"],
                "last_delivery_t": state["last_delivery_t"],
                "cumulative_delivery_t": state["cumulative_delivery_t"],
                "mean_mobility_factor": state["measured_mobility_factor"]}
