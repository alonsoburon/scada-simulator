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
        "Concentrate handling": ["Thickener", "Filter press"],
        "Tailings and reclaim": ["Tailings pump station A"],
    }
    STAGES = ("crushing", "sag_mill", "ball_mill", "cyclone", "flotation", "thickener", "filter_press", "tailings_pump")
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
            "rom_feed_tph": 1_180.0, "crusher_feed_tph": 1_080.0, "crushed_ore_tph": 1_030.0,
            "plant_feed_tph": 870.0, "sag_feed_tph": 870.0, "ball_circuit_feed_tph": 2_175.0,
            "cyclone_overflow_tph": 870.0, "tailings_tph": 849.0, "concentrate_tph": 21.0,
            "copper_feed_grade_pct": 0.62, "copper_recovery_pct": 88.0,
            "concentrate_grade_pct": 25.0, "p80_microns": 150.0,
            "ore_hardness_kwh_t": 17.0, "ore_hardness_factor": 1.0,
            "water_makeup_m3h": 260.0, "water_specific_m3_t": 0.30,
            "water_available_fraction": 1.0, "water_recovery_pct": 74.0,
            "fresh_water_shortage_m3h": 0.0,
            "sag_power_mw": 15.0, "ball_power_mw": 8.7, "crusher_power_mw": 2.2,
            "flotation_air_nm3_min": 540.0, "reagent_l_h": 245.0,
            "crusher_condition": 1.0, "sag_condition": 1.0, "ball_condition": 1.0,
            "flotation_condition": 1.0, "tailings_condition": 1.0,
            "crusher_vibration_mm_s": 2.4, "sag_vibration_mm_s": 2.8,
            "ball_vibration_mm_s": 2.4, "flotation_vibration_mm_s": 1.8,
            "tailings_vibration_mm_s": 2.1, "crushing_running": 1.0,
            "sag_running": 1.0, "flotation_running": 1.0, "measurement_drift": 0.0,
            "history": [], "noise_seed": 17,
        }

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
                power_available_fraction: float | None = None) -> None:
        """Advance coupled ore, comminution, flotation, water and power balances."""
        state["sim_seconds"] = sim_seconds
        state["noise_seed"] = seed
        jam = self._severity(events, "Conveyor jam", ("Crushing and screening",))
        mechanical = self._severity(events, "Mechanical degradation",
                                    ("Crushing and screening", "Grinding and flotation", "Tailings and reclaim"))
        upset = self._severity(events, "Process upset", ("Grinding and flotation", "Whole factory"))
        utility = self._severity(events, "Utility loss", ("Mine power network", "Grinding and flotation"))
        measurement = self._severity(events, "Signal drift", ("Grinding and flotation",))

        # Ore competency changes slowly: harder ore requires more energy per tonne.
        state["ore_hardness_factor"] = min(1.42, max(0.78,
            1.0 + 0.12 * math.sin(sim_seconds / 1_800) + 0.05 * math.sin(sim_seconds / 317 + 0.8)))
        state["ore_hardness_kwh_t"] = 17.0 * state["ore_hardness_factor"]
        state["crusher_condition"] = 1.0 - mechanical * 0.55
        state["sag_condition"] = 1.0 - mechanical * 0.48
        state["ball_condition"] = 1.0 - mechanical * 0.40
        state["flotation_condition"] = 1.0 - mechanical * 0.28
        state["tailings_condition"] = 1.0 - mechanical * 0.65

        # ROM receiving is finite storage. Integrated runs receive discrete truck
        # payloads; standalone model experiments retain a smooth mine-source input.
        scheduled_rom_feed = 1_120.0 * (1 + 0.07 * math.sin(sim_seconds / 140) + 0.025 * math.sin(sim_seconds / 23))
        jam_factor = 0.0 if jam >= 0.78 else 1.0 - jam
        crusher_capacity = 1_460.0 * state["crusher_condition"] * jam_factor
        plant_target = 900.0 * (1.0 - upset * 0.18)
        water_factor = min(1.0, max(0.0, 1.0 - water_shortage_m3h / max(plant_target * 0.30, 1.0)))
        power_factor = (max(0.0, min(1.0, power_available_fraction))
                        if power_available_fraction is not None else max(0.0, 1.0 - utility * 0.82))
        plant_target *= min(water_factor, power_factor)

        sag_power_cap = 21.0 * state["sag_condition"] * power_factor
        ball_power_cap = 16.5 * state["ball_condition"] * power_factor
        sag_capacity = sag_power_cap * 1_000.0 / max(state["ore_hardness_kwh_t"] * 1.04, 1.0)
        cyclone_recirculating_load = min(2.15, max(1.4, 2.0 + (state["p80_microns"] - 150.0) / 180.0))
        ball_capacity = ball_power_cap * 1_000.0 / (8.6 * state["ore_hardness_factor"] * cyclone_recirculating_load)
        flotation_capacity = 1_240.0 * state["flotation_condition"] * (0.90 + 0.10 * water_factor)
        requested_feed = min(plant_target, sag_capacity, ball_capacity, flotation_capacity)

        rom_discharge = min(state["rom_stockpile_t"] * 3600.0, crusher_capacity, 1_250.0)
        crushed_rate = min(rom_discharge, crusher_capacity) if jam < 0.95 else 0.0
        dt_h = 1.0 / 3600.0
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
        sag_feeder_rate = min(900.0, state["crushed_stockpile_t"] * 3600.0,
                              max(0.0, 900.0 - state["sag_bin_t"]) * 3600.0)
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
        flotation_air = 540.0 * (1 - mechanical * 0.3) * power_factor
        reagent = 245.0 * (1 + 0.12 * math.sin(sim_seconds / 600))
        recovery = min(94.0, max(40.0, (grind_recovery - water_recovery_penalty) * state["flotation_condition"]
                               * (0.96 + 0.04 * min(1.0, flotation_air / 500.0)) - upset * 12.0))
        concentrate = cyclone_overflow * (0.0062 * recovery / 100.0) / 0.25
        tailings = max(0.0, cyclone_overflow - concentrate)
        thickener_input = tailings + (concentrate * 0.025)
        thickener_capacity = 1_400.0 * state["tailings_condition"]
        tailings_pumped = min(thickener_input, thickener_capacity)
        state["tailings_thickener_inventory_t"] = min(3_000.0, max(0.0,
            state["tailings_thickener_inventory_t"] + (thickener_input - tailings_pumped) * dt_h))

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
        state["water_specific_m3_t"] = 0.30 + 0.05 * max(0.0, 1.0 - state["water_recovery_pct"] / 80.0)
        state["water_makeup_m3h"] = cyclone_overflow * state["water_specific_m3_t"]
        state["fresh_water_shortage_m3h"] = max(0.0, water_shortage_m3h)
        state["water_available_fraction"] = water_factor
        state["water_recovery_pct"] = min(82.0, max(58.0, 74.0 - upset * 12.0 - water_shortage_m3h / 100.0))
        state["flotation_air_nm3_min"] = flotation_air
        state["reagent_l_h"] = reagent

        # Fault signatures show up as actual process effects plus equipment load,
        # vibration, interlock status, and degraded mass flow.
        state["crushing_running"] = float(crushed_rate > 5)
        state["sag_running"] = float(sag_rate > 5)
        state["flotation_running"] = float(cyclone_overflow > 5)
        state["crusher_vibration_mm_s"] = 2.4 + mechanical * 12 + jam * 5
        state["sag_vibration_mm_s"] = 2.8 + mechanical * 10
        state["ball_vibration_mm_s"] = 2.4 + mechanical * 9
        state["flotation_vibration_mm_s"] = 1.8 + mechanical * 5
        state["tailings_vibration_mm_s"] = 2.1 + mechanical * 13

        if sim_seconds % 15 == 0:
            state["history"].append({"time": sim_seconds, "rom_stockpile_t": state["rom_stockpile_t"],
                "crushed_stockpile_t": state["crushed_stockpile_t"], "sag_bin_t": state["sag_bin_t"],
                "plant_feed_tph": state["plant_feed_tph"],
                "copper_recovery_pct": state["copper_recovery_pct"], "p80_microns": state["p80_microns"],
                "water_makeup_m3h": state["water_makeup_m3h"],
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
                    "equipment_running": (state["crushing_running"], "bool", 0, 1)},
                "Ore conveyors": {"ore_rate": (state["crushed_ore_tph"], "t/h", 0, 2_000),
                    "stockpile_inventory": (state["crushed_stockpile_t"], "t", 0, 8_000),
                    "motor_power": (0.45 * state["crushing_running"], "MW", 0, 1),
                    "belt_speed": (3.1 * state["crushing_running"], "m/s", 0, 8),
                    "equipment_running": (state["crushing_running"], "bool", 0, 1)},
            },
            "Grinding and flotation": {
                "SAG mill": {"ore_rate": (state["sag_feed_tph"], "t/h", 0, 2_000),
                    "motor_power": (state["sag_power_mw"], "MW", 0, 30),
                    "motor_current": (state["sag_power_mw"] * 41.0, "A", 0, 2_500),
                    "vibration_rms": (state["sag_vibration_mm_s"], "mm/s", 0, 30),
                    "equipment_running": (state["sag_running"], "bool", 0, 1),
                    "ore_hardness": (state["ore_hardness_kwh_t"], "kWh/t", 8, 32)},
                "Ball mill 01": {"ore_rate": (state["ball_circuit_feed_tph"], "t/h", 0, 4_000),
                    "motor_power": (state["ball_power_mw"], "MW", 0, 20),
                    "motor_current": (state["ball_power_mw"] * 44.0, "A", 0, 2_500),
                    "vibration_rms": (state["ball_vibration_mm_s"], "mm/s", 0, 30),
                    "equipment_running": (state["sag_running"], "bool", 0, 1)},
                "Cyclone cluster": {"ore_rate": (state["cyclone_overflow_tph"], "t/h", 0, 2_000),
                    "product_p80": (state["p80_microns"], "µm", 50, 400),
                    "circulating_load": (100 * (state["ball_circuit_feed_tph"] / max(state["sag_feed_tph"], 1) - 1), "%", 0, 300),
                    "equipment_running": (state["sag_running"], "bool", 0, 1)},
                "Flotation cells": {"ore_rate": (state["plant_feed_tph"], "t/h", 0, 2_000),
                    "copper_recovery": (state["copper_recovery_pct"], "%", 0, 100),
                    "concentrate_grade": (state["concentrate_grade_pct"], "%", 0, 40),
                    "concentrate_rate": (state["concentrate_tph"], "t/h", 0, 100),
                    "air_flow": (state["flotation_air_nm3_min"], "Nm³/min", 0, 1_000),
                    "reagent_rate": (state["reagent_l_h"], "L/h", 0, 1_000),
                    "equipment_running": (state["flotation_running"], "bool", 0, 1)},
            },
            "Concentrate handling": {
                "Thickener": {"ore_rate": (state["tailings_tph"], "t/h", 0, 2_000),
                    "tank_level": (37 + state["tailings_thickener_inventory_t"] / 3_000 * 50, "%", 0, 100),
                    "underflow_density": (1_380 + 80 * state["tailings_condition"], "kg/m³", 900, 1_800),
                    "equipment_running": (1.0, "bool", 0, 1)},
                "Filter press": {"ore_rate": (state["concentrate_tph"] * 0.975, "t/h", 0, 100),
                    "cake_moisture": (9.0 + (1 - state["tailings_condition"]) * 12, "%", 0, 40),
                    "equipment_running": (float(state["concentrate_tph"] > 1), "bool", 0, 1)},
            },
            "Tailings and reclaim": {
                "Tailings pump station A": {"ore_rate": (state["tailings_tph"], "t/h", 0, 2_000),
                    "motor_power": (0.62 * state["tailings_condition"], "MW", 0, 2),
                    "motor_current": (62 * state["tailings_condition"], "A", 0, 250),
                    "vibration_rms": (state["tailings_vibration_mm_s"], "mm/s", 0, 30),
                    "equipment_running": (float(state["tailings_tph"] > 5), "bool", 0, 1)},
            },
        }
        rows = []
        for area, assets in readings.items():
            for asset, signals in assets.items():
                for signal, (truth, units, low, high) in signals.items():
                    tag = f"{area.lower().replace(' ', '_')}.{asset.lower().replace(' ', '_')}.{signal}"
                    stable_id = sum((i + 1) * ord(char) for i, char in enumerate(tag))
                    rng = random.Random(state["noise_seed"] * 1_000_003 + state["sim_seconds"] * 9_176 + stable_id)
                    noise = rng.gauss(0.0, max(high-low, 1e-9) * (0.0015 if signal in ("product_p80", "copper_recovery") else 0.002))
                    value = truth + noise
                    if signal in ("ore_rate", "product_p80"):
                        value *= 1.0 + state.get("measurement_drift", 0.0)
                    rows.append({"tag": tag, "area": area, "unit": asset, "sensor": signal, "units": units,
                        "value": round(min(high, max(low, value)), 3), "quality": "GOOD", "low": low,
                        "high": high, "base": (low + high) / 2, "phase": 0.0,
                        "last_change": "Dynamic concentrator process model"})
        rows.extend([
            {"tag": "water_supply_network.concentrator_process.water_demand", "area": "Water supply network",
             "unit": "Concentrator process", "sensor": "water_demand", "units": "m³/h",
             "value": round(state["water_makeup_m3h"], 2), "quality": "GOOD", "low": 0, "high": 700,
             "base": 350, "phase": 0.0, "last_change": "Concentrator make-up water requirement"},
        ])
        return rows


if __name__ == "__main__":
    model = CopperConcentrator()
    state = model.initialize()
    for second in range(1, 3_601):
        model.advance(state, second, [])
    print({key: round(state[key], 2) for key in (
        "plant_feed_tph", "p80_microns", "copper_recovery_pct", "concentrate_tph", "water_makeup_m3h")})
