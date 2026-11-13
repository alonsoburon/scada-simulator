"""Low-compute mine ventilation network with fan curves and contaminant transport."""
from __future__ import annotations

import math
import random


class UndergroundVentilation:
    AREA = "Underground mining"
    ASSETS = ("Main exhaust fan A", "Main exhaust fan B", "Decline level 1",
              "Decline level 2", "Ore pass system")
    BRANCHES = {
        "Decline level 1": {"resistance": 0.0068, "volume_m3": 78_000.0,
                            "target_air_m3s": 115.0, "diesel_kw": 950.0,
                            "heat_kw": 620.0, "thermal_capacity_kwh_k": 900.0,
                            "envelope_ua_kw_k": 52.0, "methane_m3s": 0.015},
        "Decline level 2": {"resistance": 0.0105, "volume_m3": 64_000.0,
                            "target_air_m3s": 85.0, "diesel_kw": 720.0,
                            "heat_kw": 470.0, "thermal_capacity_kwh_k": 760.0,
                            "envelope_ua_kw_k": 43.0, "methane_m3s": 0.022},
        "Ore pass system": {"resistance": 0.0160, "volume_m3": 22_000.0,
                            "target_air_m3s": 40.0, "diesel_kw": 0.0,
                            "heat_kw": 110.0, "thermal_capacity_kwh_k": 300.0,
                            "envelope_ua_kw_k": 27.0, "methane_m3s": 0.006},
    }
    FAN_SHUTOFF_PA = 3_850.0
    FAN_CURVE_PA_PER_Q2 = 0.12
    FAN_MAX_FLOW_M3S = 125.0
    FAN_RATED_KW = 1_100.0
    COMMON_RESISTANCE = 0.035

    @classmethod
    def initialize(cls) -> dict:
        fans = {}
        for name in ("Main exhaust fan A", "Main exhaust fan B"):
            fans[name] = {"speed_pct": 100.0, "speed_command_pct": 100.0,
                          "condition": 1.0, "running": True, "starts": 0,
                          "power_kw": 0.0, "flow_m3s": 0.0}
        branches = {name: {"airflow_m3s": 0.0, "methane_pct": 0.08,
                           "nox_ppm": 0.5, "oxygen_pct": 20.9,
                           "temperature_c": 26.0, "dust_mg_m3": 0.4,
                           "pressure_pa": 0.0, "last_airflow_m3s": 0.0}
                    for name in cls.BRANCHES}
        return {"sim_seconds": 0, "fans": fans, "branches": branches,
                "power_available_fraction": 1.0, "fan_power_kw": 0.0,
                "total_airflow_m3s": 0.0, "network_pressure_pa": 0.0,
                "required_airflow_m3s": sum(x["target_air_m3s"] for x in cls.BRANCHES.values()),
                "gas_release_factor": 1.0, "history": []}

    @staticmethod
    def _event_severity(events: list[dict], name: str, area: str) -> str | None:
        rank = {"Small": 1, "Medium": 2, "Large": 3}
        matches = [e.get("severity", "Small") for e in events
                   if e.get("name") == name and e.get("area") in (area, "Whole factory")]
        return max(matches, key=lambda value: rank[value]) if matches else None

    @classmethod
    def _network_flow(cls, state: dict) -> tuple[float, float]:
        """Solve common fan/airway operating point, then split by branch conductance."""
        equivalent = 1.0 / sum(1.0 / math.sqrt(spec["resistance"])
                              for spec in cls.BRANCHES.values()) ** 2
        running = [fan for fan in state["fans"].values() if fan["speed_pct"] > 0.5]
        def fan_pressure(flow: float) -> float:
            if not running:
                return 0.0
            # Identical parallel fans share flow equally. Degraded fan condition
            # lowers its shutoff head, while the quadratic term captures impeller load.
            per_fan = flow / len(running)
            heads = [max(0.0, cls.FAN_SHUTOFF_PA * (f["speed_pct"] / 100) ** 2
                         * f["condition"] - cls.FAN_CURVE_PA_PER_Q2 * per_fan ** 2)
                     for f in running]
            return sum(heads) / len(heads)
        def system_pressure(flow: float) -> float:
            return (cls.COMMON_RESISTANCE + equivalent) * flow ** 2
        lo, hi = 0.0, cls.FAN_MAX_FLOW_M3S * max(1, len(running))
        for _ in range(36):
            mid = (lo + hi) / 2
            if fan_pressure(mid) > system_pressure(mid):
                lo = mid
            else:
                hi = mid
        total = (lo + hi) / 2
        return total, system_pressure(total)

    def advance(self, state: dict, sim_seconds: int, events: list[dict], *,
                seed: int = 17, power_available_fraction: float = 1.0,
                haulage_activity: float = 1.0) -> None:
        state["sim_seconds"] = sim_seconds
        mechanical = self._event_severity(events, "Mechanical degradation", self.AREA)
        utility = self._event_severity(events, "Utility loss", self.AREA)
        upset = self._event_severity(events, "Process upset", self.AREA)
        power_scale = {"Small": 0.85, "Medium": 0.55, "Large": 0.0}.get(utility, 1.0)
        state["power_available_fraction"] = max(0.0, min(1.0, power_available_fraction)) * power_scale
        gas_scale = {"Small": 1.5, "Medium": 2.8, "Large": 5.0}.get(upset, 1.0)
        state["gas_release_factor"] = gas_scale
        # A damaged fan is not instantly repaired when the event clears.
        condition_target = {"Small": 0.86, "Medium": 0.66, "Large": 0.40}.get(mechanical, 1.0)
        for i, (name, fan) in enumerate(state["fans"].items()):
            fan["condition"] += (condition_target - fan["condition"]) * 0.025
            # Short speed ramp captures VFD and rotating-inertia limits.
            target = 100.0 * math.sqrt(state["power_available_fraction"])
            fan["speed_command_pct"] = target
            should_run = state["power_available_fraction"] >= 0.08
            if fan["running"] != should_run:
                fan["running"] = should_run
                if should_run:
                    fan["starts"] += 1
            fan["speed_pct"] += max(-0.8, min(0.8, target - fan["speed_pct"]))
            fan["flow_m3s"] = 0.0
        total_flow, pressure = self._network_flow(state)
        conductances = {name: 1 / math.sqrt(spec["resistance"])
                        for name, spec in self.BRANCHES.items()}
        conductance_total = sum(conductances.values())
        dust_release = max(0.0, min(2.0, 0.18 + 0.30 * max(0.0, haulage_activity)))
        for name, spec in self.BRANCHES.items():
            zone = state["branches"][name]
            flow = total_flow * conductances[name] / conductance_total
            zone["last_airflow_m3s"] = zone["airflow_m3s"]
            zone["airflow_m3s"] = flow
            zone["pressure_pa"] = spec["resistance"] * flow ** 2
            air_changes_per_hour = flow * 3600 / spec["volume_m3"]
            methane_source = spec["methane_m3s"] * gas_scale
            target_methane_pct = 0.08 + methane_source / max(flow, 0.02) * 100.0
            # First-order perfectly mixed chamber approximation; emission and
            # dilution are separated so a fan failure accumulates gas gradually.
            tau = max(1.0, spec["volume_m3"] / max(flow, 0.02))
            zone["methane_pct"] += (target_methane_pct - zone["methane_pct"]) * min(1.0, 1.0 / tau)
            nox_source_ppm_s = spec["diesel_kw"] * max(0.0, haulage_activity) * 0.00022
            nox_target = nox_source_ppm_s / max(flow, 0.02)
            zone["nox_ppm"] += (nox_target - zone["nox_ppm"]) * min(1.0, 1.0 / tau)
            oxygen_consumption_pct = spec["diesel_kw"] * max(0.0, haulage_activity) * 0.0000012
            zone["oxygen_pct"] += ((20.9 - oxygen_consumption_pct / max(flow, 0.02))
                                   - zone["oxygen_pct"]) * min(1.0, 1.0 / tau)
            zone["dust_mg_m3"] += (dust_release / max(0.2, flow / 100.0)
                                   - zone["dust_mg_m3"]) * min(1.0, 1.0 / max(1.0, tau * .5))
            heat_balance_kw = (spec["heat_kw"] * max(0.25, haulage_activity)
                               - (spec["envelope_ua_kw_k"] + flow * 1.2)
                               * (zone["temperature_c"] - 27.0))
            zone["temperature_c"] += heat_balance_kw / spec["thermal_capacity_kwh_k"] / 3600.0
        state["total_airflow_m3s"] = total_flow
        state["network_pressure_pa"] = pressure
        total_kw = 0.0
        air_moving_fans = [fan for fan in state["fans"].values() if fan["speed_pct"] > 0.5]
        for name, fan in state["fans"].items():
            # A de-energized fan still moves air while its rotor coasts down.
            # Keep aerodynamic flow separate from the motor's energized state.
            fan["flow_m3s"] = (total_flow / len(air_moving_fans)
                               if fan["speed_pct"] > 0.5 else 0.0)
            # Fan shaft input is Q*DeltaP/efficiency; scale by condition/VFD.
            fan["power_kw"] = (fan["flow_m3s"] * pressure / 1000.0 / 0.76
                               if fan["running"] else 0.0)
            total_kw += fan["power_kw"]
        state["fan_power_kw"] = total_kw
        if sim_seconds % 10 == 0:
            state["history"].append({"sim_seconds": sim_seconds,
                "airflow_m3s": total_flow, "network_pressure_pa": pressure,
                "methane_peak_pct": max(zone["methane_pct"] for zone in state["branches"].values()),
                "nox_peak_ppm": max(zone["nox_ppm"] for zone in state["branches"].values()),
                "fan_power_kw": total_kw})
            state["history"] = state["history"][-1_800:]

    def telemetry(self, state: dict, seed: int = 17) -> list[dict]:
        rows = []
        rng_base = seed * 1_000_003 + state["sim_seconds"] * 9_176
        def add(asset: str, signal: str, value: float, units: str, low: float, high: float,
                noise_pct: float = 0.0):
            tag_name = f"underground_mining.{asset.lower().replace(' ', '_')}.{signal}"
            stable_id = sum((i + 1) * ord(char) for i, char in enumerate(tag_name))
            rng = random.Random(rng_base + stable_id)
            noisy = value + rng.gauss(0.0, (high - low) * noise_pct) if noise_pct else value
            rows.append({"tag": tag_name, "area": self.AREA, "unit": asset,
                         "sensor": signal, "units": units, "value": round(min(high, max(low, noisy)), 4),
                         "quality": "GOOD", "low": low, "high": high,
                         "base": (low + high) / 2, "phase": 0.0,
                         "last_change": "Mine ventilation process model"})
        for name, fan in state["fans"].items():
            add(name, "airflow", fan["flow_m3s"], "m³/s", 0, 260, .002)
            add(name, "speed_feedback", fan["speed_pct"], "%", 0, 100, .001)
            add(name, "static_pressure", state["network_pressure_pa"], "Pa", 0, 5000, .002)
            add(name, "motor_power", fan["power_kw"], "kW", 0, 1100, .002)
            add(name, "condition", fan["condition"] * 100, "%", 0, 100, .002)
            add(name, "running", float(fan["running"]), "bool", 0, 1)
        for name, zone in state["branches"].items():
            add(name, "airflow", zone["airflow_m3s"], "m³/s", 0, 240, .002)
            add(name, "air_velocity", zone["airflow_m3s"] / 22.0, "m/s", 0, 12, .003)
            add(name, "methane", zone["methane_pct"], "% vol", 0, 5, .002)
            add(name, "nox", zone["nox_ppm"], "ppm", 0, 100, .003)
            add(name, "oxygen", zone["oxygen_pct"], "% vol", 15, 22, .001)
            add(name, "temperature", zone["temperature_c"], "°C", 0, 60, .002)
            add(name, "dust", zone["dust_mg_m3"], "mg/m³", 0, 20, .003)
            add(name, "airway_pressure", zone["pressure_pa"], "Pa", 0, 5000, .002)
        add("Ore pass system", "total_airflow", state["total_airflow_m3s"], "m³/s", 0, 400, .002)
        add("Ore pass system", "required_airflow", state["required_airflow_m3s"], "m³/s", 0, 400)
        add("Ore pass system", "fan_power", state["fan_power_kw"], "kW", 0, 2200, .002)
        return rows

    @staticmethod
    def public_state(state: dict) -> dict:
        return {key: value for key, value in state.items() if key != "history"} | {
            "history": state["history"][-600:]}
