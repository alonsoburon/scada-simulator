"""Finite palletizing, dispatch, and cold-store balances for the factory line."""
from __future__ import annotations

import math
import random


class EndOfLineLogistics:
    AREA_ASSETS = {
        "Palletizing": ["Robot cell A", "Robot cell B", "Pallet conveyor",
                        "Stretch wrapper", "Dispatch buffer"],
        "Warehouse": ["Cold room A", "Cold room B", "Dock doors", "AGV fleet",
                      "Energy meters"],
    }
    CASES_PER_PALLET = 60.0
    CASE_BUFFER_CAPACITY = 360.0
    PALLET_BUFFER_CAPACITY_CASES = 900.0
    COLD_ROOM_CAPACITY_CASES = 6_000.0
    ROOM_SETPOINT_C = 3.0

    def initialize(self) -> dict:
        rooms = {}
        for name in ("Cold room A", "Cold room B"):
            rooms[name] = {
                "inventory_cases": 0.0, "capacity_cases": self.COLD_ROOM_CAPACITY_CASES,
                "temperature_c": 4.0, "setpoint_c": self.ROOM_SETPOINT_C,
                "thermal_mass_kwh_k": 80.0, "ua_kw_k": 2.0,
                "cooling_capacity_kw": 220.0, "cooling_kw": 0.0,
                "door_open_s": 0, "temperature_exposure_case_h": 0.0,
            }
        return {
            "sim_seconds": 0, "case_buffer_cases": 0.0,
            "pallet_buffer_cases": 0.0, "case_acceptance_fraction": 1.0,
            "cases_in_mpm": 0.0, "cases_palletized_mpm": 0.0,
            "cases_wrapped_mpm": 0.0, "cases_dispatched_mpm": 0.0,
            "dispatch_demand_mpm": 52.0, "unmet_dispatch_mpm": 0.0,
            "palletizer_service_fraction": 1.0, "wrapper_service_fraction": 1.0,
            "palletizer_cells": {
                "Robot cell A": {"capacity_cases_min": 42.0, "speed_fraction": 1.0,
                                 "availability": 1.0, "rated_output_cases_min": 42.0,
                                 "output_cases_min": 0.0,
                                 "motor_current_a": 18.0, "vibration_mm_s": 2.0},
                "Robot cell B": {"capacity_cases_min": 42.0, "speed_fraction": 1.0,
                                 "availability": 1.0, "rated_output_cases_min": 42.0,
                                 "output_cases_min": 0.0,
                                 "motor_current_a": 18.0, "vibration_mm_s": 2.0},
            },
            "wrapper_condition": 1.0, "wrapper_motor_current_a": 12.0,
            "warehouse_inventory_cases": 0.0, "warehouse_capacity_cases": 2*self.COLD_ROOM_CAPACITY_CASES,
            "warehouse_occupancy_pct": 0.0, "cold_store_temperature_c": 4.0,
            "cold_store_temperature_exposure_case_h": 0.0,
            "electric_load_mw": 0.13, "requested_load_mw": 0.13,
            "rooms": rooms, "history": [],
        }

    def migrate(self, state: dict) -> None:
        defaults = self.initialize()
        for key, value in defaults.items():
            if key == "history":
                state.setdefault(key, [])
            elif key == "rooms":
                state.setdefault(key, {})
                for room, room_state in value.items():
                    state["rooms"].setdefault(room, room_state.copy())
                    for field, initial in room_state.items():
                        state["rooms"][room].setdefault(field, initial)
            else:
                state.setdefault(key, value)
        for name, cell in defaults["palletizer_cells"].items():
            state["palletizer_cells"].setdefault(name, cell.copy())
            for key, value in cell.items():
                state["palletizer_cells"][name].setdefault(key, value)

    @staticmethod
    def _asset_severity(events: list[dict], incident: str, area: str,
                        asset: str) -> float:
        values = {"Small": 0.18, "Medium": 0.42, "Large": 0.78}
        return max((values.get(event.get("severity"), 0.0) for event in events
                    if event.get("name") == incident
                    and event.get("area") in (area, "Whole factory")
                    and (event.get("area") == "Whole factory"
                         or event.get("target_asset") in (None, asset))), default=0.0)

    @staticmethod
    def _door_open(room: dict, name: str, sim_seconds: int, seed: int) -> None:
        if room["door_open_s"] > 0:
            room["door_open_s"] -= 1
            return
        stable = sum((i + 1) * ord(char) for i, char in enumerate(name))
        rng = random.Random(seed * 1_000_003 + sim_seconds * 9_176 + stable)
        # Forklift loading cycles: A and B have offset, repeatable dock activity.
        interval = 720 if name.endswith("A") else 840
        if rng.random() < 1.0 / interval:
            room["door_open_s"] = rng.randint(15, 45)

    def advance(self, state: dict, sim_seconds: int, events: list[dict], *,
                incoming_cases_mpm: float, power_available_fraction: float = 1.0,
                seed: int = 17) -> None:
        self.migrate(state)
        dt_s = max(0, sim_seconds - state["sim_seconds"])
        state["sim_seconds"] = sim_seconds
        dt_min = dt_s / 60.0
        dt_h = dt_s / 3_600.0
        power = max(0.0, min(1.0, power_available_fraction))
        palletizer_capacity = 0.0
        for name, cell in state["palletizer_cells"].items():
            mechanical = self._asset_severity(events, "Mechanical degradation", "Palletizing", name)
            cell_jam = self._asset_severity(events, "Conveyor jam", "Palletizing", name)
            target_fraction = max(0.0, 1.0 - 0.72 * mechanical) * (1.0 - cell_jam) * power
            cell["speed_fraction"] += max(-0.02, min(0.02,
                target_fraction - cell["speed_fraction"]))
            cell["availability"] = max(0.0, 1.0 - 0.72 * mechanical) * (1.0 - cell_jam)
            cell["rated_output_cases_min"] = cell["capacity_cases_min"] * cell["speed_fraction"]
            cell["motor_current_a"] = 6.0 + 30.0 * cell["speed_fraction"]
            cell["vibration_mm_s"] = 2.0 + 8.0 * mechanical
            palletizer_capacity += cell["rated_output_cases_min"]
        cell_capacity_total = palletizer_capacity
        wrapper_mechanical = self._asset_severity(
            events, "Mechanical degradation", "Palletizing", "Stretch wrapper")
        wrapper_jam = self._asset_severity(events, "Conveyor jam", "Palletizing", "Stretch wrapper")
        pallet_conveyor_jam = self._asset_severity(
            events, "Conveyor jam", "Palletizing", "Pallet conveyor")
        if pallet_conveyor_jam:
            palletizer_capacity *= 1.0 - pallet_conveyor_jam
        dispatch_fault = max(
            self._asset_severity(events, "Process upset", "Warehouse", "AGV fleet"),
            self._asset_severity(events, "Process upset", "Warehouse", "Dock doors"))

        wrapper_condition = max(0.0, 1.0 - 0.65 * wrapper_mechanical)
        wrapper_capacity = 78.0 * wrapper_condition * (1.0 - wrapper_jam) * power
        state["wrapper_condition"] = wrapper_condition
        state["wrapper_motor_current_a"] = 5.0 + 24.0 * min(1.0, wrapper_capacity / 78.0)
        dispatch_demand = 52.0 * (1.0 - dispatch_fault)
        state["dispatch_demand_mpm"] = dispatch_demand
        state["palletizer_service_fraction"] = min(1.0, palletizer_capacity / 84.0)
        state["wrapper_service_fraction"] = min(1.0, wrapper_capacity / 78.0)

        warehouse_cases = sum(room["inventory_cases"] for room in state["rooms"].values())
        warehouse_headroom = max(0.0, state["warehouse_capacity_cases"] - warehouse_cases)
        # Compute downstream capacity first, then constrain each upstream stage
        # against its finite buffer so one scan cannot create or discard cases.
        cold_store_headroom_rate = warehouse_headroom / dt_min if dt_min > 0 else wrapper_capacity
        if dt_min > 0:
            raw_incoming = max(0.0, incoming_cases_mpm)
            wrapper_potential = min(wrapper_capacity,
                state["pallet_buffer_cases"] / dt_min + palletizer_capacity,
                cold_store_headroom_rate + dispatch_demand)
            palletized_capacity = min(palletizer_capacity,
                max(0.0, (self.PALLET_BUFFER_CAPACITY_CASES - state["pallet_buffer_cases"]) / dt_min
                    + wrapper_potential))
            preliminary_palletized = min(palletized_capacity,
                state["case_buffer_cases"] / dt_min + raw_incoming)
            accepted_cases = min(raw_incoming,
                max(0.0, (self.CASE_BUFFER_CAPACITY - state["case_buffer_cases"]) / dt_min
                    + preliminary_palletized))
            actual_palletized = min(palletized_capacity,
                state["case_buffer_cases"] / dt_min + accepted_cases)
            actual_wrapped = min(wrapper_capacity,
                state["pallet_buffer_cases"] / dt_min + actual_palletized,
                cold_store_headroom_rate + dispatch_demand)
        else:
            accepted_cases = actual_palletized = actual_wrapped = 0.0
        state["cases_in_mpm"] = accepted_cases
        state["cases_palletized_mpm"] = actual_palletized
        state["cases_wrapped_mpm"] = actual_wrapped
        for cell in state["palletizer_cells"].values():
            cell["output_cases_min"] = (actual_palletized * cell["rated_output_cases_min"]
                / cell_capacity_total if cell_capacity_total > 1e-9 else 0.0)

        wrapped_by_room = {name: 0.0 for name in state["rooms"]}
        if dt_min > 0:
            state["case_buffer_cases"] = max(0.0, min(self.CASE_BUFFER_CAPACITY,
                state["case_buffer_cases"] + (accepted_cases - actual_palletized) * dt_min))
            state["pallet_buffer_cases"] = max(0.0, min(self.PALLET_BUFFER_CAPACITY_CASES,
                state["pallet_buffer_cases"] + (actual_palletized - actual_wrapped) * dt_min))

        # Dispatcher demand is met from oldest aggregate stock; the two rooms are
        # interchangeable for this training model, with proportional withdrawal.
        warehouse_cases = sum(room["inventory_cases"] for room in state["rooms"].values())
        dispatched = min(dispatch_demand,
                         warehouse_cases / dt_min if dt_min > 0 else 0.0)
        state["cases_dispatched_mpm"] = dispatched
        state["unmet_dispatch_mpm"] = max(0.0, dispatch_demand - dispatched)
        if dt_min > 0:
            stock_after_dispatch = max(0.0, warehouse_cases - dispatched * dt_min)
            for room in state["rooms"].values():
                room["inventory_cases"] = (stock_after_dispatch * room["inventory_cases"]
                                            / warehouse_cases if warehouse_cases > 1e-9 else 0.0)
            # Split new pallets between rooms; use the remaining room's space
            # if the other one is full rather than overfill either inventory.
            wrapped_cases = actual_wrapped * dt_min
            room_count = max(1, len(state["rooms"]))
            for name, room in state["rooms"].items():
                put = min(wrapped_cases / room_count,
                          room["capacity_cases"] - room["inventory_cases"])
                room["inventory_cases"] += put
                wrapped_by_room[name] = put / dt_min
                wrapped_cases -= put
                room_count -= 1
            if wrapped_cases > 1e-9:
                for name, room in state["rooms"].items():
                    put = min(wrapped_cases, room["capacity_cases"] - room["inventory_cases"])
                    room["inventory_cases"] += put
                    wrapped_by_room[name] += put / dt_min
                    wrapped_cases -= put

        ambient = 27.0 + 6.0 * math.sin(2.0 * math.pi * (sim_seconds % 86_400) / 86_400.0)
        cold_power = 0.0
        for name, room in state["rooms"].items():
            self._door_open(room, name, sim_seconds, seed)
            warehouse_fault = self._asset_severity(events, "Utility loss", "Warehouse", name)
            # A Large utility-loss event means refrigeration has tripped; Small
            # and Medium degrade its available compressor capacity.
            refrigeration_fraction = max(0.0, 1.0 - warehouse_fault / 0.78)
            local_power = power * refrigeration_fraction
            door_ua = 7.0 if room["door_open_s"] > 0 else 0.0
            envelope_gain_kw = (room["ua_kw_k"] + door_ua) * (ambient - room["temperature_c"])
            product_load_kw = max(0.0, wrapped_by_room[name]) * 0.105 * max(
                0.0, 12.0 - room["temperature_c"])
            error = max(0.0, room["temperature_c"] - room["setpoint_c"])
            cooling = min(room["cooling_capacity_kw"] * local_power,
                          max(0.0, envelope_gain_kw + product_load_kw
                              + error * room["thermal_mass_kwh_k"] * 0.15))
            room["cooling_kw"] = cooling
            if dt_h > 0:
                room["temperature_c"] += (envelope_gain_kw + product_load_kw - cooling) * dt_h / room["thermal_mass_kwh_k"]
                room["temperature_c"] = max(-5.0, min(45.0, room["temperature_c"]))
                if room["temperature_c"] > 8.0:
                    room["temperature_exposure_case_h"] += room["inventory_cases"] * dt_h
            cold_power += cooling / 2.8

        total_cases = sum(room["inventory_cases"] for room in state["rooms"].values())
        state["warehouse_inventory_cases"] = total_cases
        state["warehouse_occupancy_pct"] = 100.0 * total_cases / state["warehouse_capacity_cases"]
        state["cold_store_temperature_c"] = max(room["temperature_c"] for room in state["rooms"].values())
        state["cold_store_temperature_exposure_case_h"] = sum(
            room["temperature_exposure_case_h"] for room in state["rooms"].values())
        state["requested_load_mw"] = (35.0 + cold_power + actual_palletized * 0.10
                                       + actual_wrapped * 0.08) / 1000.0
        state["electric_load_mw"] = state["requested_load_mw"] * power

        # Backpressure begins when any downstream buffer is nearly full. The
        # existing case accumulator in the packaging model provides a final buffer.
        case_fill = state["case_buffer_cases"] / self.CASE_BUFFER_CAPACITY
        pallet_fill = state["pallet_buffer_cases"] / self.PALLET_BUFFER_CAPACITY_CASES
        warehouse_fill = state["warehouse_occupancy_pct"] / 100.0
        congestion = max(case_fill, pallet_fill, warehouse_fill)
        state["case_acceptance_fraction"] = max(0.0, min(1.0,
            (0.98 - congestion) / 0.18))
        if sim_seconds > 0 and sim_seconds % 15 == 0:
            state["history"].append({key: state[key] for key in (
                "sim_seconds", "case_buffer_cases", "pallet_buffer_cases",
                "warehouse_inventory_cases", "warehouse_occupancy_pct",
                "cold_store_temperature_c", "cases_dispatched_mpm")})
            state["history"] = state["history"][-1_800:]

    def telemetry(self, state: dict, seed: int = 17) -> list[dict]:
        rows = []

        def add(area: str, asset: str, signal: str, value: float, units: str,
                low: float, high: float, noise: float = 0.002) -> None:
            tag = f"{area.lower().replace(' ', '_')}.{asset.lower().replace(' ', '_')}.{signal}"
            stable = sum((i + 1) * ord(ch) for i, ch in enumerate(tag))
            rng = random.Random(seed * 1_000_003 + state["sim_seconds"] * 9_176 + stable)
            if units not in {"bool", "enum"} and noise:
                value += rng.gauss(0.0, max(1e-9, high - low) * noise)
            rows.append({"tag": tag, "area": area, "unit": asset, "sensor": signal,
                         "units": units, "value": round(max(low, min(high, value)), 3),
                         "quality": "GOOD", "low": low, "high": high,
                         "base": (low + high) / 2.0, "phase": 0.0,
                         "last_change": "Finite end-of-line logistics model", "dynamic_model": True})

        add("Palletizing", "Pallet conveyor", "case_buffer", state["case_buffer_cases"],
            "cases", 0, self.CASE_BUFFER_CAPACITY)
        add("Palletizing", "Pallet conveyor", "pallet_buffer", state["pallet_buffer_cases"],
            "cases", 0, self.PALLET_BUFFER_CAPACITY_CASES)
        add("Palletizing", "Pallet conveyor", "case_acceptance", state["case_acceptance_fraction"] * 100,
            "%", 0, 100, 0.0)
        add("Palletizing", "Pallet conveyor", "palletizing_service", state["palletizer_service_fraction"] * 100,
            "%", 0, 100, 0.0)
        add("Palletizing", "Stretch wrapper", "wrapping_rate", state["cases_wrapped_mpm"] / self.CASES_PER_PALLET,
            "pallets/min", 0, 2.0)
        for name, cell in state["palletizer_cells"].items():
            add("Palletizing", name, "output_rate", cell["output_cases_min"] / self.CASES_PER_PALLET,
                "pallets/min", 0, 1.0)
            add("Palletizing", name, "availability", cell["availability"] * 100.0, "%", 0, 100, 0.0)
            add("Palletizing", name, "motor_current", cell["motor_current_a"], "A", 0, 60)
            add("Palletizing", name, "vibration", cell["vibration_mm_s"], "mm/s", 0, 25)
        add("Palletizing", "Stretch wrapper", "condition", state["wrapper_condition"] * 100.0,
            "%", 0, 100, 0.0)
        add("Palletizing", "Stretch wrapper", "motor_current", state["wrapper_motor_current_a"],
            "A", 0, 50)
        add("Palletizing", "Dispatch buffer", "dispatch_rate", state["cases_dispatched_mpm"],
            "cases/min", 0, 200)
        add("Warehouse", "Energy meters", "warehouse_occupancy", state["warehouse_occupancy_pct"],
            "%", 0, 100)
        add("Warehouse", "Energy meters", "warehouse_inventory", state["warehouse_inventory_cases"],
            "cases", 0, self.COLD_ROOM_CAPACITY_CASES * 2)
        add("Warehouse", "Energy meters", "cold_store_max_temperature", state["cold_store_temperature_c"],
            "°C", -5, 45, 0.005)
        add("Warehouse", "Energy meters", "temperature_exposure", state["cold_store_temperature_exposure_case_h"],
            "case·h", 0, 1_000_000)
        for name, room in state["rooms"].items():
            add("Warehouse", name, "inventory", room["inventory_cases"], "cases", 0,
                self.COLD_ROOM_CAPACITY_CASES)
            add("Warehouse", name, "temperature", room["temperature_c"], "°C", -5, 45, 0.005)
            add("Warehouse", name, "cooling_load", room["cooling_kw"], "kW", 0,
                room["cooling_capacity_kw"])
            add("Warehouse", name, "door_open", float(room["door_open_s"] > 0), "bool", 0, 1, 0.0)
        return rows

    @staticmethod
    def public_state(state: dict) -> dict:
        return {key: value for key, value in state.items() if key not in {"history", "rooms"}} | {
            "rooms": {name: {key: value for key, value in room.items()}
                      for name, room in state["rooms"].items()}}
