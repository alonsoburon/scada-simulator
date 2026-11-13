"""Low-compute steam and instrument-air dynamics for the synthetic factory."""
from __future__ import annotations

import random

try:  # Support both package imports in tests and direct app imports.
    from .cip_system import CleaningInPlaceSystem
except ImportError:
    from cip_system import CleaningInPlaceSystem


class FactoryUtilities:
    AREA_ASSETS = {
        "Boiler house": ["Boiler 01", "Boiler 02", "Feedwater system", "Steam header", "Condensate return"],
        "Compressed air": ["Compressor A", "Compressor B", "Dryer A", "Receiver 01", "Ring main"],
        "Thermal processing": ["Pasteurizer 01", "Pasteurizer 02", "Product balance tank"],
        "Filling": ["Filler 01"],
        "Packaging": ["Case packer A", "Accumulation conveyor"],
        "CIP": ["CIP tank A", "CIP tank B", "CIP supply", "CIP return", "Chemical dosing"],
    }
    SEVERITY = {"Small": 0.18, "Medium": 0.42, "Large": 0.78}
    HEADER_CAPACITY_T = 1.5
    RECEIVER_VOLUME_M3 = 80.0  # Receiver plus effective ring-main volume.
    CONDENSATE_CAPACITY_T = 30.0
    PRODUCT_TANK_CAPACITY_UNITS = 450.0
    PACKAGE_BUFFER_CAPACITY_UNITS = 180.0
    UNITS_PER_CASE = 3.0
    PASTEURIZATION_LIMIT_C = 72.0
    CIP_MODEL = CleaningInPlaceSystem()

    def initialize(self) -> dict:
        return {
            "sim_seconds": 0,
            "boilers": {
                "boiler_01": {"capacity_tph": 22.0, "output_tph": 16.0, "availability": 1.0, "vibration_mm_s": 2.1},
                "boiler_02": {"capacity_tph": 22.0, "output_tph": 16.0, "availability": 1.0, "vibration_mm_s": 2.0},
            },
            "steam_header_inventory_t": 1.0,
            "steam_header_pressure_bar": 8.0,
            "steam_generation_tph": 32.0,
            "steam_demand_tph": 32.0,
            "steam_consumption_tph": 32.0,
            "steam_service_fraction": 1.0,
            "condensate_inventory_t": 12.0,
            "condensate_return_tph": 25.6,
            "condensate_reuse_tph": 25.6,
            "feedwater_makeup_tph": 6.4,
            "condensate_return_queue": [25.6] * 60,
            "pasteurizers": {
                "pasteurizer_01": {"temperature_c": 82.0, "setpoint_c": 82.0,
                                   "process_rate": 90.0, "production_rate": 90.0,
                                   "diverted_rate": 0.0, "steam_service_fraction": 1.0},
                "pasteurizer_02": {"temperature_c": 82.0, "setpoint_c": 82.0,
                                   "process_rate": 90.0, "production_rate": 90.0,
                                   "diverted_rate": 0.0, "steam_service_fraction": 1.0},
            },
            "product_tank_inventory_units": 180.0,
            "product_tank_inlet_units_min": 180.0,
            "product_tank_temperature_c": 12.0,
            "product_cooling_service_fraction": 1.0,
            "chilled_water_supply_temperature_c": 6.8,
            "cooling_demand_kw": 878.0,
            "cooled_product_rate_units_min": 180.0,
            "filler_rate_units_min": 180.0,
            "filling_buffer_inventory_units": 90.0,
            "case_packer_speed_fraction": 1.0,
            "packer_rate_units_min": 180.0,
            "finished_cases_total": 0.0,
            "last_advance_seconds": 0,
            "cip": self.CIP_MODEL.initialize(),
            "cip_water_demand_m3h": 0.0,
            "cip_effluent_m3h": 0.0,
            "cip_steam_demand_tph": 0.0,
            "compressors": {
                "compressor_a": {"capacity_nm3_min": 50.0, "output_nm3_min": 41.0, "availability": 1.0, "vibration_mm_s": 2.2},
                "compressor_b": {"capacity_nm3_min": 50.0, "output_nm3_min": 37.0, "availability": 1.0, "vibration_mm_s": 2.0},
            },
            "air_demand_nm3_min": 78.0,
            "air_consumption_nm3_min": 78.0,
            "air_supply_nm3_min": 78.0,
            "air_inventory_nm3": 560.0,
            "air_receiver_pressure_bar": 7.0,
            "air_service_fraction": 1.0,
            "dryer_available": True,
            "dryer_dew_point_c": -25.0,
            "filling_air_service": 1.0,
            "filler_rate_units_min": 180.0,
            "packer_rate_cases_min": 60.0,
            "electric_load_mw": 0.55,
            "history": [],
        }

    @staticmethod
    def _event_severity(events: list[dict], name: str, areas: tuple[str, ...]) -> float:
        return max((FactoryUtilities.SEVERITY[event["severity"]]
                    for event in events if event["name"] == name
                    and event["area"] in (*areas, "Whole factory")), default=0.0)

    def migrate(self, state: dict) -> None:
        """Add utility state to a paused session created by an older simulator."""
        defaults = self.initialize()
        for key, value in defaults.items():
            if key == "history":
                state.setdefault(key, [])
            else:
                state.setdefault(key, value)
        for name, equipment in defaults["boilers"].items():
            state["boilers"].setdefault(name, equipment.copy())
        for name, equipment in defaults["compressors"].items():
            state["compressors"].setdefault(name, equipment.copy())
        for name, unit in defaults["pasteurizers"].items():
            state["pasteurizers"].setdefault(name, unit.copy())
            for key, value in unit.items():
                state["pasteurizers"][name].setdefault(key, value)

    @staticmethod
    def chilled_water_demand_kw(state: dict) -> float:
        """Sensible heat duty to cool the current hot product flow to 12 °C."""
        product_flow = sum(pasteurizer.get("process_rate", 90.0)
                           for pasteurizer in state["pasteurizers"].values())
        return max(0.0, product_flow) * 4.186 * (82.0 - 12.0) / 60.0

    def advance(self, state: dict, sim_seconds: int, events: list[dict], *,
                power_available_fraction: float = 1.0, water_available_fraction: float = 1.0,
                wastewater_available_fraction: float = 1.0,
                chilled_water_service_fraction: float = 1.0,
                chilled_water_supply_temperature_c: float = 6.8,
                seed: int = 17) -> None:
        """Advance boiler/header, condensate, compressed-air, and consumer states."""
        had_scan_time = "last_advance_seconds" in state
        self.migrate(state)
        if not had_scan_time:
            # A paused browser session can acquire this model mid-run; its first
            # utility scan must represent one scan, not the entire old uptime.
            state["last_advance_seconds"] = max(0, sim_seconds - 1)
        state["sim_seconds"] = sim_seconds
        power_factor = max(0.0, min(1.0, power_available_fraction))
        cip_mechanical = self._event_severity(events, "Mechanical degradation", ("CIP",))
        self.CIP_MODEL.advance(
            state["cip"], sim_seconds,
            steam_available_fraction=state["steam_service_fraction"],
            water_available_fraction=water_available_fraction,
            power_available_fraction=power_factor,
            wastewater_available_fraction=wastewater_available_fraction,
            mechanical_degradation=cip_mechanical,
        )
        cip_demands = self.CIP_MODEL.demands(state["cip"])
        state["cip_water_demand_m3h"] = cip_demands["water_m3h"]
        state["cip_effluent_m3h"] = cip_demands["effluent_m3h"]
        state["cip_steam_demand_tph"] = cip_demands["steam_tph"]

        boiler_fault = self._event_severity(events, "Utility loss", ("Boiler house",))
        boiler_mechanical = self._event_severity(events, "Mechanical degradation", ("Boiler house",))
        steam_demand_fault = self._event_severity(events, "Process upset", ("Thermal processing",))
        boiler_capacity_factor = max(0.0, 1.0 - boiler_fault)
        boiler_capacity_factor *= max(0.0, 1.0 - 0.48 * boiler_mechanical)
        steam_demand = 32.0 * (1.0 + 0.38 * steam_demand_fault) + state["cip_steam_demand_tph"]
        pressure = 5.0 + 4.5 * state["steam_header_inventory_t"] / self.HEADER_CAPACITY_T
        steam_service = max(0.0, min(1.0, (pressure - 5.5) / 1.8))
        actual_steam_demand = steam_demand * steam_service
        pressure_recovery_flow = max(0.0, 8.0 - pressure) * 3.0
        steam_controller_target = actual_steam_demand + pressure_recovery_flow

        boiler_total = 0.0
        for index, name in enumerate(("boiler_01", "boiler_02")):
            boiler = state["boilers"][name]
            local_factor = boiler_capacity_factor
            if boiler_mechanical > 0 and index == 0:
                local_factor *= max(0.0, 1.0 - 0.62 * boiler_mechanical)
            boiler["availability"] = local_factor * power_factor
            capacity = boiler["capacity_tph"] * local_factor * power_factor
            target = min(capacity, steam_controller_target / 2.0)
            boiler["output_tph"] += max(-0.12, min(0.08, target - boiler["output_tph"]))
            boiler["vibration_mm_s"] = 2.0 + boiler_mechanical * 7.0 if index == 0 else 2.0
            boiler_total += boiler["output_tph"]

        condensate_fraction = 0.80
        if self._event_severity(events, "Mechanical degradation", ("Boiler house",)) > 0:
            condensate_fraction *= 0.78
        queued_return = state["condensate_return_queue"].pop(0)
        state["condensate_return_queue"].append(actual_steam_demand * condensate_fraction)
        state["condensate_return_tph"] = queued_return
        state["condensate_reuse_tph"] = min(queued_return, boiler_total * 0.9)
        state["condensate_inventory_t"] = max(0.0, min(self.CONDENSATE_CAPACITY_T,
            state["condensate_inventory_t"] + (queued_return - state["condensate_reuse_tph"]) / 3600.0))
        state["feedwater_makeup_tph"] = max(0.0, boiler_total - state["condensate_reuse_tph"])
        state["steam_generation_tph"] = boiler_total
        state["steam_demand_tph"] = steam_demand
        state["steam_consumption_tph"] = actual_steam_demand
        state["steam_header_inventory_t"] = max(0.0, min(self.HEADER_CAPACITY_T,
            state["steam_header_inventory_t"] + (boiler_total - actual_steam_demand) / 3600.0))
        state["steam_header_pressure_bar"] = (5.0 + 4.5 * state["steam_header_inventory_t"]
                                               / self.HEADER_CAPACITY_T)
        state["steam_service_fraction"] = max(0.0, min(1.0,
            (state["steam_header_pressure_bar"] - 5.5) / 1.8))

        compressor_fault = self._event_severity(events, "Utility loss", ("Compressed air",))
        compressor_mechanical = self._event_severity(events, "Mechanical degradation", ("Compressed air",))
        air_demand_fault = max(
            self._event_severity(events, "Process upset", ("Filling",)),
            self._event_severity(events, "Process upset", ("Packaging",)))
        air_demand = 78.0 * (1.0 + 0.32 * air_demand_fault)
        pressure = max(0.0, state["air_inventory_nm3"] / self.RECEIVER_VOLUME_M3)
        air_service = max(0.0, min(1.0, (pressure - 4.5) / 2.0))
        actual_air_demand = air_demand * (0.10 + 0.90 * air_service)
        receiver_recovery_flow = max(0.0, 7.0 - pressure) * 15.0
        air_controller_target = actual_air_demand + receiver_recovery_flow
        air_supply = 0.0
        for index, name in enumerate(("compressor_a", "compressor_b")):
            compressor = state["compressors"][name]
            local_factor = max(0.0, 1.0 - compressor_fault)
            if compressor_mechanical > 0 and index == 0:
                local_factor *= max(0.0, 1.0 - 0.62 * compressor_mechanical)
            compressor["availability"] = local_factor * power_factor
            target = min(compressor["capacity_nm3_min"] * compressor["availability"],
                         air_controller_target / 2.0)
            compressor["output_nm3_min"] += max(-1.5, min(1.0, target - compressor["output_nm3_min"]))
            compressor["vibration_mm_s"] = 2.1 + compressor_mechanical * 8.0 if index == 0 else 2.0
            air_supply += compressor["output_nm3_min"]

        state["air_demand_nm3_min"] = air_demand
        state["air_consumption_nm3_min"] = actual_air_demand
        state["air_supply_nm3_min"] = air_supply
        state["air_inventory_nm3"] = max(0.0, min(8.0 * self.RECEIVER_VOLUME_M3,
            state["air_inventory_nm3"] + (air_supply - actual_air_demand) / 60.0))
        state["air_receiver_pressure_bar"] = state["air_inventory_nm3"] / self.RECEIVER_VOLUME_M3
        state["air_service_fraction"] = max(0.0, min(1.0,
            (state["air_receiver_pressure_bar"] - 4.5) / 2.0))
        dryer_fault = self._event_severity(events, "Mechanical degradation", ("Compressed air",))
        dryer_lost = compressor_fault >= self.SEVERITY["Large"] or dryer_fault >= 0.42
        state["dryer_available"] = not dryer_lost and power_factor >= 0.5
        state["dryer_dew_point_c"] = -25.0 if state["dryer_available"] else min(25.0, state["dryer_dew_point_c"] + 0.08)
        if state["dryer_available"]:
            state["dryer_dew_point_c"] += (-25.0 - state["dryer_dew_point_c"]) * 0.01
        state["filling_air_service"] = state["air_service_fraction"]
        state["electric_load_mw"] = (air_supply * 60.0 * 0.11 / 1000.0 + 0.08
                                     + (0.08 if state["cip"]["active"] else 0.0))

        dt_s = max(0, sim_seconds - state.get("last_advance_seconds", sim_seconds - 1))
        dt_min = dt_s / 60.0
        raw_product_rate = 0.0
        product_tank_headroom_control = max(0.5, 1.0 - 0.5 * max(0.0,
            (state["product_tank_inventory_units"] - 0.5 * self.PRODUCT_TANK_CAPACITY_UNITS)
            / (0.5 * self.PRODUCT_TANK_CAPACITY_UNITS)))
        for name, pasteurizer in state["pasteurizers"].items():
            local_steam_service = state["steam_service_fraction"]
            local_steam_service *= 1.0 - self._event_severity(
                events, "Utility loss", ("Thermal processing",))
            target_temperature = 24.0 + 60.0 * local_steam_service
            pasteurizer["temperature_c"] += (target_temperature - pasteurizer["temperature_c"]) / 360.0
            cip_is_cleaning_line = (state["cip"]["active"]
                                    and state["cip"]["target_unit"] == name)
            pasteurizer["process_rate"] = (0.0 if cip_is_cleaning_line else
                90.0 * local_steam_service * product_tank_headroom_control)
            pasteurizer["steam_service_fraction"] = local_steam_service
            pasteurizer["pasteurized_rate"] = (pasteurizer["process_rate"]
                if pasteurizer["temperature_c"] >= self.PASTEURIZATION_LIMIT_C else 0.0)
            raw_product_rate += pasteurizer["pasteurized_rate"]

        state["cooling_demand_kw"] = self.chilled_water_demand_kw(state)
        chilled_service = max(0.0, min(1.0, chilled_water_service_fraction))
        # A warm supply header loses plate-exchanger temperature approach.
        temperature_factor = max(0.0, min(1.0,
            (15.0 - chilled_water_supply_temperature_c) / 7.0))
        cooling_service = min(chilled_service, temperature_factor)
        state["product_cooling_service_fraction"] = cooling_service
        state["chilled_water_supply_temperature_c"] = chilled_water_supply_temperature_c
        cooled_product_rate = raw_product_rate * cooling_service
        state["cooled_product_rate_units_min"] = cooled_product_rate
        raw_product_rate = cooled_product_rate
        for pasteurizer in state["pasteurizers"].values():
            pasteurizer["production_rate"] = pasteurizer["pasteurized_rate"] * cooling_service
            pasteurizer["diverted_rate"] = pasteurizer["process_rate"] - pasteurizer["production_rate"]
            pasteurizer["cooling_service_fraction"] = cooling_service

        if dt_s > 0:
            old_product_temp = state["product_tank_temperature_c"]
            old_product_inventory = state["product_tank_inventory_units"]
            incoming_units = cooled_product_rate * dt_min
            thermal_mix = min(1.0, incoming_units / max(1.0, old_product_inventory + incoming_units))
            mixed_temperature = old_product_temp + (12.0 - old_product_temp) * thermal_mix
            state["product_tank_temperature_c"] = mixed_temperature + (22.0 - mixed_temperature) * dt_s / 86_400.0

        jam_severity = self._event_severity(events, "Conveyor jam", ("Packaging",))
        mechanical_filler = self._event_severity(events, "Mechanical degradation", ("Filling",))
        mechanical_packer = self._event_severity(events, "Mechanical degradation", ("Packaging",))
        filler_target_fraction = max(0.0, 1.0 - 0.85 * mechanical_filler)
        recovery_boost = (1.2 if state["filling_buffer_inventory_units"]
                          > self.PACKAGE_BUFFER_CAPACITY_UNITS * 0.15 else 1.0)
        packer_target_fraction = max(0.0, 1.0 - jam_severity - 0.75 * mechanical_packer)
        packer_target_fraction *= recovery_boost
        state["case_packer_speed_fraction"] += max(-0.02, min(0.02,
            packer_target_fraction - state["case_packer_speed_fraction"]))
        product_temperature = state["product_tank_temperature_c"]
        product_release_fraction = (1.0 if product_temperature <= 14.0 else
            max(0.0, min(1.0, (20.0 - product_temperature) / 6.0)))
        filler_target = (180.0 * state["filling_air_service"] * filler_target_fraction
                        * product_release_fraction)
        packer_target = 180.0 * state["filling_air_service"] * state["case_packer_speed_fraction"]
        old_product_inventory = state["product_tank_inventory_units"]
        old_package_inventory = state["filling_buffer_inventory_units"]
        if dt_min > 0:
            # Bound the filler by both pasteurized inventory and downstream accumulation space.
            filler_rate = min(filler_target,
                old_product_inventory / dt_min + raw_product_rate,
                (self.PACKAGE_BUFFER_CAPACITY_UNITS - old_package_inventory) / dt_min + packer_target)
            # Back off upstream production when the balance tank is full.
            product_inlet_rate = min(raw_product_rate,
                (self.PRODUCT_TANK_CAPACITY_UNITS - old_product_inventory) / dt_min + filler_rate)
            packer_rate = min(packer_target, old_package_inventory / dt_min + filler_rate)
            state["product_tank_inventory_units"] = max(0.0, min(
                self.PRODUCT_TANK_CAPACITY_UNITS,
                old_product_inventory + (product_inlet_rate - filler_rate) * dt_min))
            state["filling_buffer_inventory_units"] = max(0.0, min(
                self.PACKAGE_BUFFER_CAPACITY_UNITS,
                old_package_inventory + (filler_rate - packer_rate) * dt_min))
            state["product_tank_inlet_units_min"] = product_inlet_rate
            state["filler_rate_units_min"] = filler_rate
            state["packer_rate_units_min"] = packer_rate
            state["finished_cases_total"] += packer_rate * dt_min / self.UNITS_PER_CASE
        else:
            state["product_tank_inlet_units_min"] = raw_product_rate
            state["filler_rate_units_min"] = filler_target
            state["packer_rate_units_min"] = packer_target
        state["packer_rate_cases_min"] = state["packer_rate_units_min"] / self.UNITS_PER_CASE
        state["last_advance_seconds"] = sim_seconds

        if sim_seconds > 0 and sim_seconds % 15 == 0:
            state["history"].append({key: state[key] for key in (
                "sim_seconds", "steam_header_pressure_bar", "steam_generation_tph",
                "steam_demand_tph", "steam_service_fraction", "condensate_inventory_t",
                "air_receiver_pressure_bar", "air_supply_nm3_min", "air_demand_nm3_min",
                "air_service_fraction", "dryer_dew_point_c",
                "filler_rate_units_min", "packer_rate_cases_min",
                "product_tank_inventory_units", "filling_buffer_inventory_units",
                "product_tank_inlet_units_min", "packer_rate_units_min",
                "product_tank_temperature_c", "product_cooling_service_fraction",
                "cooling_demand_kw", "cooled_product_rate_units_min",
                "finished_cases_total")}
                | {"pasteurizer_01_temperature_c": state["pasteurizers"]["pasteurizer_01"]["temperature_c"],
                   "pasteurizer_02_temperature_c": state["pasteurizers"]["pasteurizer_02"]["temperature_c"],
                   "pasteurizer_01_rate": state["pasteurizers"]["pasteurizer_01"]["production_rate"],
                   "pasteurizer_02_rate": state["pasteurizers"]["pasteurizer_02"]["production_rate"],
                   "cip_phase_index": state["cip"]["phase_index"],
                   "cip_temperature_c": state["cip"]["temperature_c"],
                   "cip_water_demand_m3h": state["cip_water_demand_m3h"],
                   "cip_effluent_m3h": state["cip_effluent_m3h"],
                   "cip_return_ph": state["cip"]["return_ph"]})
            state["history"] = state["history"][-1_440:]

    def telemetry(self, state: dict, seed: int = 17) -> list[dict]:
        rng = random.Random(seed * 1_000_003 + state["sim_seconds"] * 9_176)
        pressure = state["steam_header_pressure_bar"]
        air_pressure = state["air_receiver_pressure_bar"]
        steam = state["steam_consumption_tph"]
        air = state["air_consumption_nm3_min"]
        values = {
            ("Boiler house", "Boiler 01"): {
                "steam_flow": (state["boilers"]["boiler_01"]["output_tph"], "t/h", 0, 25),
                "outlet_pressure": (pressure, "bar", 0, 20),
                "stack_temperature": (180 + 2.2 * state["boilers"]["boiler_01"]["output_tph"], "°C", 0, 300),
                "motor_current": (38 + 85 * state["boilers"]["boiler_01"]["availability"], "A", 0, 200),
                "vibration_rms": (state["boilers"]["boiler_01"]["vibration_mm_s"], "mm/s", 0, 25),
            },
            ("Boiler house", "Boiler 02"): {
                "steam_flow": (state["boilers"]["boiler_02"]["output_tph"], "t/h", 0, 25),
                "outlet_pressure": (pressure, "bar", 0, 20),
                "stack_temperature": (180 + 2.2 * state["boilers"]["boiler_02"]["output_tph"], "°C", 0, 300),
                "motor_current": (38 + 85 * state["boilers"]["boiler_02"]["availability"], "A", 0, 200),
                "vibration_rms": (state["boilers"]["boiler_02"]["vibration_mm_s"], "mm/s", 0, 25),
            },
            ("Boiler house", "Feedwater system"): {
                "makeup_flow": (state["feedwater_makeup_tph"], "t/h", 0, 40),
                "feedwater_pressure": (1_150 * min(1.0, state["steam_generation_tph"] / 32), "kPa", 0, 1_600),
                "condensate_tank_level": (100 * state["condensate_inventory_t"] / self.CONDENSATE_CAPACITY_T, "%", 0, 100),
                "pump_current": (42 + 35 * min(1.0, state["steam_generation_tph"] / 32), "A", 0, 150),
                "conductivity": (28.0 + 2 * state["feedwater_makeup_tph"], "µS/cm", 0, 300),
            },
            ("Boiler house", "Steam header"): {
                "line_pressure": (pressure, "bar", 0, 20), "steam_flow": (steam, "t/h", 0, 50),
                "steam_temperature": (170 + 5 * pressure, "°C", 0, 250),
                "header_inventory": (100 * state["steam_header_inventory_t"] / self.HEADER_CAPACITY_T, "%", 0, 100),
                "service_fraction": (100 * state["steam_service_fraction"], "%", 0, 100),
            },
            ("Boiler house", "Condensate return"): {
                "return_flow": (state["condensate_return_tph"], "t/h", 0, 50),
                "tank_level": (100 * state["condensate_inventory_t"] / self.CONDENSATE_CAPACITY_T, "%", 0, 100),
                "return_temperature": (82.0, "°C", 0, 150),
                "reuse_flow": (state["condensate_reuse_tph"], "t/h", 0, 50),
                "feedwater_makeup": (state["feedwater_makeup_tph"], "t/h", 0, 50),
            },
            ("Compressed air", "Compressor A"): {
                "air_flow": (state["compressors"]["compressor_a"]["output_nm3_min"], "Nm³/min", 0, 60),
                "discharge_pressure": (air_pressure + 0.4, "bar", 0, 12),
                "motor_current": (18 + 6 * state["compressors"]["compressor_a"]["output_nm3_min"], "A", 0, 400),
                "vibration_rms": (state["compressors"]["compressor_a"]["vibration_mm_s"], "mm/s", 0, 25),
                "equipment_running": (float(state["compressors"]["compressor_a"]["output_nm3_min"] > 1), "bool", 0, 1),
            },
            ("Compressed air", "Compressor B"): {
                "air_flow": (state["compressors"]["compressor_b"]["output_nm3_min"], "Nm³/min", 0, 60),
                "discharge_pressure": (air_pressure + 0.4, "bar", 0, 12),
                "motor_current": (18 + 6 * state["compressors"]["compressor_b"]["output_nm3_min"], "A", 0, 400),
                "vibration_rms": (state["compressors"]["compressor_b"]["vibration_mm_s"], "mm/s", 0, 25),
                "equipment_running": (float(state["compressors"]["compressor_b"]["output_nm3_min"] > 1), "bool", 0, 1),
            },
            ("Compressed air", "Dryer A"): {
                "dew_point": (state["dryer_dew_point_c"], "°C", -60, 30),
                "differential_pressure": (18.0 if state["dryer_available"] else 42.0, "kPa", 0, 120),
                "air_flow": (state["air_supply_nm3_min"], "Nm³/min", 0, 120),
                "equipment_running": (float(state["dryer_available"]), "bool", 0, 1),
            },
            ("Compressed air", "Receiver 01"): {
                "receiver_pressure": (air_pressure, "bar", 0, 12),
                "stored_air": (100 * state["air_inventory_nm3"] / (8 * self.RECEIVER_VOLUME_M3), "%", 0, 100),
                "inlet_flow": (state["air_supply_nm3_min"], "Nm³/min", 0, 120),
                "outlet_flow": (air, "Nm³/min", 0, 120),
            },
            ("Compressed air", "Ring main"): {
                "line_pressure": (air_pressure, "bar", 0, 12), "air_demand": (air, "Nm³/min", 0, 150),
                "air_supply": (state["air_supply_nm3_min"], "Nm³/min", 0, 150),
                "instrument_air_quality": (float(state["dryer_available"]), "bool", 0, 1),
            },
            ("Thermal processing", "Pasteurizer 01"): self._pasteurizer_signals(state, "pasteurizer_01", pressure),
            ("Thermal processing", "Pasteurizer 02"): self._pasteurizer_signals(state, "pasteurizer_02", pressure),
            ("Thermal processing", "Product balance tank"): {
                "inventory": (100 * state["product_tank_inventory_units"] / self.PRODUCT_TANK_CAPACITY_UNITS, "%", 0, 100),
                "inlet_flow": (state["product_tank_inlet_units_min"], "units/min", 0, 240),
                "outlet_flow": (state["filler_rate_units_min"], "units/min", 0, 240),
                "product_temperature": (state["product_tank_temperature_c"], "°C", 0, 140),
                "cooling_service": (100.0 * state["product_cooling_service_fraction"], "%", 0, 100),
                "cooling_duty": (state["cooling_demand_kw"], "kW", 0, 2_000),
            },
            ("Filling", "Filler 01"): {
                "instrument_air_pressure": (air_pressure, "bar", 0, 12),
                "production_rate": (state["filler_rate_units_min"], "units/min", 0, 240),
                "air_service": (100 * state["filling_air_service"], "%", 0, 100),
                "motor_current": (22 + 0.6 * state["filler_rate_units_min"], "A", 0, 200),
            },
            ("Packaging", "Case packer A"): {
                "instrument_air_pressure": (air_pressure, "bar", 0, 12),
                "production_rate": (state["packer_rate_cases_min"], "cases/min", 0, 100),
                "air_service": (100 * state["filling_air_service"], "%", 0, 100),
                "motor_current": (16 + 0.8 * state["packer_rate_cases_min"], "A", 0, 120),
                "drive_speed": (100 * state["case_packer_speed_fraction"], "%", 0, 100),
            },
            ("Packaging", "Accumulation conveyor"): {
                "buffer_inventory": (100 * state["filling_buffer_inventory_units"] / self.PACKAGE_BUFFER_CAPACITY_UNITS, "%", 0, 100),
                "infeed_rate": (state["filler_rate_units_min"], "units/min", 0, 240),
                "outfeed_rate": (state["packer_rate_units_min"], "units/min", 0, 240),
                "blocked": (float(state["filling_buffer_inventory_units"] >= self.PACKAGE_BUFFER_CAPACITY_UNITS - 1e-6), "bool", 0, 1),
            },
            ("CIP", "CIP tank A"): {
                "tank_level": (100.0 if state["cip"]["active"] else 0.0, "%", 0, 100),
                "temperature": (state["cip"]["temperature_c"], "°C", 0, 100),
                "phase_progress": (state["cip"]["phase_progress_pct"], "%", 0, 100),
                "phase_code": (state["cip"]["phase_index"], "phase", 0, len(self.CIP_MODEL.PHASES)),
            },
            ("CIP", "CIP tank B"): {
                "caustic_inventory": (state["cip"]["caustic_inventory_l"], "L", 0, 1_000),
                "sanitizer_inventory": (state["cip"]["sanitizer_inventory_l"], "L", 0, 500),
                "return_ph": (state["cip"]["return_ph"], "pH", 0, 14),
                "return_conductivity": (state["cip"]["return_conductivity_us_cm"], "µS/cm", 0, 50_000),
            },
            ("CIP", "CIP supply"): {
                "water_flow": (state["cip_water_demand_m3h"], "m³/h", 0, 40),
                "steam_flow": (state["cip_steam_demand_tph"], "t/h", 0, 20),
                "recirculation_flow": (state["cip"]["recirculation_flow_m3h"], "m³/h", 0, 60),
                "pump_available": (100.0 * state["cip"]["power_available_fraction"], "%", 0, 100),
            },
            ("CIP", "CIP return"): {
                "effluent_flow": (state["cip_effluent_m3h"], "m³/h", 0, 40),
                "return_ph": (state["cip"]["return_ph"], "pH", 0, 14),
                "return_conductivity": (state["cip"]["return_conductivity_us_cm"], "µS/cm", 0, 50_000),
                "water_availability": (100.0 * state["cip"]["water_available_fraction"], "%", 0, 100),
            },
            ("CIP", "Chemical dosing"): {
                "caustic_dosing": (state["cip"]["caustic_dosing_l_min"], "L/min", 0, 10),
                "sanitizer_dosing": (state["cip"]["sanitizer_dosing_l_min"], "L/min", 0, 10),
                "caustic_tank_level": (100.0 * state["cip"]["caustic_inventory_l"] / 500.0, "%", 0, 100),
                "sanitizer_tank_level": (100.0 * state["cip"]["sanitizer_inventory_l"] / 250.0, "%", 0, 100),
            },
        }
        rows = []
        for (area, asset), signals in values.items():
            for sensor, (truth, units, low, high) in signals.items():
                tag = f"{area.lower().replace(' ', '_')}.{asset.lower().replace(' ', '_')}.{sensor}"
                noise = 0.0 if units == "bool" else rng.gauss(0, max(high - low, 1e-9) * 0.001)
                rows.append({"tag": tag, "area": area, "unit": asset, "sensor": sensor,
                    "units": units, "value": round(min(high, max(low, truth + noise)), 3),
                    "quality": "GOOD", "low": low, "high": high, "base": (low + high) / 2,
                    "phase": 0.0, "last_change": "Factory steam and instrument-air model",
                    "dynamic_model": True})
        return rows

    @staticmethod
    def _pasteurizer_signals(state: dict, name: str, header_pressure: float) -> dict:
        pasteurizer = state["pasteurizers"][name]
        return {
            "process_temperature": (pasteurizer["temperature_c"], "°C", 0, 140),
            "steam_pressure": (header_pressure, "bar", 0, 20),
            "process_rate": (pasteurizer["process_rate"], "units/min", 0, 120),
            "production_rate": (pasteurizer["production_rate"], "units/min", 0, 120),
            "diverted_rate": (pasteurizer["diverted_rate"], "units/min", 0, 120),
            "steam_flow": (16.0 * pasteurizer["steam_service_fraction"], "t/h", 0, 30),
            "temperature_compliance": (100.0 if pasteurizer["temperature_c"] >= FactoryUtilities.PASTEURIZATION_LIMIT_C else 0.0, "%", 0, 100),
        }
