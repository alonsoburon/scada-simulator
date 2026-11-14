"""Recipe-controlled beverage batching with finite ingredient and mix-tank inventory."""
from __future__ import annotations

import random


class BatchBlending:
    AREA_ASSETS = {
        "Ingredient dosing": ["Base concentrate tank", "Syrup tank", "Base dosing pump",
                              "Syrup dosing pump", "Treated-water inlet"],
        "Batch blending": ["Mix tank A", "Blend outlet", "Recipe controller"],
        "Bulk receiving": ["Receiving weighbridge", "Tanker unloading bay",
                           "Base concentrate tanker", "Syrup tanker"],
    }
    BASE_KG_PER_L = 0.12
    SYRUP_KG_PER_L = 0.04
    WATER_L_PER_L = 0.84
    MIXER_CAPACITY_LPM = 230.0
    MIX_TANK_CAPACITY_L = 8_000.0
    MIX_TANK_TARGET_L = 6_000.0
    WATER_BUFFER_CAPACITY_L = 10_000.0
    WATER_BUFFER_TARGET_L = 8_000.0
    MAX_WATER_TURBIDITY_NTU = 5.0
    MAX_WATER_CHLORINE_MG_L = 0.8
    SUPPLIER_ROUTES = (
        (-24.3050, -69.0950), (-24.2990, -69.0830),
        (-24.2860, -69.0760), (-24.2780, -69.0680),
    )

    def initialize(self) -> dict:
        return {
            "sim_seconds": 0,
            "mix_tank_inventory_l": 4_500.0,
            "mix_tank_capacity_l": self.MIX_TANK_CAPACITY_L,
            "mix_tank_level_pct": 56.25,
            "product_demand_lpm": 180.0,
            "product_feed_lpm": 180.0,
            "product_feed_service_fraction": 1.0,
            "mixer_request_lpm": 180.0,
            "mixer_rate_lpm": 180.0,
            "mixer_power_fraction": 1.0,
            "mix_water_demand_m3h": 16.392,
            "mix_water_delivered_m3h": 16.392,
            "mix_water_received_m3h": 16.392,
            "water_turbidity_ntu": 4.0,
            "water_chlorine_mg_l": 0.45,
            "water_quality_permissive": True,
            "mix_water_in_transit_m3": 0.0,
            "mix_water_inventory_l": 5_000.0,
            "mix_water_capacity_l": self.WATER_BUFFER_CAPACITY_L,
            "mix_water_level_pct": 50.0,
            "water_service_fraction": 1.0,
            "base_tank_inventory_kg": 34_000.0,
            "base_tank_capacity_kg": 40_000.0,
            "base_shipment_state": "IDLE",
            "base_shipment_eta_s": 0,
            "base_shipment_remaining_kg": 0.0,
            "base_shipment_load_kg": 0.0,
            "base_shipment_unload_rate_kg_h": 12_000.0,
            "base_receiving_rate_kg_h": 0.0,
            "base_receiving_pump_speed_pct": 0.0,
            "base_shipment_cumulative_received_kg": 0.0,
            "base_shipment_cumulative_ordered": 0,
            "syrup_tank_inventory_kg": 17_000.0,
            "syrup_tank_capacity_kg": 20_000.0,
            "syrup_shipment_state": "IDLE",
            "syrup_shipment_eta_s": 0,
            "syrup_shipment_remaining_kg": 0.0,
            "syrup_shipment_load_kg": 0.0,
            "syrup_shipment_unload_rate_kg_h": 8_000.0,
            "syrup_receiving_rate_kg_h": 0.0,
            "syrup_receiving_pump_speed_pct": 0.0,
            "syrup_shipment_cumulative_received_kg": 0.0,
            "syrup_shipment_cumulative_ordered": 0,
            "receiving_bay_active_material": "NONE",
            "receiving_bay_service_fraction": 1.0,
            "weighbridge_state": "IDLE",
            "base_dosing_rate_kg_h": 1_296.0,
            "syrup_dosing_rate_kg_h": 432.0,
            "base_dosing_service_fraction": 1.0,
            "syrup_dosing_service_fraction": 1.0,
            "base_tank_leak_kg_h": 0.0,
            "syrup_tank_leak_kg_h": 0.0,
            "cumulative_base_leak_kg": 0.0,
            "cumulative_syrup_leak_kg": 0.0,
            "blend_outlet_service_fraction": 1.0,
            "recipe_ratio_error_pct": 0.0,
            "recipe_permissive": True,
            "cumulative_product_m3": 0.0,
            "cumulative_mixed_l": 0.0,
            "cumulative_water_m3": 0.0,
            "cumulative_water_delivery_m3": 0.0,
            "cumulative_water_overflow_l": 0.0,
            "cumulative_water_bypass_m3": 0.0,
            "cumulative_water_quality_reject_m3": 0.0,
            "water_bypass_flow_m3h": 0.0,
            "cumulative_base_kg": 0.0,
            "cumulative_syrup_kg": 0.0,
            "base_initial_inventory_kg": 34_000.0,
            "syrup_initial_inventory_kg": 17_000.0,
            "electric_load_mw": 0.025,
            "requested_load_mw": 0.025,
            "history": [],
        }

    def migrate(self, state: dict) -> None:
        for key, value in self.initialize().items():
            if key == "history":
                state.setdefault(key, [])
            else:
                state.setdefault(key, value)

    @staticmethod
    def _severity(events: list[dict], incident: str, area: str, asset: str) -> float:
        severity = {"Small": 0.18, "Medium": 0.42, "Large": 0.78}
        return max((severity.get(event.get("severity"), 0.0) for event in events
                    if event.get("name") == incident
                    and event.get("area") in (area, "Whole factory")
                    and (event.get("area") == "Whole factory"
                         or event.get("target_asset") in (None, asset))), default=0.0)

    def advance(self, state: dict, sim_seconds: int, events: list[dict], *,
                product_demand_lpm: float, water_delivery_m3h: float = 16.392,
                water_turbidity_ntu: float = 4.0, water_chlorine_mg_l: float = 0.45,
                power_available_fraction: float = 1.0, seed: int = 17) -> None:
        self.migrate(state)
        dt_s = max(0, sim_seconds - state["sim_seconds"])
        dt_min = dt_s / 60.0
        dt_h = dt_s / 3_600.0
        state["sim_seconds"] = sim_seconds
        state["product_demand_lpm"] = max(0.0, product_demand_lpm)
        state["mix_water_delivered_m3h"] = max(0.0, water_delivery_m3h)
        state["water_turbidity_ntu"] = max(0.0, water_turbidity_ntu)
        state["water_chlorine_mg_l"] = max(0.0, water_chlorine_mg_l)
        state["water_quality_permissive"] = (
            state["water_turbidity_ntu"] <= self.MAX_WATER_TURBIDITY_NTU
            and state["water_chlorine_mg_l"] <= self.MAX_WATER_CHLORINE_MG_L)

        base_fault = self._severity(events, "Mechanical degradation", "Ingredient dosing",
                                    "Base dosing pump")
        syrup_fault = self._severity(events, "Mechanical degradation", "Ingredient dosing",
                                     "Syrup dosing pump")
        base_pump_power_fault = self._severity(events, "Utility loss", "Ingredient dosing",
                                               "Base dosing pump")
        syrup_pump_power_fault = self._severity(events, "Utility loss", "Ingredient dosing",
                                                "Syrup dosing pump")
        base_tank_fault = self._severity(events, "Mechanical degradation", "Ingredient dosing",
                                         "Base concentrate tank")
        syrup_tank_fault = self._severity(events, "Mechanical degradation", "Ingredient dosing",
                                          "Syrup tank")
        water_inlet_fault = self._severity(events, "Mechanical degradation", "Ingredient dosing",
                                           "Treated-water inlet")
        mixer_fault = self._severity(events, "Mechanical degradation", "Batch blending", "Mix tank A")
        controller_fault = self._severity(events, "Mechanical degradation", "Batch blending",
                                          "Recipe controller")
        recipe_upset = self._severity(events, "Process upset", "Batch blending", "Recipe controller")
        outlet_fault = self._severity(events, "Mechanical degradation", "Batch blending", "Blend outlet")
        receiving_fault = self._severity(events, "Mechanical degradation", "Bulk receiving",
                                          "Tanker unloading bay")
        receiving_power_fault = self._severity(events, "Utility loss", "Bulk receiving",
                                               "Tanker unloading bay")
        receiving_service = (max(0.0, 1.0 - 0.75 * receiving_fault)
                             * max(0.0, 1.0 - receiving_power_fault / 0.78))
        state["receiving_bay_service_fraction"] = receiving_service
        base_tanker_fault = self._severity(events, "Mechanical degradation", "Bulk receiving",
                                           "Base concentrate tanker")
        syrup_tanker_fault = self._severity(events, "Mechanical degradation", "Bulk receiving",
                                            "Syrup tanker")
        state["base_receiving_rate_kg_h"] = 0.0
        state["syrup_receiving_rate_kg_h"] = 0.0

        # A delivery is a finite tanker load with an order threshold, road lead
        # time, shared receiving bay, and bounded unloading rate. It is never
        # teleported into a process tank. A stopped line can leave a tanker
        # waiting because storage space is still a physical limit.
        material_specs = (
            ("base", 18_000.0, 21_000.0, 600, 12_000.0),
            ("syrup", 9_000.0, 10_000.0, 720, 8_000.0),
        )
        for material, reorder, nominal_load, lead_time, unload_rate in material_specs:
            stock_key = f"{material}_tank_inventory_kg"
            state_key = f"{material}_shipment_state"
            eta_key = f"{material}_shipment_eta_s"
            remaining_key = f"{material}_shipment_remaining_kg"
            load_key = f"{material}_shipment_load_kg"
            unload_key = f"{material}_shipment_unload_rate_kg_h"
            if state[state_key] == "IDLE" and state[stock_key] <= reorder:
                load = min(nominal_load, max(0.0,
                    state[f"{material}_tank_capacity_kg"] - state[stock_key]))
                if load > 1.0:
                    state[state_key] = "EN_ROUTE"
                    state[eta_key] = lead_time
                    state[remaining_key] = load
                    state[load_key] = load
                    state[unload_key] = unload_rate
                    state[f"{material}_shipment_cumulative_ordered"] += 1
            if state[state_key] == "EN_ROUTE":
                state[eta_key] = max(0, state[eta_key] - dt_s)
                if state[eta_key] == 0:
                    state[state_key] = "QUEUED"
            elif state[state_key] == "RETURNING":
                state[eta_key] = max(0, state[eta_key] - dt_s)
                if state[eta_key] == 0:
                    state[state_key] = "IDLE"

        queued = [material for material, *_ in material_specs
                  if state[f"{material}_shipment_state"] in {"QUEUED", "UNLOADING"}]
        active = state.get("receiving_bay_active_material", "NONE")
        if active not in queued:
            active = queued[0] if queued else "NONE"
        state["receiving_bay_active_material"] = active
        state["weighbridge_state"] = "OCCUPIED" if active != "NONE" else "IDLE"
        for material, *_ in material_specs:
            state_key = f"{material}_shipment_state"
            if material != active and state[state_key] == "UNLOADING":
                state[state_key] = "QUEUED"
        for material in ("base", "syrup"):
            tanker_fault = base_tanker_fault if material == "base" else syrup_tanker_fault
            tanker_service = max(0.0, 1.0 - 0.75 * tanker_fault)
            target_speed = (100.0 * receiving_service * tanker_service
                            if material == active else 0.0)
            speed_key = f"{material}_receiving_pump_speed_pct"
            speed = state[speed_key]
            ramp_pct_per_s = 10.0 if target_speed > speed else 30.0
            change = ramp_pct_per_s * dt_s
            state[speed_key] = max(0.0, min(100.0,
                speed + max(-change, min(change, target_speed - speed))))
        if active != "NONE":
            stock_key = f"{active}_tank_inventory_kg"
            room_kg = max(0.0, state[f"{active}_tank_capacity_kg"] - state[stock_key])
            tanker_fault = base_tanker_fault if active == "base" else syrup_tanker_fault
            tanker_service = max(0.0, 1.0 - 0.75 * tanker_fault)
            pump_speed = state[f"{active}_receiving_pump_speed_pct"]
            can_unload = pump_speed > 0.1 and room_kg > 1e-6
            state[f"{active}_shipment_state"] = "UNLOADING" if can_unload else "QUEUED"
            unloaded = min(state[f"{active}_shipment_remaining_kg"], room_kg,
                           state[f"{active}_shipment_unload_rate_kg_h"]
                           * pump_speed / 100.0 * dt_h)
            state[stock_key] += unloaded
            state[f"{active}_shipment_remaining_kg"] -= unloaded
            state[f"{active}_shipment_cumulative_received_kg"] += unloaded
            state[f"{active}_receiving_rate_kg_h"] = unloaded / dt_h if dt_h > 0 else 0.0
            if state[f"{active}_shipment_remaining_kg"] <= 1e-6:
                state[f"{active}_shipment_remaining_kg"] = 0.0
                state[f"{active}_shipment_state"] = "RETURNING"
                state[f"{active}_shipment_eta_s"] = (600 if active == "base" else 720)
                state["receiving_bay_active_material"] = "NONE"
        utility_fault = max(
            self._severity(events, "Utility loss", "Batch blending", "Mix tank A"),
            self._severity(events, "Utility loss", "Batch blending", "Recipe controller"))
        utility_fraction = max(0.0, 1.0 - utility_fault / 0.78)
        state["mixer_power_fraction"] = max(0.0, min(1.0, power_available_fraction)) * utility_fraction

        demand = state["product_demand_lpm"]
        outlet_capacity = 300.0 * max(0.0, 1.0 - 0.70 * outlet_fault)
        expected_product_draw = min(demand, outlet_capacity)
        fill_bias = max(-20.0, min(50.0,
            (self.MIX_TANK_TARGET_L - state["mix_tank_inventory_l"]) / 30.0))
        mixer_request = max(0.0, min(self.MIXER_CAPACITY_LPM,
                                     demand + fill_bias))
        tank_headroom_rate = ((self.MIX_TANK_CAPACITY_L - state["mix_tank_inventory_l"])
                              / dt_min + expected_product_draw if dt_min > 0 else mixer_request)
        mixer_request = min(mixer_request, max(0.0, tank_headroom_rate))
        mixer_capacity = self.MIXER_CAPACITY_LPM * state["mixer_power_fraction"]
        mixer_capacity *= max(0.0, 1.0 - 0.70 * mixer_fault)
        mixer_capacity *= max(0.0, 1.0 - 0.70 * controller_fault)
        base_power_fraction = max(0.0, 1.0 - base_pump_power_fault / 0.78)
        syrup_power_fraction = max(0.0, 1.0 - syrup_pump_power_fault / 0.78)
        inlet_service_fraction = max(0.0, 1.0 - 0.70 * water_inlet_fault)
        quality_inlet_fraction = 1.0 if state["water_quality_permissive"] else 0.0
        base_pump_capacity = (mixer_request * max(0.0, 1.0 - 0.70 * base_fault)
                              * base_power_fraction)
        syrup_pump_capacity = (mixer_request * max(0.0, 1.0 - 0.70 * syrup_fault)
                               * syrup_power_fraction)
        base_leak_rate_kg_h = state["base_tank_capacity_kg"] * 0.01 * base_tank_fault
        syrup_leak_rate_kg_h = state["syrup_tank_capacity_kg"] * 0.01 * syrup_tank_fault
        state["base_tank_leak_kg_h"] = base_leak_rate_kg_h
        state["syrup_tank_leak_kg_h"] = syrup_leak_rate_kg_h
        base_inventory_for_batch = max(0.0,
            state["base_tank_inventory_kg"] - base_leak_rate_kg_h * dt_h)
        syrup_inventory_for_batch = max(0.0,
            state["syrup_tank_inventory_kg"] - syrup_leak_rate_kg_h * dt_h)

        water_input_lpm = state["mix_water_delivered_m3h"] * 1_000.0 / 60.0
        water_rate_lpm = water_input_lpm * inlet_service_fraction * quality_inlet_fraction
        state["mix_water_received_m3h"] = (state["mix_water_delivered_m3h"]
            * inlet_service_fraction * quality_inlet_fraction)
        state["water_bypass_flow_m3h"] = max(0.0,
            state["mix_water_delivered_m3h"] - state["mix_water_received_m3h"])
        water_available_rate_lpm = (state["mix_water_inventory_l"] / dt_min + water_rate_lpm
                                    if dt_min > 0 else mixer_request * self.WATER_L_PER_L)
        available_base_rate = (base_inventory_for_batch / (self.BASE_KG_PER_L * dt_min)
                               if dt_min > 0 else mixer_request)
        available_syrup_rate = (syrup_inventory_for_batch / (self.SYRUP_KG_PER_L * dt_min)
                                if dt_min > 0 else mixer_request)
        raw_capacity = min(mixer_request, mixer_capacity,
                           water_available_rate_lpm / self.WATER_L_PER_L,
                           base_pump_capacity, syrup_pump_capacity,
                           available_base_rate, available_syrup_rate)
        # Each batch is accepted only when the complete recipe can be met; the
        # ratio controllers do not substitute water for a depleted ingredient.
        state["recipe_ratio_error_pct"] = recipe_upset * 5.0
        state["recipe_permissive"] = state["recipe_ratio_error_pct"] <= 3.0
        mixing_rate = raw_capacity if state["recipe_permissive"] else 0.0
        base_used_kg = mixing_rate * self.BASE_KG_PER_L * dt_min
        syrup_used_kg = mixing_rate * self.SYRUP_KG_PER_L * dt_min
        water_used_l = mixing_rate * self.WATER_L_PER_L * dt_min
        if dt_min > 0:
            water_available_l = (state["mix_water_inventory_l"]
                                 + water_rate_lpm * dt_min)
            water_used_l = mixing_rate * self.WATER_L_PER_L * dt_min
            water_inventory_unbounded = water_available_l - water_used_l
            water_overflow_l = max(0.0, water_inventory_unbounded - self.WATER_BUFFER_CAPACITY_L)
            state["mix_water_inventory_l"] = max(0.0, min(
                self.WATER_BUFFER_CAPACITY_L, water_inventory_unbounded))
            state["cumulative_water_delivery_m3"] += water_rate_lpm * dt_min / 1_000.0
            state["cumulative_water_bypass_m3"] += (
                state["water_bypass_flow_m3h"] * dt_h)
            if not state["water_quality_permissive"]:
                state["cumulative_water_quality_reject_m3"] += (
                    state["mix_water_delivered_m3h"] * dt_h)
            state["cumulative_water_overflow_l"] += water_overflow_l
            state["cumulative_mixed_l"] = state.get("cumulative_mixed_l", 0.0) + mixing_rate * dt_min
            state["cumulative_water_m3"] += water_used_l / 1_000.0
            available_product_l = state["mix_tank_inventory_l"] + mixing_rate * dt_min
            product_feed_lpm = min(demand, outlet_capacity, available_product_l / dt_min)
            product_used_l = product_feed_lpm * dt_min
            state["mix_tank_inventory_l"] = max(0.0, min(
                self.MIX_TANK_CAPACITY_L, available_product_l - product_used_l))
            state["base_tank_inventory_kg"] = max(0.0,
                state["base_tank_inventory_kg"] - base_used_kg - base_leak_rate_kg_h * dt_h)
            state["syrup_tank_inventory_kg"] = max(0.0,
                state["syrup_tank_inventory_kg"] - syrup_used_kg - syrup_leak_rate_kg_h * dt_h)
            state["cumulative_base_leak_kg"] += base_leak_rate_kg_h * dt_h
            state["cumulative_syrup_leak_kg"] += syrup_leak_rate_kg_h * dt_h
            state["cumulative_product_m3"] += product_used_l / 1_000.0
            state["cumulative_base_kg"] += base_used_kg
            state["cumulative_syrup_kg"] += syrup_used_kg
        else:
            product_feed_lpm = min(demand, state["mix_tank_inventory_l"])

        state["mixer_request_lpm"] = mixer_request
        state["mixer_rate_lpm"] = mixing_rate
        state["product_feed_lpm"] = product_feed_lpm
        state["product_feed_service_fraction"] = (min(1.0, product_feed_lpm / demand)
            if demand > 1e-9 else 1.0)
        state["blend_outlet_service_fraction"] = (min(1.0, product_feed_lpm / demand)
            if demand > 1e-9 else 1.0)
        water_request_lpm = mixer_request * self.WATER_L_PER_L
        water_refill = min(80.0,
            max(0.0, (self.WATER_BUFFER_TARGET_L - state["mix_water_inventory_l"]) / 30.0),
            max(0.0, (self.WATER_BUFFER_CAPACITY_L - state["mix_water_inventory_l"]) / 30.0))
        state["water_service_fraction"] = (min(1.0,
            water_available_rate_lpm / water_request_lpm) if water_request_lpm > 1e-9 else 1.0)
        state["base_dosing_rate_kg_h"] = mixing_rate * self.BASE_KG_PER_L * 60.0
        state["syrup_dosing_rate_kg_h"] = mixing_rate * self.SYRUP_KG_PER_L * 60.0
        state["base_dosing_service_fraction"] = (min(1.0,
            state["base_dosing_rate_kg_h"] / (mixer_request * self.BASE_KG_PER_L * 60.0))
            if mixer_request > 1e-9 else 1.0)
        state["syrup_dosing_service_fraction"] = (min(1.0,
            state["syrup_dosing_rate_kg_h"] / (mixer_request * self.SYRUP_KG_PER_L * 60.0))
            if mixer_request > 1e-9 else 1.0)
        state["recipe_permissive"] = state["recipe_permissive"] and mixing_rate > 1e-9
        state["mix_tank_level_pct"] = 100.0 * state["mix_tank_inventory_l"] / self.MIX_TANK_CAPACITY_L
        state["mix_water_level_pct"] = 100.0 * state["mix_water_inventory_l"] / self.WATER_BUFFER_CAPACITY_L
        state["mix_water_demand_m3h"] = (water_request_lpm + water_refill) * 60.0 / 1_000.0
        state["mix_water_in_transit_m3"] = state["mix_water_delivered_m3h"] / 3_600.0
        receiving_load_kw = (4.0 + 0.0009 * (state["base_receiving_rate_kg_h"]
                             + state["syrup_receiving_rate_kg_h"])
                             if state["receiving_bay_active_material"] != "NONE"
                             and receiving_service > 0 else 0.0)
        state["receiving_load_mw"] = receiving_load_kw / 1_000.0
        state["electric_load_mw"] = (12.0 + 0.055 * mixing_rate + receiving_load_kw) / 1_000.0
        state["requested_load_mw"] = (12.0 + 0.055 * mixer_request + receiving_load_kw) / 1_000.0

        if sim_seconds > 0 and sim_seconds % 15 == 0:
            state["history"].append({key: state[key] for key in (
                "sim_seconds", "mix_tank_level_pct", "product_demand_lpm",
                "product_feed_lpm", "mixer_rate_lpm", "mix_water_demand_m3h",
                "base_tank_inventory_kg", "syrup_tank_inventory_kg")})
            state["history"] = state["history"][-1_800:]

    def telemetry(self, state: dict, seed: int = 17) -> list[dict]:
        rows = []

        def add(asset: str, signal: str, value: float, units: str,
                low: float, high: float, noise: float = 0.002) -> None:
            area = ("Batch blending" if asset in {
                "Mix tank A", "Blend outlet", "Recipe controller"}
                    else "Bulk receiving" if asset in {
                "Receiving weighbridge", "Tanker unloading bay",
                "Base concentrate tanker", "Syrup tanker"}
                    else "Ingredient dosing")
            tag = f"{area.lower().replace(' ', '_')}.{asset.lower().replace(' ', '_')}.{signal}"
            stable = sum((i + 1) * ord(ch) for i, ch in enumerate(tag))
            rng = random.Random(seed * 1_000_003 + state["sim_seconds"] * 9_176 + stable)
            if units not in {"bool", "enum"} and noise:
                value += rng.gauss(0.0, max(1e-9, high - low) * noise)
            digits = 6 if signal in {"gps_latitude", "gps_longitude"} else 3
            rows.append({"tag": tag, "area": area, "unit": asset, "sensor": signal,
                         "units": units, "value": round(max(low, min(high, value)), digits),
                         "quality": "GOOD", "low": low, "high": high,
                         "base": (low + high) / 2.0, "phase": 0.0,
                         "last_change": "Recipe and finite batch inventory model",
                         "dynamic_model": True})

        add("Mix tank A", "inventory", state["mix_tank_inventory_l"], "L", 0,
            self.MIX_TANK_CAPACITY_L)
        add("Mix tank A", "level", state["mix_tank_level_pct"], "%", 0, 100)
        add("Mix tank A", "feed_rate", state["product_feed_lpm"], "L/min", 0, 300)
        add("Mix tank A", "mixer_rate", state["mixer_rate_lpm"], "L/min", 0, 300)
        add("Mix tank A", "mixer_motor_current", 10.0 + state["mixer_rate_lpm"] * 0.12,
            "A", 0, 60)
        add("Mix tank A", "mixer_running", float(state["mixer_rate_lpm"] > 0), "bool", 0, 1, 0.0)
        add("Blend outlet", "product_demand", state["product_demand_lpm"], "L/min", 0, 300)
        add("Blend outlet", "product_feed_service", state["product_feed_service_fraction"] * 100,
            "%", 0, 100, 0.0)
        add("Blend outlet", "service", state["blend_outlet_service_fraction"] * 100, "%", 0, 100, 0.0)
        add("Treated-water inlet", "water_demand", state["mix_water_demand_m3h"], "m³/h", 0, 20)
        add("Treated-water inlet", "water_delivered", state["mix_water_delivered_m3h"], "m³/h", 0, 20)
        add("Treated-water inlet", "water_received", state["mix_water_received_m3h"], "m³/h", 0, 20)
        add("Treated-water inlet", "water_bypass", state["water_bypass_flow_m3h"], "m³/h", 0, 20)
        add("Treated-water inlet", "water_service", state["water_service_fraction"] * 100, "%", 0, 100, 0.0)
        add("Treated-water inlet", "turbidity", state["water_turbidity_ntu"], "NTU", 0, 100)
        add("Treated-water inlet", "chlorine_residual", state["water_chlorine_mg_l"], "mg/L", 0, 5)
        add("Treated-water inlet", "quality_permissive",
            float(state["water_quality_permissive"]), "bool", 0, 1, 0.0)
        add("Treated-water inlet", "buffer_inventory", state["mix_water_inventory_l"], "L", 0,
            self.WATER_BUFFER_CAPACITY_L)
        add("Treated-water inlet", "buffer_level", state["mix_water_level_pct"], "%", 0, 100)
        add("Base concentrate tank", "inventory", state["base_tank_inventory_kg"], "kg", 0,
            state["base_tank_capacity_kg"])
        add("Base concentrate tank", "level", 100 * state["base_tank_inventory_kg"] /
            state["base_tank_capacity_kg"], "%", 0, 100)
        add("Base concentrate tank", "leak_rate", state["base_tank_leak_kg_h"], "kg/h", 0, 500)
        add("Base dosing pump", "flow", state["base_dosing_rate_kg_h"], "kg/h", 0, 2_500)
        add("Base dosing pump", "service", state["base_dosing_service_fraction"] * 100, "%", 0, 100, 0.0)
        add("Syrup tank", "inventory", state["syrup_tank_inventory_kg"], "kg", 0,
            state["syrup_tank_capacity_kg"])
        add("Syrup tank", "level", 100 * state["syrup_tank_inventory_kg"] /
            state["syrup_tank_capacity_kg"], "%", 0, 100)
        add("Syrup tank", "leak_rate", state["syrup_tank_leak_kg_h"], "kg/h", 0, 250)
        add("Syrup dosing pump", "flow", state["syrup_dosing_rate_kg_h"], "kg/h", 0, 1_000)
        add("Syrup dosing pump", "service", state["syrup_dosing_service_fraction"] * 100, "%", 0, 100, 0.0)
        add("Recipe controller", "ratio_error", state["recipe_ratio_error_pct"], "%", -20, 20)
        add("Recipe controller", "permissive", float(state["recipe_permissive"]), "bool", 0, 1, 0.0)
        add("Receiving weighbridge", "occupied", float(state["weighbridge_state"] == "OCCUPIED"),
            "bool", 0, 1, 0.0)
        add("Tanker unloading bay", "service", state["receiving_bay_service_fraction"] * 100,
            "%", 0, 100, 0.0)
        add("Tanker unloading bay", "active_receiving_rate",
            state["base_receiving_rate_kg_h"] + state["syrup_receiving_rate_kg_h"],
            "kg/h", 0, 20_000, 0.0)
        add("Tanker unloading bay", "pump_speed",
            max(state["base_receiving_pump_speed_pct"],
                state["syrup_receiving_pump_speed_pct"]), "%", 0, 100, 0.0)
        add("Tanker unloading bay", "active_material",
            {"NONE": 0, "base": 1, "syrup": 2}.get(
                state["receiving_bay_active_material"], 0), "enum", 0, 2, 0.0)
        state_codes = {"IDLE": 0, "EN_ROUTE": 1, "QUEUED": 2, "UNLOADING": 3,
                       "RETURNING": 4}
        for material, asset in (("base", "Base concentrate tanker"),
                                ("syrup", "Syrup tanker")):
            delivery_state = state[f"{material}_shipment_state"]
            add(asset, "delivery_state", state_codes.get(delivery_state, 0), "enum", 0, 4, 0.0)
            add(asset, "eta_min", state[f"{material}_shipment_eta_s"] / 60.0,
                "min", 0, 240, 0.0)
            add(asset, "load_remaining", state[f"{material}_shipment_remaining_kg"],
                "kg", 0, 25_000, 0.0)
            add(asset, "unload_rate", state[f"{material}_receiving_rate_kg_h"],
                "kg/h", 0, 20_000, 0.0)
            add(asset, "unload_pump_speed",
                state[f"{material}_receiving_pump_speed_pct"], "%", 0, 100, 0.0)
            lead_time = 600 if material == "base" else 720
            progress = (min(1.0, max(0.0,
                1.0 - state[f"{material}_shipment_eta_s"] / lead_time))
                        if delivery_state == "EN_ROUTE" else
                        min(1.0, max(0.0, state[f"{material}_shipment_eta_s"] / lead_time))
                        if delivery_state == "RETURNING" else
                        1.0 if delivery_state in {"QUEUED", "UNLOADING"} else 0.0)
            route_position = progress * (len(self.SUPPLIER_ROUTES) - 1)
            route_segment = min(len(self.SUPPLIER_ROUTES) - 2, int(route_position))
            segment_fraction = route_position - route_segment
            start = self.SUPPLIER_ROUTES[route_segment]
            end = self.SUPPLIER_ROUTES[route_segment + 1]
            latitude = start[0] + (end[0] - start[0]) * segment_fraction
            longitude = start[1] + (end[1] - start[1]) * segment_fraction
            speed = 30.0 if delivery_state in {"EN_ROUTE", "RETURNING"} else 0.0
            add(asset, "gps_latitude", latitude, "°", -24.4, -24.2, 0.0)
            add(asset, "gps_longitude", longitude, "°", -69.2, -68.9, 0.0)
            add(asset, "gps_speed", speed, "km/h", 0, 60, 0.0)
        return rows

    @staticmethod
    def public_state(state: dict) -> dict:
        return {key: value for key, value in state.items() if key != "history"}
