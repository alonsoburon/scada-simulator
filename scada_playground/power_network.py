"""Low-compute dynamic model of a utility-fed mine microgrid.

This is a balanced, quasi-steady network with explicit source dispatch, feeder
limits, storage energy, diesel start/ramp, load shedding and frequency response.
It is intended for SCADA drills; it is not an AC load-flow or protection study.
"""
from __future__ import annotations

import math

try:
    from .instrumentation import correlated_sensor_noise
except ImportError:
    from instrumentation import correlated_sensor_noise


class MinePowerNetwork:
    AREA_ASSETS = {
        "Grid generation": ["Turbine generator 01", "Turbine generator 02", "Solar farm", "Battery storage", "Black start unit", "Diesel fuel system"],
        "Grid transmission": ["Transmission line A", "Transmission line B", "Grid substation A", "Grid substation B", "Intertie"],
        "Grid distribution": ["Distribution incomer", "Feeder north", "Feeder south", "Feeder east", "Feeder west", "Capacitor bank"],
        "Mine power network": ["Mine substation A", "Mine substation B", "Concentrator substation",
                               "Factory utility feeder", "Pit distribution", "Underground feeders",
                               "Emergency generation"],
    }
    SEVERITY = {"Small": 0.18, "Medium": 0.42, "Large": 0.78}
    FAULT_RESISTANCE_PU = {"Small": 0.12, "Medium": 0.04, "Large": 0.005}
    PRIMARY_RELAY_TMS = 0.60
    BACKUP_RELAY_TMS = 0.50
    BACKUP_COORDINATION_MARGIN_S = 1.0
    SOURCE_BASE_MVA = 50.0
    SOURCE_R_PU = 0.004
    SOURCE_X_PU = 0.035
    CAPACITOR_SWITCH_DELAY_S = 10
    NOMINAL_FREQUENCY_HZ = 50.0
    DIESEL_DROOP_MW_PER_HZ = 2.67
    DIESEL_RAMP_MW_PER_S = 0.12
    DIESEL_FUEL_CAPACITY_L = 36_000.0
    DIESEL_MIN_START_FUEL_L = 250.0
    DIESEL_IDLE_FUEL_L_H = 70.0
    DIESEL_FUEL_L_PER_MWH = 260.0
    BATTERY_DROOP_MW_PER_HZ = 2.4
    BATTERY_CHARGE_EFFICIENCY = 0.94
    BATTERY_DISCHARGE_EFFICIENCY = 0.94
    FREQUENCY_SUBSTEPS_PER_SCAN = 10
    FREQUENCY_SWING_GAIN_HZ_PER_MW_S = 0.035
    FREQUENCY_DAMPING_PER_S = 0.16
    UFLS_STAGES = ((49.2, 0.10), (48.8, 0.10), (48.4, 0.15))
    UFLS_PICKUP_DELAY_S = 0.2
    UFLS_RESTORE_FREQUENCY_HZ = 49.7
    UFLS_RESTORE_DELAY_S = 30
    DISTRIBUTION_FEEDERS = {
        "north": {"share": 0.38, "cap_share": 0.60, "nominal_kv": 33.0,
                  "rating_mva": 22.0, "r_pu": 0.018, "x_pu": 0.060, "power_factor": 0.92},
        "south": {"share": 0.32, "cap_share": 0.40, "nominal_kv": 33.0,
                  "rating_mva": 18.0, "r_pu": 0.025, "x_pu": 0.070, "power_factor": 0.88},
        "east": {"share": 0.18, "cap_share": 0.00, "nominal_kv": 11.0,
                 "rating_mva": 14.0, "r_pu": 0.035, "x_pu": 0.085, "power_factor": 0.95},
        "west": {"share": 0.12, "cap_share": 0.00, "nominal_kv": 11.0,
                 "rating_mva": 10.0, "r_pu": 0.050, "x_pu": 0.110, "power_factor": 0.90},
    }

    def initialize(self) -> dict:
        return {
            "sim_seconds": 0, "frequency_hz": 50.0, "frequency_rocof_hz_s": 0.0,
            "frequency_nadir_hz": 50.0, "frequency_window_nadir_hz": 50.0,
            "voltage_pu": 1.0,
            "source_voltage_pu": 1.0, "capacitor_bank_stage": 1,
            "capacitor_control_timer_s": 0,
            "capacitor_bank_mvar": 1.2, "distribution_losses_mw": 0.575,
            "distribution_feeders": {name: {"active_power_mw": 0.0, "reactive_power_mvar": 0.0,
                "apparent_power_mva": 0.0, "current_a": 0.0, "voltage_pu": 1.0,
                "voltage_drop_pu": 0.0, "loss_mw": 0.0, "loading_pct": 0.0,
                "breaker_closed": True, "energized": True,
                "relay_pickup": False, "relay_current_multiple": 0.0,
                "relay_time_to_trip_s": 0.0, "relay_timer_s": 0.0,
                "trip_count": 0, "reclose_count": 0, "reclose_timer_s": 0.0,
                "lockout": False, "last_trip_reason": "", "relay_operated": False,
                "relay_operation_count": 0, "breaker_failure_count": 0,
                "pickup_current_a": 0.0, "fault_current_a": 0.0,
                "fault_location_pct": 0.0}
                for name in self.DISTRIBUTION_FEEDERS},
            "distribution_incomer": {"breaker_closed": True, "relay_pickup": False,
                "current_a": 0.0, "relay_current_a": 0.0, "relay_current_multiple": 0.0,
                "relay_time_to_trip_s": 0.0, "relay_timer_s": 0.0,
                "trip_count": 0, "lockout": False, "last_trip_reason": "",
                "relay_operation_count": 0, "breaker_failure_count": 0},
            "tie_capacity_mw": 44.0, "mine_transformer_capacity_mw": 39.0,
            "process_feeder_capacity_mw": 36.0, "water_feeder_capacity_mw": 2.5,
            "solar_mw": 2.2, "diesel_mw": 0.0, "diesel_available_mw": 8.0,
            "diesel_start_timer_s": 0, "diesel_started": False,
            "diesel_fuel_inventory_l": self.DIESEL_FUEL_CAPACITY_L,
            "diesel_fuel_consumption_l_h": 0.0, "diesel_fuel_used_l": 0.0,
            "diesel_governor_bias_mw": 0.0, "diesel_dispatch_target_mw": 0.0,
            "battery_soc_pct": 82.0, "battery_energy_mwh": 16.4,
            "battery_capacity_mwh": 20.0, "battery_power_mw": 0.0,
            "battery_frequency_response_mw": 0.0,
            "battery_dispatch_target_mw": 0.0,
            "battery_scan_average_power_mw": 0.0,
            "battery_conversion_loss_mw": 0.0,
            "battery_scan_average_loss_mw": 0.0,
            "battery_discharge_ramp_mw_s": 6.0, "battery_charge_ramp_mw_s": 1.5,
            "transformers": {
                "grid_a": {"top_oil_c": 58.0, "hotspot_c": 73.0, "cooling_stage": 0},
                "grid_b": {"top_oil_c": 56.0, "hotspot_c": 70.0, "cooling_stage": 0},
                "mine_a": {"top_oil_c": 64.0, "hotspot_c": 80.0, "cooling_stage": 0},
                "mine_b": {"top_oil_c": 56.0, "hotspot_c": 70.0, "cooling_stage": 0},
            },
            "battery_discharge_limit_mw": 6.0, "battery_charge_limit_mw": 3.0,
            "grid_import_mw": 0.0, "total_generation_mw": 0.0,
            "site_load_mw": 6.0, "water_load_mw": 0.5,
            "site_building_load_mw": 0.0, "site_nonbuilding_request_mw": 6.0,
            "site_flexible_power_fraction": 1.0,
            "process_load_mw": 28.0, "requested_load_mw": 34.5,
            "served_load_mw": 34.5, "load_shed_mw": 0.0,
            "process_power_fraction": 1.0, "water_power_fraction": 1.0,
            "factory_utilities_power_fraction": 1.0,
            "factory_utilities_requested_mw": 0.0, "factory_utilities_load_mw": 0.0,
            "motor_starting_load_mw": 0.0,
            "ufls_stage": 0, "ufls_restore_timer_s": 0,
            "ufls_pickup_timer_s": 0.0,
            "ufls_shed_mw": 0.0, "ufls_operated_this_scan_mw": 0.0,
            "process_after_ufls_mw": 28.0,
            "unserved_load_mw": 0.0, "power_deficit": False,
            "history": [], "seed": 17,
        }

    @classmethod
    def _radial_feeder_flows(cls, mine_load_mw: float, source_voltage_pu: float,
                             capacitor_mvar: float,
                             active_power_by_feeder: dict[str, float] | None = None) -> dict[str, dict[str, float]]:
        """Estimate four radial feeder flows with linearized DistFlow voltage drop and I²R loss."""
        voltage = max(0.70, source_voltage_pu)
        flows = {}
        for name, spec in cls.DISTRIBUTION_FEEDERS.items():
            active = (max(0.0, active_power_by_feeder[name]) if active_power_by_feeder is not None
                      else max(0.0, mine_load_mw) * spec["share"])
            reactive = active * math.tan(math.acos(spec["power_factor"]))
            reactive -= capacitor_mvar * spec["cap_share"]
            apparent = math.hypot(active, reactive)
            base = spec["rating_mva"]
            p_pu, q_pu = active / base, reactive / base
            drop = (spec["r_pu"] * p_pu + spec["x_pu"] * q_pu) / voltage
            end_voltage = min(1.08, max(0.70, voltage - drop))
            loss = (apparent / base) ** 2 * spec["r_pu"] * base / (voltage**2)
            sending_active = active + loss
            sending_apparent = math.hypot(sending_active, reactive)
            current = sending_apparent * 1_000.0 / (
                math.sqrt(3.0) * spec["nominal_kv"] * end_voltage)
            flows[name] = {"active_power_mw": sending_active,
                "reactive_power_mvar": reactive, "apparent_power_mva": sending_apparent,
                "current_a": current, "voltage_pu": end_voltage,
                "voltage_drop_pu": drop, "loss_mw": loss,
                "loading_pct": 100.0 * sending_apparent / base}
        return flows

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

    @classmethod
    def _set_ufls_shed_state(cls, state: dict, process_request_mw: float) -> None:
        """Calculate the requested process load and block total for the latched UFLS stage."""
        stage = max(0, min(len(cls.UFLS_STAGES), int(state.get("ufls_stage", 0))))
        state["ufls_stage"] = stage
        shed_fraction = sum(block_mw_fraction for _, block_mw_fraction
                            in cls.UFLS_STAGES[:stage])
        state["ufls_shed_mw"] = max(0.0, process_request_mw) * shed_fraction
        state["process_after_ufls_mw"] = max(0.0, process_request_mw - state["ufls_shed_mw"])

    @classmethod
    def _advance_ufls_relay(cls, state: dict, process_request_mw: float,
                            interval_s: float) -> None:
        """Integrate UFLS pickup/restoration timers at the frequency substep rate."""
        stage = state["ufls_stage"]
        frequency = state["frequency_hz"]
        if stage < len(cls.UFLS_STAGES) and frequency <= cls.UFLS_STAGES[stage][0]:
            state["ufls_pickup_timer_s"] = round(state["ufls_pickup_timer_s"] + interval_s, 6)
            state["ufls_restore_timer_s"] = 0.0
            if state["ufls_pickup_timer_s"] + 1e-9 >= cls.UFLS_PICKUP_DELAY_S:
                stage += 1
                state["ufls_pickup_timer_s"] = 0.0
        elif stage > 0 and frequency >= cls.UFLS_RESTORE_FREQUENCY_HZ:
            state["ufls_pickup_timer_s"] = 0.0
            state["ufls_restore_timer_s"] = round(
                state["ufls_restore_timer_s"] + interval_s, 6)
            if state["ufls_restore_timer_s"] + 1e-9 >= cls.UFLS_RESTORE_DELAY_S:
                stage -= 1
                state["ufls_restore_timer_s"] = 0.0
        else:
            state["ufls_pickup_timer_s"] = 0.0
            state["ufls_restore_timer_s"] = 0.0
        state["ufls_stage"] = stage
        cls._set_ufls_shed_state(state, process_request_mw)

    def advance(self, state: dict, sim_seconds: int, events: list[dict], *,
                process_load_mw: float, water_load_mw: float,
                utility_load_mw: float = 0.0, motor_starting_load_mw: float = 0.0,
                site_building_load_mw: float = 0.0,
                site_nonbuilding_request_mw: float = 6.0,
                seed: int = 17) -> None:
        """Dispatch sources and allocate power to site, water, and process loads."""
        initial_state = self.initialize()
        for key in ("frequency_rocof_hz_s", "distribution_losses_mw",
                    "source_voltage_pu", "capacitor_bank_stage", "capacitor_bank_mvar",
                    "capacitor_control_timer_s",
                    "diesel_governor_bias_mw", "diesel_dispatch_target_mw",
                    "battery_frequency_response_mw", "battery_dispatch_target_mw",
                    "battery_scan_average_power_mw",
                    "distribution_feeders", "battery_discharge_ramp_mw_s",
                    "battery_charge_ramp_mw_s"):
            state.setdefault(key, initial_state[key])
        state.setdefault("frequency_nadir_hz", state.get("frequency_hz", 50.0))
        state.setdefault("frequency_window_nadir_hz", state.get("frequency_hz", 50.0))
        state.setdefault("factory_utilities_power_fraction", 1.0)
        state.setdefault("factory_utilities_requested_mw", 0.0)
        state.setdefault("factory_utilities_load_mw", 0.0)
        state.setdefault("diesel_fuel_inventory_l", self.DIESEL_FUEL_CAPACITY_L)
        state.setdefault("diesel_fuel_consumption_l_h", 0.0)
        state.setdefault("diesel_fuel_used_l", 0.0)
        state.setdefault("battery_conversion_loss_mw", 0.0)
        state.setdefault("battery_scan_average_loss_mw", 0.0)
        state.setdefault("site_flexible_power_fraction", 1.0)
        for name, initial in initial_state["transformers"].items():
            state.setdefault("transformers", {}).setdefault(name, initial.copy())
        state["sim_seconds"], state["seed"] = sim_seconds, seed
        site_building_load_mw = max(0.0, site_building_load_mw)
        site_request = max(0.0, site_nonbuilding_request_mw) + site_building_load_mw
        state["site_building_load_mw"] = site_building_load_mw
        state["site_nonbuilding_request_mw"] = max(0.0, site_nonbuilding_request_mw)
        state.setdefault("ufls_stage", 0)
        state.setdefault("ufls_restore_timer_s", 0)
        state.setdefault("ufls_pickup_timer_s", 0.0)
        state.setdefault("ufls_shed_mw", 0.0)
        state.setdefault("ufls_operated_this_scan_mw", 0.0)
        motor_starting_load_mw = max(0.0, motor_starting_load_mw)
        state["motor_starting_load_mw"] = motor_starting_load_mw
        process_request = max(0.0, process_load_mw) + motor_starting_load_mw
        state.setdefault("process_after_ufls_mw", process_request)
        self._set_ufls_shed_state(state, process_request)
        self._advance_feeder_protection(state, sim_seconds, events)
        connected_load_at_tick_start = state.get("served_load_mw", 34.5)
        connected_process_at_tick_start = state.get("process_load_mw", process_load_mw)
        faults = [event for event in events if event["name"] == "Utility loss"]
        gen_faults = [event for event in events if event["name"] == "Mechanical degradation"]

        tie_factor = 1.0
        mine_factor = 1.0
        process_factor = 1.0
        water_factor = 1.0
        generation_factor = 1.0
        utility_factor = 1.0
        site_factor = 1.0
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

        # The mine's four distribution branches have distinct customer groups.
        # Losing one branch disconnects its local demand; upstream generation
        # cannot supply a load behind an open breaker.
        protection = state["distribution_feeders"]
        distribution_online = state["distribution_incomer"]["breaker_closed"]
        process_factor = min(process_factor,
            distribution_online * (0.45 * protection["north"]["breaker_closed"]
            + 0.30 * protection["south"]["breaker_closed"]
            + 0.15 * protection["east"]["breaker_closed"]
            + 0.10 * protection["west"]["breaker_closed"]))
        water_factor = min(water_factor, float(distribution_online and protection["south"]["breaker_closed"]))
        utility_factor = min(utility_factor,
            distribution_online * (0.30 * protection["north"]["breaker_closed"]
            + 0.15 * protection["south"]["breaker_closed"]
            + 0.30 * protection["east"]["breaker_closed"]
            + 0.25 * protection["west"]["breaker_closed"]))
        site_factor = min(site_factor,
            distribution_online * (0.65 * protection["south"]["breaker_closed"]
            + 0.25 * protection["east"]["breaker_closed"]
            + 0.10 * protection["west"]["breaker_closed"]))

        state["tie_capacity_mw"] = 44.0 * tie_factor
        state["mine_transformer_capacity_mw"] = 39.0 * mine_factor
        state["process_feeder_capacity_mw"] = 36.0 * process_factor * mine_factor
        state["water_feeder_capacity_mw"] = 2.5 * water_factor * mine_factor

        solar = (2.2 + 0.65 * math.sin(sim_seconds / 430.0) + 0.22 * math.sin(sim_seconds / 71.0))
        solar = min(3.2, max(0.8, solar)) * generation_factor
        diesel_cap = 8.0 * generation_factor
        process_request = max(0.0, process_load_mw) + motor_starting_load_mw
        water_request = max(0.0, water_load_mw)
        utility_request = max(0.0, utility_load_mw)
        available_site_request = site_request * site_factor
        available_water_request = water_request * water_factor
        available_utility_request = utility_request * utility_factor
        process_dispatch_request = state["process_after_ufls_mw"]
        process_feeder_request = min(process_dispatch_request * process_factor + available_utility_request,
                                     state["process_feeder_capacity_mw"])
        dispatch_request = (available_site_request + min(available_water_request, state["water_feeder_capacity_mw"])
                            + process_feeder_request)
        total_load_request = site_request + water_request + utility_request + process_request
        estimated_network_losses = max(0.0, state.get("distribution_losses_mw", 0.55))
        source_request = dispatch_request + estimated_network_losses
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

        residual_after_grid_and_solar = max(0.0, source_request - tie_cap - solar)
        if (residual_after_grid_and_solar > 0.8 and diesel_cap > 0
                and state["diesel_fuel_inventory_l"] >= self.DIESEL_MIN_START_FUEL_L):
            state["diesel_start_timer_s"] += 1
            if state["diesel_start_timer_s"] >= 20:
                state["diesel_started"] = True
        else:
            state["diesel_start_timer_s"] = max(0, state["diesel_start_timer_s"] - 2)
        diesel_base_target = (min(diesel_cap, residual_after_grid_and_solar)
                              if state["diesel_started"] else 0.0)
        state["diesel_dispatch_target_mw"] = diesel_base_target

        grid_for_load = min(tie_cap, max(0.0, source_request - solar - state["diesel_mw"]))
        deficit = max(0.0, source_request - solar - state["diesel_mw"] - grid_for_load)
        battery_base_target = min(state["battery_discharge_limit_mw"], deficit,
                                  state["battery_energy_mwh"] * 3600.0)
        if deficit <= 1e-9 and state["battery_energy_mwh"] < state["battery_capacity_mwh"]:
            charge_headroom = min(state["battery_charge_limit_mw"], tie_cap - grid_for_load,
                                  (state["battery_capacity_mwh"] - state["battery_energy_mwh"]) * 3600.0)
            battery_base_target = -max(0.0, charge_headroom)
        state["battery_dispatch_target_mw"] = battery_base_target
        charge_power = max(0.0, -state["battery_power_mw"])
        # The utility interchange balances actual, ramp-limited output, including
        # charging as additional demand.
        grid_import = min(tie_cap, max(0.0,
            source_request - solar - state["diesel_mw"] - state["battery_power_mw"]))
        state["solar_mw"] = solar
        state["diesel_available_mw"] = diesel_cap
        state["grid_import_mw"] = grid_import
        state["total_generation_mw"] = grid_import + solar + state["diesel_mw"] + max(0.0, state["battery_power_mw"])

        available = max(0.0, state["total_generation_mw"] - charge_power - estimated_network_losses)
        # Preserve critical controls and water first, then factory utility services,
        # then the concentrator; flexible site auxiliaries are last.
        served_critical_site = min(3.0 * site_factor, available)
        available = max(0.0, available - served_critical_site)
        served_water = min(available_water_request, state["water_feeder_capacity_mw"], available)
        available = max(0.0, available - served_water)
        feeder_headroom = max(0.0, state["process_feeder_capacity_mw"])
        served_utility = min(available_utility_request, feeder_headroom, available)
        feeder_headroom -= served_utility
        available = max(0.0, available - served_utility)
        served_process = min(process_dispatch_request * process_factor, feeder_headroom, available)
        available = max(0.0, available - served_process)
        site_flexible_request = max(0.0, available_site_request - 3.0 * site_factor)
        served_flexible_site = min(site_flexible_request, available)
        served_site = served_critical_site + served_flexible_site
        state["process_requested_mw"] = process_request
        state["water_requested_mw"] = water_request
        state["factory_utilities_requested_mw"] = utility_request
        state["factory_utilities_load_mw"] = served_utility
        state["process_load_mw"] = served_process
        state["water_load_mw"] = served_water
        state["site_load_mw"] = served_site
        state["site_flexible_power_fraction"] = (
            min(1.0, served_flexible_site / site_flexible_request)
            if site_flexible_request > 1e-9 else 1.0)
        state["requested_load_mw"] = total_load_request
        state["source_requested_mw"] = source_request
        state["served_load_mw"] = served_site + served_water + served_utility + served_process
        state["load_shed_mw"] = max(0.0, site_request - served_site)
        state["unserved_load_mw"] = max(0.0, total_load_request - state["served_load_mw"])
        state["process_power_fraction"] = (min(1.0, served_process / process_request)
                                             if process_request > 1e-6 else 1.0)
        state["water_power_fraction"] = (min(1.0, served_water / water_request)
                                          if water_request > 1e-6 else 1.0)
        state["factory_utilities_power_fraction"] = (min(1.0, served_utility / utility_request)
            if utility_request > 1e-6 else 1.0)
        state["power_deficit"] = state["unserved_load_mw"] > 0.05

        # Resolve frequency and relay action at 100 ms. The dispatch setpoints
        # remain one-second controls, while UFLS contacts can trip between scans.
        previous_frequency = state["frequency_hz"]
        grid_coupling = 0.98 * min(1.0, tie_cap / 44.0)
        grid_coupling_rate = -math.log(max(1e-9, 1.0 - grid_coupling))
        frequency_interval_s = 1.0 / self.FREQUENCY_SUBSTEPS_PER_SCAN
        # Carry disconnected load through the first disturbed scan, but let a
        # newly available restoration load enter the frequency balance now.
        # Using only the previous scan here made a restored grid appear to feed
        # full load while the frequency model still saw the islanded load, which
        # caused a fictitious overfrequency spike on reconnection.
        connected_load = max(connected_load_at_tick_start, state["served_load_mw"])
        connected_process = max(0.0, connected_process_at_tick_start, served_process)
        ufls_disconnect_this_scan = 0.0
        battery_power_integral_mw_s = 0.0
        diesel_fuel_scan_l = 0.0
        for _ in range(self.FREQUENCY_SUBSTEPS_PER_SCAN):
            diesel_droop = ((self.NOMINAL_FREQUENCY_HZ - state["frequency_hz"])
                            * self.DIESEL_DROOP_MW_PER_HZ if state["diesel_started"] else 0.0)
            if state["diesel_started"]:
                fuel_for_interval_l = (state["diesel_fuel_inventory_l"] * 3600.0
                                       / frequency_interval_s)
                fuel_limited_power_mw = max(0.0,
                    (fuel_for_interval_l - self.DIESEL_IDLE_FUEL_L_H)
                    / self.DIESEL_FUEL_L_PER_MWH)
                diesel_target = min(diesel_cap, fuel_limited_power_mw,
                                    max(0.0, diesel_base_target + diesel_droop))
            else:
                diesel_target = 0.0
            state["diesel_governor_bias_mw"] = diesel_target - diesel_base_target
            diesel_delta_limit = self.DIESEL_RAMP_MW_PER_S * frequency_interval_s
            state["diesel_mw"] += min(diesel_delta_limit,
                max(-diesel_delta_limit, diesel_target - state["diesel_mw"]))
            if state["diesel_started"]:
                fuel_rate_l_h = (self.DIESEL_IDLE_FUEL_L_H
                                 + max(0.0, state["diesel_mw"])
                                 * self.DIESEL_FUEL_L_PER_MWH)
                fuel_used_l = min(state["diesel_fuel_inventory_l"],
                                  fuel_rate_l_h * frequency_interval_s / 3600.0)
                state["diesel_fuel_inventory_l"] -= fuel_used_l
                state["diesel_fuel_used_l"] += fuel_used_l
                diesel_fuel_scan_l += fuel_used_l
                if state["diesel_fuel_inventory_l"] <= 1e-9:
                    state["diesel_fuel_inventory_l"] = 0.0
                    state["diesel_mw"] = 0.0
                    state["diesel_started"] = False
            if state["diesel_mw"] < 0.01 and state["diesel_start_timer_s"] == 0:
                state["diesel_mw"] = 0.0
                state["diesel_started"] = False

            battery_droop = ((self.NOMINAL_FREQUENCY_HZ - state["frequency_hz"])
                             * self.BATTERY_DROOP_MW_PER_HZ)
            battery_target = min(state["battery_discharge_limit_mw"], max(
                -state["battery_charge_limit_mw"],
                state["battery_dispatch_target_mw"] + battery_droop))
            state["battery_frequency_response_mw"] = (
                battery_target - state["battery_dispatch_target_mw"])
            previous_battery = state["battery_power_mw"]
            battery_ramp = (state["battery_discharge_ramp_mw_s"]
                            if battery_target > previous_battery
                            else state["battery_charge_ramp_mw_s"])
            battery_delta_limit = battery_ramp * frequency_interval_s
            state["battery_power_mw"] += min(battery_delta_limit,
                max(-battery_delta_limit, battery_target - previous_battery))
            if sim_seconds > 0:
                if state["battery_power_mw"] > 0.0:
                    energy_limited_power = (state["battery_energy_mwh"] * 3600.0
                        * self.BATTERY_DISCHARGE_EFFICIENCY / frequency_interval_s)
                    state["battery_power_mw"] = min(state["battery_power_mw"],
                                                     energy_limited_power)
                elif state["battery_power_mw"] < 0.0:
                    charge_room_power = ((state["battery_capacity_mwh"]
                        - state["battery_energy_mwh"]) * 3600.0
                        / (self.BATTERY_CHARGE_EFFICIENCY * frequency_interval_s))
                    state["battery_power_mw"] = max(state["battery_power_mw"],
                                                     -charge_room_power)
                if state["battery_power_mw"] >= 0.0:
                    energy_delta_mwh = (-state["battery_power_mw"] * frequency_interval_s
                        / (3600.0 * self.BATTERY_DISCHARGE_EFFICIENCY))
                else:
                    energy_delta_mwh = (-state["battery_power_mw"] * frequency_interval_s
                        * self.BATTERY_CHARGE_EFFICIENCY / 3600.0)
                state["battery_energy_mwh"] = min(state["battery_capacity_mwh"], max(0.0,
                    state["battery_energy_mwh"] + energy_delta_mwh))
            state["battery_soc_pct"] = (100.0 * state["battery_energy_mwh"]
                                         / state["battery_capacity_mwh"])
            battery_power_integral_mw_s += (state["battery_power_mw"]
                                             * frequency_interval_s)
            charge_power = max(0.0, -state["battery_power_mw"])
            grid_import = min(tie_cap, max(0.0,
                source_request - solar - state["diesel_mw"] - state["battery_power_mw"]))
            state["grid_import_mw"] = grid_import
            state["total_generation_mw"] = (grid_import + solar + state["diesel_mw"]
                                             + max(0.0, state["battery_power_mw"]))
            net = (state["total_generation_mw"] - connected_load - charge_power
                   - estimated_network_losses)
            swing_rate = (self.FREQUENCY_SWING_GAIN_HZ_PER_MW_S * net
                          - self.FREQUENCY_DAMPING_PER_S
                          * (state["frequency_hz"] - self.NOMINAL_FREQUENCY_HZ))
            state["frequency_hz"] += frequency_interval_s * swing_rate
            # An energized utility intertie is a stiff reference, represented as
            # a continuous first-order coupling instead of an end-of-scan snap.
            coupling = 1.0 - math.exp(-grid_coupling_rate * frequency_interval_s)
            state["frequency_hz"] += coupling * (
                self.NOMINAL_FREQUENCY_HZ - state["frequency_hz"])
            previous_ufls_shed = state["ufls_shed_mw"]
            self._advance_ufls_relay(state, process_request, frequency_interval_s)
            shed_increment = max(0.0, state["ufls_shed_mw"] - previous_ufls_shed)
            if shed_increment > 0.0:
                disconnected = min(shed_increment, connected_process)
                connected_process -= disconnected
                connected_load -= disconnected
                served_process = max(0.0, served_process - disconnected)
                ufls_disconnect_this_scan += disconnected
            state["frequency_nadir_hz"] = min(
                state.get("frequency_nadir_hz", self.NOMINAL_FREQUENCY_HZ),
                state["frequency_hz"])
            state["frequency_window_nadir_hz"] = min(
                state.get("frequency_window_nadir_hz", self.NOMINAL_FREQUENCY_HZ),
                state["frequency_hz"])
        state["frequency_rocof_hz_s"] = (state["frequency_hz"] - previous_frequency)
        state["diesel_fuel_consumption_l_h"] = diesel_fuel_scan_l * 3_600.0
        state["ufls_operated_this_scan_mw"] = ufls_disconnect_this_scan
        state["battery_scan_average_power_mw"] = battery_power_integral_mw_s
        battery_ac_power = state["battery_power_mw"]
        state["battery_conversion_loss_mw"] = (
            battery_ac_power * (1.0 / self.BATTERY_DISCHARGE_EFFICIENCY - 1.0)
            if battery_ac_power >= 0.0 else
            -battery_ac_power * (1.0 - self.BATTERY_CHARGE_EFFICIENCY))
        state["battery_scan_average_loss_mw"] = state["battery_conversion_loss_mw"]
        # Publish the scan's load allocation against the source output reached
        # by the end of the 100 ms source-control substeps. This keeps a source
        # starting/ramping during the scan from appearing unavailable for the
        # entire scan to downstream plant models.
        available = max(0.0, state["total_generation_mw"]
                        - max(0.0, -state["battery_power_mw"])
                        - estimated_network_losses)
        served_critical_site = min(3.0 * site_factor, available)
        available = max(0.0, available - served_critical_site)
        served_water = min(available_water_request, state["water_feeder_capacity_mw"], available)
        available = max(0.0, available - served_water)
        feeder_headroom = max(0.0, state["process_feeder_capacity_mw"])
        served_utility = min(available_utility_request, feeder_headroom, available)
        feeder_headroom -= served_utility
        available = max(0.0, available - served_utility)
        served_process = min(state["process_after_ufls_mw"] * process_factor,
                             feeder_headroom, available)
        available = max(0.0, available - served_process)
        site_flexible_request = max(0.0, available_site_request - 3.0 * site_factor)
        served_flexible_site = min(site_flexible_request, available)
        served_site = served_critical_site + served_flexible_site
        state["factory_utilities_load_mw"] = served_utility
        state["water_load_mw"] = served_water
        state["site_load_mw"] = served_site
        state["site_flexible_power_fraction"] = (
            min(1.0, served_flexible_site / site_flexible_request)
            if site_flexible_request > 1e-9 else 1.0)
        state["load_shed_mw"] = max(0.0, site_request - served_site)
        state["process_load_mw"] = served_process
        state["served_load_mw"] = served_site + served_water + served_utility + served_process
        state["unserved_load_mw"] = max(0.0, total_load_request - state["served_load_mw"])
        state["process_power_fraction"] = (min(1.0, served_process / process_request)
                                             if process_request > 1e-6 else 1.0)
        state["water_power_fraction"] = (min(1.0, served_water / water_request)
                                          if water_request > 1e-6 else 1.0)
        state["factory_utilities_power_fraction"] = (min(1.0, served_utility / utility_request)
            if utility_request > 1e-6 else 1.0)
        state["power_deficit"] = state["unserved_load_mw"] > 0.05
        reactive_load_mvar = (
            served_process * math.tan(math.acos(0.88))
            + served_water * math.tan(math.acos(0.95))
            + served_utility * math.tan(math.acos(0.90))
            + served_site * math.tan(math.acos(0.95)))
        net_reactive_mvar = reactive_load_mvar - state["capacitor_bank_mvar"]
        source_impedance_drop_pu = (
            self.SOURCE_R_PU * dispatch_request
            + self.SOURCE_X_PU * net_reactive_mvar) / self.SOURCE_BASE_MVA
        source_voltage = min(1.08, max(0.78,
            1.0 - source_impedance_drop_pu
            - max(0.0, dispatch_request - state["total_generation_mw"]) * 0.012
            - state["load_shed_mw"] * 0.004))
        capacitor_stage = state["capacitor_bank_stage"]
        requested_capacitor_stage = capacitor_stage
        if source_voltage < 0.96 and capacitor_stage < 2:
            requested_capacitor_stage = 2
        elif source_voltage > 1.03 and capacitor_stage > 1:
            requested_capacitor_stage = 1
        if requested_capacitor_stage != capacitor_stage:
            state["capacitor_control_timer_s"] += 1
            if state["capacitor_control_timer_s"] >= self.CAPACITOR_SWITCH_DELAY_S:
                state["capacitor_bank_stage"] = requested_capacitor_stage
                state["capacitor_bank_mvar"] = {1: 1.2, 2: 3.5}[requested_capacitor_stage]
                state["capacitor_control_timer_s"] = 0
        else:
            state["capacitor_control_timer_s"] = 0
        state["source_voltage_pu"] = source_voltage
        closed = {name: bool(distribution_online and protection[name]["breaker_closed"])
                  for name in self.DISTRIBUTION_FEEDERS}
        customer_shares = {
            "process": {"north": 0.45, "south": 0.30, "east": 0.15, "west": 0.10},
            "utility": {"north": 0.30, "south": 0.15, "east": 0.30, "west": 0.25},
            "site": {"south": 0.65, "east": 0.25, "west": 0.10},
        }
        def branch_allocation(served: float, group: str) -> dict[str, float]:
            shares = customer_shares[group]
            available_share = sum(share for name, share in shares.items() if closed[name])
            return {name: served * share / available_share if closed[name] and available_share > 0 else 0.0
                    for name, share in shares.items()}
        def solve_feeder_flows() -> dict[str, dict[str, float]]:
            process_by_feeder = branch_allocation(served_process, "process")
            utility_by_feeder = branch_allocation(served_utility, "utility")
            site_by_feeder = branch_allocation(served_site, "site")
            feeder_loads = {name:
                process_by_feeder.get(name, 0.0) + utility_by_feeder.get(name, 0.0)
                + site_by_feeder.get(name, 0.0)
                + (served_water if name == "south" and closed["south"] else 0.0)
                for name in self.DISTRIBUTION_FEEDERS}
            flows = self._radial_feeder_flows(
                state["served_load_mw"], source_voltage, state["capacitor_bank_mvar"], feeder_loads)
            for name, flow in flows.items():
                prior = state["distribution_feeders"][name]
                flow.update({key: prior[key] for key in (
                    "breaker_closed", "relay_pickup", "relay_current_multiple", "relay_time_to_trip_s",
                    "relay_timer_s", "trip_count", "reclose_count", "reclose_timer_s",
                    "lockout", "last_trip_reason", "pickup_current_a", "fault_current_a",
                    "fault_location_pct", "relay_operation_count", "breaker_failure_count",
                    "relay_operated")})
                flow["energized"] = bool(flow["breaker_closed"] and distribution_online)
                if not flow["energized"]:
                    flow.update(active_power_mw=0.0, reactive_power_mvar=0.0,
                                apparent_power_mva=0.0, current_a=0.0, loss_mw=0.0, loading_pct=0.0,
                                voltage_pu=0.0, voltage_drop_pu=source_voltage)
                elif flow["relay_pickup"]:
                    flow["current_a"] = flow["fault_current_a"]
                    flow["voltage_pu"] = max(0.70, flow["voltage_pu"]
                                              - min(0.28, 0.035 * flow["relay_current_multiple"]))
            return flows

        # Reconcile dispatch against this scan's actual I²R losses. A previous
        # scan's loss estimate can be badly low just after a feeder is restored.
        other_generation = (solar + state["diesel_mw"] + max(0.0, state["battery_power_mw"]))
        charge_power = max(0.0, -state["battery_power_mw"])
        for _ in range(4):
            feeder_flows = solve_feeder_flows()
            state["distribution_losses_mw"] = sum(flow["loss_mw"] for flow in feeder_flows.values())
            required_grid = (state["served_load_mw"] + charge_power
                             + state["distribution_losses_mw"] - other_generation)
            grid_import = min(tie_cap, max(0.0, required_grid))
            state["grid_import_mw"] = grid_import
            state["total_generation_mw"] = other_generation + grid_import
            shortfall = max(0.0, required_grid - tie_cap)
            if shortfall <= 1e-7:
                break
            # Preserve the same priority order as dispatch: shed flexible site
            # demand first, then process, utility, water, and critical site load.
            for key in ("flexible", "process", "utility", "water", "critical"):
                if shortfall <= 1e-7:
                    break
                if key == "flexible":
                    cut = min(served_flexible_site, shortfall)
                    served_flexible_site -= cut
                    served_site -= cut
                elif key == "process":
                    cut = min(served_process, shortfall)
                    served_process -= cut
                elif key == "utility":
                    cut = min(served_utility, shortfall)
                    served_utility -= cut
                elif key == "water":
                    cut = min(served_water, shortfall)
                    served_water -= cut
                else:
                    cut = min(served_critical_site, shortfall)
                    served_critical_site -= cut
                    served_site -= cut
                shortfall -= cut
            state["site_load_mw"] = served_site
            state["water_load_mw"] = served_water
            state["factory_utilities_load_mw"] = served_utility
            state["process_load_mw"] = served_process
            state["served_load_mw"] = served_site + served_water + served_utility + served_process
            state["load_shed_mw"] = max(0.0, site_request - served_site)
            state["unserved_load_mw"] = max(0.0, total_load_request - state["served_load_mw"])
            state["process_power_fraction"] = (min(1.0, served_process / process_request)
                                                if process_request > 1e-6 else 1.0)
            state["water_power_fraction"] = (min(1.0, served_water / water_request)
                                               if water_request > 1e-6 else 1.0)
            state["factory_utilities_power_fraction"] = (min(1.0, served_utility / utility_request)
                if utility_request > 1e-6 else 1.0)
            state["site_flexible_power_fraction"] = (
                min(1.0, served_flexible_site / site_flexible_request)
                if site_flexible_request > 1e-9 else 1.0)
            state["power_deficit"] = state["unserved_load_mw"] > 0.05
        feeder_flows = solve_feeder_flows()
        state["distribution_feeders"] = feeder_flows
        state["distribution_losses_mw"] = sum(flow["loss_mw"] for flow in feeder_flows.values())
        energized_voltages = [flow["voltage_pu"] for flow in feeder_flows.values()
                              if flow["energized"]]
        state["voltage_pu"] = (min([source_voltage, *energized_voltages])
                                if state["distribution_incomer"]["breaker_closed"] else 0.0)
        incomer = state["distribution_incomer"]
        normal_incomer_current = ((state["served_load_mw"] + state["distribution_losses_mw"])
                                  * 1000.0 / (math.sqrt(3.0) * 33.0 * 0.95))
        incomer["current_a"] = (incomer["relay_current_a"] if incomer["relay_current_a"] > 0.0
                                else normal_incomer_current) if incomer["breaker_closed"] else 0.0
        state["power_deficit"] = state["power_deficit"] or state["frequency_hz"] < 49.5

        # Winding temperature responds faster than oil temperature. The thermal
        # state carries across scans, so sustained loading can trigger cooling
        # stages and eventually reduce the transformer transfer limit.
        transformer = state["transformers"]
        mine_a_mw = (state["process_load_mw"] + state["factory_utilities_load_mw"]
                     + state["water_load_mw"] + state["site_load_mw"]) * 0.54
        mine_b_mw = (state["process_load_mw"] + state["factory_utilities_load_mw"]
                     + state["water_load_mw"] + state["site_load_mw"]) * 0.46
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
                "diesel_fuel_inventory_l", "diesel_fuel_consumption_l_h",
                "diesel_fuel_used_l",
                "factory_utilities_load_mw", "factory_utilities_power_fraction",
                "ufls_stage", "ufls_restore_timer_s", "ufls_shed_mw",
                "ufls_operated_this_scan_mw",
                "battery_scan_average_power_mw",
                "load_shed_mw", "process_power_fraction", "water_power_fraction",
                "frequency_rocof_hz_s", "frequency_nadir_hz",
                "frequency_window_nadir_hz", "distribution_losses_mw")}
                | {f"{name}_{measurement}": transformer[name][field]
                   for name in ("mine_a", "mine_b")
                   for measurement, field in (("oil_c", "top_oil_c"),
                                              ("hotspot_c", "hotspot_c"))})
            state["history"] = state["history"][-1_440:]
            state["frequency_window_nadir_hz"] = state["frequency_hz"]

    def telemetry(self, state: dict) -> list[dict]:
        """Publish simulated grid and mine electrical measurements as SCADA tags."""
        v = state["voltage_pu"]
        source_v = state.get("source_voltage_pu", v)
        grid = state["grid_import_mw"]
        mine = state["process_load_mw"] + state["water_load_mw"] + state["site_load_mw"]
        diesel = state["diesel_mw"]
        values = {
            "Grid generation": {
                "Turbine generator 01": {"active_power": (diesel * .6, "MW", 0, 8), "governor_bias": (state["diesel_governor_bias_mw"] * .6, "MW", -8, 8), "grid_frequency": (state["frequency_hz"], "Hz", 47, 53), "breaker_state": (float(diesel > .1), "bool", 0, 1), "transformer_oil_temp": (62 + diesel * 1.7, "°C", 0, 150)},
                "Turbine generator 02": {"active_power": (diesel * .4, "MW", 0, 8), "governor_bias": (state["diesel_governor_bias_mw"] * .4, "MW", -8, 8), "grid_frequency": (state["frequency_hz"], "Hz", 47, 53), "breaker_state": (float(diesel > .1), "bool", 0, 1), "transformer_oil_temp": (58 + diesel * 1.5, "°C", 0, 150)},
                "Solar farm": {"active_power": (state["solar_mw"], "MW", 0, 5), "grid_frequency": (state["frequency_hz"], "Hz", 47, 53), "breaker_state": (float(state["solar_mw"] > .1), "bool", 0, 1), "power_quality_thd": (2.2, "%", 0, 20)},
                "Battery storage": {"active_power": (state["battery_power_mw"], "MW", -4, 6), "scan_average_power": (state["battery_scan_average_power_mw"], "MW", -4, 6), "conversion_loss": (state["battery_conversion_loss_mw"], "MW", 0, 1), "frequency_response": (state["battery_frequency_response_mw"], "MW", -6, 6), "state_of_charge": (state["battery_soc_pct"], "%", 0, 100), "breaker_state": (1.0, "bool", 0, 1), "grid_frequency": (state["frequency_hz"], "Hz", 47, 53)},
                "Black start unit": {"active_power": (0.0, "MW", 0, 3), "breaker_state": (0.0, "bool", 0, 1), "grid_frequency": (state["frequency_hz"], "Hz", 47, 53), "equipment_running": (0.0, "bool", 0, 1)},
                "Diesel fuel system": {
                    "fuel_inventory": (state["diesel_fuel_inventory_l"], "L", 0,
                                       self.DIESEL_FUEL_CAPACITY_L),
                    "tank_level": (100.0 * state["diesel_fuel_inventory_l"]
                                   / self.DIESEL_FUEL_CAPACITY_L, "%", 0, 100),
                    "fuel_flow": (state["diesel_fuel_consumption_l_h"], "L/h", 0, 3_000),
                    "rated_load_endurance": (
                        state["diesel_fuel_inventory_l"]
                        / (self.DIESEL_IDLE_FUEL_L_H + 8.0 * self.DIESEL_FUEL_L_PER_MWH),
                        "h", 0, 100),
                },
            },
            "Grid transmission": {
                "Transmission line A": {"active_power": (grid * .52, "MW", 0, 30), "line_voltage": (220 * source_v, "kV", 180, 260), "line_current": (grid * .52 * 1000 / (math.sqrt(3) * 220), "A", 0, 3000), "breaker_state": (float(state["tie_capacity_mw"] > 23), "bool", 0, 1)},
                "Transmission line B": {"active_power": (grid * .48, "MW", 0, 30), "line_voltage": (220 * source_v, "kV", 180, 260), "line_current": (grid * .48 * 1000 / (math.sqrt(3) * 220), "A", 0, 3000), "breaker_state": (float(state["tie_capacity_mw"] > 2), "bool", 0, 1)},
                "Grid substation A": {"active_power": (grid * .52, "MW", 0, 30), "line_voltage": (220 * source_v, "kV", 180, 260), "reactive_power": (grid * .18, "MVAr", -200, 200), "transformer_oil_temp": (state["transformers"]["grid_a"]["top_oil_c"], "°C", 0, 150), "winding_hotspot_temp": (state["transformers"]["grid_a"]["hotspot_c"], "°C", 0, 180), "cooling_stage": (state["transformers"]["grid_a"]["cooling_stage"], "stage", 0, 2)},
                "Grid substation B": {"active_power": (grid * .48, "MW", 0, 30), "line_voltage": (220 * source_v, "kV", 180, 260), "reactive_power": (grid * .16, "MVAr", -200, 200), "transformer_oil_temp": (state["transformers"]["grid_b"]["top_oil_c"], "°C", 0, 150), "winding_hotspot_temp": (state["transformers"]["grid_b"]["hotspot_c"], "°C", 0, 180), "cooling_stage": (state["transformers"]["grid_b"]["cooling_stage"], "stage", 0, 2)},
                "Intertie": {"active_power": (grid, "MW", 0, 60), "line_voltage": (220 * source_v, "kV", 180, 260), "grid_frequency": (state["frequency_hz"], "Hz", 47, 53), "frequency_rocof": (state["frequency_rocof_hz_s"], "Hz/s", -5, 5), "breaker_state": (float(state["tie_capacity_mw"] > 2), "bool", 0, 1)},
            },
            "Grid distribution": {
                "Distribution incomer": {
                    "active_power": ((state["served_load_mw"] + state["distribution_losses_mw"])
                                     * float(state["distribution_incomer"]["breaker_closed"]), "MW", 0, 60),
                    "line_voltage": (33 * source_v * float(state["distribution_incomer"]["breaker_closed"]), "kV", 0, 40),
                    "line_current": (state["distribution_incomer"]["current_a"], "A", 0, 10000),
                    "underfrequency_stage": (state["ufls_stage"], "stage", 0, len(self.UFLS_STAGES)),
                    "underfrequency_shed_power": (state["ufls_shed_mw"], "MW", 0, 20),
                    "underfrequency_operated_this_scan": (
                        state["ufls_operated_this_scan_mw"], "MW", 0, 20),
                    "underfrequency_pickup_timer": (
                        state["ufls_pickup_timer_s"], "s", 0, self.UFLS_PICKUP_DELAY_S),
                    "underfrequency_restore_timer": (state["ufls_restore_timer_s"], "s", 0,
                                                       self.UFLS_RESTORE_DELAY_S),
                    "breaker_state": (float(state["distribution_incomer"]["breaker_closed"]), "bool", 0, 1),
                    "relay_pickup": (float(state["distribution_incomer"]["relay_pickup"]), "bool", 0, 1),
                    "relay_current_multiple": (state["distribution_incomer"]["relay_current_multiple"], "x pickup", 0, 20),
                    "relay_timer": (state["distribution_incomer"]["relay_timer_s"], "s", 0, 3600),
                    "trip_count": (state["distribution_incomer"]["trip_count"], "count", 0, 1000),
                    "lockout": (float(state["distribution_incomer"]["lockout"]), "bool", 0, 1),
                },
                "Feeder north": {"active_power": (state["distribution_feeders"]["north"]["active_power_mw"], "MW", 0, 30), "line_voltage": (33 * state["distribution_feeders"]["north"]["voltage_pu"], "kV", 0, 40), "line_current": (state["distribution_feeders"]["north"]["current_a"], "A", 0, 10000), "reactive_power": (state["distribution_feeders"]["north"]["reactive_power_mvar"], "MVAr", -10, 20), "breaker_state": (float(state["distribution_feeders"]["north"]["breaker_closed"]), "bool", 0, 1)},
                "Feeder south": {"active_power": (state["distribution_feeders"]["south"]["active_power_mw"], "MW", 0, 30), "line_voltage": (33 * state["distribution_feeders"]["south"]["voltage_pu"], "kV", 0, 40), "line_current": (state["distribution_feeders"]["south"]["current_a"], "A", 0, 10000), "reactive_power": (state["distribution_feeders"]["south"]["reactive_power_mvar"], "MVAr", -10, 20), "breaker_state": (float(state["distribution_feeders"]["south"]["breaker_closed"]), "bool", 0, 1)},
                "Feeder east": {"active_power": (state["distribution_feeders"]["east"]["active_power_mw"], "MW", 0, 30), "line_voltage": (11 * state["distribution_feeders"]["east"]["voltage_pu"], "kV", 0, 14), "line_current": (state["distribution_feeders"]["east"]["current_a"], "A", 0, 10000), "reactive_power": (state["distribution_feeders"]["east"]["reactive_power_mvar"], "MVAr", -10, 20), "breaker_state": (float(state["distribution_feeders"]["east"]["breaker_closed"]), "bool", 0, 1)},
                "Feeder west": {"active_power": (state["distribution_feeders"]["west"]["active_power_mw"], "MW", 0, 30), "line_voltage": (11 * state["distribution_feeders"]["west"]["voltage_pu"], "kV", 0, 14), "line_current": (state["distribution_feeders"]["west"]["current_a"], "A", 0, 10000), "reactive_power": (state["distribution_feeders"]["west"]["reactive_power_mvar"], "MVAr", -10, 20), "breaker_state": (float(state["distribution_feeders"]["west"]["breaker_closed"]), "bool", 0, 1)},
                "Capacitor bank": {"active_power": (0.0, "MW", 0, 1), "reactive_power": (-state["capacitor_bank_mvar"], "MVAr", -10, 10), "line_voltage": (33 * source_v, "kV", 25, 40), "breaker_state": (1.0, "bool", 0, 1), "stage": (state["capacitor_bank_stage"], "stage", 1, 2), "control_timer": (state["capacitor_control_timer_s"], "s", 0, self.CAPACITOR_SWITCH_DELAY_S)},
            },
            "Mine power network": {
                "Mine substation A": {"active_power": (mine * .54, "MW", 0, 40), "line_voltage": (33 * source_v, "kV", 25, 40), "reactive_power": (mine * .2, "MVAr", -200, 200), "transformer_oil_temp": (state["transformers"]["mine_a"]["top_oil_c"], "°C", 0, 150), "winding_hotspot_temp": (state["transformers"]["mine_a"]["hotspot_c"], "°C", 0, 180), "cooling_stage": (state["transformers"]["mine_a"]["cooling_stage"], "stage", 0, 2), "breaker_state": (float(state["mine_transformer_capacity_mw"] > 10), "bool", 0, 1)},
                "Mine substation B": {"active_power": (mine * .46, "MW", 0, 40), "line_voltage": (33 * source_v, "kV", 25, 40), "reactive_power": (mine * .17, "MVAr", -200, 200), "transformer_oil_temp": (state["transformers"]["mine_b"]["top_oil_c"], "°C", 0, 150), "winding_hotspot_temp": (state["transformers"]["mine_b"]["hotspot_c"], "°C", 0, 180), "cooling_stage": (state["transformers"]["mine_b"]["cooling_stage"], "stage", 0, 2), "breaker_state": (float(state["mine_transformer_capacity_mw"] > 10), "bool", 0, 1)},
                "Pit distribution": {"active_power": (3.6 * state["water_power_fraction"], "MW", 0, 12), "line_voltage": (11 * v, "kV", 8, 14), "line_current": (110.0 * state["water_power_fraction"], "A", 0, 3000), "breaker_state": (float(state["water_power_fraction"] > .05), "bool", 0, 1)},
                "Underground feeders": {"active_power": (4.2, "MW", 0, 12), "line_voltage": (11 * v, "kV", 8, 14), "line_current": (125.0, "A", 0, 3000), "breaker_state": (1.0, "bool", 0, 1)},
                "Emergency generation": {"active_power": (0.0, "MW", 0, 8), "grid_frequency": (state["frequency_hz"], "Hz", 47, 53), "breaker_state": (0.0, "bool", 0, 1), "equipment_running": (0.0, "bool", 0, 1)},
                "Factory utility feeder": {
                    "active_power": (state["factory_utilities_load_mw"], "MW", 0, 8),
                    "requested_power": (state["factory_utilities_requested_mw"], "MW", 0, 8),
                    "available_fraction": (100 * state["factory_utilities_power_fraction"], "%", 0, 100),
                    "breaker_state": (float(state["factory_utilities_power_fraction"] > 0.05), "bool", 0, 1),
                },
            },
        }
        rows = []
        for area, assets in values.items():
            for asset, signals in assets.items():
                for signal, (truth, units, low, high) in signals.items():
                    tag_id = f"{area.lower().replace(' ', '_')}.{asset.lower().replace(' ', '_')}.{signal}"
                    noise_scale = .0005 if signal == "available_fraction" else .001
                    discrete = units in ("bool", "enum", "stage", "count")
                    noise = (correlated_sensor_noise(
                        state, tag_id, state["seed"], state["sim_seconds"],
                        max(high - low, 1e-9) * noise_scale)
                        if not discrete else 0.0)
                    rows.append({"tag": tag_id, "area": area, "unit": asset, "sensor": signal,
                        "units": units, "value": round(min(high, max(low, truth + noise)), 3),
                        "quality": "GOOD", "low": low, "high": high, "base": (low + high) / 2,
                        "phase": 0.0, "last_change": "Dynamic mine electrical model"})
        for name, feeder in state["distribution_feeders"].items():
            asset = f"Feeder {name}"
            for signal, value, units, low, high in (
                ("relay_pickup", float(feeder["relay_pickup"]), "bool", 0, 1),
                ("relay_current_multiple", feeder["relay_current_multiple"], "x pickup", 0, 20),
                ("relay_timer", feeder["relay_timer_s"], "s", 0, 3600),
                ("relay_time_to_trip", feeder["relay_time_to_trip_s"], "s", 0, 3600),
                ("trip_count", feeder["trip_count"], "count", 0, 1000),
                ("reclose_count", feeder["reclose_count"], "count", 0, 1000),
                ("relay_operation_count", feeder["relay_operation_count"], "count", 0, 1000),
                ("breaker_failure_count", feeder["breaker_failure_count"], "count", 0, 1000),
                ("fault_current", feeder["fault_current_a"], "A", 0, 10000),
                ("fault_location", feeder["fault_location_pct"], "%", 0, 100),
                ("lockout", float(feeder["lockout"]), "bool", 0, 1),
            ):
                rows.append({"tag": f"grid_distribution.feeder_{name}.{signal}",
                    "area": "Grid distribution", "unit": asset, "sensor": signal,
                    "units": units, "value": round(value, 3), "quality": "GOOD",
                    "low": low, "high": high, "base": value, "phase": 0.0,
                    "last_change": "Feeder overcurrent relay model"})
        rows.extend([
            {"tag": "mine_power_network.concentrator_substation.active_power", "area": "Mine power network",
             "unit": "Concentrator substation", "sensor": "active_power", "units": "MW",
             "value": round(state["process_load_mw"], 3), "quality": "GOOD", "low": 0, "high": 50,
             "base": 25, "phase": 0.0, "last_change": "Served process electrical load"},
            {"tag": "mine_power_network.concentrator_substation.requested_power", "area": "Mine power network",
             "unit": "Concentrator substation", "sensor": "requested_power", "units": "MW",
             "value": round(state.get("process_requested_mw", state["process_load_mw"]), 3),
             "quality": "GOOD", "low": 0, "high": 50, "base": 25, "phase": 0.0,
             "last_change": "Process electrical load and motor-starting demand"},
            {"tag": "mine_power_network.concentrator_substation.unserved_power", "area": "Mine power network",
             "unit": "Concentrator substation", "sensor": "unserved_power", "units": "MW",
             "value": round(max(0.0, state.get("process_requested_mw", state["process_load_mw"])
                                 - state["process_load_mw"]), 3),
             "quality": "GOOD", "low": 0, "high": 50, "base": 0, "phase": 0.0,
             "last_change": "Unserved process load"},
            {"tag": "mine_power_network.water_pump_station.pump_load", "area": "Mine power network",
             "unit": "Water pump station", "sensor": "active_power", "units": "MW",
             "value": round(state["water_load_mw"], 3), "quality": "GOOD", "low": 0, "high": 2,
             "base": .1, "phase": 0.0, "last_change": "Water pump electrical load"},
            {"tag": "mine_power_network.process_motor_starts.starting_load", "area": "Mine power network",
             "unit": "Process motor starts", "sensor": "starting_load", "units": "MW",
             "value": round(state.get("motor_starting_load_mw", 0.0), 3), "quality": "GOOD",
             "low": 0, "high": 12, "base": 0, "phase": 0.0,
             "last_change": "PLC motor acceleration transient"},
        ])
        return rows

    @classmethod
    def _advance_feeder_protection(cls, state: dict, sim_seconds: int, events: list[dict]) -> None:
        """Estimate three-phase fault current and coordinate feeder with incomer relays."""
        feeders = state.setdefault("distribution_feeders", {})
        initial_state = cls().initialize()
        initial = initial_state["distribution_feeders"]
        incomer = state.setdefault("distribution_incomer", initial_state["distribution_incomer"].copy())
        for key, value in initial_state["distribution_incomer"].items():
            incomer.setdefault(key, value)
        transmission_trip = any(event.get("name") == "Utility loss"
            and event.get("area") in ("Grid transmission", "Whole factory")
            and event.get("severity") == "Large" for event in events)
        grid_connected = state.get("tie_capacity_mw", 44.0) > 2.0 and not transmission_trip
        if grid_connected:
            source_fault_mva = 400.0
        else:
            source_fault_mva = max(80.0, 8.0 * (max(0.0, state.get("diesel_mw", 0.0))
                                    + max(0.0, state.get("battery_power_mw", 0.0))))
        incomer_fault_current_a = 0.0
        backup_event = None
        for name in cls.DISTRIBUTION_FEEDERS:
            feeder = feeders.setdefault(name, initial[name].copy())
            for key, value in initial[name].items():
                feeder.setdefault(key, value)
            asset = f"Feeder {name}"
            fault = next((event for event in events if event.get("name") == "Electrical fault"
                          and event.get("area") == "Grid distribution"
                          and event.get("target_asset") == asset), None)
            spec = cls.DISTRIBUTION_FEEDERS[name]
            pickup_a = spec["rating_mva"] * 1000 / (math.sqrt(3) * spec["nominal_kv"]) * 1.15
            fault_current = 0.0
            location_pct = float(fault.get("fault_location_pct", 90.0)) if fault else 0.0
            if fault and incomer["breaker_closed"] and feeder["breaker_closed"]:
                z_base_ohm = spec["nominal_kv"] ** 2 / spec["rating_mva"]
                line_fraction = location_pct / 100.0
                source_z_ohm = spec["nominal_kv"] ** 2 / source_fault_mva
                source_r_ohm = source_z_ohm / math.sqrt(101.0)  # assumed X/R = 10
                source_x_ohm = source_r_ohm * 10.0
                fault_r_ohm = cls.FAULT_RESISTANCE_PU[fault["severity"]] * z_base_ohm
                total_r_ohm = source_r_ohm + spec["r_pu"] * z_base_ohm * line_fraction + fault_r_ohm
                total_x_ohm = source_x_ohm + spec["x_pu"] * z_base_ohm * line_fraction
                fault_current = spec["nominal_kv"] * 1000.0 / (
                    math.sqrt(3.0) * math.hypot(total_r_ohm, total_x_ohm))
            multiple = fault_current / pickup_a if fault_current > 0.0 else 0.0
            feeder["relay_current_multiple"] = multiple
            feeder["fault_current_a"] = fault_current
            feeder["fault_location_pct"] = location_pct
            feeder["relay_pickup"] = multiple > 1.0 and feeder["breaker_closed"] and incomer["breaker_closed"]
            trip_s = cls.PRIMARY_RELAY_TMS * 0.14 / (multiple ** 0.02 - 1.0) if multiple > 1.0 else 0.0
            feeder["relay_time_to_trip_s"] = trip_s
            if (fault and multiple > 1.0 and feeder["breaker_closed"]
                    and incomer["breaker_closed"] and not feeder["lockout"]):
                feeder["relay_timer_s"] += 1
                if feeder["relay_timer_s"] >= max(1, math.ceil(trip_s)):
                    if not feeder["relay_operated"]:
                        feeder["relay_operated"] = True
                        feeder["relay_operation_count"] += 1
                        if fault.get("primary_breaker_failure", False):
                            feeder["breaker_failure_count"] += 1
                        else:
                            feeder["breaker_closed"] = False
                            feeder["trip_count"] += 1
                            feeder["last_trip_reason"] = f"Overcurrent {multiple:.1f}× pickup"
                            feeder["lockout"] = feeder["trip_count"] >= 2
                            feeder["reclose_timer_s"] = 0
                            feeder["relay_timer_s"] = 0
                            feeder["fault_current_a"] = 0.0
                            feeder["relay_current_multiple"] = 0.0
                            feeder["relay_time_to_trip_s"] = 0.0
            elif not fault:
                feeder["relay_timer_s"] = 0.0
                feeder["relay_operated"] = False
            else:
                feeder["relay_timer_s"] = 0.0
                if not feeder["breaker_failure_count"] or not feeder["relay_pickup"]:
                    feeder["relay_operated"] = False
            feeder["relay_pickup"] = multiple > 1.0 and feeder["breaker_closed"] and incomer["breaker_closed"]
            if fault and feeder["breaker_closed"] and incomer["breaker_closed"]:
                incomer_fault_current_a += fault_current * spec["nominal_kv"] / 33.0
                backup_event = fault
            if not feeder["breaker_closed"] and not feeder["lockout"]:
                feeder["reclose_timer_s"] += 1
                if feeder["reclose_timer_s"] >= 15:
                    feeder["breaker_closed"] = True
                    feeder["reclose_count"] += 1
                    feeder["reclose_timer_s"] = 0
                    feeder["last_trip_reason"] = ""
                    feeder["relay_timer_s"] = 0.0
                    feeder["relay_operated"] = False
            if feeder["lockout"]:
                feeder["breaker_closed"] = False
            feeder["pickup_current_a"] = round(pickup_a, 2)

        incomer_pickup_a = 50_000.0 / (math.sqrt(3.0) * 33.0) * 1.10
        incomer_multiple = incomer_fault_current_a / incomer_pickup_a
        incomer["relay_current_a"] = incomer_fault_current_a
        incomer["relay_current_multiple"] = incomer_multiple
        incomer["relay_pickup"] = incomer_multiple > 1.0 and incomer["breaker_closed"]
        backup_curve_s = (cls.BACKUP_RELAY_TMS * 0.14 / (incomer_multiple ** 0.02 - 1.0)
                          if incomer_multiple > 1.0 else 0.0)
        incomer["relay_time_to_trip_s"] = (backup_curve_s + cls.BACKUP_COORDINATION_MARGIN_S
                                           if backup_curve_s else 0.0)
        if backup_event and incomer["relay_pickup"] and not incomer["lockout"]:
            incomer["relay_timer_s"] += 1
            if incomer["relay_timer_s"] >= max(1, math.ceil(incomer["relay_time_to_trip_s"])):
                incomer["breaker_closed"] = False
                incomer["lockout"] = True
                incomer["trip_count"] += 1
                incomer["relay_operation_count"] += 1
                incomer["last_trip_reason"] = (
                    f"Backup trip for {backup_event['target_asset']} breaker failure")
                incomer["relay_current_a"] = 0.0
                incomer["relay_current_multiple"] = 0.0
                incomer["relay_pickup"] = False
                incomer["relay_timer_s"] = 0.0
        elif not backup_event:
            incomer["relay_timer_s"] = 0.0

    @classmethod
    def reset_feeder_protection(cls, state: dict, feeder_name: str, *, fault_active: bool) -> None:
        """Manually reset a cleared feeder lockout; retain relay event counters."""
        if feeder_name == "distribution_incomer":
            incomer = state["distribution_incomer"]
            if fault_active:
                raise ValueError("Clear the feeder fault before resetting protection lockout")
            if not incomer["lockout"]:
                raise ValueError("Distribution incomer is not locked out")
            incomer["lockout"] = False
            incomer["breaker_closed"] = True
            incomer["relay_pickup"] = False
            incomer["current_a"] = 0.0
            incomer["relay_current_a"] = 0.0
            incomer["relay_current_multiple"] = 0.0
            incomer["relay_timer_s"] = 0.0
            incomer["last_trip_reason"] = ""
            return
        if feeder_name not in cls.DISTRIBUTION_FEEDERS:
            raise ValueError(f"Unknown feeder: {feeder_name}")
        feeder = state["distribution_feeders"][feeder_name]
        if fault_active:
            raise ValueError("Clear the feeder fault before resetting protection lockout")
        if not feeder["lockout"]:
            raise ValueError(f"Feeder {feeder_name} is not locked out")
        feeder["lockout"] = False
        feeder["breaker_closed"] = True
        feeder["relay_pickup"] = False
        feeder["relay_current_multiple"] = 0.0
        feeder["relay_timer_s"] = 0.0
        feeder["reclose_timer_s"] = 0.0
        feeder["last_trip_reason"] = ""
