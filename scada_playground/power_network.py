"""Low-compute dynamic model of a utility-fed mine microgrid.

This is a balanced, quasi-steady network with explicit source dispatch, feeder
limits, storage energy, diesel start/ramp, load shedding and frequency response.
It is intended for SCADA drills; it is not an AC load-flow or protection study.
"""
from __future__ import annotations

import math
import random


class MinePowerNetwork:
    AREA_ASSETS = {
        "Grid generation": ["Turbine generator 01", "Turbine generator 02", "Solar farm", "Battery storage", "Black start unit"],
        "Grid transmission": ["Transmission line A", "Transmission line B", "Grid substation A", "Grid substation B", "Intertie"],
        "Grid distribution": ["Feeder north", "Feeder south", "Feeder east", "Feeder west", "Capacitor bank"],
        "Mine power network": ["Mine substation A", "Mine substation B", "Pit distribution", "Underground feeders", "Emergency generation"],
    }
    SEVERITY = {"Small": 0.18, "Medium": 0.42, "Large": 0.78}

    def initialize(self) -> dict:
        return {
            "sim_seconds": 0, "frequency_hz": 50.0, "voltage_pu": 1.0,
            "tie_capacity_mw": 44.0, "mine_transformer_capacity_mw": 39.0,
            "process_feeder_capacity_mw": 32.0, "water_feeder_capacity_mw": 2.5,
            "solar_mw": 2.2, "diesel_mw": 0.0, "diesel_available_mw": 8.0,
            "diesel_start_timer_s": 0, "diesel_started": False,
            "battery_soc_pct": 82.0, "battery_energy_mwh": 16.4,
            "battery_capacity_mwh": 20.0, "battery_power_mw": 0.0,
            "transformers": {
                "grid_a": {"top_oil_c": 58.0, "hotspot_c": 73.0, "cooling_stage": 0},
                "grid_b": {"top_oil_c": 56.0, "hotspot_c": 70.0, "cooling_stage": 0},
                "mine_a": {"top_oil_c": 64.0, "hotspot_c": 80.0, "cooling_stage": 0},
                "mine_b": {"top_oil_c": 56.0, "hotspot_c": 70.0, "cooling_stage": 0},
            },
            "battery_discharge_limit_mw": 6.0, "battery_charge_limit_mw": 3.0,
            "grid_import_mw": 0.0, "total_generation_mw": 0.0,
            "site_load_mw": 6.0, "water_load_mw": 0.5,
            "process_load_mw": 28.0, "requested_load_mw": 34.5,
            "served_load_mw": 34.5, "load_shed_mw": 0.0,
            "process_power_fraction": 1.0, "water_power_fraction": 1.0,
            "unserved_load_mw": 0.0, "power_deficit": False,
            "history": [], "seed": 17,
        }

    @staticmethod
    def _advance_transformer(thermal: dict, load_mva: float, rating_mva: float,
                             seconds: int, ambient_c: float = 25.0) -> None:
        """Advance a two-mass top-oil/hot-spot model with staged cooling."""
        load_pu = max(0.0, load_mva / max(rating_mva, 1e-6))
        if thermal["cooling_stage"] == 0:
            if thermal["top_oil_c"] >= 72.0:
                thermal["cooling_stage"] = 1
        elif thermal["cooling_stage"] == 1:
            if thermal["top_oil_c"] >= 82.0:
                thermal["cooling_stage"] = 2
            elif thermal["top_oil_c"] <= 68.0:
                thermal["cooling_stage"] = 0
        elif thermal["top_oil_c"] <= 78.0:
            thermal["cooling_stage"] = 1

        cooling = (1.0, 1.12, 1.24)[thermal["cooling_stage"]]
        oil_rise = 55.0 * load_pu**1.6 / cooling
        target_oil = ambient_c + oil_rise
        # Top oil responds over hours; winding hot spot responds within minutes.
        thermal["top_oil_c"] += (target_oil - thermal["top_oil_c"]) * (1.0 - math.exp(-seconds / 14_400.0))
        winding_rise = 25.0 * load_pu**2.0
        target_hotspot = thermal["top_oil_c"] + winding_rise
        thermal["hotspot_c"] += (target_hotspot - thermal["hotspot_c"]) * (1.0 - math.exp(-seconds / 300.0))

    def advance(self, state: dict, sim_seconds: int, events: list[dict], *,
                process_load_mw: float, water_load_mw: float, seed: int = 17) -> None:
        """Dispatch sources and allocate power to site, water, and process loads."""
        for name, initial in self.initialize()["transformers"].items():
            state.setdefault("transformers", {}).setdefault(name, initial.copy())
        state["sim_seconds"], state["seed"] = sim_seconds, seed
        faults = [event for event in events if event["name"] == "Utility loss"]
        gen_faults = [event for event in events if event["name"] == "Mechanical degradation"]

        tie_factor = 1.0
        mine_factor = 1.0
        process_factor = 1.0
        water_factor = 1.0
        generation_factor = 1.0
        for event in faults:
            severity = self.SEVERITY[event["severity"]]
            area = event["area"]
            if area in ("Whole factory", "Grid transmission"):
                # A Large transmission loss trips the intertie. Smaller disturbances
                # leave it connected at a reduced import limit.
                factor = 0.0 if area == "Grid transmission" and event["severity"] == "Large" else 1.0 - severity
                tie_factor = min(tie_factor, factor)
            if area in ("Whole factory", "Mine power network"):
                mine_factor = min(mine_factor, 1.0 - severity)
            if area == "Grid distribution":
                process_factor = min(process_factor, 1.0 - severity)
                water_factor = min(water_factor, 1.0 - severity * 0.55)
            if area == "Water supply network":
                water_factor = min(water_factor, 1.0 - severity * 0.55)
            if area == "Grid generation":
                generation_factor = min(generation_factor, 1.0 - severity)
        if any(event["area"] in ("Whole factory", "Grid generation") for event in gen_faults):
            generation_factor *= 0.72

        state["tie_capacity_mw"] = 44.0 * tie_factor
        state["mine_transformer_capacity_mw"] = 39.0 * mine_factor
        state["process_feeder_capacity_mw"] = 32.0 * process_factor * mine_factor
        state["water_feeder_capacity_mw"] = 2.5 * water_factor * mine_factor

        solar = (2.2 + 0.65 * math.sin(sim_seconds / 430.0) + 0.22 * math.sin(sim_seconds / 71.0))
        solar = min(3.2, max(0.8, solar)) * generation_factor
        diesel_cap = 8.0 * generation_factor
        process_request = max(0.0, process_load_mw)
        water_request = max(0.0, water_load_mw)
        site_request = 6.0
        dispatch_request = (site_request + min(water_request, state["water_feeder_capacity_mw"])
                            + min(process_request, state["process_feeder_capacity_mw"]))
        mine_thermal = state["transformers"]
        mine_a_factor = max(0.0, min(1.0, 1.0 - max(0.0, mine_thermal["mine_a"]["hotspot_c"] - 110.0) / 30.0))
        mine_b_factor = max(0.0, min(1.0, 1.0 - max(0.0, mine_thermal["mine_b"]["hotspot_c"] - 110.0) / 30.0))
        mine_transformer_limit = min(24.0 * 0.95 * mine_a_factor / 0.54,
                                     24.0 * 0.95 * mine_b_factor / 0.46)
        state["mine_transformer_capacity_mw"] = min(state["mine_transformer_capacity_mw"],
                                                      mine_transformer_limit)
        grid_a_factor = max(0.0, min(1.0, 1.0 - max(0.0, mine_thermal["grid_a"]["hotspot_c"] - 110.0) / 30.0))
        grid_b_factor = max(0.0, min(1.0, 1.0 - max(0.0, mine_thermal["grid_b"]["hotspot_c"] - 110.0) / 30.0))
        grid_transformer_limit = min(30.0 * 0.95 * grid_a_factor / 0.52,
                                     30.0 * 0.95 * grid_b_factor / 0.48)
        tie_cap = min(state["tie_capacity_mw"], state["mine_transformer_capacity_mw"],
                      grid_transformer_limit)

        residual_after_grid_and_solar = max(0.0, dispatch_request - tie_cap - solar)
        if residual_after_grid_and_solar > 0.8 and diesel_cap > 0:
            state["diesel_start_timer_s"] += 1
            if state["diesel_start_timer_s"] >= 20:
                state["diesel_started"] = True
        else:
            state["diesel_start_timer_s"] = max(0, state["diesel_start_timer_s"] - 2)
        diesel_target = min(diesel_cap, residual_after_grid_and_solar) if state["diesel_started"] else 0.0
        state["diesel_mw"] += min(0.12, max(-0.12, diesel_target - state["diesel_mw"]))
        if state["diesel_mw"] < 0.01 and state["diesel_start_timer_s"] == 0:
            state["diesel_mw"] = 0.0
            state["diesel_started"] = False

        grid_for_load = min(tie_cap, max(0.0, dispatch_request - solar - state["diesel_mw"]))
        deficit = max(0.0, dispatch_request - solar - state["diesel_mw"] - grid_for_load)
        battery_target = min(state["battery_discharge_limit_mw"], deficit,
                             state["battery_energy_mwh"] * 3600.0)
        if deficit <= 1e-9 and state["battery_energy_mwh"] < state["battery_capacity_mwh"]:
            charge_headroom = min(state["battery_charge_limit_mw"], tie_cap - grid_for_load,
                                  (state["battery_capacity_mwh"] - state["battery_energy_mwh"]) * 3600.0)
            battery_target = -max(0.0, charge_headroom)
        # The storage inverter has a finite ramp and cannot reverse direction instantly.
        previous_battery = state["battery_power_mw"]
        state["battery_power_mw"] = min(previous_battery + 0.8,
                                         max(previous_battery - 0.8, battery_target))
        if state["battery_power_mw"] > state["battery_energy_mwh"] * 3600.0:
            state["battery_power_mw"] = state["battery_energy_mwh"] * 3600.0
        charge_power = max(0.0, -state["battery_power_mw"])
        grid_import = min(tie_cap, grid_for_load + charge_power)
        energy_change = 0.0 if sim_seconds == 0 else -state["battery_power_mw"] / 3600.0
        state["battery_energy_mwh"] = min(state["battery_capacity_mwh"], max(0.0,
            state["battery_energy_mwh"] + energy_change))
        state["battery_soc_pct"] = 100.0 * state["battery_energy_mwh"] / state["battery_capacity_mwh"]
        state["solar_mw"] = solar
        state["diesel_available_mw"] = diesel_cap
        state["grid_import_mw"] = grid_import
        state["total_generation_mw"] = grid_import + solar + state["diesel_mw"] + max(0.0, state["battery_power_mw"])

        available = state["total_generation_mw"]
        # Preserve 3 MW of essential mine services, then prioritize process water
        # and the concentrator before shedding the remaining 3 MW of site auxiliaries.
        served_critical_site = min(3.0, available)
        available = max(0.0, available - served_critical_site)
        served_water = min(water_request, state["water_feeder_capacity_mw"], available)
        available = max(0.0, available - served_water)
        served_process = min(process_request, state["process_feeder_capacity_mw"], available)
        available = max(0.0, available - served_process)
        served_flexible_site = min(3.0, available)
        served_site = served_critical_site + served_flexible_site
        state["process_requested_mw"] = process_request
        state["water_requested_mw"] = water_request
        state["process_load_mw"] = served_process
        state["water_load_mw"] = served_water
        state["site_load_mw"] = served_site
        state["requested_load_mw"] = dispatch_request
        state["served_load_mw"] = served_site + served_water + served_process
        state["load_shed_mw"] = max(0.0, site_request - served_site)
        state["unserved_load_mw"] = max(0.0, dispatch_request - state["served_load_mw"] - charge_power)
        state["process_power_fraction"] = (min(1.0, served_process / process_request)
                                             if process_request > 1e-6 else 1.0)
        state["water_power_fraction"] = (min(1.0, served_water / water_request)
                                          if water_request > 1e-6 else 1.0)
        state["power_deficit"] = state["unserved_load_mw"] > 0.05

        # Aggregate swing response with primary damping; shed site auxiliaries first.
        # Frequency responds to demand requested at the start of this tick. Process
        # derating feeds back on the next tick, so a sudden deficit causes a real
        # short transient before motor loads settle to the available supply.
        net = state["total_generation_mw"] - max(0.0, dispatch_request - state["load_shed_mw"]) - charge_power
        if state["frequency_hz"] < 49.55 and state["load_shed_mw"] < 3.0:
            extra_shed = min(3.0 - state["load_shed_mw"], max(0.0, 1.5 - state["load_shed_mw"]))
            state["load_shed_mw"] += extra_shed
            state["site_load_mw"] = max(3.0, state["site_load_mw"] - extra_shed)
            state["served_load_mw"] = state["site_load_mw"] + served_water + served_process
            net = state["total_generation_mw"] - max(0.0, dispatch_request - state["load_shed_mw"]) - charge_power
        state["frequency_hz"] = min(51.0, max(47.0,
            state["frequency_hz"] + 0.035 * net - 0.16 * (state["frequency_hz"] - 50.0)))
        state["voltage_pu"] = min(1.08, max(0.78,
            1.0 - max(0.0, dispatch_request - state["total_generation_mw"]) * 0.012
            - state["load_shed_mw"] * 0.004))
        state["power_deficit"] = state["power_deficit"] or state["frequency_hz"] < 49.5

        # Winding temperature responds faster than oil temperature. The thermal
        # state carries across scans, so sustained loading can trigger cooling
        # stages and eventually reduce the transformer transfer limit.
        transformer = state["transformers"]
        mine_a_mw = state["process_load_mw"] * 0.54 + state["water_load_mw"] * 0.54 + state["site_load_mw"] * 0.54
        mine_b_mw = state["process_load_mw"] * 0.46 + state["water_load_mw"] * 0.46 + state["site_load_mw"] * 0.46
        grid_a_mw = state["grid_import_mw"] * 0.52
        grid_b_mw = state["grid_import_mw"] * 0.48
        self._advance_transformer(transformer["mine_a"], mine_a_mw * 1.02, 24.0, 1)
        self._advance_transformer(transformer["mine_b"], mine_b_mw * 1.02, 24.0, 1)
        self._advance_transformer(transformer["grid_a"], grid_a_mw * 1.02, 30.0, 1)
        self._advance_transformer(transformer["grid_b"], grid_b_mw * 1.02, 30.0, 1)
        if sim_seconds > 0 and sim_seconds % 15 == 0:
            state["history"].append({key: state[key] for key in (
                "sim_seconds", "frequency_hz", "voltage_pu", "grid_import_mw", "solar_mw",
                "diesel_mw", "battery_soc_pct", "process_load_mw", "served_load_mw",
                "load_shed_mw", "process_power_fraction", "water_power_fraction")}
                | {f"{name}_{measurement}": thermal[name][field]
                   for name in ("mine_a", "mine_b")
                   for measurement, field in (("oil_c", "top_oil_c"),
                                              ("hotspot_c", "hotspot_c"))})
            state["history"] = state["history"][-1_440:]

    def telemetry(self, state: dict) -> list[dict]:
        """Publish simulated grid and mine electrical measurements as SCADA tags."""
        rng = random.Random(state["seed"] * 1_000_003 + state["sim_seconds"] * 9_176)
        v = state["voltage_pu"]
        grid = state["grid_import_mw"]
        mine = state["process_load_mw"] + state["water_load_mw"] + state["site_load_mw"]
        diesel = state["diesel_mw"]
        values = {
            "Grid generation": {
                "Turbine generator 01": {"active_power": (diesel * .6, "MW", 0, 8), "grid_frequency": (state["frequency_hz"], "Hz", 47, 53), "breaker_state": (float(diesel > .1), "bool", 0, 1), "transformer_oil_temp": (62 + diesel * 1.7, "°C", 0, 150)},
                "Turbine generator 02": {"active_power": (diesel * .4, "MW", 0, 8), "grid_frequency": (state["frequency_hz"], "Hz", 47, 53), "breaker_state": (float(diesel > .1), "bool", 0, 1), "transformer_oil_temp": (58 + diesel * 1.5, "°C", 0, 150)},
                "Solar farm": {"active_power": (state["solar_mw"], "MW", 0, 5), "grid_frequency": (state["frequency_hz"], "Hz", 47, 53), "breaker_state": (float(state["solar_mw"] > .1), "bool", 0, 1), "power_quality_thd": (2.2, "%", 0, 20)},
                "Battery storage": {"active_power": (state["battery_power_mw"], "MW", -4, 6), "state_of_charge": (state["battery_soc_pct"], "%", 0, 100), "breaker_state": (1.0, "bool", 0, 1), "grid_frequency": (state["frequency_hz"], "Hz", 47, 53)},
                "Black start unit": {"active_power": (0.0, "MW", 0, 3), "breaker_state": (0.0, "bool", 0, 1), "grid_frequency": (state["frequency_hz"], "Hz", 47, 53), "equipment_running": (0.0, "bool", 0, 1)},
            },
            "Grid transmission": {
                "Transmission line A": {"active_power": (grid * .52, "MW", 0, 30), "line_voltage": (220 * v, "kV", 180, 260), "line_current": (grid * .52 * 1000 / (math.sqrt(3) * 220), "A", 0, 3000), "breaker_state": (float(state["tie_capacity_mw"] > 23), "bool", 0, 1)},
                "Transmission line B": {"active_power": (grid * .48, "MW", 0, 30), "line_voltage": (220 * v, "kV", 180, 260), "line_current": (grid * .48 * 1000 / (math.sqrt(3) * 220), "A", 0, 3000), "breaker_state": (float(state["tie_capacity_mw"] > 2), "bool", 0, 1)},
                "Grid substation A": {"active_power": (grid * .52, "MW", 0, 30), "line_voltage": (220 * v, "kV", 180, 260), "reactive_power": (grid * .18, "MVAr", -200, 200), "transformer_oil_temp": (state["transformers"]["grid_a"]["top_oil_c"], "°C", 0, 150), "winding_hotspot_temp": (state["transformers"]["grid_a"]["hotspot_c"], "°C", 0, 180), "cooling_stage": (state["transformers"]["grid_a"]["cooling_stage"], "stage", 0, 2)},
                "Grid substation B": {"active_power": (grid * .48, "MW", 0, 30), "line_voltage": (220 * v, "kV", 180, 260), "reactive_power": (grid * .16, "MVAr", -200, 200), "transformer_oil_temp": (state["transformers"]["grid_b"]["top_oil_c"], "°C", 0, 150), "winding_hotspot_temp": (state["transformers"]["grid_b"]["hotspot_c"], "°C", 0, 180), "cooling_stage": (state["transformers"]["grid_b"]["cooling_stage"], "stage", 0, 2)},
                "Intertie": {"active_power": (grid, "MW", 0, 60), "line_voltage": (220 * v, "kV", 180, 260), "grid_frequency": (state["frequency_hz"], "Hz", 47, 53), "breaker_state": (float(state["tie_capacity_mw"] > 2), "bool", 0, 1)},
            },
            "Grid distribution": {
                "Feeder north": {"active_power": (mine * .38, "MW", 0, 30), "line_voltage": (33 * v, "kV", 25, 40), "line_current": (mine * .38 * 1000 / (math.sqrt(3) * 33), "A", 0, 3000), "breaker_state": (float(state["process_power_fraction"] > .05), "bool", 0, 1)},
                "Feeder south": {"active_power": (mine * .32, "MW", 0, 30), "line_voltage": (33 * v, "kV", 25, 40), "line_current": (mine * .32 * 1000 / (math.sqrt(3) * 33), "A", 0, 3000), "breaker_state": (float(state["water_power_fraction"] > .05), "bool", 0, 1)},
                "Feeder east": {"active_power": (mine * .18, "MW", 0, 30), "line_voltage": (11 * v, "kV", 8, 14), "line_current": (mine * .18 * 1000 / (math.sqrt(3) * 11), "A", 0, 3000), "breaker_state": (1.0, "bool", 0, 1)},
                "Feeder west": {"active_power": (mine * .12, "MW", 0, 30), "line_voltage": (11 * v, "kV", 8, 14), "line_current": (mine * .12 * 1000 / (math.sqrt(3) * 11), "A", 0, 3000), "breaker_state": (1.0, "bool", 0, 1)},
                "Capacitor bank": {"active_power": (0.0, "MW", 0, 1), "reactive_power": (-3.5 if v < .96 else -1.2, "MVAr", -10, 10), "line_voltage": (33 * v, "kV", 25, 40), "breaker_state": (1.0, "bool", 0, 1)},
            },
            "Mine power network": {
                "Mine substation A": {"active_power": (mine * .54, "MW", 0, 40), "line_voltage": (33 * v, "kV", 25, 40), "reactive_power": (mine * .2, "MVAr", -200, 200), "transformer_oil_temp": (state["transformers"]["mine_a"]["top_oil_c"], "°C", 0, 150), "winding_hotspot_temp": (state["transformers"]["mine_a"]["hotspot_c"], "°C", 0, 180), "cooling_stage": (state["transformers"]["mine_a"]["cooling_stage"], "stage", 0, 2), "breaker_state": (float(state["mine_transformer_capacity_mw"] > 10), "bool", 0, 1)},
                "Mine substation B": {"active_power": (mine * .46, "MW", 0, 40), "line_voltage": (33 * v, "kV", 25, 40), "reactive_power": (mine * .17, "MVAr", -200, 200), "transformer_oil_temp": (state["transformers"]["mine_b"]["top_oil_c"], "°C", 0, 150), "winding_hotspot_temp": (state["transformers"]["mine_b"]["hotspot_c"], "°C", 0, 180), "cooling_stage": (state["transformers"]["mine_b"]["cooling_stage"], "stage", 0, 2), "breaker_state": (float(state["mine_transformer_capacity_mw"] > 10), "bool", 0, 1)},
                "Pit distribution": {"active_power": (3.6 * state["water_power_fraction"], "MW", 0, 12), "line_voltage": (11 * v, "kV", 8, 14), "line_current": (110.0 * state["water_power_fraction"], "A", 0, 3000), "breaker_state": (float(state["water_power_fraction"] > .05), "bool", 0, 1)},
                "Underground feeders": {"active_power": (4.2, "MW", 0, 12), "line_voltage": (11 * v, "kV", 8, 14), "line_current": (125.0, "A", 0, 3000), "breaker_state": (1.0, "bool", 0, 1)},
                "Emergency generation": {"active_power": (0.0, "MW", 0, 8), "grid_frequency": (state["frequency_hz"], "Hz", 47, 53), "breaker_state": (0.0, "bool", 0, 1), "equipment_running": (0.0, "bool", 0, 1)},
            },
        }
        rows = []
        for area, assets in values.items():
            for asset, signals in assets.items():
                for signal, (truth, units, low, high) in signals.items():
                    tag_id = f"{area.lower().replace(' ', '_')}.{asset.lower().replace(' ', '_')}.{signal}"
                    noise = rng.gauss(0.0, max(high-low, 1e-9) * .001) if signal not in ("breaker_state", "equipment_running") else 0.0
                    rows.append({"tag": tag_id, "area": area, "unit": asset, "sensor": signal,
                        "units": units, "value": round(min(high, max(low, truth + noise)), 3),
                        "quality": "GOOD", "low": low, "high": high, "base": (low + high) / 2,
                        "phase": 0.0, "last_change": "Dynamic mine electrical model"})
        rows.extend([
            {"tag": "mine_power_network.concentrator_substation.active_power", "area": "Mine power network",
             "unit": "Concentrator substation", "sensor": "active_power", "units": "MW",
             "value": round(state["process_load_mw"], 3), "quality": "GOOD", "low": 0, "high": 50,
             "base": 25, "phase": 0.0, "last_change": "Process electrical demand"},
            {"tag": "mine_power_network.water_pump_station.pump_load", "area": "Mine power network",
             "unit": "Water pump station", "sensor": "active_power", "units": "MW",
             "value": round(state["water_load_mw"], 3), "quality": "GOOD", "low": 0, "high": 2,
             "base": .1, "phase": 0.0, "last_change": "Water pump electrical load"},
        ])
        return rows
