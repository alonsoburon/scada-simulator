"""Low-compute chilled-water plant and thermal storage dynamics."""
from __future__ import annotations

import math

try:
    from .instrumentation import correlated_sensor_noise
except ImportError:
    from instrumentation import correlated_sensor_noise


class ChilledWaterPlant:
    AREA = "Chilled water"
    CHILLER_CAPACITY_KW = {"chiller_a": 700.0, "chiller_b": 700.0}
    STORAGE_CAPACITY_MWH = 1.5
    STORAGE_TARGET_MWH = 0.95
    STORAGE_CHARGE_LIMIT_KW = 120.0
    CHILLED_WATER_DESIGN_DELTA_T_K = 6.0
    WATER_HEAT_CAPACITY_KWH_PER_M3_K = 1.163
    SITE_ATMOSPHERIC_PRESSURE_KPA = 70.0
    COOLING_TOWER_APPROACH_K = 5.0
    CHILLER_CONDENSER_APPROACH_K = 5.0
    PRODUCT_TARGET_C = 12.0
    PRODUCT_INLET_C = 82.0
    PRODUCT_CP_KJ_KG_K = 4.186
    PRODUCT_DENSITY_KG_PER_UNIT = 1.0
    SEVERITY = {"Small": 0.20, "Medium": 0.48, "Large": 1.0}

    def initialize(self) -> dict:
        ambient = 25.0
        humidity = 26.0
        wet_bulb = self._wet_bulb_temperature_c(ambient, humidity)
        condenser_entering = wet_bulb + self.COOLING_TOWER_APPROACH_K
        condenser_saturation = condenser_entering + self.CHILLER_CONDENSER_APPROACH_K
        evaporating = 6.0 - 3.0
        initial_cop = max(2.5, min(7.0,
            0.52 * (evaporating + 273.15) / max(10.0, condenser_saturation - evaporating)))
        return {
            "sim_seconds": 0,
            "ambient_temperature_c": ambient,
            "ambient_relative_humidity_pct": humidity,
            "wet_bulb_temperature_c": wet_bulb,
            "condenser_saturation_temperature_c": condenser_saturation,
            "chillers": {name: {"capacity_kw": capacity, "output_kw": capacity * 0.68,
                                "available_fraction": 1.0, "available_capacity_kw": capacity,
                                "running": True, "vibration_mm_s": 1.8}
                         for name, capacity in self.CHILLER_CAPACITY_KW.items()},
            "cooling_demand_kw": 878.0,
            "cooling_production_kw": 878.0,
            "cooling_served_kw": 878.0,
            "cooling_service_fraction": 1.0,
            "storage_mwh": self.STORAGE_TARGET_MWH,
            "storage_capacity_mwh": self.STORAGE_CAPACITY_MWH,
            "storage_charge_kw": 0.0,
            "storage_discharge_kw": 0.0,
            "storage_discharge_capacity_kw": 0.0,
            "supply_temperature_c": 6.0,
            "return_temperature_c": 11.0,
            "condenser_entering_temperature_c": condenser_entering,
            "cop": initial_cop,
            "pump_flow_m3h": 180.0,
            "pump_available_fraction": 1.0,
            "electric_load_mw": 0.24,
            "compressor_starts": 0,
            "history": [],
        }

    def migrate(self, state: dict) -> None:
        defaults = self.initialize()
        for key, value in defaults.items():
            if key == "chillers":
                state.setdefault(key, {})
                for name, unit in value.items():
                    state[key].setdefault(name, unit.copy())
                    for field, default in unit.items():
                        state[key][name].setdefault(field, default)
            elif key == "history":
                state.setdefault(key, [])
            else:
                state.setdefault(key, value)

    @classmethod
    def _severity(cls, events: list[dict], incident: str, asset: str | None = None) -> float:
        values = []
        for event in events:
            if event.get("name") != incident:
                continue
            if event.get("area") not in (cls.AREA, "Whole factory"):
                continue
            target = event.get("target_asset")
            if asset is None and target is not None:
                continue
            if asset is not None and (target is None
                    or target.lower().replace(" ", "_") != asset):
                continue
            values.append(cls.SEVERITY.get(event.get("severity", "Small"), 0.0))
        return max(values, default=0.0)

    @staticmethod
    def product_cooling_demand_kw(product_flow_units_min: float,
                                  inlet_temperature_c: float = 82.0,
                                  target_temperature_c: float = 12.0) -> float:
        """Sensible heat duty from the pasteurizer outlet to the packaging target."""
        flow = max(0.0, product_flow_units_min)
        delta_t = max(0.0, inlet_temperature_c - target_temperature_c)
        return flow * ChilledWaterPlant.PRODUCT_DENSITY_KG_PER_UNIT \
            * ChilledWaterPlant.PRODUCT_CP_KJ_KG_K * delta_t / 60.0

    @classmethod
    def _wet_bulb_temperature_c(cls, dry_bulb_c: float, relative_humidity_pct: float) -> float:
        """Solve a ventilated-psychrometer balance at the site's assumed air pressure."""
        humidity = max(1.0, min(100.0, relative_humidity_pct)) / 100.0

        def saturation_vapor_pressure_kpa(temp_c: float) -> float:
            return 0.61121 * math.exp((18.678 - temp_c / 234.5)
                                      * (temp_c / (257.14 + temp_c)))

        vapor_pressure = humidity * saturation_vapor_pressure_kpa(dry_bulb_c)
        low, high = dry_bulb_c - 60.0, dry_bulb_c
        for _ in range(32):
            wet_bulb = (low + high) * 0.5
            psychrometric_factor = (0.00066 * (1.0 + 0.00115 * wet_bulb)
                                    * cls.SITE_ATMOSPHERIC_PRESSURE_KPA)
            estimated_vapor_pressure = (saturation_vapor_pressure_kpa(wet_bulb)
                                        - psychrometric_factor * (dry_bulb_c - wet_bulb))
            if estimated_vapor_pressure < vapor_pressure:
                low = wet_bulb
            else:
                high = wet_bulb
        return max(dry_bulb_c - 60.0, min(dry_bulb_c, (low + high) * 0.5))

    def advance(self, state: dict, sim_seconds: int, events: list[dict], *,
                cooling_demand_kw: float, power_available_fraction: float = 1.0,
                seed: int = 17) -> None:
        """Advance one second of chiller, pump, cooling-load, and buffer behavior."""
        previous_output = {name: unit["output_kw"] for name, unit in state["chillers"].items()}
        state["sim_seconds"] = sim_seconds
        day_phase = 2.0 * math.pi * (sim_seconds % 86_400) / 86_400.0
        ambient = 25.0 + 5.0 * math.sin(day_phase)
        ambient += correlated_sensor_noise(state, "chilled_water.weather.dry_bulb",
                                           seed, sim_seconds, 0.15, tau_seconds=300.0)
        state["ambient_temperature_c"] = ambient
        humidity = 24.0 - 0.9 * (ambient - 25.0) + 2.0 * math.sin(day_phase + math.pi / 2.0)
        humidity += correlated_sensor_noise(state, "chilled_water.weather.relative_humidity",
                                            seed, sim_seconds, 1.5, tau_seconds=600.0)
        humidity = min(95.0, max(5.0, humidity))
        wet_bulb = self._wet_bulb_temperature_c(ambient, humidity)
        state["ambient_relative_humidity_pct"] = humidity
        state["wet_bulb_temperature_c"] = wet_bulb
        power = max(0.0, min(1.0, power_available_fraction))
        global_mechanical = self._severity(events, "Mechanical degradation")
        pump_fault = global_mechanical
        local_power_loss = self._severity(events, "Utility loss")
        pump_trip = self._severity(events, "Utility loss", "primary_pumps")
        tower_loss = self._severity(events, "Utility loss", "cooling_tower")
        tower_degradation = max(global_mechanical,
            self._severity(events, "Mechanical degradation", "cooling_tower"))
        tower_factor = max(0.0, 1.0 - tower_loss - 0.55 * tower_degradation)
        pump_fraction = max(0.0, 1.0 - pump_fault * 0.70)
        pump_fraction *= (1.0 - local_power_loss) * (1.0 - pump_trip) * power
        state["pump_available_fraction"] = pump_fraction
        state["pump_flow_m3h"] = 180.0 * pump_fraction
        condenser_temp = (wet_bulb + self.COOLING_TOWER_APPROACH_K
                          + 2.0 * (1.0 - pump_fraction))
        condenser_temp += 12.0 * (1.0 - tower_factor)
        state["condenser_entering_temperature_c"] = condenser_temp
        condensing_temp = condenser_temp + self.CHILLER_CONDENSER_APPROACH_K
        state["condenser_saturation_temperature_c"] = condensing_temp
        evaporating_temp = state["supply_temperature_c"] - 3.0
        lift = max(10.0, condensing_temp - evaporating_temp)
        state["cop"] = max(2.5, min(7.0, 0.52 * (evaporating_temp + 273.15) / lift))
        hydraulic_capacity_kw = (state["pump_flow_m3h"]
                                 * self.WATER_HEAT_CAPACITY_KWH_PER_M3_K
                                 * self.CHILLED_WATER_DESIGN_DELTA_T_K)
        hydraulic_derate = min(1.0, hydraulic_capacity_kw
                               / max(sum(self.CHILLER_CAPACITY_KW.values()), 1e-9))
        capacity_by_unit = {}
        available_capacity = 0.0
        for name, unit in state["chillers"].items():
            unit_loss = self._severity(events, "Utility loss", name)
            unit_degradation = max(global_mechanical,
                self._severity(events, "Mechanical degradation", name))
            availability = power * (1.0 - local_power_loss) * (1.0 - unit_loss)
            availability *= max(0.0, 1.0 - 0.65 * unit_degradation)
            ambient_derate = max(0.65, 1.0 - 0.012 * max(0.0, condensing_temp - 25.0)) * tower_factor
            unit["available_fraction"] = availability
            capacity = unit["capacity_kw"] * availability * ambient_derate * hydraulic_derate
            unit["available_capacity_kw"] = capacity
            capacity_by_unit[name] = capacity
            available_capacity += capacity
            unit_degradation = self._severity(events, "Mechanical degradation", name)
            unit["vibration_mm_s"] = 1.8 + 6.0 * unit_degradation + max(0.0, 0.3 - availability) * 2.0

        demand = max(0.0, cooling_demand_kw)
        state["cooling_demand_kw"] = demand
        charge_target = min(self.STORAGE_CHARGE_LIMIT_KW,
                            max(0.0, self.STORAGE_TARGET_MWH - state["storage_mwh"]) * 3600.0)
        production_target = min(available_capacity, demand + charge_target)
        remaining_target = production_target
        for name, unit in state["chillers"].items():
            target = min(capacity_by_unit[name], remaining_target)
            prior = unit["output_kw"]
            ramp = 18.0 if target > prior else 35.0
            if unit["available_fraction"] <= 0.01 or capacity_by_unit[name] <= 0.01:
                # No compressor output without power, chilled-water flow, or
                # heat rejection capacity.
                unit["output_kw"] = 0.0
            else:
                unit["output_kw"] = min(capacity_by_unit[name], max(0.0,
                    prior + max(-ramp, min(ramp, target - prior))))
            unit["running"] = unit["output_kw"] > 5.0
            remaining_target = max(0.0, remaining_target - target)

        production = sum(unit["output_kw"] for unit in state["chillers"].values())
        dt_s = max(0, sim_seconds - state.get("last_advance_seconds", sim_seconds - 1))
        deficit_kw = max(0.0, demand - production)
        available_store_kw = (state["storage_mwh"] * 3_600_000.0 / dt_s if dt_s > 0 else 0.0)
        storage_discharge_capacity_kw = max(0.0, hydraulic_capacity_kw - production)
        state["storage_discharge_capacity_kw"] = storage_discharge_capacity_kw
        discharge = min(deficit_kw, available_store_kw, storage_discharge_capacity_kw)
        served = min(demand, production + discharge)
        net_storage_kw = production - served
        state["storage_mwh"] = max(0.0, min(self.STORAGE_CAPACITY_MWH,
            state["storage_mwh"] + net_storage_kw * dt_s / 3_600_000.0))
        state["cooling_production_kw"] = production
        state["cooling_served_kw"] = served
        state["cooling_service_fraction"] = served / demand if demand > 1e-9 else 1.0
        state["storage_charge_kw"] = max(0.0, net_storage_kw)
        state["storage_discharge_kw"] = max(0.0, -net_storage_kw)
        # A low storage state means less cold reserve and a warmer supply header.
        storage_fraction = state["storage_mwh"] / self.STORAGE_CAPACITY_MWH
        state["supply_temperature_c"] = (6.0 + 2.0 * (1.0 - storage_fraction)
                                          + 5.0 * (1.0 - state["cooling_service_fraction"]))
        return_rise = min(8.0, demand * state["cooling_service_fraction"]
                          / max(100.0, state["pump_flow_m3h"] * 1.163))
        state["return_temperature_c"] = state["supply_temperature_c"] + return_rise
        compressor_power_mw = production / max(state["cop"], 1.0) / 1_000.0
        pump_power_mw = 0.085 * pump_fraction**3
        tower_power_mw = 0.035 * min(1.0, available_capacity / 1_400.0)
        state["electric_load_mw"] = compressor_power_mw + pump_power_mw + tower_power_mw
        state["compressor_starts"] += sum(
            unit["output_kw"] > 5.0 and previous_output[name] <= 5.0
            for name, unit in state["chillers"].items())
        state["last_advance_seconds"] = sim_seconds
        if sim_seconds > 0 and sim_seconds % 15 == 0:
            state["history"].append({key: state[key] for key in (
                "sim_seconds", "ambient_temperature_c", "ambient_relative_humidity_pct",
                "wet_bulb_temperature_c", "condenser_saturation_temperature_c",
                "cooling_demand_kw",
                "cooling_production_kw", "cooling_served_kw", "cooling_service_fraction",
                "storage_mwh", "storage_discharge_capacity_kw",
                "supply_temperature_c", "return_temperature_c", "cop",
                "pump_flow_m3h", "electric_load_mw")})
            state["history"] = state["history"][-1_440:]

    def telemetry(self, state: dict, seed: int = 17) -> list[dict]:
        values: dict[tuple[str, str], dict[str, tuple[float, str, float, float]]] = {
            ("Chilled water", "Chilled water header"): {
                "supply_temperature": (state["supply_temperature_c"], "°C", 0, 25),
                "return_temperature": (state["return_temperature_c"], "°C", 0, 30),
                "cooling_demand": (state["cooling_demand_kw"], "kW", 0, 2_000),
                "cooling_available": (state["cooling_served_kw"], "kW", 0, 2_000),
                "service_fraction": (100.0 * state["cooling_service_fraction"], "%", 0, 100),
                "supply_flow": (state["pump_flow_m3h"], "m³/h", 0, 300),
            },
            ("Chilled water", "Thermal storage"): {
                "state_of_charge": (100.0 * state["storage_mwh"] / self.STORAGE_CAPACITY_MWH, "%", 0, 100),
                "stored_cooling": (state["storage_mwh"], "MWh", 0, self.STORAGE_CAPACITY_MWH),
                "charge_rate": (state["storage_charge_kw"], "kW", 0, 200),
                "discharge_rate": (state["storage_discharge_kw"], "kW", 0, 2_000),
                "available_discharge_capacity": (
                    state["storage_discharge_capacity_kw"], "kW", 0, 2_000),
            },
            ("Chilled water", "Cooling tower"): {
                "ambient_temperature": (state["ambient_temperature_c"], "°C", -20, 60),
                "ambient_relative_humidity": (
                    state["ambient_relative_humidity_pct"], "%", 0, 100),
                "wet_bulb_temperature": (state["wet_bulb_temperature_c"], "°C", -20, 60),
                "condenser_entering_temperature": (state["condenser_entering_temperature_c"], "°C", -10, 80),
                "condenser_saturation_temperature": (
                    state["condenser_saturation_temperature_c"], "°C", -10, 100),
                "plant_cop": (state["cop"], "ratio", 0, 10),
                "electrical_load": (state["electric_load_mw"] * 1_000.0, "kW", 0, 1_000),
            },
        }
        for name, unit in state["chillers"].items():
            asset = "Chiller A" if name == "chiller_a" else "Chiller B"
            values[("Chilled water", asset)] = {
                "cooling_output": (unit["output_kw"], "kW", 0, 1_000),
                "available_capacity": (unit["available_capacity_kw"], "kW", 0, 1_000),
                "motor_current": (unit["output_kw"] / max(state["cop"], 1.0) * 1.45, "A", 0, 250),
                "vibration_rms": (unit["vibration_mm_s"], "mm/s", 0, 25),
                "equipment_running": (float(unit["running"]), "bool", 0, 1),
            }
        rows = []
        for (area, asset), signals in values.items():
            for sensor, (truth, units, low, high) in signals.items():
                tag_name = f"{area.lower().replace(' ', '_')}.{asset.lower().replace(' ', '_')}.{sensor}"
                noise = (0.0 if units == "bool" else correlated_sensor_noise(
                    state, tag_name, seed, state["sim_seconds"], (high - low) * 0.001))
                rows.append({"tag": tag_name, "area": area, "unit": asset, "sensor": sensor,
                    "units": units, "value": round(min(high, max(low, truth + noise)), 3),
                    "quality": "GOOD", "low": low, "high": high, "base": (low + high) / 2,
                    "phase": 0.0, "last_change": "Chilled-water plant model", "dynamic_model": True})
        return rows

    @staticmethod
    def public_state(state: dict) -> dict:
        return {key: value for key, value in state.items()
                if key not in {"history", "_instrument_noise"}} | {
            "history": state["history"][-600:]}
