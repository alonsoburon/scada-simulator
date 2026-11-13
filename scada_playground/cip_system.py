"""Compact cleaning-in-place sequencing with water, steam, and line interlocks."""
from __future__ import annotations

import math


class CleaningInPlaceSystem:
    """Low-compute CIP cycle for one of two pasteurizer product lines."""

    PASTEURIZERS = ("pasteurizer_01", "pasteurizer_02")
    PHASES = (
        # name, seconds, water m3/h, steam t/h, target °C, minimum °C,
        # return pH, conductivity µS/cm, caustic L/min, sanitizer L/min
        ("DRAIN", 45, 0.0, 0.0, 20.0, 0.0, 7.0, 30.0, 0.0, 0.0),
        ("PRE_RINSE", 90, 18.0, 0.0, 25.0, 0.0, 7.0, 90.0, 0.0, 0.0),
        ("CAUSTIC_WASH", 180, 6.0, 5.0, 62.0, 55.0, 12.2, 22_000.0, 2.0, 0.0),
        ("INTERMEDIATE_RINSE", 90, 18.0, 0.0, 28.0, 0.0, 8.0, 700.0, 0.0, 0.0),
        ("HOT_SANITIZE", 180, 9.0, 9.0, 80.0, 72.0, 7.4, 500.0, 0.0, 1.0),
        ("FINAL_RINSE", 90, 18.0, 0.0, 25.0, 0.0, 7.0, 50.0, 0.0, 0.0),
        ("VERIFY_RELEASE", 30, 0.0, 0.0, 22.0, 0.0, 7.0, 30.0, 0.0, 0.0),
    )

    def initialize(self) -> dict:
        return {
            "active": False,
            "target_unit": "",
            "phase": "IDLE",
            "phase_index": 0,
            "phase_elapsed_s": 0.0,
            "phase_progress_pct": 0.0,
            "cycles_completed": 0,
            "start_seconds": None,
            "last_advance_seconds": 0,
            "water_available_fraction": 1.0,
            "power_available_fraction": 1.0,
            "water_demand_m3h": 0.0,
            "water_return_m3h": 0.0,
            "steam_demand_tph": 0.0,
            "effluent_m3h": 0.0,
            "effluent_generated_m3h": 0.0,
            "wastewater_available_fraction": 1.0,
            "recirculation_flow_m3h": 0.0,
            "temperature_c": 20.0,
            "return_ph": 7.0,
            "return_conductivity_us_cm": 30.0,
            "caustic_inventory_l": 500.0,
            "sanitizer_inventory_l": 250.0,
            "caustic_dosing_l_min": 0.0,
            "sanitizer_dosing_l_min": 0.0,
            "water_starved_seconds": 0.0,
            "start_block_reason": "",
        }

    def migrate(self, state: dict) -> None:
        for key, value in self.initialize().items():
            state.setdefault(key, value)

    @staticmethod
    def _phase_tuple(state: dict) -> tuple:
        return CleaningInPlaceSystem.PHASES[state["phase_index"]]

    def start(self, state: dict, target_unit: str = "pasteurizer_01",
              sim_seconds: int = 0) -> bool:
        self.migrate(state)
        if target_unit not in self.PASTEURIZERS:
            raise ValueError(f"target_unit must be one of: {', '.join(self.PASTEURIZERS)}")
        if state["active"]:
            state["start_block_reason"] = "CIP skid is already cleaning a pasteurizer"
            return False
        if state["caustic_inventory_l"] < 50:
            state["start_block_reason"] = "Caustic inventory is too low to start a complete cycle"
            return False
        if state["sanitizer_inventory_l"] < 30:
            state["start_block_reason"] = "Sanitizer inventory is too low to start a complete cycle"
            return False
        state.update(active=True, target_unit=target_unit, phase="DRAIN", phase_index=0,
                     phase_elapsed_s=0.0, phase_progress_pct=0.0,
                     start_seconds=sim_seconds, last_advance_seconds=sim_seconds,
                     water_starved_seconds=0.0, start_block_reason="")
        return True

    def advance(self, state: dict, sim_seconds: int, *, steam_available_fraction: float,
                water_available_fraction: float, power_available_fraction: float,
                wastewater_available_fraction: float = 1.0,
                mechanical_degradation: float = 0.0) -> dict:
        self.migrate(state)
        dt_s = max(0, sim_seconds - state["last_advance_seconds"])
        state["last_advance_seconds"] = sim_seconds
        state["steam_available_fraction"] = max(0.0, min(1.0, steam_available_fraction))
        state["water_available_fraction"] = max(0.0, min(1.0, water_available_fraction))
        state["power_available_fraction"] = max(0.0, min(1.0, power_available_fraction))
        state["wastewater_available_fraction"] = max(0.0, min(1.0, wastewater_available_fraction))

        if not state["active"]:
            state.update(phase="IDLE", phase_progress_pct=0.0, water_demand_m3h=0.0,
                         water_return_m3h=0.0, steam_demand_tph=0.0,
                         recirculation_flow_m3h=0.0, caustic_dosing_l_min=0.0,
                         sanitizer_dosing_l_min=0.0,
                         temperature_c=max(20.0, state["temperature_c"] - dt_s / 300.0))
            state["effluent_generated_m3h"] = 0.0
            state["effluent_m3h"] = 0.0
            return state

        (phase, duration, water, steam, target_temp, min_temp, target_ph, target_cond,
         caustic_rate, sanitizer_rate) = self._phase_tuple(state)
        pump_fraction = state["power_available_fraction"] * max(0.0, 1.0 - mechanical_degradation)
        wastewater_ready = state["wastewater_available_fraction"] >= 0.80
        phase_water = water * pump_fraction if wastewater_ready else 0.0
        target_temp = 20.0 + (target_temp - 20.0) * state["steam_available_fraction"]
        state["temperature_c"] += (target_temp - state["temperature_c"]) * (1.0 - math.exp(-dt_s / 90.0))
        state["return_ph"] += (target_ph - state["return_ph"]) * min(1.0, dt_s / 35.0)
        state["return_conductivity_us_cm"] += (target_cond - state["return_conductivity_us_cm"]) * min(1.0, dt_s / 35.0)
        heat_ready = min_temp <= 0.0 or state["temperature_c"] >= min_temp
        water_ready = water <= 0.0 or state["water_available_fraction"] >= 0.80
        pump_ready = pump_fraction >= 0.75
        can_advance = heat_ready and water_ready and pump_ready and wastewater_ready

        if water > 0 and not water_ready:
            state["water_starved_seconds"] += dt_s
        else:
            state["water_starved_seconds"] = max(0.0, state["water_starved_seconds"] - dt_s * 2)

        state["water_demand_m3h"] = phase_water
        state["water_return_m3h"] = phase_water * state["water_available_fraction"]
        state["steam_demand_tph"] = steam if pump_ready else 0.0
        state["effluent_generated_m3h"] = state["water_return_m3h"] * (0.95 if water > 0 else 0.0)
        state["effluent_m3h"] = state["effluent_generated_m3h"]
        state["recirculation_flow_m3h"] = (30.0 * pump_fraction
            if phase in ("CAUSTIC_WASH", "HOT_SANITIZE") else 0.0)
        state["caustic_dosing_l_min"] = caustic_rate if can_advance else 0.0
        state["sanitizer_dosing_l_min"] = sanitizer_rate if can_advance else 0.0
        if can_advance:
            state["phase_elapsed_s"] += dt_s
            state["caustic_inventory_l"] = max(0.0,
                state["caustic_inventory_l"] - caustic_rate * dt_s / 60.0)
            state["sanitizer_inventory_l"] = max(0.0,
                state["sanitizer_inventory_l"] - sanitizer_rate * dt_s / 60.0)
        state["phase_progress_pct"] = min(100.0, 100.0 * state["phase_elapsed_s"] / duration)

        if state["phase_elapsed_s"] >= duration:
            next_index = state["phase_index"] + 1
            if next_index >= len(self.PHASES):
                state.update(active=False, phase="IDLE", phase_index=0,
                             phase_elapsed_s=0.0, phase_progress_pct=100.0,
                             cycles_completed=state["cycles_completed"] + 1,
                             target_unit="")
            else:
                state.update(phase_index=next_index, phase_elapsed_s=0.0,
                             phase_progress_pct=0.0)
        state["phase"] = self._phase_tuple(state)[0] if state["active"] else "IDLE"
        return state

    def demands(self, state: dict) -> dict:
        self.migrate(state)
        if not state["active"]:
            return {"water_m3h": 0.0, "steam_tph": 0.0,
                    "effluent_m3h": state["effluent_m3h"]}
        return {"water_m3h": state["water_demand_m3h"],
                "steam_tph": state["steam_demand_tph"],
                "effluent_m3h": state["effluent_m3h"]}
