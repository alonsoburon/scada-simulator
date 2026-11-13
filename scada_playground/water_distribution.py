"""Pressure-dependent district water distribution fed by the treated-water tank."""
from __future__ import annotations

import math
import random


class WaterDistribution:
    """Three pressure districts, an elevated service tank, and one refill VFD."""

    AREA = "Water distribution"
    TANK_CAPACITY_M3 = 800.0
    TANK_BASE_ELEVATION_M = 165.0
    TANK_HEIGHT_M = 12.0
    TANK_SETPOINT_PCT = 65.0
    PUMP_MAX_FLOW_M3H = 180.0
    PUMP_MAX_HEAD_M = 50.0
    PUMP_STATIC_LIFT_M = 20.0
    PUMP_EFFICIENCY = 0.74
    ZONES = {
        "District zone A": {"base_demand_m3h": 48.0, "elevation_m": 115.0,
                            "pipe_length_m": 3_200.0, "pipe_diameter_m": 0.20,
                            "minor_loss_k": 24.0, "design_pressure_kpa": 350.0,
                            "minimum_pressure_kpa": 150.0, "prv_setpoint_kpa": 350.0,
                            "reference_leak_m3h": 0.90},
        "District zone B": {"base_demand_m3h": 32.0, "elevation_m": 130.0,
                            "pipe_length_m": 4_100.0, "pipe_diameter_m": 0.16,
                            "minor_loss_k": 30.0, "design_pressure_kpa": 300.0,
                            "minimum_pressure_kpa": 120.0, "prv_setpoint_kpa": 300.0,
                            "reference_leak_m3h": 0.80},
        "District zone C": {"base_demand_m3h": 16.0, "elevation_m": 145.0,
                            "pipe_length_m": 4_200.0, "pipe_diameter_m": 0.15,
                            "minor_loss_k": 36.0, "design_pressure_kpa": 210.0,
                            "minimum_pressure_kpa": 100.0, "prv_setpoint_kpa": 210.0,
                            "reference_leak_m3h": 0.45},
    }
    POWER_LOSS = {"Small": 0.75, "Medium": 0.42, "Large": 0.0}
    MECHANICAL_FACTOR = {"Small": 0.90, "Medium": 0.72, "Large": 0.52}
    UPSET_DEMAND_FACTOR = {"Small": 1.08, "Medium": 1.18, "Large": 1.32}

    @classmethod
    def initialize(cls, initial_tank_volume_m3: float | None = None) -> dict:
        volume = (cls.TANK_CAPACITY_M3 * cls.TANK_SETPOINT_PCT / 100.0
                  if initial_tank_volume_m3 is None else max(0.0, min(cls.TANK_CAPACITY_M3,
                                                                      initial_tank_volume_m3)))
        zones = {name: {**spec, "customer_demand_m3h": spec["base_demand_m3h"],
                        "customer_served_m3h": 0.0, "leak_flow_m3h": 0.0,
                        "flow_m3h": 0.0, "pressure_kpa": 0.0,
                        "upstream_pressure_kpa": 0.0, "service_fraction": 1.0,
                        "prv_position_pct": 0.0, "chlorine_residual_mg_l": 0.45,
                        "turbidity_ntu": 1.0, "water_age_h": 0.0,
                        "unmet_demand_m3h": 0.0}
                  for name, spec in cls.ZONES.items()}
        state = {"sim_seconds": 0, "tank_volume_m3": volume,
                "tank_level_pct": volume / cls.TANK_CAPACITY_M3 * 100.0,
                "tank_capacity_m3": cls.TANK_CAPACITY_M3,
                "tank_water_surface_elevation_m": cls.TANK_BASE_ELEVATION_M
                    + volume / cls.TANK_CAPACITY_M3 * cls.TANK_HEIGHT_M,
                "tank_setpoint_pct": cls.TANK_SETPOINT_PCT,
                "pump_running": False, "pump_flow_request_m3h": 0.0,
                "pump_flow_m3h": 0.0, "pump_power_kw": 0.0,
                "pump_head_m": cls.PUMP_STATIC_LIFT_M,
                "pump_speed_pct": 0.0, "pump_condition_factor": 1.0,
                "pump_power_fraction": 1.0, "transfer_request_m3h": 0.0,
                "transfer_received_m3h": 0.0,
                "customer_demand_m3h": sum(z["base_demand_m3h"] for z in cls.ZONES.values()),
                "customer_served_m3h": 0.0, "leak_flow_m3h": 0.0,
                "unmet_demand_m3h": 0.0, "service_fraction": 1.0,
                "non_revenue_water_pct": 0.0, "chlorine_residual_mg_l": 0.45,
                "turbidity_ntu": 1.0,
                "tank_spill_m3": 0.0, "cumulative_leakage_m3": 0.0,
                "cumulative_delivery_m3": 0.0, "history": [], "zones": zones}
        for name, spec in cls.ZONES.items():
            zones[name].update(cls._branch_operating_point(
                state, name, spec, volume, 1.0, 1.0))
        state["customer_served_m3h"] = sum(z["customer_served_m3h"] for z in zones.values())
        state["customer_demand_m3h"] = sum(z["customer_demand_m3h"] for z in zones.values())
        state["leak_flow_m3h"] = sum(z["leak_flow_m3h"] for z in zones.values())
        state["unmet_demand_m3h"] = sum(z["unmet_demand_m3h"] for z in zones.values())
        state["service_fraction"] = (state["customer_served_m3h"] / state["customer_demand_m3h"]
                                      if state["customer_demand_m3h"] > 1e-9 else 1.0)
        return state

    @staticmethod
    def _severity(events: list[dict], event_name: str) -> str | None:
        rank = {"Small": 1, "Medium": 2, "Large": 3}
        matches = [event.get("severity", "Small") for event in events
                   if event.get("name") == event_name
                   and event.get("area") in ("Water distribution", "Whole factory")]
        return max(matches, key=lambda value: rank[value]) if matches else None

    @staticmethod
    def _daily_demand_factor(sim_seconds: int) -> float:
        hour = sim_seconds % 86_400 / 3_600.0
        return max(0.60, min(1.40,
            1.0 + 0.16 * math.sin(2 * math.pi * (hour - 7) / 24)
            + 0.13 * math.sin(2 * math.pi * (hour - 19) / 24)
            + 0.04 * math.sin(2 * math.pi * (hour - 13) / 24)))

    @classmethod
    def prepare(cls, state: dict, sim_seconds: int, events: list[dict], *,
                grid_power_fraction: float | None = None) -> float:
        """Issue the tank-level refill request before treated-water hydraulics run."""
        if grid_power_fraction is None:
            grid_power_fraction = 1.0
        local_power_loss = cls._severity(events, "Utility loss")
        power_factor = max(0.0, min(1.0, grid_power_fraction)) * cls.POWER_LOSS.get(local_power_loss, 1.0)
        mechanical = cls._severity(events, "Mechanical degradation")
        condition = cls.MECHANICAL_FACTOR.get(mechanical, 1.0)
        upset = cls._severity(events, "Process upset")
        demand_factor = cls._daily_demand_factor(sim_seconds) * cls.UPSET_DEMAND_FACTOR.get(upset, 1.0)
        state["sim_seconds"] = sim_seconds
        state["pump_condition_factor"] = condition
        state["pump_power_fraction"] = power_factor
        expected_demand = sum(spec["base_demand_m3h"] for spec in cls.ZONES.values()) * demand_factor
        state["customer_demand_m3h"] = expected_demand
        # A compact level controller adds refill flow in proportion to tank-level
        # error while tracking the expected district demand. Ramp rate is applied
        # to the VFD flow in advance(), below.
        level_error_pct = cls.TANK_SETPOINT_PCT - state["tank_level_pct"]
        desired = max(0.0, min(cls.PUMP_MAX_FLOW_M3H * condition * math.sqrt(power_factor),
                               expected_demand + 3.0 * level_error_pct))
        state["pump_flow_request_m3h"] = desired
        state["pump_running"] = power_factor > 0.01 and desired > 0.1
        prior = state.get("pump_flow_m3h", 0.0)
        state["pump_flow_m3h"] = prior + max(-3.0, min(3.0, desired - prior))
        if power_factor <= 0.01:
            state["pump_flow_m3h"] = 0.0
        state["transfer_request_m3h"] = max(0.0, state["pump_flow_m3h"])
        return state["transfer_request_m3h"]

    @classmethod
    def _branch_operating_point(cls, state: dict, name: str, spec: dict,
                                tank_volume_m3: float, demand_factor: float,
                                leak_factor: float, supply_head_fraction: float = 1.0) -> dict:
        tank_level_fraction = max(0.0, min(1.0, tank_volume_m3 / cls.TANK_CAPACITY_M3))
        water_surface = cls.TANK_BASE_ELEVATION_M + tank_level_fraction * cls.TANK_HEIGHT_M
        static_head_m = max(0.0, water_surface - spec["elevation_m"])
        base_pressure = static_head_m * 9.80665 * supply_head_fraction
        customer_request = spec["base_demand_m3h"] * demand_factor
        pressure = min(spec["prv_setpoint_kpa"], base_pressure)
        customer_served = leak = pressure_loss_kpa = 0.0
        for _ in range(8):
            service_fraction = math.sqrt(max(0.0, min(1.0,
                (pressure - spec["minimum_pressure_kpa"])
                / max(1e-6, spec["design_pressure_kpa"] - spec["minimum_pressure_kpa"]))))
            customer_served = customer_request * service_fraction
            leak = (spec["reference_leak_m3h"] * leak_factor
                    * math.sqrt(max(0.0, pressure / spec["design_pressure_kpa"])))
            flow_m3s = (customer_served + leak) / 3600.0
            area = math.pi * spec["pipe_diameter_m"] ** 2 / 4.0
            velocity = flow_m3s / area
            reynolds = velocity * spec["pipe_diameter_m"] / 1.004e-6
            if reynolds < 2_300:
                friction = 64.0 / max(reynolds, 1.0)
            else:
                friction = 0.25 / math.log10(
                    0.00015 / (3.7 * spec["pipe_diameter_m"])
                    + 5.74 / max(reynolds, 1.0) ** 0.9) ** 2
            head_loss_m = (friction * spec["pipe_length_m"] / spec["pipe_diameter_m"]
                           + spec["minor_loss_k"]) * velocity ** 2 / (2 * 9.80665)
            pressure_loss_kpa = head_loss_m * 9.80665
            upstream = max(0.0, base_pressure - pressure_loss_kpa)
            pressure = min(spec["prv_setpoint_kpa"], upstream)
        service_fraction = math.sqrt(max(0.0, min(1.0,
            (pressure - spec["minimum_pressure_kpa"])
            / max(1e-6, spec["design_pressure_kpa"] - spec["minimum_pressure_kpa"]))))
        customer_served = customer_request * service_fraction
        leak = (spec["reference_leak_m3h"] * leak_factor
                * math.sqrt(max(0.0, pressure / spec["design_pressure_kpa"])))
        total_flow = customer_served + leak
        prv_position = (max(0.0, min(100.0,
            100.0 * (base_pressure - spec["prv_setpoint_kpa"])
            / max(1.0, base_pressure))) if base_pressure > spec["prv_setpoint_kpa"] else 0.0)
        pipe_volume_m3 = math.pi * spec["pipe_diameter_m"] ** 2 / 4.0 * spec["pipe_length_m"]
        water_age_h = pipe_volume_m3 / max(total_flow, 0.05)
        branch_chlorine = state["chlorine_residual_mg_l"] * math.exp(
            -water_age_h * 3600.0 / 172_800.0)
        return {"customer_demand_m3h": customer_request,
                "customer_served_m3h": customer_served,
                "unmet_demand_m3h": max(0.0, customer_request - customer_served),
                "leak_flow_m3h": leak, "flow_m3h": total_flow,
                "pressure_kpa": pressure, "upstream_pressure_kpa": max(0.0, base_pressure - pressure_loss_kpa),
                "pressure_loss_kpa": pressure_loss_kpa,
                "service_fraction": service_fraction,
                "prv_position_pct": prv_position,
                "chlorine_residual_mg_l": branch_chlorine,
                "turbidity_ntu": state["turbidity_ntu"], "water_age_h": water_age_h}

    @classmethod
    def advance(cls, state: dict, sim_seconds: int, events: list[dict], *,
                treated_transfer_m3h: float, source_chlorine_mg_l: float,
                source_turbidity_ntu: float = 1.0) -> dict:
        """Receive treated water, solve district service, and conserve tank inventory."""
        state["sim_seconds"] = sim_seconds
        state["transfer_received_m3h"] = max(0.0, treated_transfer_m3h)
        inflow_m3 = state["transfer_received_m3h"] / 3600.0
        spill = max(0.0, state["tank_volume_m3"] + inflow_m3 - cls.TANK_CAPACITY_M3)
        inflow_m3 = max(0.0, inflow_m3 - spill)
        volume_available = state["tank_volume_m3"] + inflow_m3
        state["tank_level_pct"] = volume_available / cls.TANK_CAPACITY_M3 * 100.0
        state["tank_water_surface_elevation_m"] = (cls.TANK_BASE_ELEVATION_M
            + volume_available / cls.TANK_CAPACITY_M3 * cls.TANK_HEIGHT_M)
        upset = cls._severity(events, "Process upset")
        demand_factor = cls._daily_demand_factor(sim_seconds) * cls.UPSET_DEMAND_FACTOR.get(upset, 1.0)
        leak_factor = 1.0 + (1.0 - state["pump_condition_factor"]) * 0.35
        def solve_zones(head_fraction: float) -> dict:
            return {name: {**spec, **cls._branch_operating_point(
                state, name, spec, volume_available, demand_factor, leak_factor, head_fraction)}
                for name, spec in cls.ZONES.items()}

        zones = solve_zones(1.0)
        requested_outflow_m3h = sum(zone["flow_m3h"] for zone in zones.values())
        # The model advances in one-second scans. If the tank contains less water
        # than the requested scan withdrawal, reduce hydraulic head until the
        # pressure-dependent customer demand and leakage fit the available volume.
        max_scan_outflow_m3h = volume_available * 3600.0
        if volume_available <= 1e-8:
            zones = solve_zones(0.0)
        elif requested_outflow_m3h > max_scan_outflow_m3h + 1e-9:
            low_head, high_head = 0.0, 1.0
            # Twelve pressure trials are enough for this three-branch teaching
            # network (relative head resolution <0.025%) without dominating a
            # one-second simulation scan when the reserve tank is nearly dry.
            for _ in range(12):
                mid_head = (low_head + high_head) / 2.0
                trial = solve_zones(mid_head)
                trial_flow = sum(zone["flow_m3h"] for zone in trial.values())
                if trial_flow > max_scan_outflow_m3h:
                    high_head = mid_head
                else:
                    low_head = mid_head
            zones = solve_zones(low_head)
        total_customer_request = sum(zone["customer_demand_m3h"] for zone in zones.values())
        customer_served = sum(zone["customer_served_m3h"] for zone in zones.values())
        leakage = sum(zone["leak_flow_m3h"] for zone in zones.values())
        outflow_m3h = customer_served + leakage
        actual_outflow_m3 = min(outflow_m3h / 3600.0, volume_available)
        actual_outflow_m3h = actual_outflow_m3 * 3600.0
        state["tank_volume_m3"] = max(0.0, volume_available - actual_outflow_m3)
        state["tank_level_pct"] = state["tank_volume_m3"] / cls.TANK_CAPACITY_M3 * 100.0
        state["tank_water_surface_elevation_m"] = (cls.TANK_BASE_ELEVATION_M
            + state["tank_volume_m3"] / cls.TANK_CAPACITY_M3 * cls.TANK_HEIGHT_M)
        old_volume = max(0.0, state["tank_volume_m3"] + actual_outflow_m3 - inflow_m3)
        old_chlorine_mass = state["chlorine_residual_mg_l"] * old_volume
        inlet_chlorine_mass = max(0.0, source_chlorine_mg_l) * inflow_m3
        mixed_volume = old_volume + inflow_m3
        chlorine = ((old_chlorine_mass + inlet_chlorine_mass) / mixed_volume
                    if mixed_volume > 1e-9 else 0.0)
        decay_multiplier = {"Small": 1.8, "Medium": 3.0, "Large": 5.0}.get(upset, 1.0)
        state["chlorine_residual_mg_l"] = max(0.0, chlorine * math.exp(-decay_multiplier / 172_800.0))
        old_turbidity_mass = state["turbidity_ntu"] * old_volume
        inlet_turbidity_mass = max(0.0, source_turbidity_ntu) * inflow_m3
        mixed_turbidity = ((old_turbidity_mass + inlet_turbidity_mass) / mixed_volume
                           if mixed_volume > 1e-9 else 0.0)
        # Settling is slow; severe process disturbances can resuspend solids.
        resuspension_ntu = {"Small": 0.015, "Medium": 0.06, "Large": 0.20}.get(upset, 0.0)
        state["turbidity_ntu"] = max(0.0, mixed_turbidity * math.exp(-1.0 / 86_400.0)
                                     + resuspension_ntu)
        state["customer_demand_m3h"] = total_customer_request
        state["customer_served_m3h"] = min(customer_served, actual_outflow_m3h)
        state["leak_flow_m3h"] = min(leakage, max(0.0, actual_outflow_m3h - state["customer_served_m3h"]))
        state["unmet_demand_m3h"] = max(0.0, total_customer_request - state["customer_served_m3h"])
        state["service_fraction"] = (state["customer_served_m3h"] / total_customer_request
                                      if total_customer_request > 1e-9 else 1.0)
        state["non_revenue_water_pct"] = (100.0 * state["leak_flow_m3h"] / actual_outflow_m3h
                                          if actual_outflow_m3h > 1e-9 else 0.0)
        state["tank_spill_m3"] = spill
        state["cumulative_leakage_m3"] += state["leak_flow_m3h"] / 3600.0
        state["cumulative_delivery_m3"] += actual_outflow_m3
        state["pump_head_m"] = (cls.PUMP_STATIC_LIFT_M
            + 12.0 * (state["transfer_received_m3h"] / cls.PUMP_MAX_FLOW_M3H) ** 2)
        state["pump_power_kw"] = (state["transfer_received_m3h"] / 3600.0 * 1000.0
            * 9.80665 * state["pump_head_m"] / 1000.0 / cls.PUMP_EFFICIENCY
            if state["pump_running"] else 0.0)
        state["pump_speed_pct"] = (100.0 * state["pump_flow_m3h"] / cls.PUMP_MAX_FLOW_M3H
                                    if state["pump_running"] else 0.0)
        state["zones"] = zones
        if sim_seconds % 15 == 0:
            state["history"].append({"sim_seconds": sim_seconds,
                "tank_level_pct": state["tank_level_pct"],
                "pump_flow_m3h": state["transfer_received_m3h"],
                "customer_demand_m3h": state["customer_demand_m3h"],
                "customer_served_m3h": state["customer_served_m3h"],
                "leak_flow_m3h": state["leak_flow_m3h"],
                "unmet_demand_m3h": state["unmet_demand_m3h"],
                "chlorine_residual_mg_l": state["chlorine_residual_mg_l"],
                "zone_a_pressure_kpa": zones["District zone A"]["pressure_kpa"],
                "zone_b_pressure_kpa": zones["District zone B"]["pressure_kpa"],
                "zone_c_pressure_kpa": zones["District zone C"]["pressure_kpa"]})
            state["history"] = state["history"][-1_440:]
        return {"delivered_m3h": actual_outflow_m3h,
                "customer_served_m3h": state["customer_served_m3h"],
                "leak_flow_m3h": state["leak_flow_m3h"],
                "unmet_demand_m3h": state["unmet_demand_m3h"],
                "spill_m3": spill}

    def telemetry(self, state: dict, seed: int = 17) -> list[dict]:
        rows = []
        rng_base = seed * 1_000_003 + state["sim_seconds"] * 9_176
        def add(asset: str, signal: str, value: float, units: str,
                low: float, high: float, noise_pct: float = 0.0) -> None:
            name = f"water_distribution.{asset.lower().replace(' ', '_')}.{signal}"
            stable = sum((index + 1) * ord(char) for index, char in enumerate(name))
            rng = random.Random(rng_base + stable)
            observed = value + rng.gauss(0.0, (high - low) * noise_pct) if noise_pct else value
            rows.append({"tag": name, "area": self.AREA, "unit": asset,
                         "sensor": signal, "units": units,
                         "value": round(min(high, max(low, observed)), 4),
                         "quality": "GOOD", "low": low, "high": high,
                         "base": (low + high) / 2, "phase": 0.0,
                         "last_change": "District hydraulic model"})
        for name, zone in state["zones"].items():
            add(name, "flow_rate", zone["flow_m3h"], "m³/h", 0, 160, .004)
            add(name, "customer_demand", zone["customer_demand_m3h"], "m³/h", 0, 120, .003)
            add(name, "unmet_demand", zone["unmet_demand_m3h"], "m³/h", 0, 120, .003)
            add(name, "line_pressure", zone["pressure_kpa"], "kPa", 0, 800, .003)
            add(name, "leak_flow", zone["leak_flow_m3h"], "m³/h", 0, 20, .01)
            add(name, "chlorine_residual", zone["chlorine_residual_mg_l"], "mg/L", 0, 2, .005)
            add(name, "turbidity", zone["turbidity_ntu"], "NTU", 0, 100, .005)
            add(name, "water_age", zone["water_age_h"], "h", 0, 168, .005)
            add(name, "prv_position", zone["prv_position_pct"], "%", 0, 100, .003)
        add("Elevated tank", "tank_level", state["tank_level_pct"], "%", 0, 100, .002)
        add("Elevated tank", "stored_volume", state["tank_volume_m3"], "m³", 0, self.TANK_CAPACITY_M3, .002)
        add("Elevated tank", "inlet_flow", state["transfer_received_m3h"], "m³/h", 0, 240, .003)
        add("Elevated tank", "level_setpoint", state["tank_setpoint_pct"], "%", 0, 100)
        add("Elevated tank", "chlorine_residual", state["chlorine_residual_mg_l"], "mg/L", 0, 2, .005)
        add("Elevated tank", "turbidity", state["turbidity_ntu"], "NTU", 0, 100, .005)
        add("Elevated tank", "refill_pump_power", state["pump_power_kw"], "kW", 0, 100, .003)
        add("Elevated tank", "refill_pump_head", state["pump_head_m"], "m", 0, 100, .003)
        add("Leak monitoring", "total_leak_flow", state["leak_flow_m3h"], "m³/h", 0, 60, .005)
        add("Leak monitoring", "non_revenue_water", state["non_revenue_water_pct"], "%", 0, 100, .003)
        add("Leak monitoring", "network_service_fraction", state["service_fraction"] * 100, "%", 0, 100)
        add("Leak monitoring", "unmet_customer_demand", state["unmet_demand_m3h"], "m³/h", 0, 200, .003)
        return rows

    @staticmethod
    def public_state(state: dict) -> dict:
        return {key: value for key, value in state.items() if key != "history"} | {
            "history": state["history"][-600:]}
