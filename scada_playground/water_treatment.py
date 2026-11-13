"""Compact service-water clarification, filtration, and disinfection model.

Water quality is tracked as concentrations through a finite treated-water tank;
this is an operator-training approximation, not a potable-water design model.
"""
from __future__ import annotations

import math


class ServiceWaterTreatment:
    FILTER_CAPACITY_KG = 450.0
    FILTER_BACKWASH_SECONDS = 120
    BACKWASH_FLOW_M3H = 20.0
    FILTER_MAX_FLOW_M3H = 420.0

    def initialize(self) -> dict:
        return {
            "mode": "FILTERING", "influent_turbidity_ntu": 42.0,
            "effluent_turbidity_ntu": 2.28, "filter_differential_pressure_kpa": 18.0,
            "filter_solids_kg": 0.0, "backwash_remaining_s": 0,
            "coagulant_inventory_kg": 1_800.0, "chlorine_inventory_kg": 180.0,
            "coagulant_dose_mg_l": 30.0, "chlorine_dose_mg_l": 1.1,
            "chlorine_residual_mg_l": 0.55, "treatment_power_fraction": 1.0,
            "electrical_load_kw": 55.0,
            "cumulative_backwash_m3": 0.0, "cumulative_treated_m3": 0.0,
            "last_advance_seconds": 0,
        }

    def migrate(self, state: dict) -> None:
        for key, value in self.initialize().items():
            state.setdefault(key, value)

    def prepare(self, state: dict, sim_seconds: int, events: list[dict],
                power_available_fraction: float) -> None:
        """Update raw-water quality and treatment availability before hydraulics."""
        self.migrate(state)
        fault = max(({"Small": .15, "Medium": .35, "Large": .65}[event["severity"]]
                     for event in events if event["name"] == "Process upset"
                     and event["area"] in ("Water supply network", "Water treatment", "Whole factory")),
                    default=0.0)
        mechanical = max(({"Small": .15, "Medium": .35, "Large": .65}[event["severity"]]
                          for event in events if event["name"] == "Mechanical degradation"
                          and event["area"] in ("Water treatment", "Whole factory")), default=0.0)
        local_power = min(({"Small": .75, "Medium": .42, "Large": 0.0}[event["severity"]]
                           for event in events if event["name"] == "Utility loss"
                           and event["area"] in ("Water treatment", "Whole factory")), default=1.0)
        state["influent_turbidity_ntu"] = 42.0 + 110.0 * fault + 5.0 * math.sin(sim_seconds / 190.0)
        state["treatment_power_fraction"] = max(0.0, min(1.0, power_available_fraction)) * local_power
        state["mechanical_degradation"] = mechanical

        if state["mode"] == "BACKWASH":
            return
        if state["filter_solids_kg"] >= self.FILTER_CAPACITY_KG:
            state["mode"] = "BACKWASH"
            state["backwash_remaining_s"] = self.FILTER_BACKWASH_SECONDS
        elif state["treatment_power_fraction"] < .15:
            state["mode"] = "POWER_HOLD"
        else:
            state["mode"] = "FILTERING"

    def advance(self, state: dict, sim_seconds: int, flow_m3h: float,
                backwash_water_m3h: float = 0.0) -> None:
        """Advance one scan; flow is the measured flow entering the filter train."""
        self.migrate(state)
        dt = max(0, sim_seconds - state.get("last_advance_seconds", sim_seconds - 1))
        state["last_advance_seconds"] = sim_seconds
        flow_m3h = max(0.0, flow_m3h)
        if state["mode"] == "BACKWASH":
            state["cumulative_backwash_m3"] += max(0.0, backwash_water_m3h) * dt / 3600.0
            if backwash_water_m3h >= self.BACKWASH_FLOW_M3H * .8:
                state["backwash_remaining_s"] = max(0, state["backwash_remaining_s"] - dt)
            if state["backwash_remaining_s"] == 0:
                state["filter_solids_kg"] = self.FILTER_CAPACITY_KG * .08
                state["filter_differential_pressure_kpa"] = 18.0 + 52.0 * .08**1.35
                state["mode"] = "FILTERING"
        elif state["mode"] == "FILTERING":
            mechanical_factor = 1.0 - state.get("mechanical_degradation", 0.0)
            coagulant_factor = min(1.0, state["coagulant_inventory_kg"] / 15.0)
            chemical_effectiveness = coagulant_factor * mechanical_factor
            coagulated_turbidity = state["influent_turbidity_ntu"] * (1.0 - .78 * chemical_effectiveness)
            state["effluent_turbidity_ntu"] = max(.15, coagulated_turbidity * .22 + .25)
            solids_kg_m3 = state["influent_turbidity_ntu"] * .0015
            state["filter_solids_kg"] += flow_m3h / 3600.0 * solids_kg_m3 * dt
            state["filter_solids_kg"] = min(self.FILTER_CAPACITY_KG, state["filter_solids_kg"])
            state["filter_differential_pressure_kpa"] = 18.0 + 52.0 * (
                state["filter_solids_kg"] / self.FILTER_CAPACITY_KG) ** 1.35
            water_m3 = flow_m3h * dt / 3600.0
            state["coagulant_inventory_kg"] = max(0.0, state["coagulant_inventory_kg"]
                - water_m3 * state["coagulant_dose_mg_l"] / 1000.0)
            state["chlorine_inventory_kg"] = max(0.0, state["chlorine_inventory_kg"]
                - water_m3 * state["chlorine_dose_mg_l"] / 1000.0)
            state["cumulative_treated_m3"] += water_m3
            state["chlorine_residual_mg_l"] = min(1.5,
                max(0.0, .75 * min(1.0, state["chlorine_inventory_kg"] / 3.0)
                    - .12 * state.get("mechanical_degradation", 0.0)))
        else:
            state["effluent_turbidity_ntu"] = state["influent_turbidity_ntu"]
            state["chlorine_residual_mg_l"] *= math.exp(-0.00001 * dt)
        state["electrical_load_kw"] = ({"FILTERING": 55.0, "BACKWASH": 78.0,
                                         "POWER_HOLD": 8.0}.get(state["mode"], 8.0))

    def telemetry(self, state: dict) -> list[dict]:
        def row(asset: str, signal: str, value: float, units: str,
                low: float, high: float) -> dict:
            return {"tag": f"water_treatment.{asset}.{signal}", "area": "Water treatment",
                    "unit": asset.replace("_", " ").title(), "sensor": signal,
                    "value": round(min(high, max(low, value)), 3), "units": units,
                    "quality": "GOOD", "low": low, "high": high,
                    "base": (low + high) / 2, "phase": 0.0,
                    "last_change": "Water treatment model"}

        return [
            row("filter_train", "filtering", float(state["mode"] == "FILTERING"), "bool", 0, 1),
            row("filter_train", "backwash_active", float(state["mode"] == "BACKWASH"), "bool", 0, 1),
            row("filter_train", "power_hold", float(state["mode"] == "POWER_HOLD"), "bool", 0, 1),
            row("filter_train", "differential_pressure", state["filter_differential_pressure_kpa"], "kPa", 0, 120),
            row("filter_train", "solids_loading", 100 * state["filter_solids_kg"] / self.FILTER_CAPACITY_KG, "%", 0, 100),
            row("clarifier_feed", "influent_turbidity", state["influent_turbidity_ntu"], "NTU", 0, 500),
            row("treated_water", "effluent_turbidity", state["effluent_turbidity_ntu"], "NTU", 0, 100),
            row("treated_water", "chlorine_residual", state["chlorine_residual_mg_l"], "mg/L", 0, 2),
            row("chemical_dosing", "coagulant_inventory", state["coagulant_inventory_kg"], "kg", 0, 2_000),
            row("chemical_dosing", "chlorine_inventory", state["chlorine_inventory_kg"], "kg", 0, 250),
            row("filter_train", "backwash_remaining", state["backwash_remaining_s"], "s", 0, self.FILTER_BACKWASH_SECONDS),
            row("treatment_electrical", "motor_power", state["electrical_load_kw"], "kW", 0, 150),
            row("treatment_electrical", "power_available", 100 * state["treatment_power_fraction"], "%", 0, 100),
        ]
