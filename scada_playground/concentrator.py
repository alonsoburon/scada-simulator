"""Compact dynamic model of an open-pit copper concentrator.

Balances are intentionally explicit. This is a training model, not a design
tool: it represents buffering, equipment limits, grind, water, power, and metal
recovery at a one-second supervisory timescale.
"""
from __future__ import annotations

import math
import random


class CopperConcentrator:
    AREA_ASSETS = {
        "Open-pit mining": ["Pit sector A"],
        "Crushing and screening": ["Primary crusher", "Ore conveyors"],
        "Grinding and flotation": ["SAG mill", "Ball mill 01", "Cyclone cluster", "Flotation cells"],
        "Concentrate handling": ["Concentrate thickener", "Filter press"],
        "Tailings and reclaim": ["Tailings thickener", "Tailings pump station A"],
    }
    STAGES = ("crushing", "sag_mill", "ball_mill", "cyclone", "flotation",
              "concentrate_thickener", "filter_press", "tailings_thickener", "tailings_pump")
    SIGNALS = (
        ("ore_rate", "t/h", 0, 2_000), ("motor_power", "MW", 0, 30),
        ("motor_current", "A", 0, 2_500), ("vibration_rms", "mm/s", 0, 30),
        ("equipment_running", "bool", 0, 1),
    )

    def initialize(self) -> dict:
        return {
            "sim_seconds": 0, "rom_stockpile_t": 18_000.0, "rom_feed_curtailed_t": 0.0,
            "initial_rom_stockpile_t": 18_000.0, "cumulative_rom_received_t": 0.0,
            "cumulative_rom_discharge_t": 0.0, "initial_crushed_stockpile_t": 3_200.0,
            "cumulative_crushed_ore_t": 0.0, "cumulative_sag_feeder_t": 0.0,
            "initial_sag_bin_t": 410.0, "cumulative_sag_processed_t": 0.0,
            "rom_feed_curtailed_tph": 0.0,
            "rom_delivery_window_t": [], "rom_curtailed_window_t": [],
            "crushed_stockpile_t": 3_200.0,
            "sag_bin_t": 410.0, "tailings_thickener_inventory_t": 1_100.0,
            "tailings_thickener_capacity_t": 3_000.0,
            "concentrate_thickener_inventory_t": 28.0,
            "concentrate_thickener_capacity_t": 120.0,
            "filter_press_phase": "FILLING", "filter_press_cycle_s": 0,
            "filter_press_batch_t": 0.0, "filter_press_batch_capacity_t": 3.2,
            "filter_press_cycle_count": 0,
            "concentrate_cake_inventory_t": 90.0, "concentrate_cake_capacity_t": 500.0,
            "cumulative_concentrate_produced_t": 0.0,
            "cumulative_concentrate_shipped_t": 0.0,
            "cumulative_tailings_received_t": 0.0,
            "cumulative_tailings_pumped_t": 0.0,
            "rom_feed_tph": 1_180.0, "crusher_feed_tph": 1_080.0, "crushed_ore_tph": 1_030.0,
            "plant_feed_tph": 870.0, "sag_feed_tph": 870.0, "ball_circuit_feed_tph": 2_175.0,
            "cyclone_overflow_tph": 870.0, "tailings_tph": 849.0, "concentrate_tph": 21.0,
            "copper_feed_grade_pct": 0.62, "copper_recovery_pct": 88.0,
            "concentrate_grade_pct": 25.0, "p80_microns": 150.0,
            "ore_hardness_kwh_t": 17.0, "ore_hardness_factor": 1.0,
            "water_makeup_m3h": 452.0, "gross_process_water_m3h": 783.0,
            "concentrator_reclaim_m3h": 331.0, "tailings_reclaim_water_m3h": 326.0,
            "concentrate_thickener_overflow_m3h": 5.0, "filter_press_filtrate_m3h": 0.0,
            "filter_press_batch_filtrate_inventory_m3": 0.0,
            "tailings_water_feed_m3h": 765.0, "tailings_underflow_solids_pct": 68.0,
            "concentrate_water_feed_m3h": 18.0, "water_specific_m3_t": 0.90,
            "water_model_revision": 2,
            "water_available_fraction": 1.0, "water_recovery_pct": 42.0,
            "fresh_water_shortage_m3h": 0.0,
            "sag_power_mw": 15.0, "ball_power_mw": 8.7, "crusher_power_mw": 2.2,
            "flotation_air_nm3_min": 540.0, "reagent_l_h": 245.0,
            "crusher_condition": 1.0, "sag_condition": 1.0, "ball_condition": 1.0,
            "flotation_condition": 1.0, "tailings_condition": 1.0,
            "concentrate_condition": 1.0,
            "crusher_vibration_mm_s": 2.4, "sag_vibration_mm_s": 2.8,
            "ball_vibration_mm_s": 2.4, "flotation_vibration_mm_s": 1.8,
            "tailings_vibration_mm_s": 2.1, "crushing_running": 1.0,
            "sag_running": 1.0, "flotation_running": 1.0, "measurement_drift": 0.0,
            "ball_running": 1.0, "crusher_motor_running": 1.0, "conveyors_motor_running": 1.0,
            "sag_motor_running": 1.0, "ball_motor_running": 1.0,
            "flotation_motor_running": 1.0, "tailings_motor_running": 1.0,
            "tailings_thickener_motor_running": 1.0,
            "concentrate_thickener_motor_running": 1.0, "filter_press_motor_running": 1.0,
            "filter_press_feed_tph": 0.0, "filter_press_cake_tph": 0.0,
            "concentrate_shipped_tph": 0.0, "tailings_thickener_feed_tph": 0.0,
            "history": [], "noise_seed": 17,
        }

    def migrate(self, state: dict) -> None:
        """Add downstream-circuit state when a browser session predates this model."""
        keys = ("tailings_thickener_capacity_t", "concentrate_thickener_inventory_t",
                    "concentrate_thickener_capacity_t", "filter_press_phase",
                    "filter_press_cycle_s", "filter_press_batch_t",
                    "filter_press_batch_capacity_t", "filter_press_cycle_count",
                    "concentrate_cake_inventory_t", "concentrate_cake_capacity_t",
                    "cumulative_concentrate_produced_t", "cumulative_concentrate_shipped_t",
                    "cumulative_tailings_received_t", "cumulative_tailings_pumped_t",
                    "ball_running", "crusher_motor_running", "conveyors_motor_running",
                    "sag_motor_running", "ball_motor_running", "flotation_motor_running",
                    "tailings_motor_running", "tailings_thickener_motor_running",
                    "concentrate_thickener_motor_running", "filter_press_motor_running",
                    "filter_press_feed_tph", "filter_press_cake_tph",
                    "concentrate_shipped_tph", "tailings_thickener_feed_tph",
                    "concentrate_condition", "gross_process_water_m3h",
                    "concentrator_reclaim_m3h", "tailings_reclaim_water_m3h",
                    "filter_press_filtrate_m3h", "filter_press_batch_filtrate_inventory_m3",
                    "concentrate_thickener_overflow_m3h", "tailings_water_feed_m3h",
                    "tailings_underflow_solids_pct", "concentrate_water_feed_m3h",
                    "water_model_revision")
        if all(key in state for key in keys) and state.get("water_model_revision", 0) >= 2:
            return
        previous_water_revision = state.get("water_model_revision", 0)
        defaults = self.initialize()
        for key in keys:
            state.setdefault(key, defaults[key])
        if previous_water_revision < 2:
            feed_tph = max(0.0, state.get("plant_feed_tph", 0.0))
            concentrate_tph = max(0.0, state.get("concentrate_tph", 0.0))
            tailings_tph = max(0.0, feed_tph - concentrate_tph)
            gross = feed_tph * 0.90
            tailings_fraction = tailings_tph / feed_tph if feed_tph > 1e-9 else 0.0
            tails_water_feed = gross * tailings_fraction
            concentrate_water_feed = gross - tails_water_feed
            underflow_solids = 68.0
            tails_underflow_water = tailings_tph * (100.0 - underflow_solids) / underflow_solids
            tails_return = max(0.0, tails_water_feed - tails_underflow_water) * 0.92
            concentrate_underflow_water = concentrate_tph * (1.0 / 0.60 - 1.0)
            concentrate_overflow = max(0.0, concentrate_water_feed - concentrate_underflow_water) * 0.92
            if state.get("tailings_thickener_motor_running", 1.0) < 0.5:
                tails_return = 0.0
            if state.get("concentrate_thickener_motor_running", 1.0) < 0.5:
                concentrate_overflow = 0.0
            recovered = min(gross, tails_return + concentrate_overflow)
            state.update(water_specific_m3_t=0.90, gross_process_water_m3h=gross,
                         tailings_water_feed_m3h=tails_water_feed,
                         tailings_underflow_solids_pct=underflow_solids,
                         concentrate_water_feed_m3h=concentrate_water_feed,
                         tailings_reclaim_water_m3h=tails_return,
                         concentrate_thickener_overflow_m3h=concentrate_overflow,
                         filter_press_filtrate_m3h=0.0,
                         concentrator_reclaim_m3h=recovered,
                         water_makeup_m3h=max(0.0, gross - recovered),
                         water_recovery_pct=(100.0 * recovered / gross if gross > 1e-9 else 0.0),
                         water_model_revision=2)

    @staticmethod
    def _severity(events: list[dict], name: str, areas: tuple[str, ...]) -> float:
        factors = {"Small": 0.18, "Medium": 0.42, "Large": 0.78}
        return max((factors[event["severity"]] for event in events
                    if event["name"] == name and event["area"] in (*areas, "Whole factory")), default=0.0)

    @staticmethod
    def _smooth(old: float, requested: float, seconds: int = 90) -> float:
        return old + (requested - old) / max(seconds, 1)

    def advance(self, state: dict, sim_seconds: int, events: list[dict], *,
                water_shortage_m3h: float = 0.0, seed: int = 17,
                mine_delivery_t: float | None = None,
                power_available_fraction: float | None = None,
                equipment_available: dict[str, bool] | None = None) -> None:
        """Advance coupled ore, comminution, flotation, water and power balances."""
        self.migrate(state)
        state["sim_seconds"] = sim_seconds
        state["noise_seed"] = seed
        equipment_available = equipment_available or {
            "tailings_pump": True, "tailings_thickener": True,
            "concentrate_thickener": True, "filter_press": True, "flotation": True, "ball_mill": True,
            "sag_mill": True, "conveyors": True, "crusher": True,
        }
        jam = self._severity(events, "Conveyor jam", ("Crushing and screening",))
        upset = self._severity(events, "Process upset", ("Grinding and flotation", "Whole factory"))
        utility = self._severity(events, "Utility loss", ("Mine power network", "Grinding and flotation"))
        measurement = self._severity(events, "Signal drift", ("Grinding and flotation",))
        # Fresh ore carries a gross process-water ratio; thickening and pressing
        # determine what returns. Water shortage constrains throughput separately.
        state["water_specific_m3_t"] = 0.90

        # Ore competency changes slowly: harder ore requires more energy per tonne.
        state["ore_hardness_factor"] = min(1.42, max(0.78,
            1.0 + 0.12 * math.sin(sim_seconds / 1_800) + 0.05 * math.sin(sim_seconds / 317 + 0.8)))
        state["ore_hardness_kwh_t"] = 17.0 * state["ore_hardness_factor"]
        crushing_wear = self._severity(events, "Mechanical degradation", ("Crushing and screening",))
        grinding_wear = self._severity(events, "Mechanical degradation", ("Grinding and flotation",))
        concentrate_wear = self._severity(events, "Mechanical degradation", ("Concentrate handling",))
        tailings_wear = self._severity(events, "Mechanical degradation", ("Tailings and reclaim",))
        state["crusher_condition"] = 1.0 - crushing_wear * 0.55
        state["sag_condition"] = 1.0 - grinding_wear * 0.48
        state["ball_condition"] = 1.0 - grinding_wear * 0.40
        state["flotation_condition"] = 1.0 - grinding_wear * 0.28
        state["tailings_condition"] = 1.0 - tailings_wear * 0.65
        state["concentrate_condition"] = 1.0 - concentrate_wear * 0.35

        # ROM receiving is finite storage. Integrated runs receive discrete truck
        # payloads; standalone model experiments retain a smooth mine-source input.
        scheduled_rom_feed = 1_120.0 * (1 + 0.07 * math.sin(sim_seconds / 140) + 0.025 * math.sin(sim_seconds / 23))
        jam_factor = 0.0 if jam >= 0.78 else 1.0 - jam
        crusher_running = equipment_available.get("crusher", True) and equipment_available.get("conveyors", True)
        crusher_capacity = (1_460.0 * state["crusher_condition"] * jam_factor) if crusher_running else 0.0
        plant_target = 900.0 * (1.0 - upset * 0.18)
        water_factor = min(1.0, max(0.0, 1.0 - water_shortage_m3h / max(plant_target * 0.30, 1.0)))
        power_factor = (max(0.0, min(1.0, power_available_fraction))
                        if power_available_fraction is not None else max(0.0, 1.0 - utility * 0.82))
        dt_h = 1.0 / 3600.0
        tailings_capacity_t = state["tailings_thickener_capacity_t"]
        tailings_sink_ready = (equipment_available.get("tailings_thickener", True)
                               and equipment_available.get("tailings_pump", True))
        tailings_backpressure = 1.0
        if not tailings_sink_ready:
            tailings_backpressure = min(1.0, max(0.0,
                (0.94 * tailings_capacity_t - state["tailings_thickener_inventory_t"])
                / max(0.14 * tailings_capacity_t, 1.0)))
        concentrate_capacity_t = state["concentrate_thickener_capacity_t"]
        concentrate_sink_ready = (equipment_available.get("concentrate_thickener", True)
                                  and equipment_available.get("filter_press", True))
        concentrate_backpressure = 1.0
        if not concentrate_sink_ready:
            concentrate_backpressure = min(1.0, max(0.0,
                (0.94 * concentrate_capacity_t - state["concentrate_thickener_inventory_t"])
                / max(0.14 * concentrate_capacity_t, 1.0)))
        plant_target *= min(water_factor, power_factor, tailings_backpressure,
                            concentrate_backpressure)

        sag_power_cap = (21.0 * state["sag_condition"] * power_factor
                         if equipment_available.get("sag_mill", True) else 0.0)
        ball_power_cap = (16.5 * state["ball_condition"] * power_factor
                          if equipment_available.get("ball_mill", True) else 0.0)
        sag_capacity = sag_power_cap * 1_000.0 / max(state["ore_hardness_kwh_t"] * 1.04, 1.0)
        cyclone_recirculating_load = min(2.15, max(1.4, 2.0 + (state["p80_microns"] - 150.0) / 180.0))
        ball_capacity = ball_power_cap * 1_000.0 / (8.6 * state["ore_hardness_factor"] * cyclone_recirculating_load)
        flotation_capacity = (1_240.0 * state["flotation_condition"] * (0.90 + 0.10 * water_factor)
                              if equipment_available.get("flotation", True) else 0.0)
        requested_feed = min(plant_target, sag_capacity, ball_capacity, flotation_capacity)

        rom_discharge = min(state["rom_stockpile_t"] * 3600.0, crusher_capacity, 1_250.0)
        crushed_rate = min(rom_discharge, crusher_capacity) if jam < 0.95 else 0.0
        rom_space_t = max(0.0, 45_000.0 - state["rom_stockpile_t"])
        if mine_delivery_t is None:
            rom_feed = scheduled_rom_feed
            rom_received = min(rom_feed, rom_space_t * 3600.0) / 3600.0
            curtailed_t = max(0.0, rom_feed * dt_h - rom_received)
            state["rom_feed_tph"] = self._smooth(state["rom_feed_tph"], rom_received * 3600.0, 30)
            state["rom_feed_curtailed_tph"] = curtailed_t * 3600.0
        else:
            mine_delivery_t = max(0.0, mine_delivery_t)
            rom_received = min(mine_delivery_t, rom_space_t)
            curtailed_t = mine_delivery_t - rom_received
            state["rom_delivery_window_t"].append(rom_received)
            state["rom_curtailed_window_t"].append(curtailed_t)
            state["rom_delivery_window_t"] = state["rom_delivery_window_t"][-600:]
            state["rom_curtailed_window_t"] = state["rom_curtailed_window_t"][-600:]
            rom_feed = sum(state["rom_delivery_window_t"]) * 6.0
            state["rom_feed_tph"] = self._smooth(state["rom_feed_tph"], rom_feed, 30)
            state["rom_feed_curtailed_tph"] = (sum(state["rom_curtailed_window_t"])
                                                * 3_600.0 / max(1, len(state["rom_curtailed_window_t"])))
        sag_feeder_enabled = (equipment_available.get("sag_mill", True)
                              and equipment_available.get("ball_mill", True))
        sag_feeder_rate = (min(900.0, state["crushed_stockpile_t"] * 3600.0,
                               max(0.0, 900.0 - state["sag_bin_t"]) * 3600.0)
                           if sag_feeder_enabled else 0.0)
        sag_available_t = state["sag_bin_t"] + sag_feeder_rate / 3600.0
        sag_rate = min(requested_feed, sag_available_t * 3600.0)
        ball_rate = min(sag_rate * cyclone_recirculating_load, ball_power_cap * 1_000.0 / max(8.6 * state["ore_hardness_factor"], 1))
        cyclone_overflow = min(sag_rate, ball_rate / cyclone_recirculating_load, flotation_capacity)

        state["rom_feed_curtailed_t"] += curtailed_t
        state["cumulative_rom_received_t"] += rom_received
        state["cumulative_rom_discharge_t"] += rom_discharge * dt_h
        state["cumulative_crushed_ore_t"] += crushed_rate * dt_h
        state["cumulative_sag_feeder_t"] += sag_feeder_rate * dt_h
        state["cumulative_sag_processed_t"] += sag_rate * dt_h
        state["rom_stockpile_t"] = min(45_000.0, max(0.0,
            state["rom_stockpile_t"] + rom_received - rom_discharge * dt_h))
        state["crushed_stockpile_t"] = min(8_000.0, max(0.0,
            state["crushed_stockpile_t"] + (crushed_rate - sag_feeder_rate) * dt_h))
        state["sag_bin_t"] = min(900.0, max(0.0,
            state["sag_bin_t"] + (sag_feeder_rate - sag_rate) * dt_h))

        # Grind responds with residence-time lag. Harder ore and reduced mill power
        # make product coarser; cyclone bypass/efficiency moves the cut size.
        target_p80 = 125.0 + 240.0 * (state["ore_hardness_factor"] - 1.0) + max(0.0, cyclone_recirculating_load - 1.8) * 28
        target_p80 += max(0.0, 1.0 - power_factor) * 45 + max(0.0, 1.0 - state["ball_condition"]) * 30
        state["p80_microns"] = self._smooth(state["p80_microns"], min(300.0, max(70.0, target_p80)), 240)

        # A copper mass balance estimates concentrate tonnage from feed grade,
        # recovery, and product grade; tailings carry the remaining solids/copper.
        grind_recovery = 91.0 - max(0.0, state["p80_microns"] - 110.0) * 0.045
        water_recovery_penalty = (1.0 - water_factor) * 17.0
        flotation_air = 540.0 * (1 - grinding_wear * 0.3) * power_factor
        reagent = 245.0 * (1 + 0.12 * math.sin(sim_seconds / 600))
        recovery = min(94.0, max(40.0, (grind_recovery - water_recovery_penalty) * state["flotation_condition"]
                               * (0.96 + 0.04 * min(1.0, flotation_air / 500.0)) - upset * 12.0))
        concentrate = cyclone_overflow * (0.0062 * recovery / 100.0) / 0.25
        tailings = max(0.0, cyclone_overflow - concentrate)
        state["cumulative_concentrate_produced_t"] += concentrate * dt_h
        state["cumulative_tailings_received_t"] += tailings * dt_h

        # Concentrate thickening buffers feed to a cyclic filter press. Filling
        # adds cake, squeezing pauses feed, and cake discharge is a short pulse.
        state["filter_press_feed_tph"] = 0.0
        state["filter_press_cake_tph"] = 0.0
        state["filter_press_filtrate_m3h"] = 0.0
        press_ready = (equipment_available.get("filter_press", True)
                       and equipment_available.get("concentrate_thickener", True))
        if press_ready:
            if state["filter_press_phase"] == "FILLING":
                remaining_batch_t = max(0.0, state["filter_press_batch_capacity_t"]
                                        - state["filter_press_batch_t"])
                available_concentrate_t = (state["concentrate_thickener_inventory_t"]
                                           + concentrate * dt_h - 10.0)
                press_feed = min(42.0 * state["concentrate_condition"],
                                 max(0.0, available_concentrate_t) / dt_h,
                                 remaining_batch_t / dt_h)
                state["filter_press_feed_tph"] = press_feed
                state["filter_press_batch_t"] += press_feed * dt_h
                if state["filter_press_batch_t"] >= state["filter_press_batch_capacity_t"] - 1e-9:
                    state["filter_press_phase"] = "SQUEEZING"
                    state["filter_press_cycle_s"] = 0
            elif state["filter_press_phase"] == "SQUEEZING":
                squeeze_seconds_remaining = max(1, 90 - state["filter_press_cycle_s"])
                filtrate_flow = state["filter_press_batch_filtrate_inventory_m3"] * 3_600.0 / squeeze_seconds_remaining
                state["filter_press_filtrate_m3h"] = filtrate_flow
                state["filter_press_batch_filtrate_inventory_m3"] = max(
                    0.0, state["filter_press_batch_filtrate_inventory_m3"] - filtrate_flow * dt_h)
                state["filter_press_cycle_s"] += 1
                if state["filter_press_cycle_s"] >= 90:
                    state["filter_press_phase"] = "DISCHARGING"
                    state["filter_press_cycle_s"] = 0
            elif state["filter_press_phase"] == "DISCHARGING":
                cake_room_t = max(0.0, state["concentrate_cake_capacity_t"]
                                  - state["concentrate_cake_inventory_t"])
                cake_rate = min(650.0, state["filter_press_batch_t"] / dt_h,
                                (cake_room_t / dt_h) + 26.0)
                state["filter_press_cake_tph"] = cake_rate
                state["filter_press_batch_t"] = max(0.0,
                    state["filter_press_batch_t"] - cake_rate * dt_h)
                if state["filter_press_batch_t"] <= 1e-9:
                    state["filter_press_batch_t"] = 0.0
                    state["filter_press_phase"] = "FILLING"
                    state["filter_press_cycle_s"] = 0
                    state["filter_press_cycle_count"] += 1

        press_feed = state["filter_press_feed_tph"]
        # Filter-cake moisture is 15% by mass. Water above that retained amount
        # accumulates with the batch and leaves only during the squeeze phase.
        press_feed_water_m3h = press_feed * (1.0 / 0.60 - 1.0)
        cake_retained_water_m3h = press_feed * (1.0 / 0.85 - 1.0)
        state["filter_press_batch_filtrate_inventory_m3"] += max(
            0.0, press_feed_water_m3h - cake_retained_water_m3h) * dt_h
        state["concentrate_thickener_inventory_t"] = min(concentrate_capacity_t, max(0.0,
            state["concentrate_thickener_inventory_t"] + (concentrate - press_feed) * dt_h))
        concentrate_shipped = min(26.0,
            (state["concentrate_cake_inventory_t"] + state["filter_press_cake_tph"] * dt_h) / dt_h)
        state["concentrate_shipped_tph"] = concentrate_shipped
        state["concentrate_cake_inventory_t"] = min(state["concentrate_cake_capacity_t"], max(0.0,
            state["concentrate_cake_inventory_t"]
            + (state["filter_press_cake_tph"] - concentrate_shipped) * dt_h))
        state["cumulative_concentrate_shipped_t"] += concentrate_shipped * dt_h

        # Tailings are a separate solids stream. A stopped thickener/pump train
        # fills its finite inventory, which eventually backpressures flotation.
        tailings_pump_capacity = (1_400.0 * state["tailings_condition"]
                                  if tailings_sink_ready else 0.0)
        pumpable_tailings_t = (state["tailings_thickener_inventory_t"] + tailings * dt_h)
        tailings_inventory_error = max(0.0, state["tailings_thickener_inventory_t"] - 1_100.0)
        tailings_pump_request = tailings + tailings_inventory_error * 0.75
        tailings_pumped = min(tailings_pump_capacity, tailings_pump_request,
                              pumpable_tailings_t / dt_h)
        state["tailings_thickener_feed_tph"] = tailings
        state["tailings_thickener_inventory_t"] = min(tailings_capacity_t, max(0.0,
            state["tailings_thickener_inventory_t"] + (tailings - tailings_pumped) * dt_h))
        state["cumulative_tailings_pumped_t"] += tailings_pumped * dt_h

        state["crusher_feed_tph"] = self._smooth(state["crusher_feed_tph"], crushed_rate, 45)
        state["crushed_ore_tph"] = state["crusher_feed_tph"]
        state["plant_feed_tph"] = self._smooth(state["plant_feed_tph"], cyclone_overflow, 75)
        state["sag_feed_tph"] = self._smooth(state["sag_feed_tph"], sag_rate, 65)
        state["ball_circuit_feed_tph"] = self._smooth(state["ball_circuit_feed_tph"], ball_rate, 75)
        state["cyclone_overflow_tph"] = state["plant_feed_tph"]
        state["tailings_tph"] = self._smooth(state["tailings_tph"], tailings_pumped, 60)
        state["concentrate_tph"] = self._smooth(state["concentrate_tph"], concentrate, 120)
        state["copper_recovery_pct"] = self._smooth(state["copper_recovery_pct"], recovery, 180)
        state["concentrate_grade_pct"] = min(30.0, max(15.0, 25.0 - max(0.0, state["p80_microns"] - 150) * 0.03))

        sag_power = min(sag_power_cap, sag_rate * state["ore_hardness_kwh_t"] * 1.04 / 1_000)
        ball_power = min(ball_power_cap, ball_rate * 8.6 * state["ore_hardness_factor"] / 1_000)
        state["sag_power_mw"] = self._smooth(state["sag_power_mw"], sag_power, 15)
        state["ball_power_mw"] = self._smooth(state["ball_power_mw"], ball_power, 15)
        state["crusher_power_mw"] = self._smooth(state["crusher_power_mw"], 2.2 * (crushed_rate / 1_200), 10)
        gross_process_water = cyclone_overflow * state["water_specific_m3_t"]
        total_solids = tailings + concentrate
        tails_fraction = tailings / total_solids if total_solids > 1e-9 else 0.0
        tailings_water_feed = gross_process_water * tails_fraction
        concentrate_water_feed = gross_process_water - tailings_water_feed
        tailings_underflow_solids = min(70.0, max(55.0, 68.0 - upset * 10.0))
        tails_underflow_water = tailings * (100.0 - tailings_underflow_solids) / tailings_underflow_solids
        tailings_overflow_water = max(0.0, tailings_water_feed - tails_underflow_water)
        tailings_return = (tailings_overflow_water * max(0.0, 0.92 - upset * 0.24)
                           if equipment_available.get("tailings_thickener", True) else 0.0)
        concentrate_underflow_water = concentrate * (1.0 / 0.60 - 1.0)
        concentrate_overflow = max(0.0, concentrate_water_feed - concentrate_underflow_water)
        concentrate_thickener_return = (concentrate_overflow * 0.92
                                        if equipment_available.get("concentrate_thickener", True) else 0.0)
        filtrate_return = (state["filter_press_filtrate_m3h"]
                           if equipment_available.get("filter_press", True) else 0.0)
        recovered_water = min(gross_process_water,
                              tailings_return + concentrate_thickener_return + filtrate_return)
        state["gross_process_water_m3h"] = gross_process_water
        state["tailings_reclaim_water_m3h"] = tailings_return
        state["tailings_water_feed_m3h"] = tailings_water_feed
        state["tailings_underflow_solids_pct"] = tailings_underflow_solids
        state["concentrate_water_feed_m3h"] = concentrate_water_feed
        state["concentrate_thickener_overflow_m3h"] = concentrate_thickener_return
        state["filter_press_filtrate_m3h"] = filtrate_return
        state["concentrator_reclaim_m3h"] = recovered_water
        state["water_makeup_m3h"] = max(0.0, gross_process_water - recovered_water)
        state["fresh_water_shortage_m3h"] = max(0.0, water_shortage_m3h)
        state["water_available_fraction"] = water_factor
        state["water_recovery_pct"] = (100.0 * recovered_water / gross_process_water
                                       if gross_process_water > 1e-9 else 0.0)
        state["flotation_air_nm3_min"] = flotation_air
        state["reagent_l_h"] = reagent

        # Fault signatures show up as actual process effects plus equipment load,
        # vibration, interlock status, and degraded mass flow.
        state["crushing_running"] = float(crushed_rate > 5 and crusher_running)
        state["sag_running"] = float(sag_rate > 5 and equipment_available.get("sag_mill", True))
        state["ball_running"] = float(ball_rate > 5 and equipment_available.get("ball_mill", True))
        state["flotation_running"] = float(cyclone_overflow > 5 and equipment_available.get("flotation", True))
        state["crusher_motor_running"] = float(equipment_available.get("crusher", True))
        state["conveyors_motor_running"] = float(equipment_available.get("conveyors", True))
        state["sag_motor_running"] = float(equipment_available.get("sag_mill", True))
        state["ball_motor_running"] = float(equipment_available.get("ball_mill", True))
        state["flotation_motor_running"] = float(equipment_available.get("flotation", True))
        state["tailings_motor_running"] = float(equipment_available.get("tailings_pump", True))
        state["crusher_vibration_mm_s"] = 2.4 + crushing_wear * 12 + jam * 5
        state["sag_vibration_mm_s"] = 2.8 + grinding_wear * 10
        state["ball_vibration_mm_s"] = 2.4 + grinding_wear * 9
        state["flotation_vibration_mm_s"] = 1.8 + grinding_wear * 5
        state["tailings_vibration_mm_s"] = 2.1 + tailings_wear * 13
        state["concentrate_condition"] = 1.0 - concentrate_wear * 0.35
        state["tailings_thickener_motor_running"] = float(equipment_available.get("tailings_thickener", True))
        state["concentrate_thickener_motor_running"] = float(equipment_available.get("concentrate_thickener", True))
        state["filter_press_motor_running"] = float(equipment_available.get("filter_press", True))

        if sim_seconds % 15 == 0:
            state["history"].append({"time": sim_seconds, "rom_stockpile_t": state["rom_stockpile_t"],
                "crushed_stockpile_t": state["crushed_stockpile_t"], "sag_bin_t": state["sag_bin_t"],
                "plant_feed_tph": state["plant_feed_tph"],
                "copper_recovery_pct": state["copper_recovery_pct"], "p80_microns": state["p80_microns"],
                "tailings_thickener_inventory_t": state["tailings_thickener_inventory_t"],
                "concentrate_thickener_inventory_t": state["concentrate_thickener_inventory_t"],
                "concentrate_cake_inventory_t": state["concentrate_cake_inventory_t"],
                "water_makeup_m3h": state["water_makeup_m3h"],
                "concentrator_reclaim_m3h": state["concentrator_reclaim_m3h"],
                "power_mw": state["sag_power_mw"] + state["ball_power_mw"] + state["crusher_power_mw"]})
            state["history"] = state["history"][-1_440:]

        # Measurement drift is applied only to the process quality/reading surface,
        # not the plant's internal mass or energy balances.
        state["measurement_drift"] = measurement

    def telemetry(self, state: dict) -> list[dict]:
        """Translate process truth into stable, noisy SCADA tag records."""
        readings = {
            "Open-pit mining": {
                "Pit sector A": {"ore_rate": (state["rom_feed_tph"], "t/h", 0, 2_000),
                    "rom_stockpile": (state["rom_stockpile_t"], "t", 0, 45_000),
                    "mine_feed_curtailed": (state["rom_feed_curtailed_tph"], "t/h", 0, 2_000)},
            },
            "Crushing and screening": {
                "Primary crusher": {"ore_rate": (state["crushed_ore_tph"], "t/h", 0, 2_000),
                    "motor_power": (state["crusher_power_mw"], "MW", 0, 5),
                    "motor_current": (state["crusher_power_mw"] * 31.0, "A", 0, 250),
                    "vibration_rms": (state["crusher_vibration_mm_s"], "mm/s", 0, 30),
                    "equipment_running": (state.get("crusher_motor_running", state["crushing_running"]), "bool", 0, 1)},
                "Ore conveyors": {"ore_rate": (state["crushed_ore_tph"], "t/h", 0, 2_000),
                    "stockpile_inventory": (state["crushed_stockpile_t"], "t", 0, 8_000),
                    "motor_power": (0.45 * state["crushing_running"], "MW", 0, 1),
                    "belt_speed": (3.1 * state["crushing_running"], "m/s", 0, 8),
                    "equipment_running": (state.get("conveyors_motor_running", state["crushing_running"]), "bool", 0, 1)},
            },
            "Grinding and flotation": {
                "SAG mill": {"ore_rate": (state["sag_feed_tph"], "t/h", 0, 2_000),
                    "motor_power": (state["sag_power_mw"], "MW", 0, 30),
                    "motor_current": (state["sag_power_mw"] * 41.0, "A", 0, 2_500),
                    "vibration_rms": (state["sag_vibration_mm_s"], "mm/s", 0, 30),
                    "equipment_running": (state.get("sag_motor_running", state["sag_running"]), "bool", 0, 1),
                    "ore_hardness": (state["ore_hardness_kwh_t"], "kWh/t", 8, 32)},
                "Ball mill 01": {"ore_rate": (state["ball_circuit_feed_tph"], "t/h", 0, 4_000),
                    "motor_power": (state["ball_power_mw"], "MW", 0, 20),
                    "motor_current": (state["ball_power_mw"] * 44.0, "A", 0, 2_500),
                    "vibration_rms": (state["ball_vibration_mm_s"], "mm/s", 0, 30),
                    "equipment_running": (state.get("ball_motor_running", state["ball_running"]), "bool", 0, 1)},
                "Cyclone cluster": {"ore_rate": (state["cyclone_overflow_tph"], "t/h", 0, 2_000),
                    "product_p80": (state["p80_microns"], "µm", 50, 400),
                    "circulating_load": (100 * (state["ball_circuit_feed_tph"] / max(state["sag_feed_tph"], 1) - 1), "%", 0, 300),
                    "equipment_running": (float(state["cyclone_overflow_tph"] > 5), "bool", 0, 1)},
                "Flotation cells": {"ore_rate": (state["plant_feed_tph"], "t/h", 0, 2_000),
                    "copper_recovery": (state["copper_recovery_pct"], "%", 0, 100),
                    "concentrate_grade": (state["concentrate_grade_pct"], "%", 0, 40),
                    "concentrate_rate": (state["concentrate_tph"], "t/h", 0, 100),
                    "air_flow": (state["flotation_air_nm3_min"], "Nm³/min", 0, 1_000),
                    "reagent_rate": (state["reagent_l_h"], "L/h", 0, 1_000),
                    "equipment_running": (state.get("flotation_motor_running", state["flotation_running"]), "bool", 0, 1)},
            },
            "Concentrate handling": {
                "Concentrate thickener": {
                    "feed_rate": (state["concentrate_tph"], "t/h", 0, 100),
                    "tank_level": (8 + state["concentrate_thickener_inventory_t"]
                                   / state["concentrate_thickener_capacity_t"] * 84, "%", 0, 100),
                    "underflow_rate": (state["filter_press_feed_tph"], "t/h", 0, 60),
                    "motor_power": (0.11 * state["concentrate_condition"]
                                    * state["concentrate_thickener_motor_running"], "MW", 0, 1),
                    "equipment_running": (state["concentrate_thickener_motor_running"], "bool", 0, 1)},
                "Filter press": {
                    "feed_rate": (state["filter_press_feed_tph"], "t/h", 0, 60),
                    "cake_rate": (state["filter_press_cake_tph"], "t/h", 0, 700),
                    "cake_stockpile": (state["concentrate_cake_inventory_t"], "t", 0, 500),
                    "cake_moisture": (9.0 + (1 - state["concentrate_condition"]) * 12, "%", 0, 40),
                    "cycle_count": (float(state["filter_press_cycle_count"]), "cycles", 0, 100_000),
                    "equipment_running": (state["filter_press_motor_running"], "bool", 0, 1)},
            },
            "Tailings and reclaim": {
                "Tailings thickener": {"feed_rate": (state["tailings_thickener_feed_tph"], "t/h", 0, 2_000),
                    "tank_level": (state["tailings_thickener_inventory_t"]
                                   / state["tailings_thickener_capacity_t"] * 100, "%", 0, 100),
                    "underflow_rate": (state["tailings_tph"], "t/h", 0, 2_000),
                    "equipment_running": (state["tailings_thickener_motor_running"], "bool", 0, 1)},
                "Tailings pump station A": {"ore_rate": (state["tailings_tph"], "t/h", 0, 2_000),
                    "motor_power": (0.62 * state["tailings_condition"]
                                    * state["tailings_motor_running"], "MW", 0, 2),
                    "motor_current": (62 * state["tailings_condition"]
                                      * state["tailings_motor_running"], "A", 0, 250),
                    "vibration_rms": (state["tailings_vibration_mm_s"], "mm/s", 0, 30),
                    "equipment_running": (state.get("tailings_motor_running", float(state["tailings_tph"] > 5)), "bool", 0, 1)},
            },
        }
        rows = []
        for area, assets in readings.items():
            for asset, signals in assets.items():
                for signal, (truth, units, low, high) in signals.items():
                    tag = f"{area.lower().replace(' ', '_')}.{asset.lower().replace(' ', '_')}.{signal}"
                    stable_id = sum((i + 1) * ord(char) for i, char in enumerate(tag))
                    rng = random.Random(state["noise_seed"] * 1_000_003 + state["sim_seconds"] * 9_176 + stable_id)
                    noise = (0.0 if units in ("bool", "cycles") else
                             rng.gauss(0.0, max(high-low, 1e-9) *
                                       (0.0015 if signal in ("product_p80", "copper_recovery") else 0.002)))
                    value = truth + noise
                    if signal in ("ore_rate", "product_p80"):
                        value *= 1.0 + state.get("measurement_drift", 0.0)
                    rows.append({"tag": tag, "area": area, "unit": asset, "sensor": signal, "units": units,
                        "value": round(min(high, max(low, value)), 3), "quality": "GOOD", "low": low,
                        "high": high, "base": (low + high) / 2, "phase": 0.0,
                        "last_change": "Dynamic concentrator process model"})
        for sensor, value, units, limit, description in (
                ("water_demand", state["gross_process_water_m3h"], "m³/h", 1_200,
                 "Concentrator gross process-water draw"),
                ("water_makeup", state["water_makeup_m3h"], "m³/h", 1_200,
                 "Fresh make-up after process-water recovery"),
                ("water_reclaim_return", state["concentrator_reclaim_m3h"], "m³/h", 1_200,
                 "Recovered water returned to the process tank"),
                ("tailings_reclaim_return", state["tailings_reclaim_water_m3h"], "m³/h", 1_200,
                 "Thickener overflow after underflow water retention"),
                ("concentrate_thickener_overflow", state["concentrate_thickener_overflow_m3h"], "m³/h", 100,
                 "Concentrate thickener overflow to reclaim"),
                ("filter_press_filtrate", state["filter_press_filtrate_m3h"], "m³/h", 100,
                 "Batch filtrate released during the squeeze phase"),
                ("tailings_underflow_solids", state["tailings_underflow_solids_pct"], "%", 100,
                 "Tailings thickener underflow solids target"),
                ("water_recovery", state["water_recovery_pct"], "%", 100,
                 "Measured process-water recovery")):
            rows.append({"tag": f"water_supply_network.concentrator_process.{sensor}",
                         "area": "Water supply network", "unit": "Concentrator process",
                         "sensor": sensor, "units": units, "value": round(value, 2),
                         "quality": "GOOD", "low": 0, "high": limit, "base": limit / 2,
                         "phase": 0.0, "last_change": description})
        return rows


if __name__ == "__main__":
    model = CopperConcentrator()
    state = model.initialize()
    for second in range(1, 3_601):
        model.advance(state, second, [])
    print({key: round(state[key], 2) for key in (
        "plant_feed_tph", "p80_microns", "copper_recovery_pct", "concentrate_tph", "water_makeup_m3h")})
