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
    QUEUE_CREEP_SPEED_KMH = 3.0
    SERVICE_POSITION_TOLERANCE_KM = 0.004
    DOWNHILL_BRAKING_SENSITIVITY = 0.065
    TRUCK_TARE_T = 188.0
    ENGINE_RATED_POWER_KW = 2_200.0
    DRIVETRAIN_EFFICIENCY = 0.36
    HAUL_ROAD_ROLLING_RESISTANCE = 0.025
    DIESEL_SPECIFIC_CONSUMPTION_L_KWH = 0.24
    ACCESSORY_POWER_KW = 65.0
    IDLE_FUEL_LPH = 95.0
    SERVICE_BRAKE_POWER_SHARE = 0.18
    BRAKE_SYSTEM_HEAT_CAPACITY_KJ_C = 3_000.0
    BRAKE_COOLING_TIME_S = 900.0
    FUEL_BAY_CAPACITY_L = 60_000.0
    FUEL_BAY_REFUEL_RATE_L_S = 7.5
    FUEL_RESERVE_TRIGGER_PCT = 15.0
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
            grade_pct = 100.0 * (end[3] - start[3]) / max(distance * 1_000.0, 1.0)
            if grade_pct >= 0:
                gross_mass_t = self.TRUCK_TARE_T + payload
                resistance = self.HAUL_ROAD_ROLLING_RESISTANCE + grade_pct / 100.0
                power_limited_speed = (self.ENGINE_RATED_POWER_KW * self.DRIVETRAIN_EFFICIENCY
                                       / max(gross_mass_t * 9.81 * resistance, 1e-9) * 3.6)
                speed = min(loaded_speed, power_limited_speed)
            else:
                speed = loaded_speed / (1.0 + abs(grade_pct) * self.DOWNHILL_BRAKING_SENSITIVITY)
            travel = max(10.0, distance / speed * 3600.0 * 1.5)
            cycle.append({"kind": "travel_loaded", "duration": travel, "from": start, "to": end,
                          "speed_limit_kmh": speed, "distance_km": distance,
                          "road_grade_pct": grade_pct,
                          "label": f"Loaded · {start[0]} to {end[0]}"})
        dwell("tip_queue", 20 + (truck_no * 11) % 40, 3, "Queue at crusher tip")
        dwell("dumping", 75 + (truck_no * 7) % 25, 3, "Dumping at crusher")
        for index in range(3, len(route)):
            start, end = route[index], route[(index + 1) % len(route)]
            distance = self.distance_km((start[1], start[2]), (end[1], end[2]))
            grade_pct = 100.0 * (end[3] - start[3]) / max(distance * 1_000.0, 1.0)
            if grade_pct >= 0:
                resistance = self.HAUL_ROAD_ROLLING_RESISTANCE + grade_pct / 100.0
                power_limited_speed = (self.ENGINE_RATED_POWER_KW * self.DRIVETRAIN_EFFICIENCY
                                       / max(self.TRUCK_TARE_T * 9.81 * resistance, 1e-9) * 3.6)
                speed = min(empty_speed, power_limited_speed)
            else:
                speed = empty_speed / (1.0 + abs(grade_pct) * self.DOWNHILL_BRAKING_SENSITIVITY)
            travel = max(10.0, distance / speed * 3600.0 * 1.5)
            cycle.append({"kind": "travel_empty", "duration": travel, "from": start, "to": end,
                          "speed_limit_kmh": speed, "distance_km": distance,
                          "road_grade_pct": grade_pct,
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

    @classmethod
    def _fuel_rate_lph(cls, frame: dict) -> float:
        if frame.get("state", "").startswith(("Refueling", "Fuel bay empty", "Stopped")):
            return 0.0
        speed = frame["speed_kmh"]
        if speed <= 2.0:
            return cls.IDLE_FUEL_LPH
        grade = frame.get("road_grade_pct", 0.0) / 100.0
        gross_mass_t = cls.TRUCK_TARE_T + max(0.0, frame.get("payload_t", 0.0))
        resistance = max(0.0, cls.HAUL_ROAD_ROLLING_RESISTANCE + grade)
        wheel_power_kw = gross_mass_t * 9.81 * resistance * speed / 3.6
        engine_power_kw = cls.ACCESSORY_POWER_KW + wheel_power_kw / cls.DRIVETRAIN_EFFICIENCY
        return max(cls.IDLE_FUEL_LPH,
                   engine_power_kw * cls.DIESEL_SPECIFIC_CONSUMPTION_L_KWH)

    @classmethod
    def _engine_load_pct(cls, frame: dict) -> float:
        if frame.get("state", "").startswith(("Refueling", "Fuel bay empty", "Stopped")):
            return 0.0
        speed = frame["speed_kmh"]
        if speed <= 2.0:
            return 100.0 * cls.ACCESSORY_POWER_KW / cls.ENGINE_RATED_POWER_KW
        grade = frame.get("road_grade_pct", 0.0) / 100.0
        gross_mass_t = cls.TRUCK_TARE_T + max(0.0, frame.get("payload_t", 0.0))
        resistance = max(0.0, cls.HAUL_ROAD_ROLLING_RESISTANCE + grade)
        wheel_power_kw = gross_mass_t * 9.81 * resistance * speed / 3.6
        engine_power_kw = cls.ACCESSORY_POWER_KW + wheel_power_kw / cls.DRIVETRAIN_EFFICIENCY
        return min(100.0, 100.0 * engine_power_kw / cls.ENGINE_RATED_POWER_KW)

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
                    "payload_t": payload, "road_grade_pct": segment.get("road_grade_pct", 0.0),
                    "state": segment["label"], "cycle_state": kind,
                    "payload_target_t": payload_limit, "cycle_elapsed_s": phase,
                    "mobile": True, "mobility_factor": mobility}
        else:
            point = segment["point"]
            latitude, longitude = point[1], point[2]
            if kind == "loading":
                payload = payload_limit * elapsed / segment["duration"]
            elif kind == "tip_queue":
                payload = payload_limit
            elif kind == "dumping":
                payload = payload_limit * (1.0 - elapsed / segment["duration"])
        state_label = "Standby · ROM stockpile high" if paused_for_rom and kind in ("pit_queue", "loading") else segment["label"]
        return {"unit": unit, "vehicle_type": "Haul truck", "latitude": latitude,
                "longitude": longitude, "speed_kmh": speed, "heading": heading,
                "payload_t": max(0.0, payload), "road_grade_pct": 0.0,
                "state": state_label, "cycle_state": kind,
                "payload_target_t": payload_limit, "cycle_elapsed_s": phase,
                "mobile": kind not in ("pit_queue", "loading", "tip_queue", "dumping"),
                "mobility_factor": mobility}

    def initialize(self) -> dict:
        truck_states = {}
        for index, unit in enumerate(self.truck_names):
            period = self.cycles[unit][-1]["end"]
            phase = (period * index / self.TRUCK_COUNT) % period
            frame = self._frame(unit, phase)
            _segment, elapsed = self._segment_at(unit, phase)
            shovel_arrival = (-elapsed if frame["cycle_state"] in ("pit_queue", "loading")
                              else None)
            tip_arrival = (-elapsed if frame["cycle_state"] in ("tip_queue", "dumping")
                           else None)
            loading_ore_t = frame["payload_t"] if frame["cycle_state"] == "loading" else 0.0
            loading_copper_t = loading_ore_t * 0.0062
            truck_states[unit] = {"phase_s": phase, "frame": frame, "total_delivered_t": 0.0,
                                  "total_delivered_copper_t": 0.0,
                                  "payload_grade_pct": 0.62,
                                  "payload_copper_remaining_t": frame["payload_t"] * 0.0062,
                                  "loading_ore_t": loading_ore_t,
                                  "loading_copper_t": loading_copper_t,
                                  "engine_hours": 0.0, "fuel_used_l": 0.0,
                                  "fuel_capacity_l": 4_500.0,
                                  "refuels_completed": 0, "fuel_starved": False,
                                  "cumulative_fuel_consumed_l": 0.0,
                                  "position_latitude": frame["latitude"],
                                  "position_longitude": frame["longitude"],
                                  "shovel_arrival_s": shovel_arrival,
                                  "loading_queue_since_s": (-elapsed if frame["cycle_state"] == "loading" else None),
                                  "tip_arrival_s": tip_arrival,
                                  "dump_queue_since_s": (-elapsed if frame["cycle_state"] == "dumping" else None),
                                  "tire_temperature_c": 60.0 + (index % 4) * 1.5,
                                  "brake_temperature_c": 65.0 + (index % 5) * 2.0}
        return {"sim_seconds": 0, "trucks": truck_states, "last_delivery_t": 0.0,
                "last_mined_t": 0.0, "cumulative_mined_t": 0.0,
                "last_delivery_copper_t": 0.0, "delivery_grade_pct": 0.0,
                "cumulative_delivery_t": 0.0, "recent_deliveries_t": [], "delivery_rate_tph": 0.0,
                "cumulative_delivery_copper_t": 0.0, "average_delivery_grade_pct": 0.0,
                "fuel_bay_capacity_l": self.FUEL_BAY_CAPACITY_L,
                "fuel_bay_inventory_l": self.FUEL_BAY_CAPACITY_L,
                "fuel_bay_total_dispensed_l": 0.0,
                "fuel_bay_active_truck": None, "fuel_bay_queue": [],
                "rom_dispatch_paused": False, "measured_mobility_factor": 1.0,
                "shovel_queue_length": 0, "tip_queue_length": 0,
                "active_shovel_truck": None, "active_tip_truck": None}

    @staticmethod
    def _severity(events: list[dict], incident: str, area: str) -> float:
        severity_factor = {"Small": 0.18, "Medium": 0.42, "Large": 0.78}
        return max((severity_factor[event["severity"]] for event in events
                    if event["name"] == incident and event["area"] in (area, "Whole factory")), default=0.0)

    def migrate(self, state: dict) -> None:
        """Add shared fueling-bay state to an already running browser session."""
        state.setdefault("fuel_bay_capacity_l", self.FUEL_BAY_CAPACITY_L)
        state.setdefault("fuel_bay_inventory_l", self.FUEL_BAY_CAPACITY_L)
        state.setdefault("fuel_bay_total_dispensed_l", 0.0)
        state.setdefault("fuel_bay_active_truck", None)
        state.setdefault("fuel_bay_queue", [])
        for truck in state.get("trucks", {}).values():
            truck.setdefault("fuel_starved", False)
            truck.setdefault("cumulative_fuel_consumed_l", truck.get("fuel_used_l", 0.0))

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

    def advance(self, state: dict, sim_seconds: int, events: list[dict], rom_inventory_t: float,
                ore_grade_pct: float = 0.62) -> float:
        """Advance trucks and report this tick's ROM delivery tonnes and copper mass."""
        self.migrate(state)
        state["sim_seconds"] = sim_seconds
        ore_grade_pct = min(1.2, max(0.25, ore_grade_pct))
        for key, default in (("last_delivery_copper_t", 0.0), ("delivery_grade_pct", 0.0),
                             ("last_mined_t", 0.0), ("cumulative_mined_t", 0.0),
                             ("cumulative_delivery_copper_t", 0.0),
                             ("average_delivery_grade_pct", 0.0)):
            state.setdefault(key, default)
        fleet_fault = self._severity(events, "Mechanical degradation", "Mobile fleet")
        pit_fault = self._severity(events, "Mechanical degradation", "Open-pit mining")
        utility_fault = self._severity(events, "Utility loss", "Mine power network")
        mobility = max(0.0, 1.0 - fleet_fault * 0.68)
        shovel_rate = max(0.0, 1.0 - pit_fault * 0.72 - utility_fault * 0.55)
        paused_rom = state["rom_dispatch_paused"]
        if paused_rom and rom_inventory_t < self.ROM_RESUME_AT_T:
            paused_rom = False
        committed_rom_t = sum(
            truck["frame"]["payload_t"] for truck in state["trucks"].values()
            if truck["frame"]["cycle_state"] in ("travel_loaded", "tip_queue", "dumping"))
        projected_rom_t = rom_inventory_t + committed_rom_t
        if not paused_rom and projected_rom_t >= self.ROM_PAUSE_AT_T:
            paused_rom = True
        state["rom_dispatch_paused"] = paused_rom
        state["projected_rom_inventory_t"] = projected_rom_t
        state["measured_mobility_factor"] = mobility

        # One metered nozzle serves the FIFO queue at the pit-side fuel bay.
        # Refueling transfers diesel from a finite site tank into the truck tank.
        active_fuel_truck = state.get("fuel_bay_active_truck")
        if active_fuel_truck not in state["trucks"] or (active_fuel_truck and
                state["trucks"][active_fuel_truck]["fuel_used_l"] <= 1e-6):
            active_fuel_truck = None
        fuel_candidates = sorted(
            (unit for unit, truck in state["trucks"].items()
             if truck["frame"]["cycle_state"] == "pit_queue"
             and truck["fuel_used_l"] >= truck["fuel_capacity_l"]
                 * (1.0 - self.FUEL_RESERVE_TRIGGER_PCT / 100.0)),
            key=lambda unit: (state["trucks"][unit].get("shovel_arrival_s")
                              if state["trucks"][unit].get("shovel_arrival_s") is not None
                              else float("inf"), unit))
        if active_fuel_truck is None and fuel_candidates:
            active_fuel_truck = fuel_candidates[0]
        state["fuel_bay_active_truck"] = active_fuel_truck
        state["fuel_bay_queue"] = [unit for unit in fuel_candidates if unit != active_fuel_truck]

        # Older open browser sessions predate queue timestamps; seed those
        # timestamps from their saved cycle phase before forming queue order.
        for unit, truck in state["trucks"].items():
            cycle_state = truck["frame"]["cycle_state"]
            _segment, elapsed = self._segment_at(unit, truck["phase_s"])
            truck.setdefault("position_latitude", truck["frame"]["latitude"])
            truck.setdefault("position_longitude", truck["frame"]["longitude"])
            for key in ("shovel_arrival_s", "loading_queue_since_s", "tip_arrival_s",
                        "dump_queue_since_s"):
                truck.setdefault(key, None)
            if cycle_state in ("pit_queue", "loading") and truck["shovel_arrival_s"] is None:
                truck["shovel_arrival_s"] = sim_seconds - elapsed
            if cycle_state == "loading" and truck["loading_queue_since_s"] is None:
                truck["loading_queue_since_s"] = sim_seconds - elapsed
            if cycle_state in ("tip_queue", "dumping") and truck["tip_arrival_s"] is None:
                truck["tip_arrival_s"] = sim_seconds - elapsed
            if cycle_state == "dumping" and truck["dump_queue_since_s"] is None:
                truck["dump_queue_since_s"] = sim_seconds - elapsed

        # Each loading face and crusher tip is a single shared service point.
        # Truck arrival times form FIFO queues; only the head truck can load or dump.
        shovel_candidates = sorted(
            (unit for unit, truck in state["trucks"].items()
             if truck["frame"]["cycle_state"] == "loading"),
            key=lambda unit: (state["trucks"][unit].get("loading_queue_since_s")
                              if state["trucks"][unit].get("loading_queue_since_s") is not None
                              else float("inf"), unit))
        tip_candidates = sorted(
            (unit for unit, truck in state["trucks"].items()
             if truck["frame"]["cycle_state"] == "dumping"),
            key=lambda unit: (state["trucks"][unit].get("dump_queue_since_s")
                              if state["trucks"][unit].get("dump_queue_since_s") is not None
                              else float("inf"), unit))
        active_shovel = shovel_candidates[0] if shovel_candidates else None
        active_tip = tip_candidates[0] if tip_candidates else None

        deliveries = 0.0
        delivery_copper_t = 0.0
        mined_t = 0.0
        for unit, truck in state["trucks"].items():
            old_phase = truck["phase_s"]
            old_frame = truck["frame"]
            for key in ("shovel_arrival_s", "loading_queue_since_s", "tip_arrival_s",
                        "dump_queue_since_s"):
                truck.setdefault(key, None)
            truck.setdefault("total_delivered_copper_t", truck.get("total_delivered_t", 0.0) * 0.0062)
            truck.setdefault("payload_grade_pct", 0.62)
            truck.setdefault("loading_ore_t", 0.0)
            truck.setdefault("loading_copper_t", 0.0)
            truck.setdefault("payload_copper_remaining_t", old_frame.get("payload_t", 0.0) * 0.0062)
            at_shovel = old_frame["cycle_state"] in ("pit_queue", "loading")
            refueling = active_fuel_truck == unit
            waiting_fuel_bay = unit in state["fuel_bay_queue"]
            fuel_starved = truck.get("fuel_starved", False)
            progress = (0.0 if refueling or waiting_fuel_bay or fuel_starved
                        or (paused_rom and at_shovel) else
                        shovel_rate if old_frame["cycle_state"] == "loading" else 1.0)
            if old_frame["cycle_state"] == "loading" and unit != active_shovel:
                progress = 0.0
            elif old_frame["cycle_state"] == "dumping" and unit != active_tip:
                progress = 0.0
            if old_frame["cycle_state"] == "loading" and unit == active_shovel:
                if self._service_position_distance_km(truck, "shovel") > self.SERVICE_POSITION_TOLERANCE_KM:
                    progress = 0.0
            elif old_frame["cycle_state"] == "dumping" and unit == active_tip:
                if self._service_position_distance_km(truck, "tip") > self.SERVICE_POSITION_TOLERANCE_KM:
                    progress = 0.0
            if old_frame["cycle_state"] in ("travel_loaded", "travel_empty"):
                progress *= mobility
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
            copper_delta_t = min(truck["payload_copper_remaining_t"],
                                 delta * truck["payload_grade_pct"] / 100.0)
            truck["payload_copper_remaining_t"] = max(
                0.0, truck["payload_copper_remaining_t"] - copper_delta_t)
            delivery_copper_t += copper_delta_t
            truck["phase_s"] = new_phase
            truck["frame"] = self._frame(unit, new_phase, mobility, paused_rom)
            new_cycle_state = truck["frame"]["cycle_state"]
            loaded_delta_t = (max(0.0, truck["frame"]["payload_t"] - old_frame["payload_t"])
                              if old_frame["cycle_state"] == "loading" else 0.0)
            if loaded_delta_t > 0.0:
                mined_t += loaded_delta_t
                truck["loading_ore_t"] += loaded_delta_t
                truck["loading_copper_t"] += loaded_delta_t * ore_grade_pct / 100.0
                truck["payload_copper_remaining_t"] += loaded_delta_t * ore_grade_pct / 100.0
                truck["payload_grade_pct"] = (100.0 * truck["loading_copper_t"]
                                               / max(truck["loading_ore_t"], 1e-9))
            if old_frame["cycle_state"] == "loading" and new_cycle_state != "loading":
                if truck["loading_ore_t"] > 1e-9:
                    truck["payload_grade_pct"] = (100.0 * truck["loading_copper_t"]
                                                   / truck["loading_ore_t"])
            elif new_cycle_state == "loading" and old_frame["cycle_state"] != "loading":
                truck["loading_ore_t"] = 0.0
                truck["loading_copper_t"] = 0.0
                truck["payload_copper_remaining_t"] = 0.0
                truck["payload_grade_pct"] = 0.0
            if new_cycle_state == "pit_queue" and old_frame["cycle_state"] != "pit_queue":
                truck["shovel_arrival_s"] = sim_seconds
                truck["loading_queue_since_s"] = None
            elif new_cycle_state == "loading" and old_frame["cycle_state"] != "loading":
                if truck["shovel_arrival_s"] is None:
                    truck["shovel_arrival_s"] = sim_seconds
                truck["loading_queue_since_s"] = sim_seconds
            elif old_frame["cycle_state"] == "loading" and new_cycle_state != "loading":
                truck["shovel_arrival_s"] = None
                truck["loading_queue_since_s"] = None

            if new_cycle_state == "tip_queue" and old_frame["cycle_state"] != "tip_queue":
                truck["tip_arrival_s"] = sim_seconds
                truck["dump_queue_since_s"] = None
            elif new_cycle_state == "dumping" and old_frame["cycle_state"] != "dumping":
                if truck["tip_arrival_s"] is None:
                    truck["tip_arrival_s"] = sim_seconds
                truck["dump_queue_since_s"] = sim_seconds
            elif old_frame["cycle_state"] == "dumping" and new_cycle_state != "dumping":
                truck["tip_arrival_s"] = None
                truck["dump_queue_since_s"] = None
            if refueling:
                dispensed_l = min(self.FUEL_BAY_REFUEL_RATE_L_S,
                                  truck["fuel_used_l"], state["fuel_bay_inventory_l"])
                truck["fuel_used_l"] = max(0.0, truck["fuel_used_l"] - dispensed_l)
                state["fuel_bay_inventory_l"] -= dispensed_l
                state["fuel_bay_total_dispensed_l"] += dispensed_l
                if dispensed_l > 0.0:
                    truck["fuel_starved"] = False
                if truck["fuel_used_l"] <= 1e-6:
                    truck["fuel_used_l"] = 0.0
                    truck["refuels_completed"] += 1
                    state["fuel_bay_active_truck"] = None
                truck["frame"]["state"] = ("Refueling at pit fuel bay" if state["fuel_bay_inventory_l"] > 0
                                             else "Fuel bay empty · awaiting delivery")
                truck["frame"]["speed_kmh"] = 0.0
                truck["frame"]["payload_t"] = 0.0
            elif waiting_fuel_bay:
                truck["frame"]["state"] = "Waiting for pit fuel bay"
                truck["frame"]["speed_kmh"] = 0.0
                truck["frame"]["payload_t"] = 0.0
            elif fuel_starved:
                truck["frame"]["state"] = "Stopped · out of fuel"
                truck["frame"]["speed_kmh"] = 0.0
                truck["frame"]["payload_t"] = 0.0
            truck["total_delivered_t"] += delta
            truck["total_delivered_copper_t"] += copper_delta_t
            cycle_state = truck["frame"]["cycle_state"]
            engine_running = ((cycle_state != "pit_queue" or not paused_rom)
                              and not refueling and not fuel_starved)
            if engine_running:
                truck["engine_hours"] += 1 / 3600
                fuel_rate_lph = self._fuel_rate_lph(truck["frame"])
                fuel_consumed_l = min(fuel_rate_lph / 3600.0,
                                      max(0.0, truck["fuel_capacity_l"] - truck["fuel_used_l"]))
                truck["fuel_used_l"] += fuel_consumed_l
                truck["cumulative_fuel_consumed_l"] += fuel_consumed_l
                if truck["fuel_used_l"] >= truck["fuel_capacity_l"] - 1e-6:
                    truck["fuel_used_l"] = truck["fuel_capacity_l"]
                    truck["fuel_starved"] = True
                    truck["frame"]["state"] = "Stopped · out of fuel"
                    truck["frame"]["speed_kmh"] = 0.0
            speed = truck["frame"]["speed_kmh"]
            truck["tire_temperature_c"] += (58 + speed * .24 - truck["tire_temperature_c"]) / 300.0
            grade = truck["frame"].get("road_grade_pct", 0.0) / 100.0
            gross_mass_t = self.TRUCK_TARE_T + truck["frame"].get("payload_t", 0.0)
            braking_resistance = max(0.0, -(grade + self.HAUL_ROAD_ROLLING_RESISTANCE))
            brake_power_kw = (gross_mass_t * 9.81 * braking_resistance * speed / 3.6
                              * self.SERVICE_BRAKE_POWER_SHARE)
            brake_heat_c = brake_power_kw / self.BRAKE_SYSTEM_HEAT_CAPACITY_KJ_C
            cooled_temperature = (65.0 + (truck["brake_temperature_c"] - 65.0)
                                  * math.exp(-1.0 / self.BRAKE_COOLING_TIME_S))
            truck["brake_temperature_c"] = min(400.0, cooled_temperature + brake_heat_c)

        state["last_delivery_t"] = deliveries
        state["last_delivery_copper_t"] = delivery_copper_t
        state["last_mined_t"] = mined_t
        state["cumulative_mined_t"] += mined_t
        state["delivery_grade_pct"] = (100.0 * delivery_copper_t / deliveries
                                        if deliveries > 1e-9 else 0.0)
        state["cumulative_delivery_t"] += deliveries
        state["cumulative_delivery_copper_t"] += delivery_copper_t
        state["average_delivery_grade_pct"] = (
            100.0 * state["cumulative_delivery_copper_t"] / state["cumulative_delivery_t"]
            if state["cumulative_delivery_t"] > 1e-9 else 0.0)
        state["recent_deliveries_t"].append(deliveries)
        state["recent_deliveries_t"] = state["recent_deliveries_t"][-600:]
        sample_count = max(1, len(state["recent_deliveries_t"]))
        state["delivery_rate_tph"] = sum(state["recent_deliveries_t"]) * 3_600.0 / sample_count
        shovel_line = sorted(
            (unit for unit, truck in state["trucks"].items()
             if truck["frame"]["cycle_state"] in ("pit_queue", "loading")),
            key=lambda unit: (state["trucks"][unit].get("shovel_arrival_s")
                              if state["trucks"][unit].get("shovel_arrival_s") is not None
                              else float("inf"), unit))
        tip_line = sorted(
            (unit for unit, truck in state["trucks"].items()
             if truck["frame"]["cycle_state"] in ("tip_queue", "dumping")),
            key=lambda unit: (state["trucks"][unit].get("tip_arrival_s")
                              if state["trucks"][unit].get("tip_arrival_s") is not None
                              else float("inf"), unit))
        # The truck currently being served occupies slot zero; all others queue
        # along the approach road rather than teleporting to the same map point.
        for truck in state["trucks"].values():
            if truck["frame"]["cycle_state"] not in ("pit_queue", "loading", "tip_queue", "dumping"):
                truck["position_latitude"] = truck["frame"]["latitude"]
                truck["position_longitude"] = truck["frame"]["longitude"]
        self._place_queue(state, shovel_line, active_shovel, "shovel")
        self._place_queue(state, tip_line, active_tip, "tip")
        state["active_shovel_truck"] = (active_shovel if active_shovel in shovel_line else None)
        state["active_tip_truck"] = (active_tip if active_tip in tip_line else None)
        state["shovel_queue_length"] = max(0, len(shovel_line)
                                           - int(state["active_shovel_truck"] is not None))
        state["tip_queue_length"] = max(0, len(tip_line)
                                        - int(state["active_tip_truck"] is not None))
        return deliveries

    def _place_queue(self, state: dict, line: list[str], active: str | None,
                     resource: str) -> None:
        """Give queued vehicles distinct map positions on the approach road."""
        if resource == "shovel":
            approach = self._queue_approach("shovel")
            waiting_label = "Shovel queue"
        else:
            approach = self._queue_approach("tip")
            waiting_label = "Crusher tip queue"
        first = ([active] if active in line else [])
        ordered = first + [unit for unit in line if unit != active]
        queue_spacing_km = 0.04
        approach_km = max(approach["distance_km"], queue_spacing_km)
        start, end = approach["from"], approach["to"]
        queue_heading = math.degrees(math.atan2(end[2] - start[2], end[1] - start[1])) % 360.0
        for rank, unit in enumerate(ordered):
            truck = state["trucks"][unit]
            frame = truck["frame"]
            fraction = max(0.0, 1.0 - rank * queue_spacing_km / approach_km)
            target_lat = start[1] + fraction * (end[1] - start[1])
            target_lon = start[2] + fraction * (end[2] - start[2])
            current_lat = truck["position_latitude"]
            current_lon = truck["position_longitude"]
            distance_km = self.distance_km((current_lat, current_lon), (target_lat, target_lon))
            moved_km = min(distance_km, self.QUEUE_CREEP_SPEED_KMH / 3_600.0)
            move_fraction = moved_km / distance_km if distance_km > 1e-12 else 1.0
            frame["latitude"] = current_lat + move_fraction * (target_lat - current_lat)
            frame["longitude"] = current_lon + move_fraction * (target_lon - current_lon)
            truck["position_latitude"] = frame["latitude"]
            truck["position_longitude"] = frame["longitude"]
            if distance_km > 1e-12:
                frame["heading"] = queue_heading
            frame["speed_kmh"] = moved_km * 3_600.0
            if rank > 0 and frame["cycle_state"] in ("loading", "dumping"):
                frame["state"] = f"{waiting_label} · position {rank + 1}"
            frame["queue_position"] = rank + 1

    def _queue_approach(self, resource: str) -> dict:
        if resource == "shovel":
            return self.cycles[self.truck_names[0]][-1]
        destination = self.routes["Haul truck"][3]
        return next(segment for segment in self.cycles[self.truck_names[0]]
                    if segment["kind"] == "travel_loaded" and segment["to"] == destination)

    def _service_position_distance_km(self, truck: dict, resource: str) -> float:
        approach = self._queue_approach(resource)
        target = approach["to"]
        return self.distance_km((truck["position_latitude"], truck["position_longitude"]),
                                (target[1], target[2]))

    def telemetry(self, state: dict, seed: int = 17) -> list[dict]:
        rows = []
        for unit, truck in state["trucks"].items():
            frame = truck["frame"]
            number = int(unit.rsplit(" ", 1)[1])
            fuel_capacity = truck["fuel_capacity_l"]
            cycle_state = frame["cycle_state"]
            if cycle_state == "pit_queue":
                waiting_since = truck.get("shovel_arrival_s")
                queue_wait = state["sim_seconds"] - waiting_since if waiting_since is not None else 0.0
            elif cycle_state == "loading":
                if state.get("active_shovel_truck") == unit:
                    queue_wait = (self._service_position_distance_km(truck, "shovel")
                                  * 3_600.0 / self.QUEUE_CREEP_SPEED_KMH)
                else:
                    waiting_since = truck.get("loading_queue_since_s")
                    queue_wait = state["sim_seconds"] - waiting_since if waiting_since is not None else 0.0
            elif cycle_state == "tip_queue":
                waiting_since = truck.get("tip_arrival_s")
                queue_wait = state["sim_seconds"] - waiting_since if waiting_since is not None else 0.0
            elif cycle_state == "dumping":
                if state.get("active_tip_truck") == unit:
                    queue_wait = (self._service_position_distance_km(truck, "tip")
                                  * 3_600.0 / self.QUEUE_CREEP_SPEED_KMH)
                else:
                    waiting_since = truck.get("dump_queue_since_s")
                    queue_wait = state["sim_seconds"] - waiting_since if waiting_since is not None else 0.0
            else:
                queue_wait = 0.0
            queue_wait = max(0.0, queue_wait)
            readings = {
                "gps_latitude": (frame["latitude"], "°", -24.4, -24.2),
                "gps_longitude": (frame["longitude"], "°", -69.2, -68.9),
                "gps_speed": (frame["speed_kmh"], "km/h", 0, 60),
                "heading": (frame["heading"], "°", 0, 360),
                "payload": (frame["payload_t"], "t", 0, 250),
                "payload_grade": (truck.get("payload_grade_pct", 0.62)
                                  if frame["payload_t"] > 1e-6 else 0.0, "% Cu", 0, 1.2),
                "payload_target": (frame["payload_target_t"], "t", 0, 250),
                "road_grade": (frame.get("road_grade_pct", 0.0), "%", -20, 20),
                "engine_load": (self._engine_load_pct(frame), "%", 0, 100),
                "fuel_rate": ((0.0 if frame["state"].startswith(
                                    ("Standby", "Refueling", "Fuel bay empty", "Stopped")) else
                               self._fuel_rate_lph(frame)), "L/h", 0, 1_000),
                "fuel_level": (max(0.0, 100.0 - truck["fuel_used_l"] / fuel_capacity * 100), "%", 0, 100),
                "refuel_status": (float(state.get("fuel_bay_active_truck") == unit), "bool", 0, 1),
                "fuel_starved": (float(truck.get("fuel_starved", False)), "bool", 0, 1),
                "refuels_completed": (truck["refuels_completed"], "count", 0, 100_000),
                "engine_hours": (truck["engine_hours"], "h", 0, 100_000),
                "tire_temperature": (truck["tire_temperature_c"], "°C", 0, 180),
                "brake_temperature": (truck["brake_temperature_c"], "°C", 0, 400),
                "cycle_state": (float(self.STATE_CODE[frame["cycle_state"]]), "state_code", 1, 6),
                "loads_completed": (truck["total_delivered_t"] / frame["payload_target_t"], "loads", 0, 1_000_000),
                "queue_position": (frame.get("queue_position", 0), "position", 0, self.TRUCK_COUNT),
                "queue_wait": (queue_wait, "s", 0, 86_400),
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
        for asset, values in (
                ("Shovel dispatch", {
                    "queue_length": (state.get("shovel_queue_length", 0), "trucks", 0, self.TRUCK_COUNT),
                    "active_truck": (self.truck_names.index(state["active_shovel_truck"]) + 1
                                     if state.get("active_shovel_truck") in self.truck_names else 0,
                                     "truck_id", 0, self.TRUCK_COUNT),
                    "ore_rate": (state.get("last_mined_t", 0.0) * 3_600.0, "t/h", 0, 3_500),
                    "cumulative_ore_mined": (state.get("cumulative_mined_t", 0.0), "t", 0, 1e9),
                }),
                ("Crusher tip dispatch", {
                    "queue_length": (state.get("tip_queue_length", 0), "trucks", 0, self.TRUCK_COUNT),
                    "active_truck": (self.truck_names.index(state["active_tip_truck"]) + 1
                                     if state.get("active_tip_truck") in self.truck_names else 0,
                                     "truck_id", 0, self.TRUCK_COUNT),
                }),
                ("Mine fuel bay", {
                    "tank_inventory": (state.get("fuel_bay_inventory_l", 0.0), "L", 0,
                                       state.get("fuel_bay_capacity_l", self.FUEL_BAY_CAPACITY_L)),
                    "tank_level": (100.0 * state.get("fuel_bay_inventory_l", 0.0)
                                   / max(state.get("fuel_bay_capacity_l", self.FUEL_BAY_CAPACITY_L), 1.0),
                                   "%", 0, 100),
                    "dispensing_flow": (self.FUEL_BAY_REFUEL_RATE_L_S * 3_600.0
                                        if state.get("fuel_bay_active_truck")
                                        and state.get("fuel_bay_inventory_l", 0.0) > 0 else 0.0,
                                        "L/h", 0, self.FUEL_BAY_REFUEL_RATE_L_S * 3_600.0),
                    "queue_length": (len(state.get("fuel_bay_queue", [])), "trucks", 0, self.TRUCK_COUNT),
                    "active_truck": (self.truck_names.index(state["fuel_bay_active_truck"]) + 1
                                     if state.get("fuel_bay_active_truck") in self.truck_names else 0,
                                     "truck_id", 0, self.TRUCK_COUNT),
                    "fuel_dispensed_total": (state.get("fuel_bay_total_dispensed_l", 0.0), "L", 0, 1e9),
                })):
            for sensor, (value, units, low, high) in values.items():
                rows.append({"tag": f"mobile_fleet.{asset.lower().replace(' ', '_')}.{sensor}",
                    "area": self.AREA, "unit": asset, "sensor": sensor, "units": units,
                    "value": value, "quality": "GOOD", "low": low, "high": high,
                    "base": value, "phase": 0.0,
                    "last_change": "Shared single-bay FIFO dispatch model"})
        rows.append({"tag": "mobile_fleet.rom_dispatch.projected_inventory",
            "area": self.AREA, "unit": "ROM dispatch", "sensor": "projected_inventory",
            "units": "t", "value": round(state.get("projected_rom_inventory_t", 0.0), 2),
            "quality": "GOOD", "low": 0, "high": self.ROM_CAPACITY_T,
            "base": self.ROM_PAUSE_AT_T, "phase": 0.0,
            "last_change": "ROM stock plus committed truck payloads"})
        return rows

    @staticmethod
    def positions(state: dict) -> list[dict]:
        return [{key: value for key, value in truck["frame"].items()
                 if key in {"unit", "vehicle_type", "latitude", "longitude", "speed_kmh", "heading", "payload_t", "road_grade_pct", "queue_position", "state"}}
                for truck in state["trucks"].values()]

    @staticmethod
    def summary(state: dict) -> dict:
        trucks = list(state["trucks"].values())
        active = sum(truck["frame"]["cycle_state"] not in ("pit_queue", "loading") or
                     not state["rom_dispatch_paused"] for truck in trucks)
        return {"sim_seconds": state["sim_seconds"], "truck_count": len(trucks), "active_trucks": active,
                "rom_dispatch_paused": state["rom_dispatch_paused"],
                "projected_rom_inventory_t": state.get("projected_rom_inventory_t", 0.0),
                "shovel_queue_length": state.get("shovel_queue_length", 0),
                "tip_queue_length": state.get("tip_queue_length", 0),
                "active_shovel_truck": state.get("active_shovel_truck"),
                "active_tip_truck": state.get("active_tip_truck"),
                "delivery_rate_tph": state["delivery_rate_tph"],
                "last_mined_t": state.get("last_mined_t", 0.0),
                "cumulative_mined_t": state.get("cumulative_mined_t", 0.0),
                "last_delivery_t": state["last_delivery_t"],
                "last_delivery_copper_t": state.get("last_delivery_copper_t", 0.0),
                "delivery_grade_pct": state.get("delivery_grade_pct", 0.0),
                "cumulative_delivery_t": state["cumulative_delivery_t"],
                "cumulative_delivery_copper_t": state.get("cumulative_delivery_copper_t", 0.0),
                "average_delivery_grade_pct": state.get("average_delivery_grade_pct", 0.0),
                "fuel_bay_capacity_l": state.get("fuel_bay_capacity_l", HaulTruckFleet.FUEL_BAY_CAPACITY_L),
                "fuel_bay_inventory_l": state.get("fuel_bay_inventory_l", HaulTruckFleet.FUEL_BAY_CAPACITY_L),
                "fuel_bay_level_pct": (100.0 * state.get("fuel_bay_inventory_l", 0.0)
                                       / max(state.get("fuel_bay_capacity_l", HaulTruckFleet.FUEL_BAY_CAPACITY_L), 1.0)),
                "fuel_bay_total_dispensed_l": state.get("fuel_bay_total_dispensed_l", 0.0),
                "fuel_bay_active_truck": state.get("fuel_bay_active_truck"),
                "fuel_bay_queue": list(state.get("fuel_bay_queue", [])),
                "fuel_bay_flow_l_min": (HaulTruckFleet.FUEL_BAY_REFUEL_RATE_L_S * 60.0
                                         if state.get("fuel_bay_active_truck")
                                         and state.get("fuel_bay_inventory_l", 0.0) > 0.0 else 0.0),
                "fuel_starved_trucks": [name for name, truck in state["trucks"].items()
                                         if truck.get("fuel_starved", False)],
                "mean_mobility_factor": state["measured_mobility_factor"]}
