"""Low-compute thermal, ventilation, and HVAC model for the site's buildings."""
from __future__ import annotations

import math
import random


class SiteBuildings:
    """Eight mixed-use thermal zones with slow envelope and faster air response."""

    AREA = "Site buildings"
    ASSETS = {
        "Control room": {"volume_m3": 1_200.0, "thermal_mass_kwh_k": 38.0, "ua_kw_k": 2.0,
                         "hvac_capacity_kw": 90.0, "cop": 3.2, "peak_people": 18,
                         "night_people_fraction": 0.75, "base_ach": 0.45, "solar_gain_kw": 5.0,
                         "equipment_gain_kw": 7.0, "setpoint_c": 22.0, "heating": True,
                         "door_interval_s": 900, "door_exchange_ach": 1.0},
        "Workshop": {"volume_m3": 12_000.0, "thermal_mass_kwh_k": 210.0, "ua_kw_k": 13.0,
                     "hvac_capacity_kw": 360.0, "cop": 2.9, "peak_people": 24,
                     "night_people_fraction": 0.08, "base_ach": 0.35, "solar_gain_kw": 12.0,
                     "equipment_gain_kw": 22.0, "setpoint_c": 20.0, "heating": True,
                     "door_interval_s": 180, "door_exchange_ach": 2.2},
        "Warehouse": {"volume_m3": 24_000.0, "thermal_mass_kwh_k": 360.0, "ua_kw_k": 18.0,
                      "hvac_capacity_kw": 180.0, "cop": 3.0, "peak_people": 8,
                      "night_people_fraction": 0.05, "base_ach": 0.20, "solar_gain_kw": 24.0,
                      "equipment_gain_kw": 8.0, "setpoint_c": 20.0, "heating": True,
                      "door_interval_s": 240, "door_exchange_ach": 2.5},
        "Laboratory": {"volume_m3": 2_800.0, "thermal_mass_kwh_k": 76.0, "ua_kw_k": 3.5,
                       "hvac_capacity_kw": 150.0, "cop": 3.1, "peak_people": 16,
                       "night_people_fraction": 0.12, "base_ach": 0.8, "solar_gain_kw": 9.0,
                       "equipment_gain_kw": 18.0, "setpoint_c": 21.0, "heating": True,
                       "door_interval_s": 720, "door_exchange_ach": 0.8},
        "Substation building": {"volume_m3": 4_500.0, "thermal_mass_kwh_k": 95.0, "ua_kw_k": 5.0,
                                "hvac_capacity_kw": 150.0, "cop": 3.4, "peak_people": 2,
                                "night_people_fraction": 0.08, "base_ach": 0.15, "solar_gain_kw": 5.0,
                                "equipment_gain_kw": 35.0, "setpoint_c": 25.0, "heating": False,
                                "door_interval_s": 900, "door_exchange_ach": 1.2},
        "Pump house": {"volume_m3": 6_000.0, "thermal_mass_kwh_k": 120.0, "ua_kw_k": 6.0,
                       "hvac_capacity_kw": 105.0, "cop": 3.0, "peak_people": 3,
                       "night_people_fraction": 0.08, "base_ach": 0.25, "solar_gain_kw": 7.0,
                       "equipment_gain_kw": 48.0, "setpoint_c": 25.0, "heating": False,
                       "door_interval_s": 600, "door_exchange_ach": 1.4},
        "Office block": {"volume_m3": 18_000.0, "thermal_mass_kwh_k": 300.0, "ua_kw_k": 15.0,
                         "hvac_capacity_kw": 480.0, "cop": 3.1, "peak_people": 115,
                         "night_people_fraction": 0.06, "base_ach": 0.7, "solar_gain_kw": 48.0,
                         "equipment_gain_kw": 20.0, "setpoint_c": 22.0, "heating": True,
                         "door_interval_s": 450, "door_exchange_ach": 0.8},
        "Gatehouse": {"volume_m3": 420.0, "thermal_mass_kwh_k": 14.0, "ua_kw_k": 1.1,
                      "hvac_capacity_kw": 24.0, "cop": 2.8, "peak_people": 4,
                      "night_people_fraction": 0.65, "base_ach": 0.5, "solar_gain_kw": 2.0,
                      "equipment_gain_kw": 1.5, "setpoint_c": 22.0, "heating": True,
                      "door_interval_s": 240, "door_exchange_ach": 1.3},
    }
    MECHANICAL_CAPACITY = {"Small": 0.82, "Medium": 0.62, "Large": 0.35}
    UTILITY_FACTOR = {"Small": 0.75, "Medium": 0.42, "Large": 0.0}

    @staticmethod
    def _ambient_temperature(sim_seconds: int) -> float:
        hour = (sim_seconds % 86_400) / 3_600.0
        return 19.0 + 12.0 * math.sin(2.0 * math.pi * (hour - 10.0) / 24.0)

    @staticmethod
    def _day_occupancy(hour: float) -> float:
        # Day crew enters around 06:00 and leaves around 18:00 with smooth shifts.
        return max(0.0, min(1.0, (hour - 5.5) / 1.0, (19.0 - hour) / 1.0))

    def initialize(self) -> dict:
        zones = {}
        for name, spec in self.ASSETS.items():
            zones[name] = {
                **spec, "temperature_c": 22.0, "humidity_pct": 38.0, "co2_ppm": 520.0,
                "filter_loading_pct": 0.0, "filter_dp_pa": 95.0, "hvac_mode": "OFF",
                "hvac_state": "OFF", "mode_elapsed_s": 180, "restart_delay_s": 0,
                "fan_running": False, "door_open_s": 0, "door_state": False,
                "occupancy_count": 0.0, "setpoint_effective_c": spec["setpoint_c"],
                "sensible_output_kw": 0.0, "fan_airflow_fraction": 1.0,
                "ventilation_rate_ach": spec["base_ach"],
                "condition_factor": 1.0, "power_fraction": 1.0,
                "compressor_starts": 0, "electric_power_kw": 0.0,
                "requested_power_kw": 0.0, "supply_temperature_c": self._ambient_temperature(0),
                "ambient_noise_dba": 38.0,
            }
        return {"sim_seconds": 0, "ambient_temperature_c": self._ambient_temperature(0),
                "ambient_humidity_pct": 36.0, "power_available_fraction": 1.0,
                "electric_load_mw": 0.0, "requested_load_mw": 0.0,
                "zones": zones, "history": []}

    def migrate(self, state: dict) -> None:
        initial = self.initialize()
        for key, value in initial.items():
            if key == "zones":
                state.setdefault(key, {})
                for name, zone in value.items():
                    state[key].setdefault(name, zone)
            elif key == "history":
                state.setdefault(key, [])
            else:
                state.setdefault(key, value)
        for name, zone in state["zones"].items():
            defaults = initial["zones"].get(name)
            if defaults:
                for key, value in defaults.items():
                    zone.setdefault(key, value)

    @staticmethod
    def _active_severity(events: list[dict], incident: str, area: str) -> str | None:
        severity_rank = {"Small": 1, "Medium": 2, "Large": 3}
        active = [event["severity"] for event in events if event.get("name") == incident
                  and event.get("area") in (area, "Whole factory")]
        return max(active, key=lambda value: severity_rank[value]) if active else None

    @staticmethod
    def _occupancy(spec: dict, sim_seconds: int) -> float:
        hour = (sim_seconds % 86_400) / 3_600.0
        day = SiteBuildings._day_occupancy(hour)
        shift_fraction = spec["night_people_fraction"] + (1.0 - spec["night_people_fraction"]) * day
        # A smooth lunch/shift lull keeps occupancy non-constant without step changes.
        lull = 1.0 - 0.08 * max(0.0, math.sin(math.pi * (hour - 11.0) / 2.0))
        return max(0.0, spec["peak_people"] * shift_fraction * lull)

    @staticmethod
    def _advance_door(zone: dict, name: str, sim_seconds: int, seed: int) -> None:
        if zone["door_open_s"] > 0:
            zone["door_open_s"] -= 1
        else:
            rng = random.Random(seed * 1_000_003 + sim_seconds * 9_176
                                + sum((i + 1) * ord(ch) for i, ch in enumerate(name)))
            if rng.random() < 1.0 / zone["door_interval_s"]:
                zone["door_open_s"] = rng.randint(8, 35)
        zone["door_state"] = zone["door_open_s"] > 0

    def advance(self, state: dict, sim_seconds: int, events: list[dict], *,
                seed: int = 17, power_available_fraction: float = 1.0) -> None:
        """Advance each room by one second, preserving thermal inertia and airflow."""
        self.migrate(state)
        ambient = self._ambient_temperature(sim_seconds)
        hour = (sim_seconds % 86_400) / 3_600.0
        outdoor_rh = max(15.0, min(55.0, 35.0 - 0.65 * (ambient - 19.0)
                                  + 5.0 * math.sin(sim_seconds / 10_800.0)))
        solar = max(0.0, math.sin(math.pi * (hour - 6.0) / 12.0))
        local_utility_fault = self._active_severity(events, "Utility loss", self.AREA)
        local_power = self.UTILITY_FACTOR.get(local_utility_fault, 1.0)
        grid_power = max(0.0, min(1.0, power_available_fraction))
        mechanical = self._active_severity(events, "Mechanical degradation", self.AREA)
        mechanical_factor = self.MECHANICAL_CAPACITY.get(mechanical, 1.0)
        state["sim_seconds"] = sim_seconds
        state["ambient_temperature_c"] = ambient
        state["ambient_humidity_pct"] = outdoor_rh
        state["power_available_fraction"] = grid_power * local_power
        total_request_kw = total_actual_kw = 0.0

        for name, zone in state["zones"].items():
            spec = self.ASSETS[name]
            self._advance_door(zone, name, sim_seconds, seed)
            occupancy = self._occupancy(spec, sim_seconds)
            zone["occupancy_count"] = occupancy
            unoccupied = occupancy < max(1.0, spec["peak_people"] * 0.12)
            zone["setpoint_effective_c"] = spec["setpoint_c"] + (2.0 if unoccupied else 0.0)
            zone["condition_factor"] = mechanical_factor
            zone["power_fraction"] = state["power_available_fraction"]

            # Filter fouling is slow; a high differential pressure gradually
            # restricts ventilation and lowers the effective fan airflow.
            dust_factor = 1.8 if name in ("Workshop", "Warehouse", "Pump house") else 1.0
            zone["filter_loading_pct"] = min(100.0, zone["filter_loading_pct"] + dust_factor / 86_400.0)
            zone["filter_dp_pa"] = (95.0 + 560.0 * (zone["filter_loading_pct"] / 100.0) ** 1.35
                                    + (1.0 - mechanical_factor) * 350.0)
            zone["fan_airflow_fraction"] = max(0.25, 1.0 - zone["filter_dp_pa"] / 1_800.0)
            ventilation_request_ach = spec["base_ach"] + max(
                0.0, min(1.0, (zone["co2_ppm"] - 800.0) / 900.0)) * 0.9
            ventilation_request_ach *= zone["fan_airflow_fraction"]
            if zone["door_state"]:
                ventilation_request_ach += spec["door_exchange_ach"]
            ventilation_ach = ventilation_request_ach * state["power_available_fraction"]
            zone["ventilation_rate_ach"] = ventilation_ach

            temperature = zone["temperature_c"]
            setpoint = zone["setpoint_effective_c"]
            mode = zone["hvac_mode"]
            if mode == "OFF":
                if temperature >= setpoint + 0.8:
                    desired_mode = "COOL"
                elif spec["heating"] and temperature <= setpoint - 1.2:
                    desired_mode = "HEAT"
                else:
                    desired_mode = "OFF"
            elif mode == "COOL":
                desired_mode = "OFF" if temperature <= setpoint - 0.5 else "COOL"
            else:
                desired_mode = "OFF" if temperature >= setpoint + 0.4 else "HEAT"

            if state["power_available_fraction"] < 0.10:
                if mode != "OFF":
                    zone["restart_delay_s"] = 60
                zone["hvac_mode"] = "OFF"
                zone["hvac_state"] = "POWER_LOSS"
                zone["mode_elapsed_s"] = 0
                desired_mode = "OFF"
            elif zone["restart_delay_s"] > 0:
                zone["restart_delay_s"] -= 1
                zone["hvac_mode"] = "OFF"
                zone["hvac_state"] = "RESTART_DELAY"
                desired_mode = "OFF"
                zone["mode_elapsed_s"] = 0
            else:
                zone["mode_elapsed_s"] += 1
                if mode == "OFF":
                    if desired_mode != "OFF" and zone["mode_elapsed_s"] >= 120:
                        zone["hvac_mode"] = desired_mode
                        zone["hvac_state"] = "RUNNING"
                        zone["mode_elapsed_s"] = 0
                        zone["compressor_starts"] += 1
                elif desired_mode == "OFF" and zone["mode_elapsed_s"] >= 180:
                    zone["hvac_mode"] = "OFF"
                    zone["hvac_state"] = "OFF"
                    zone["mode_elapsed_s"] = 0
                else:
                    zone["hvac_state"] = "RUNNING"

            mode = zone["hvac_mode"]
            rated_kw = spec["hvac_capacity_kw"] * mechanical_factor * state["power_available_fraction"]
            if mode == "COOL":
                modulation = min(1.0, max(0.0, (temperature - (setpoint - 0.5)) / 2.0))
                sensible_output = rated_kw * modulation
            elif mode == "HEAT":
                modulation = min(1.0, max(0.0, ((setpoint + 0.3) - temperature) / 2.0))
                sensible_output = -rated_kw * modulation
            else:
                sensible_output = 0.0
            zone["sensible_output_kw"] = sensible_output
            zone["fan_running"] = mode != "OFF" and state["power_available_fraction"] >= 0.10
            zone["supply_temperature_c"] = (12.0 if mode == "COOL" else 36.0 if mode == "HEAT"
                                            else ambient)

            door_ua = (spec["volume_m3"] * 1.2 * 1005.0 / 3600.0 / 1000.0
                       * spec["door_exchange_ach"] if zone["door_state"] else 0.0)
            envelope_heat_kw = (spec["ua_kw_k"] + door_ua) * (ambient - temperature)
            occupancy_gain_kw = occupancy * 0.095
            internal_gain_kw = spec["equipment_gain_kw"] * (0.25 + 0.75 * self._day_occupancy(hour))
            solar_gain_kw = spec["solar_gain_kw"] * solar
            net_heat_kw = (envelope_heat_kw + occupancy_gain_kw + internal_gain_kw
                           + solar_gain_kw - sensible_output)
            zone["temperature_c"] += net_heat_kw / (spec["thermal_mass_kwh_k"] * 3_600.0)
            zone["temperature_c"] = min(55.0, max(-10.0, zone["temperature_c"]))

            humidity = zone["humidity_pct"]
            humidity_gain = occupancy * 0.000025
            dehumidification = max(0.0, sensible_output) * 0.000012 if mode == "COOL" else 0.0
            humidity += ((outdoor_rh - humidity) * max(0.1, ventilation_ach) / 3_600.0
                         + humidity_gain - dehumidification)
            zone["humidity_pct"] = min(100.0, max(5.0, humidity))

            co2 = zone["co2_ppm"]
            co2_generation_ppm_s = occupancy * 5.0 / spec["volume_m3"]
            co2 += co2_generation_ppm_s - max(0.12, ventilation_ach) * (co2 - 420.0) / 3_600.0
            zone["co2_ppm"] = min(5_000.0, max(350.0, co2))

            fan_kw = (2.0 + ventilation_request_ach * 1.6) if zone["fan_running"] else 0.0
            compressor_kw = abs(sensible_output) / spec["cop"]
            standby_kw = 0.08 if state["power_available_fraction"] > 0 else 0.0
            zone["requested_power_kw"] = abs(sensible_output) / spec["cop"] + fan_kw + standby_kw
            zone["electric_power_kw"] = zone["requested_power_kw"] * state["power_available_fraction"]
            zone["ambient_noise_dba"] = 38.0 + (5.0 if zone["fan_running"] else 0.0) + min(12.0, occupancy * 0.08)
            total_request_kw += zone["requested_power_kw"]
            total_actual_kw += zone["electric_power_kw"]

        state["requested_load_mw"] = total_request_kw / 1_000.0
        state["electric_load_mw"] = total_actual_kw / 1_000.0
        state["history"].append({"time": sim_seconds, "ambient_temperature_c": ambient,
                                 "mean_indoor_temperature_c": sum(z["temperature_c"] for z in state["zones"].values()) / len(state["zones"]),
                                 "mean_co2_ppm": sum(z["co2_ppm"] for z in state["zones"].values()) / len(state["zones"]),
                                 "electric_load_mw": state["electric_load_mw"]})
        state["history"] = state["history"][-1_800:]

    def telemetry(self, state: dict, seed: int = 17) -> list[dict]:
        rows = []

        def add(asset: str, signal: str, value: float, units: str, low: float, high: float,
                noise_fraction: float = 0.001) -> None:
            tag_name = f"site_buildings.{asset.lower().replace(' ', '_')}.{signal}"
            stable_id = sum((i + 1) * ord(char) for i, char in enumerate(tag_name))
            rng = random.Random(seed * 1_000_003 + state["sim_seconds"] * 9_176 + stable_id)
            if units not in {"bool", "enum"} and noise_fraction:
                value += rng.gauss(0.0, max(high - low, 1e-9) * noise_fraction)
            rows.append({"tag": tag_name, "area": self.AREA, "unit": asset, "sensor": signal,
                         "units": units, "value": round(min(high, max(low, value)), 3),
                         "quality": "GOOD", "low": low, "high": high,
                         "base": (low + high) / 2.0, "phase": 0.0,
                         "last_change": "Building thermal and HVAC model", "dynamic_model": True})

        for name, zone in state["zones"].items():
            add(name, "indoor_temperature", zone["temperature_c"], "°C", -10, 55, 0.0015)
            add(name, "indoor_humidity", zone["humidity_pct"], "%RH", 0, 100, 0.005)
            add(name, "hvac_supply_temp", zone["supply_temperature_c"], "°C", -10, 50, 0.002)
            add(name, "hvac_filter_dp", zone["filter_dp_pa"], "Pa", 0, 1_200, 0.004)
            add(name, "occupancy_count", zone["occupancy_count"], "people", 0, 250, 0.004)
            add(name, "door_state", float(zone["door_state"]), "bool", 0, 1, 0.0)
            add(name, "utility_power", zone["electric_power_kw"], "kW", 0, 800, 0.003)
            add(name, "ambient_noise", zone["ambient_noise_dba"], "dBA", 0, 140, 0.002)
            add(name, "co2_ppm", zone["co2_ppm"], "ppm", 250, 5_000, 0.002)
            add(name, "temperature_setpoint", zone["setpoint_effective_c"], "°C", 15, 32, 0.0)
            add(name, "hvac_mode", {"OFF": 0.0, "COOL": 1.0, "HEAT": 2.0}.get(zone["hvac_mode"], 3.0),
                "enum", 0, 3, 0.0)
            add(name, "hvac_condition", zone["condition_factor"] * 100.0, "%", 0, 100, 0.001)
            add(name, "ventilation_rate", zone["ventilation_rate_ach"], "ACH", 0, 8, 0.003)
        return rows

    @staticmethod
    def public_state(state: dict) -> dict:
        return {key: value for key, value in state.items() if key != "history"}
