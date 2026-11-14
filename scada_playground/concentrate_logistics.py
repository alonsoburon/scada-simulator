"""Low-compute dryer, concentrate silo, and rail-train solids balances."""
from __future__ import annotations


class ConcentrateLogistics:
    """Dry filter cake and dispatch finite concentrate train loads."""

    AREA_ASSETS = {
        "Concentrate handling": ["Concentrate dryer", "Loadout silo", "Rail loadout"],
    }
    DRYER_CAPACITY_TPH = 35.0
    DRYER_OUTLET_MOISTURE_PCT = 8.0
    PRODUCT_MOISTURE_LIMIT_PCT = 10.0
    DRYER_TEMPERATURE_SETPOINT_C = 165.0
    DRYER_HEATUP_TIME_S = 120.0
    DRYER_COOLDOWN_TIME_S = 900.0
    SILO_CAPACITY_T = 1_600.0
    SILO_DISPATCH_LEVEL_T = 1_200.0
    TRAIN_CAPACITY_T = 2_500.0
    TRAIN_LOADING_RATE_TPH = 450.0
    TRAIN_ARRIVAL_LEAD_S = 5_400
    TRAIN_DEPARTURE_TRANSIT_S = 14_400
    TRAIN_RETURN_TRANSIT_S = 10_800
    TRAIN_MIN_LOAD_T = 1_000.0
    TRAIN_MINIMUM_EMPTY_WAIT_S = 900
    TRAIN_MAX_LOADING_S = 28_800
    WATER_LATENT_HEAT_MWH_T = 0.627
    DRYER_THERMAL_EFFICIENCY = 0.72

    def initialize(self, state: dict) -> None:
        defaults = {
            "concentrate_dryer_temperature_c": self.DRYER_TEMPERATURE_SETPOINT_C,
            "concentrate_dryer_inlet_moisture_pct": 15.0,
            "concentrate_dryer_outlet_moisture_pct": self.DRYER_OUTLET_MOISTURE_PCT,
            "concentrate_dryer_feed_tph": 0.0,
            "concentrate_dryer_thermal_duty_mw": 0.0,
            "concentrate_dryer_power_mw": 0.0,
            "concentrate_dryer_condition": 1.0,
            "concentrate_dryer_moisture_permissive": True,
            "cumulative_dryer_feed_t": 0.0,
            "cumulative_dryer_copper_t": 0.0,
            "cumulative_dryer_water_removed_t": 0.0,
            "loadout_silo_inventory_t": 1_000.0,
            "loadout_silo_capacity_t": self.SILO_CAPACITY_T,
            "loadout_silo_level_pct": 100.0
                * state.get("loadout_silo_inventory_t", 1_000.0)
                / max(state.get("loadout_silo_capacity_t", self.SILO_CAPACITY_T), 1e-9),
            "loadout_silo_copper_inventory_t": 250.0,
            "initial_loadout_silo_inventory_t": 1_000.0,
            "initial_loadout_silo_copper_inventory_t": 250.0,
            "cumulative_concentrate_loaded_t": 0.0,
            "cumulative_concentrate_copper_loaded_t": 0.0,
            "rail_train_state": "WAITING",
            "rail_train_eta_s": 0,
            "rail_train_inventory_t": 0.0,
            "rail_train_copper_inventory_t": 0.0,
            "rail_train_capacity_t": self.TRAIN_CAPACITY_T,
            "rail_train_dispatches": 0,
            "rail_train_departures": 0,
            "rail_train_loading_elapsed_s": 0,
            "rail_train_empty_wait_s": 0,
            "rail_train_load_rate_tph": 0.0,
            "rail_train_last_manifest_t": 0.0,
            "rail_train_last_grade_pct": 0.0,
            "rail_loadout_service_fraction": 1.0,
            "rail_loadout_power_mw": 0.0,
            "concentrate_logistics_sim_seconds": state.get("sim_seconds", 0),
            "concentrate_cake_level_pct": 100.0 * state.get("concentrate_cake_inventory_t", 90.0)
                / max(state.get("concentrate_cake_capacity_t", 500.0), 1e-9),
        }
        for key, value in defaults.items():
            state.setdefault(key, value)
        state.setdefault("initial_concentrate_cake_inventory_t",
                         state.get("concentrate_cake_inventory_t", 90.0))
        state.setdefault("initial_concentrate_cake_copper_inventory_t",
                         state.get("concentrate_cake_copper_inventory_t", 22.5))
        state.setdefault("cumulative_filter_press_cake_t", 0.0)
        state.setdefault("cumulative_filter_press_cake_copper_t", 0.0)

    @staticmethod
    def _severity(events: list[dict], name: str, asset: str) -> float:
        factors = {"Small": 0.18, "Medium": 0.42, "Large": 0.78}
        return max((factors.get(event.get("severity"), 0.0) for event in events
                    if event.get("name") == name
                    and event.get("area") in ("Concentrate handling", "Whole factory")
                    and (event.get("area") == "Whole factory"
                         or event.get("target_asset") in (None, asset))), default=0.0)

    @staticmethod
    def _moisture_water_per_dry_t(moisture_pct: float) -> float:
        wet_fraction = min(0.95, max(0.0, moisture_pct / 100.0))
        return wet_fraction / max(1.0 - wet_fraction, 1e-9)

    def advance(self, state: dict, sim_seconds: int, events: list[dict], *,
                power_available_fraction: float = 1.0) -> None:
        self.initialize(state)
        previous = state.get("concentrate_logistics_sim_seconds", sim_seconds - 1)
        dt_s = max(0, sim_seconds - previous)
        dt_h = dt_s / 3_600.0
        state["concentrate_logistics_sim_seconds"] = sim_seconds

        dryer_mechanical = self._severity(events, "Mechanical degradation", "Concentrate dryer")
        dryer_power_fault = self._severity(events, "Utility loss", "Concentrate dryer")
        dryer_upset = self._severity(events, "Process upset", "Concentrate dryer")
        silo_mechanical = self._severity(events, "Mechanical degradation", "Loadout silo")
        silo_power_fault = self._severity(events, "Utility loss", "Loadout silo")
        rail_mechanical = self._severity(events, "Mechanical degradation", "Rail loadout")
        rail_power_fault = self._severity(events, "Utility loss", "Rail loadout")
        power = min(1.0, max(0.0, power_available_fraction))
        dryer_power = power * max(0.0, 1.0 - dryer_power_fault / 0.78)
        dryer_condition = max(0.0, 1.0 - 0.75 * dryer_mechanical)
        state["concentrate_dryer_condition"] = dryer_condition

        if dt_s > 0:
            temp = state["concentrate_dryer_temperature_c"]
            if dryer_power > 0.01:
                temp += (self.DRYER_TEMPERATURE_SETPOINT_C - temp) * min(
                    1.0, dt_s / self.DRYER_HEATUP_TIME_S) * dryer_power
            else:
                temp += (25.0 - temp) * min(1.0, dt_s / self.DRYER_COOLDOWN_TIME_S)
            state["concentrate_dryer_temperature_c"] = temp

        thermal_service = min(1.0, max(0.0,
            (state["concentrate_dryer_temperature_c"] - 70.0) / 95.0))
        inlet_moisture = min(35.0, 15.0 + (1.0 - state.get("concentrate_condition", 1.0)) * 8.0
                             + dryer_upset * 5.0)
        outlet_moisture = min(35.0, self.DRYER_OUTLET_MOISTURE_PCT + dryer_upset * 8.0
                              + (1.0 - thermal_service) * max(0.0, inlet_moisture - 8.0))
        state["concentrate_dryer_inlet_moisture_pct"] = inlet_moisture
        state["concentrate_dryer_outlet_moisture_pct"] = outlet_moisture
        state["concentrate_dryer_moisture_permissive"] = (
            outlet_moisture <= self.PRODUCT_MOISTURE_LIMIT_PCT)

        silo_service = (power * max(0.0, 1.0 - silo_power_fault / 0.78)
                        * max(0.0, 1.0 - 0.70 * silo_mechanical))
        cake_inventory = max(0.0, state["concentrate_cake_inventory_t"])
        silo_room = max(0.0, state["loadout_silo_capacity_t"]
                        - state["loadout_silo_inventory_t"])
        inlet_water_ratio = self._moisture_water_per_dry_t(inlet_moisture)
        outlet_water_ratio = self._moisture_water_per_dry_t(outlet_moisture)
        water_removed_ratio = max(0.0, inlet_water_ratio - outlet_water_ratio)
        water_removed_per_solid_mwh = (water_removed_ratio * self.WATER_LATENT_HEAT_MWH_T
                                        / self.DRYER_THERMAL_EFFICIENCY)
        max_by_heat_tph = (2.25 / water_removed_per_solid_mwh
                           if water_removed_per_solid_mwh > 1e-9 else self.DRYER_CAPACITY_TPH)
        dryer_capacity = (self.DRYER_CAPACITY_TPH * dryer_condition * thermal_service
                          * dryer_power * (0.65 + 0.35 * silo_service))
        dryer_capacity = min(dryer_capacity, max_by_heat_tph * dryer_power)
        dryer_rate = (min(dryer_capacity, cake_inventory / dt_h, silo_room / dt_h)
                      if dt_h > 0 and state["concentrate_dryer_moisture_permissive"
                      ] and silo_service > 0.01 else 0.0)
        cake_grade = (state["concentrate_cake_copper_inventory_t"]
                      / max(cake_inventory, 1e-9))
        dryer_solids_t = dryer_rate * dt_h
        dryer_copper_t = min(state["concentrate_cake_copper_inventory_t"],
                             dryer_solids_t * cake_grade)
        state["concentrate_cake_inventory_t"] = max(0.0, cake_inventory - dryer_solids_t)
        state["concentrate_cake_copper_inventory_t"] = max(
            0.0, state["concentrate_cake_copper_inventory_t"] - dryer_copper_t)
        state["loadout_silo_inventory_t"] += dryer_solids_t
        state["loadout_silo_copper_inventory_t"] += dryer_copper_t
        state["cumulative_dryer_feed_t"] += dryer_solids_t
        state["cumulative_dryer_copper_t"] += dryer_copper_t
        state["cumulative_dryer_water_removed_t"] += dryer_solids_t * water_removed_ratio
        state["concentrate_dryer_feed_tph"] = dryer_rate
        state["concentrate_dryer_thermal_duty_mw"] = (
            dryer_rate * water_removed_per_solid_mwh)
        state["concentrate_dryer_power_mw"] = (
            0.16 + dryer_rate * 0.006 if dryer_rate > 1e-6 and dryer_power > 0.01 else 0.0)

        rail_service = (power * max(0.0, 1.0 - rail_power_fault / 0.78)
                        * max(0.0, 1.0 - 0.75 * rail_mechanical))
        state["rail_loadout_service_fraction"] = rail_service
        state["rail_train_load_rate_tph"] = 0.0
        train_state = state["rail_train_state"]
        if train_state == "WAITING" and state["loadout_silo_inventory_t"] >= self.SILO_DISPATCH_LEVEL_T:
            train_state = "EN_ROUTE"
            state["rail_train_eta_s"] = self.TRAIN_ARRIVAL_LEAD_S
            state["rail_train_dispatches"] += 1
        if train_state == "EN_ROUTE":
            state["rail_train_eta_s"] = max(0, state["rail_train_eta_s"] - dt_s)
            if state["rail_train_eta_s"] == 0:
                train_state = "LOADING"
                state["rail_train_loading_elapsed_s"] = 0
                state["rail_train_empty_wait_s"] = 0
        elif train_state == "DEPARTING":
            state["rail_train_eta_s"] = max(0, state["rail_train_eta_s"] - dt_s)
            if state["rail_train_eta_s"] == 0:
                loaded = state["rail_train_inventory_t"]
                copper = state["rail_train_copper_inventory_t"]
                state["rail_train_last_manifest_t"] = loaded
                state["rail_train_last_grade_pct"] = (
                    100.0 * copper / loaded if loaded > 1e-9 else 0.0)
                state["cumulative_concentrate_shipped_t"] += loaded
                state["cumulative_concentrate_copper_shipped_t"] += copper
                state["rail_train_departures"] += 1
                state["rail_train_inventory_t"] = 0.0
                state["rail_train_copper_inventory_t"] = 0.0
                state["rail_train_eta_s"] = self.TRAIN_RETURN_TRANSIT_S
                train_state = "RETURNING"
        elif train_state == "RETURNING":
            state["rail_train_eta_s"] = max(0, state["rail_train_eta_s"] - dt_s)
            if state["rail_train_eta_s"] == 0:
                train_state = "WAITING"

        if train_state in {"LOADING", "ON_HOLD"}:
            if rail_service > 0.01:
                train_state = "LOADING"
                if dt_h > 0:
                    space = max(0.0, state["rail_train_capacity_t"]
                                - state["rail_train_inventory_t"])
                    load = min(self.TRAIN_LOADING_RATE_TPH * rail_service,
                               space / dt_h, state["loadout_silo_inventory_t"] / dt_h)
                    source_grade = (state["loadout_silo_copper_inventory_t"]
                                    / max(state["loadout_silo_inventory_t"], 1e-9))
                    copper_loaded = min(state["loadout_silo_copper_inventory_t"],
                                        load * dt_h * source_grade)
                    loaded_t = load * dt_h
                    state["loadout_silo_inventory_t"] = max(0.0,
                        state["loadout_silo_inventory_t"] - loaded_t)
                    state["loadout_silo_copper_inventory_t"] = max(0.0,
                        state["loadout_silo_copper_inventory_t"] - copper_loaded)
                    state["rail_train_inventory_t"] += loaded_t
                    state["rail_train_copper_inventory_t"] += copper_loaded
                    state["cumulative_concentrate_loaded_t"] += loaded_t
                    state["cumulative_concentrate_copper_loaded_t"] += copper_loaded
                    state["rail_train_load_rate_tph"] = load
                    state["rail_train_loading_elapsed_s"] += dt_s
                    state["rail_train_empty_wait_s"] = (
                        state["rail_train_empty_wait_s"] + dt_s
                        if state["loadout_silo_inventory_t"] < 5.0 else 0)
                    full_train = (state["rail_train_inventory_t"]
                                  >= state["rail_train_capacity_t"] - 1e-6)
                    drained_train = (state["rail_train_inventory_t"] >= self.TRAIN_MIN_LOAD_T
                                     and state["rail_train_empty_wait_s"]
                                     >= self.TRAIN_MINIMUM_EMPTY_WAIT_S)
                    detained_train = (state["rail_train_inventory_t"] > 0.0
                                      and state["rail_train_loading_elapsed_s"]
                                      >= self.TRAIN_MAX_LOADING_S)
                    if full_train or drained_train or detained_train:
                        train_state = "DEPARTING"
                        state["rail_train_eta_s"] = self.TRAIN_DEPARTURE_TRANSIT_S
            else:
                train_state = "ON_HOLD"

        state["rail_train_state"] = train_state
        state["concentrate_shipped_tph"] = state["rail_train_load_rate_tph"]
        state["rail_loadout_power_mw"] = (
            0.20 * rail_service if train_state == "LOADING" and rail_service > 0.01 else 0.0)
        state["concentrate_logistics_power_mw"] = (
            state["concentrate_dryer_power_mw"] + state["rail_loadout_power_mw"])
        state["total_power_mw"] = (state.get("total_power_mw", 0.0)
                                    + state["concentrate_logistics_power_mw"])
        state["concentrate_cake_level_pct"] = (
            100.0 * state["concentrate_cake_inventory_t"]
            / max(state["concentrate_cake_capacity_t"], 1e-9))
        state["loadout_silo_level_pct"] = (
            100.0 * state["loadout_silo_inventory_t"]
            / max(state["loadout_silo_capacity_t"], 1e-9))

    def telemetry(self, state: dict, seed: int = 17) -> list[dict]:
        del seed
        values = {
            "Concentrate dryer": {
                "feed_rate": (state["concentrate_dryer_feed_tph"], "t/h", 0, 100),
                "inlet_moisture": (state["concentrate_dryer_inlet_moisture_pct"], "%", 0, 40),
                "outlet_moisture": (state["concentrate_dryer_outlet_moisture_pct"], "%", 0, 40),
                "temperature": (state["concentrate_dryer_temperature_c"], "°C", 0, 250),
                "thermal_duty": (state["concentrate_dryer_thermal_duty_mw"], "MW", 0, 10),
                "motor_power": (state["concentrate_dryer_power_mw"], "MW", 0, 5),
                "moisture_permissive": (float(state["concentrate_dryer_moisture_permissive"]), "bool", 0, 1),
                "condition": (100.0 * state["concentrate_dryer_condition"], "%", 0, 100),
            },
            "Loadout silo": {
                "inventory": (state["loadout_silo_inventory_t"], "t", 0, state["loadout_silo_capacity_t"]),
                "level": (state["loadout_silo_level_pct"], "%", 0, 100),
                "copper_inventory": (state["loadout_silo_copper_inventory_t"], "t Cu", 0, 800),
                "dispatch_threshold": (self.SILO_DISPATCH_LEVEL_T, "t", 0, state["loadout_silo_capacity_t"]),
            },
            "Rail loadout": {
                "train_state": ({"WAITING": 0, "EN_ROUTE": 1, "LOADING": 2,
                                 "ON_HOLD": 3, "DEPARTING": 4, "RETURNING": 5}
                                .get(state["rail_train_state"], 0), "enum", 0, 5),
                "train_eta": (state["rail_train_eta_s"] / 3600.0, "h", 0, 24),
                "train_inventory": (state["rail_train_inventory_t"], "t", 0,
                                    state["rail_train_capacity_t"]),
                "train_copper_inventory": (state["rail_train_copper_inventory_t"], "t Cu", 0, 800),
                "load_rate": (state["rail_train_load_rate_tph"], "t/h", 0, 1_000),
                "service": (state["rail_loadout_service_fraction"] * 100, "%", 0, 100),
                "last_manifest": (state["rail_train_last_manifest_t"], "t", 0,
                                  state["rail_train_capacity_t"]),
                "departures": (state["rail_train_departures"], "trains", 0, 1_000_000),
                "concentrate_shipped": (state["cumulative_concentrate_shipped_t"], "t", 0, 1e9),
            },
        }
        rows = []
        for asset, signals in values.items():
            for signal, (value, units, low, high) in signals.items():
                tag = f"concentrate_handling.{asset.lower().replace(' ', '_')}.{signal}"
                rows.append({"tag": tag, "area": "Concentrate handling", "unit": asset,
                    "sensor": signal, "units": units,
                    "value": round(min(high, max(low, value)), 3), "quality": "GOOD",
                    "low": low, "high": high, "base": (low + high) / 2,
                    "phase": 0.0, "last_change": "Finite dryer, silo, and rail shipment model",
                    "dynamic_model": True})
        return rows
