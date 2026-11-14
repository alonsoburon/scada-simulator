"""Compact dynamic model of an open-pit copper concentrator.

Balances are intentionally explicit. This is a training model, not a design
tool: it represents buffering, equipment limits, grind, water, power, and metal
recovery at a one-second supervisory timescale.
"""
from __future__ import annotations

import math
import random


class CopperConcentrator:
    GRINDING_CIRCUIT_RESIDENCE_S = 900.0
    FLOTATION_REAGENT_TANK_CAPACITY_L = 50_000.0
    FLOTATION_REAGENT_INITIAL_L = 25_000.0
    FLOTATION_REAGENT_DOSE_L_T = 0.28
    FLOTATION_REAGENT_PUMP_CAPACITY_L_H = 360.0
    BALL_SPECIFIC_ENERGY_KWH_T = 5.15
    FLOTATION_CELL_COUNT = 6
    FLOTATION_BANK_VOLUME_M3 = 540.0
    SECONDARY_CRUSHER_CAPACITY_TPH = 1_520.0
    CONE_CRUSHER_CAPACITY_TPH = 1_480.0
    SCREEN_CAPACITY_TPH = 1_700.0
    SCREEN_BASE_EFFICIENCY = 0.90
    GEOLOGICAL_DOMAINS = (
        # name, weight, mean Cu grade, grade scatter, hardness, hardness scatter
        ("weathered", 0.14, 0.59, 0.10, 0.84, 0.08),
        ("altered_low_grade", 0.21, 0.46, 0.07, 0.96, 0.10),
        ("standard_porphyry", 0.39, 0.65, 0.09, 1.00, 0.10),
        ("silicified_hard", 0.17, 0.72, 0.10, 1.31, 0.08),
        ("high_grade_breccia", 0.09, 0.91, 0.11, 1.17, 0.11),
    )
    GEOLOGICAL_DOMAIN_CODES = {domain[0]: code for code, domain in enumerate(GEOLOGICAL_DOMAINS)}
    MIN_BLOCK_TONNAGE_T = 500.0
    MAX_BLOCK_TONNAGE_T = 2_200.0
    ORE_BLEND_RESIDENCE_S = 1_800
    TAILINGS_STORAGE_CAPACITY_T = 2_500_000.0
    TAILINGS_STORAGE_INITIAL_T = 1_650_000.0
    TAILINGS_POND_CAPACITY_M3 = 120_000.0
    TAILINGS_POND_INITIAL_M3 = 30_000.0
    TAILINGS_POND_TARGET_M3 = 25_000.0
    TAILINGS_POND_DERATE_START_FRACTION = 0.92
    TAILINGS_POND_DERATE_STOP_FRACTION = 0.98
    TAILINGS_DECANT_CAPACITY_M3H = 500.0
    TAILINGS_DECANT_LEVELING_S = 7_200.0
    TAILINGS_EVAPORATION_M3H = 0.15
    TAILINGS_PORE_WATER_M3_PER_T = 0.15
    TAILINGS_DECANT_HEAD_M = 28.0
    TAILINGS_DECANT_PUMP_EFFICIENCY = 0.72
    AREA_ASSETS = {
        "Open-pit mining": ["Pit sector A"],
        "Crushing and screening": ["Primary crusher", "Secondary crusher", "Cone crusher 01",
                                   "Screen deck A", "Ore conveyors"],
        "Grinding and flotation": ["SAG mill", "Ball mill 01", "Cyclone cluster", "Flotation cells",
                                   "Flotation air blower", "Flotation reagent dosing pump"],
        "Concentrate handling": ["Concentrate thickener", "Filter press"],
        "Tailings and reclaim": ["Tailings thickener", "Tailings pump station A",
                                 "Tailings decant pump", "Tailings storage facility"],
    }
    STAGES = ("crushing", "sag_mill", "ball_mill", "cyclone", "flotation",
              "concentrate_thickener", "filter_press", "tailings_thickener", "tailings_pump")
    MAINTAINABLE_EQUIPMENT = {
        "crusher": ("Primary crusher", "crusher"),
        "secondary_crusher": ("Secondary crusher", "secondary_crusher"),
        "cone_crusher": ("Cone crusher 01", "cone_crusher"),
        "screen_deck": ("Screen deck A", "screen_deck"),
        "conveyors": ("Ore conveyors", "conveyors"),
        "sag_mill": ("SAG mill", "sag_mill"),
        "ball_mill": ("Ball mill", "ball_mill"),
        "flotation": ("Flotation cells", "flotation"),
        "concentrate_thickener": ("Concentrate thickener", "concentrate_thickener"),
        "filter_press": ("Filter press", "filter_press"),
        "tailings_thickener": ("Tailings thickener", "tailings_thickener"),
        "tailings_pump": ("Tailings pump", "tailings_pump"),
        "tailings_decant_pump": ("Tailings decant pump", "tailings_decant_pump"),
    }
    EQUIPMENT_TARGET_ALIASES = {"tailings_pump": ("Tailings pump station A",)}
    MAINTENANCE_AREAS = {
        "crusher": "Crushing and screening", "conveyors": "Crushing and screening",
        "secondary_crusher": "Crushing and screening", "cone_crusher": "Crushing and screening",
        "screen_deck": "Crushing and screening",
        "sag_mill": "Grinding and flotation", "ball_mill": "Grinding and flotation",
        "flotation": "Grinding and flotation",
        "concentrate_thickener": "Concentrate handling", "filter_press": "Concentrate handling",
        "tailings_thickener": "Tailings and reclaim", "tailings_pump": "Tailings and reclaim",
        "tailings_decant_pump": "Tailings and reclaim",
    }
    SIGNALS = (
        ("ore_rate", "t/h", 0, 2_000), ("motor_power", "MW", 0, 30),
        ("motor_current", "A", 0, 2_500), ("vibration_rms", "mm/s", 0, 30),
        ("equipment_running", "bool", 0, 1),
    )

    def initialize(self) -> dict:
        flotation_solids_per_cell_t = (
            self.FLOTATION_BANK_VOLUME_M3 / (1.0 / 2.7 + 0.90) / self.FLOTATION_CELL_COUNT)
        per_cell_recovery = 1.0 - (1.0 - 0.88) ** (1.0 / self.FLOTATION_CELL_COUNT)
        copper_input_tph = 870.0 * 0.0062
        flotation_cell_copper_inventory_t = []
        for _ in range(self.FLOTATION_CELL_COUNT):
            # Seed the six perfectly mixed cells at a nominal 88% bank recovery.
            flotation_cell_copper_inventory_t.append(
                copper_input_tph * flotation_solids_per_cell_t / 870.0 * (1.0 - per_cell_recovery))
            copper_input_tph *= 1.0 - per_cell_recovery
        return {
            "sim_seconds": 0, "rom_stockpile_t": 18_000.0, "rom_feed_curtailed_t": 0.0,
            "initial_rom_stockpile_t": 18_000.0, "cumulative_rom_received_t": 0.0,
            # Copper inventories travel with ore through each finite buffer. This
            # lets ROM stockpiles blend geology instead of changing plant grade
            # instantaneously when the mine moves to a new block.
            "rom_copper_inventory_t": 18_000.0 * 0.0062,
            "crushed_copper_inventory_t": 3_200.0 * 0.0062,
            "sag_bin_copper_inventory_t": 410.0 * 0.0062,
            "mine_block_grade_pct": 0.62, "cumulative_mine_copper_received_t": 0.0,
            "mine_block_index": 0, "mine_block_domain": "standard_porphyry",
            "mine_block_domain_code": self.GEOLOGICAL_DOMAIN_CODES["standard_porphyry"],
            "mine_block_start_s": 0, "mine_block_target_t": 900.0,
            "mine_block_mined_t": 0.0, "mine_block_remaining_t": 900.0,
            "mine_block_hardness_factor": 1.0,
            "cumulative_rom_discharge_t": 0.0, "initial_crushed_stockpile_t": 3_200.0,
            "cumulative_crushed_ore_t": 0.0, "cumulative_sag_feeder_t": 0.0,
            "initial_sag_bin_t": 410.0, "cumulative_sag_processed_t": 0.0,
            "rom_feed_curtailed_tph": 0.0,
            "rom_delivery_window_t": [], "rom_curtailed_window_t": [],
            "crushed_stockpile_t": 3_200.0,
            "sag_bin_t": 410.0, "tailings_thickener_inventory_t": 1_100.0,
            "tailings_thickener_capacity_t": 3_000.0,
            "tailings_thickener_water_capacity_m3": 3_000.0,
            "tailings_thickener_water_inventory_m3": 1_100.0 * (32.0 / 68.0),
            "tailings_thickener_overflow_capacity_m3h": 600.0,
            "tailings_thickener_overflow_water_m3h": 364.0,
            "tailings_underflow_water_m3h": 399.5,
            "tailings_thickener_spill_m3": 0.0,
            "cumulative_tailings_water_feed_m3": 0.0,
            "cumulative_tailings_underflow_water_m3": 0.0,
            "cumulative_tailings_overflow_water_m3": 0.0,
            "cumulative_tailings_water_spill_m3": 0.0,
            "tailings_storage_capacity_t": self.TAILINGS_STORAGE_CAPACITY_T,
            "tailings_storage_initial_t": self.TAILINGS_STORAGE_INITIAL_T,
            "tailings_storage_inventory_t": self.TAILINGS_STORAGE_INITIAL_T,
            "tailings_storage_copper_inventory_t": self.TAILINGS_STORAGE_INITIAL_T * 0.00075,
            "tailings_storage_water_inventory_m3": (
                self.TAILINGS_STORAGE_INITIAL_T * self.TAILINGS_PORE_WATER_M3_PER_T),
            "tailings_deposition_tph": 0.0,
            "cumulative_tailings_deposited_t": 0.0,
            "cumulative_tailings_copper_deposited_t": 0.0,
            "cumulative_tailings_water_retained_m3": 0.0,
            "tailings_pond_capacity_m3": self.TAILINGS_POND_CAPACITY_M3,
            "tailings_pond_inventory_m3": self.TAILINGS_POND_INITIAL_M3,
            "tailings_pond_backpressure": 1.0,
            "tailings_decant_water_m3h": 0.0,
            "tailings_decant_pump_power_mw": 0.0,
            "tailings_decant_condition": 1.0, "tailings_decant_motor_running": 1.0,
            "tailings_decant_vibration_mm_s": 1.8,
            "cumulative_tailings_decant_m3": 0.0,
            "tailings_pond_spill_m3": 0.0,
            "cumulative_tailings_pond_spill_m3": 0.0,
            "cumulative_tailings_pond_evaporation_m3": 0.0,
            "concentrate_thickener_inventory_t": 28.0,
            "concentrate_thickener_capacity_t": 120.0,
            "concentrate_thickener_copper_inventory_t": 28.0 * 0.25,
            "filter_press_phase": "FILLING", "filter_press_cycle_s": 0,
            "filter_press_batch_t": 0.0, "filter_press_batch_capacity_t": 3.2,
            "filter_press_batch_copper_inventory_t": 0.0,
            "filter_press_cycle_count": 0,
            "concentrate_cake_inventory_t": 90.0, "concentrate_cake_capacity_t": 500.0,
            "concentrate_cake_copper_inventory_t": 90.0 * 0.25,
            "cumulative_concentrate_produced_t": 0.0,
            "cumulative_concentrate_shipped_t": 0.0,
            "cumulative_concentrate_copper_produced_t": 0.0,
            "cumulative_concentrate_copper_shipped_t": 0.0,
            "cumulative_tailings_received_t": 0.0,
            "cumulative_tailings_pumped_t": 0.0,
            "tailings_thickener_copper_inventory_t": 1_100.0 * 0.00075,
            "cumulative_tailings_copper_received_t": 0.0,
            "cumulative_tailings_copper_pumped_t": 0.0,
            "rom_feed_tph": 1_180.0, "crusher_feed_tph": 1_080.0, "crushed_ore_tph": 1_030.0,
            "secondary_crusher_feed_tph": 1_144.44, "cone_crusher_feed_tph": 1_144.44,
            "screen_feed_tph": 1_144.44, "screen_oversize_tph": 114.44,
            "screen_efficiency_pct": 90.0,
            "plant_feed_tph": 870.0, "sag_feed_tph": 870.0, "ball_circuit_feed_tph": 1_689.0,
            "cyclone_overflow_tph": 870.0, "tailings_tph": 849.0, "concentrate_tph": 21.0,
            "copper_feed_grade_pct": 0.62, "copper_recovery_pct": 88.0,
            "flotation_cell_copper_inventory_t": flotation_cell_copper_inventory_t,
            "flotation_copper_inventory_t": sum(flotation_cell_copper_inventory_t),
            "reagent_inventory_l": self.FLOTATION_REAGENT_INITIAL_L,
            "reagent_tank_capacity_l": self.FLOTATION_REAGENT_TANK_CAPACITY_L,
            "reagent_consumed_l": 0.0, "reagent_delivery_total_l": 0.0,
            "reagent_dose_l_h": 245.0, "reagent_requested_l_h": 245.0,
            "reagent_pump_condition": 1.0, "reagent_pump_flow_factor": 1.0,
            "flotation_copper_product_tph": 870.0 * 0.0062 * 0.88,
            "flotation_cell_collector_factor": [1.0] * self.FLOTATION_CELL_COUNT,
            "cumulative_flotation_copper_feed_t": 0.0,
            "cumulative_flotation_copper_product_t": 0.0,
            "cumulative_flotation_copper_tailings_t": 0.0,
            "concentrate_grade_pct": 25.0, "p80_microns": 150.0,
            "grind_coarse_fraction": 0.6327, "grind_intermediate_fraction": 0.3561,
            "grind_fine_fraction": 0.0112, "cyclone_overflow_fraction": 0.5152,
            "circulating_load_ratio": 0.9411, "flotation_residence_time_min": 28.3,
            "coarse_breakage_per_pass": 0.798, "intermediate_breakage_per_pass": 0.600,
            "grind_model_revision": 2,
            "ore_hardness_kwh_t": 17.0, "ore_hardness_factor": 1.0,
            "water_makeup_m3h": 443.0, "gross_process_water_m3h": 783.0,
            "concentrator_reclaim_m3h": 340.0, "tailings_reclaim_water_m3h": 335.0,
            "concentrate_thickener_overflow_m3h": 5.0, "filter_press_filtrate_m3h": 0.0,
            "filter_press_batch_filtrate_inventory_m3": 0.0,
            "tailings_water_feed_m3h": 765.0, "tailings_underflow_solids_pct": 68.0,
            "concentrate_water_feed_m3h": 18.0, "water_specific_m3_t": 0.90,
            "water_model_revision": 3,
            "water_available_fraction": 1.0, "water_recovery_pct": 42.0,
            "fresh_water_shortage_m3h": 0.0,
            "sag_power_mw": 15.0, "ball_power_mw": 8.7, "crusher_power_mw": 2.2,
            "secondary_crusher_power_mw": 0.858, "cone_crusher_power_mw": 1.202,
            "screen_power_mw": 0.252,
            "conveyor_power_mw": 0.45, "flotation_air_power_mw": 1.62,
            "flotation_aux_power_mw": 0.35, "tailings_pump_power_mw": 0.62,
            "tailings_thickener_power_mw": 0.18, "concentrate_thickener_power_mw": 0.11,
            "filter_press_power_mw": 0.12, "process_aux_power_mw": 0.50,
            "total_power_mw": 32.162,
            "flotation_air_nm3_min": 540.0, "reagent_l_h": 245.0,
            "crusher_condition": 1.0, "sag_condition": 1.0, "ball_condition": 1.0,
            "secondary_crusher_condition": 1.0, "cone_crusher_condition": 1.0,
            "screen_deck_condition": 1.0,
            "flotation_condition": 1.0, "tailings_condition": 1.0,
            "concentrate_condition": 1.0,
            "equipment_health": {name: 1.0 for name in self.MAINTAINABLE_EQUIPMENT},
            "crusher_vibration_mm_s": 2.4, "sag_vibration_mm_s": 2.8,
            "secondary_crusher_vibration_mm_s": 2.7, "cone_crusher_vibration_mm_s": 2.6,
            "screen_deck_vibration_mm_s": 2.0,
            "ball_vibration_mm_s": 2.4, "flotation_vibration_mm_s": 1.8,
            "tailings_vibration_mm_s": 2.1, "crushing_running": 1.0,
            "sag_running": 1.0, "flotation_running": 1.0, "measurement_drift": 0.0,
            "ball_running": 1.0, "crusher_motor_running": 1.0, "conveyors_motor_running": 1.0,
            "secondary_crusher_motor_running": 1.0, "cone_crusher_motor_running": 1.0,
            "screen_deck_motor_running": 1.0,
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
                    "rom_copper_inventory_t", "crushed_copper_inventory_t",
                    "sag_bin_copper_inventory_t", "mine_block_grade_pct",
                    "cumulative_mine_copper_received_t", "mine_block_index",
                    "mine_block_domain", "mine_block_domain_code", "mine_block_start_s",
                    "mine_block_target_t", "mine_block_mined_t", "mine_block_remaining_t",
                    "mine_block_hardness_factor",
                    "flotation_cell_copper_inventory_t", "flotation_copper_inventory_t",
                    "reagent_inventory_l", "reagent_tank_capacity_l", "reagent_consumed_l",
                    "reagent_delivery_total_l", "reagent_dose_l_h", "reagent_requested_l_h",
                    "reagent_pump_condition", "reagent_pump_flow_factor",
                    "flotation_copper_product_tph", "cumulative_flotation_copper_feed_t",
                    "flotation_cell_collector_factor",
                    "cumulative_flotation_copper_product_t", "cumulative_flotation_copper_tailings_t",
                    "concentrate_thickener_copper_inventory_t", "filter_press_batch_copper_inventory_t",
                    "concentrate_cake_copper_inventory_t", "cumulative_concentrate_copper_produced_t",
                    "cumulative_concentrate_copper_shipped_t", "tailings_thickener_copper_inventory_t",
                    "cumulative_tailings_copper_received_t", "cumulative_tailings_copper_pumped_t",
                    "tailings_storage_capacity_t", "tailings_storage_initial_t",
                    "tailings_storage_inventory_t", "tailings_storage_copper_inventory_t",
                    "tailings_storage_water_inventory_m3",
                    "tailings_deposition_tph",
                    "cumulative_tailings_deposited_t", "cumulative_tailings_copper_deposited_t",
                    "cumulative_tailings_water_retained_m3",
                    "tailings_pond_capacity_m3", "tailings_pond_inventory_m3",
                    "tailings_pond_backpressure",
                    "tailings_decant_water_m3h", "tailings_pond_spill_m3",
                    "cumulative_tailings_decant_m3",
                    "cumulative_tailings_pond_spill_m3", "cumulative_tailings_pond_evaporation_m3",
                    "conveyor_power_mw", "secondary_crusher_power_mw", "cone_crusher_power_mw",
                    "screen_power_mw", "secondary_crusher_feed_tph", "cone_crusher_feed_tph",
                    "screen_feed_tph", "screen_oversize_tph", "screen_efficiency_pct",
                    "secondary_crusher_motor_running", "cone_crusher_motor_running",
                    "screen_deck_motor_running", "secondary_crusher_condition",
                    "cone_crusher_condition", "screen_deck_condition",
                    "flotation_air_power_mw", "flotation_aux_power_mw",
                    "tailings_pump_power_mw", "tailings_thickener_power_mw",
                    "tailings_decant_pump_power_mw",
                    "tailings_decant_condition", "tailings_decant_motor_running",
                    "tailings_decant_vibration_mm_s",
                    "concentrate_thickener_power_mw", "filter_press_power_mw",
                    "process_aux_power_mw", "total_power_mw",
                    "concentrate_thickener_capacity_t", "filter_press_phase",
                    "filter_press_cycle_s", "filter_press_batch_t",
                    "filter_press_batch_capacity_t", "filter_press_cycle_count",
                    "concentrate_cake_inventory_t", "concentrate_cake_capacity_t",
                    "cumulative_concentrate_produced_t", "cumulative_concentrate_shipped_t",
                    "cumulative_tailings_received_t", "cumulative_tailings_pumped_t",
                    "ball_running", "crusher_motor_running", "conveyors_motor_running",
                    "secondary_crusher_motor_running", "cone_crusher_motor_running",
                    "screen_deck_motor_running",
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
                    "water_model_revision", "tailings_thickener_water_capacity_m3",
                    "tailings_thickener_water_inventory_m3", "tailings_thickener_overflow_capacity_m3h",
                    "tailings_thickener_overflow_water_m3h", "tailings_underflow_water_m3h",
                    "tailings_thickener_spill_m3", "cumulative_tailings_water_feed_m3",
                    "cumulative_tailings_underflow_water_m3", "cumulative_tailings_overflow_water_m3",
                    "cumulative_tailings_water_spill_m3", "grind_coarse_fraction",
                    "grind_intermediate_fraction", "grind_fine_fraction",
                    "cyclone_overflow_fraction", "circulating_load_ratio",
                    "flotation_residence_time_min", "coarse_breakage_per_pass",
                    "intermediate_breakage_per_pass", "grind_model_revision")
        previous_grind_revision = state.get("grind_model_revision", 0)
        if (all(key in state for key in keys) and state.get("water_model_revision", 0) >= 3
                and previous_grind_revision >= 2):
            return
        history_defaults = {"tailings_thickener_water_inventory_m3": 0.0,
            "copper_feed_grade_pct": 0.62, "mine_block_grade_pct": 0.62,
            "flotation_copper_inventory_t": 0.9,
            "grind_coarse_fraction": 0.6327, "grind_intermediate_fraction": 0.3561,
            "grind_fine_fraction": 0.0112, "cyclone_overflow_fraction": 0.5152,
            "circulating_load_ratio": 0.9411, "flotation_residence_time_min": 28.3,
            "coarse_breakage_per_pass": 0.798, "intermediate_breakage_per_pass": 0.600}
        for sample in state.get("history", []):
            sample.setdefault("tailings_thickener_water_inventory_m3", 0.0)
            sample.setdefault("copper_feed_grade_pct", 0.62)
            sample.setdefault("mine_block_grade_pct", 0.62)
            sample.setdefault("flotation_copper_inventory_t", 0.9)
            sample.setdefault("reagent_inventory_l", self.FLOTATION_REAGENT_INITIAL_L)
            sample.setdefault("reagent_dose_l_h", 245.0)
            sample.setdefault("secondary_crusher_feed_tph",
                              sample.get("crusher_feed_tph", 1_030.0) / self.SCREEN_BASE_EFFICIENCY)
            sample.setdefault("cone_crusher_feed_tph", sample["secondary_crusher_feed_tph"])
            sample.setdefault("screen_feed_tph", sample["secondary_crusher_feed_tph"])
            sample.setdefault("screen_oversize_tph",
                              sample["screen_feed_tph"] - sample.get("crusher_feed_tph", 1_030.0))
            sample.setdefault("screen_efficiency_pct", 100.0 * self.SCREEN_BASE_EFFICIENCY)
            for index in range(self.FLOTATION_CELL_COUNT):
                sample.setdefault(f"collector_availability_cell_{index + 1:02d}", 100.0)
            for key in ("grind_coarse_fraction", "grind_intermediate_fraction", "grind_fine_fraction",
                        "cyclone_overflow_fraction", "circulating_load_ratio", "flotation_residence_time_min",
                        "coarse_breakage_per_pass", "intermediate_breakage_per_pass"):
                sample.setdefault(key, history_defaults[key])
        previous_water_revision = state.get("water_model_revision", 0)
        defaults = self.initialize()
        health = state.setdefault("equipment_health", defaults["equipment_health"].copy())
        for name in self.MAINTAINABLE_EQUIPMENT:
            health.setdefault(name, 1.0)
        missing = {key for key in keys if key not in state}
        for key in keys:
            state.setdefault(key, defaults[key])
        if "tailings_storage_inventory_t" in missing:
            legacy_deposited_t = max(0.0, state.get("cumulative_tailings_pumped_t", 0.0))
            state["tailings_storage_inventory_t"] = min(
                state["tailings_storage_capacity_t"],
                state["tailings_storage_initial_t"] + legacy_deposited_t)
            state["cumulative_tailings_deposited_t"] = legacy_deposited_t
        if "tailings_storage_copper_inventory_t" in missing:
            state["tailings_storage_copper_inventory_t"] = (
                state["tailings_storage_initial_t"] * 0.00075
                + max(0.0, state.get("cumulative_tailings_copper_pumped_t", 0.0)))
            state["cumulative_tailings_copper_deposited_t"] = max(
                0.0, state.get("cumulative_tailings_copper_pumped_t", 0.0))
        if "tailings_storage_water_inventory_m3" in missing:
            state["tailings_storage_water_inventory_m3"] = (
                state["tailings_storage_inventory_t"] * self.TAILINGS_PORE_WATER_M3_PER_T)
        legacy_grade_inventory = {
            "rom_copper_inventory_t": ("rom_stockpile_t", 0.0062),
            "crushed_copper_inventory_t": ("crushed_stockpile_t", 0.0062),
            "sag_bin_copper_inventory_t": ("sag_bin_t", 0.0062),
            "concentrate_thickener_copper_inventory_t": ("concentrate_thickener_inventory_t", 0.25),
            "filter_press_batch_copper_inventory_t": ("filter_press_batch_t", 0.25),
            "concentrate_cake_copper_inventory_t": ("concentrate_cake_inventory_t", 0.25),
            "tailings_thickener_copper_inventory_t": ("tailings_thickener_inventory_t", 0.00075),
        }
        for copper_key, (solids_key, grade_fraction) in legacy_grade_inventory.items():
            if copper_key in missing:
                state[copper_key] = max(0.0, state.get(solids_key, 0.0)) * grade_fraction
        power_components = ("conveyor_power_mw", "secondary_crusher_power_mw",
                            "cone_crusher_power_mw", "screen_power_mw",
                            "flotation_air_power_mw", "flotation_aux_power_mw",
                            "tailings_pump_power_mw", "tailings_thickener_power_mw",
                            "tailings_decant_pump_power_mw",
                            "concentrate_thickener_power_mw", "filter_press_power_mw",
                            "process_aux_power_mw", "total_power_mw")
        if any(key in missing for key in power_components):
            state["conveyor_power_mw"] = (0.45 * state.get("crushed_ore_tph", 0.0) / 1_200.0
                                          * state.get("conveyors_motor_running", 1.0))
            state["secondary_crusher_power_mw"] = (0.75 * state.get("secondary_crusher_feed_tph", 0.0)
                / 1_000.0 * state.get("secondary_crusher_motor_running", 1.0))
            state["cone_crusher_power_mw"] = (1.05 * state.get("cone_crusher_feed_tph", 0.0)
                / 1_000.0 * state.get("cone_crusher_motor_running", 1.0))
            state["screen_power_mw"] = (0.22 * state.get("screen_feed_tph", 0.0)
                / 1_000.0 * state.get("screen_deck_motor_running", 1.0))
            state["flotation_air_power_mw"] = (
                state.get("flotation_air_nm3_min", 0.0) * 60.0 * 0.05 / 1_000.0)
            state["flotation_aux_power_mw"] = (0.35 * min(1.0, state.get("plant_feed_tph", 0.0) / 900.0)
                                               * state.get("flotation_motor_running", 1.0))
            state["tailings_pump_power_mw"] = (
                0.62 * min(1.0, state.get("tailings_tph", 0.0) / 1_400.0)
                / max(state.get("tailings_condition", 1.0), 0.35)
                * state.get("tailings_motor_running", 1.0))
            state["tailings_decant_pump_power_mw"] = (
                9.81 * self.TAILINGS_DECANT_HEAD_M
                * state.get("tailings_decant_water_m3h", 0.0)
                / (3_600.0 * self.TAILINGS_DECANT_PUMP_EFFICIENCY * 1_000.0))
            state["tailings_thickener_power_mw"] = (
                0.18 * min(1.0, state.get("tailings_thickener_feed_tph", 0.0) / 900.0)
                * state.get("tailings_thickener_motor_running", 1.0))
            state["concentrate_thickener_power_mw"] = (
                0.11 * min(1.0, state.get("concentrate_tph", 0.0) / 26.0)
                * (1.0 + 1.0 - state.get("concentrate_condition", 1.0))
                * state.get("concentrate_thickener_motor_running", 1.0))
            state["filter_press_power_mw"] = (
                0.12 * state.get("filter_press_motor_running", 1.0)
                * float(state.get("filter_press_phase") in ("FILLING", "SQUEEZING")))
            state["process_aux_power_mw"] = 0.50
            state["total_power_mw"] = sum(state.get(key, 0.0) for key in (
                "sag_power_mw", "ball_power_mw", "crusher_power_mw", "conveyor_power_mw",
                "secondary_crusher_power_mw", "cone_crusher_power_mw", "screen_power_mw",
                "flotation_air_power_mw", "flotation_aux_power_mw", "tailings_pump_power_mw",
                "tailings_decant_pump_power_mw",
                "tailings_thickener_power_mw", "concentrate_thickener_power_mw",
                "filter_press_power_mw", "process_aux_power_mw"))
        if previous_grind_revision < 2:
            for key in ("grind_coarse_fraction", "grind_intermediate_fraction", "grind_fine_fraction",
                        "cyclone_overflow_fraction", "circulating_load_ratio", "coarse_breakage_per_pass",
                        "intermediate_breakage_per_pass"):
                state[key] = defaults[key]
            state["ball_circuit_feed_tph"] = (state.get("sag_feed_tph", 870.0)
                                              / defaults["cyclone_overflow_fraction"])
            state["grind_model_revision"] = 2
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
        if previous_water_revision < 3:
            state["tailings_thickener_water_inventory_m3"] = (
                state["tailings_thickener_inventory_t"] * 32.0 / 68.0)
            state["water_model_revision"] = 3
    @staticmethod
    def _severity(events: list[dict], name: str, areas: tuple[str, ...]) -> float:
        factors = {"Small": 0.18, "Medium": 0.42, "Large": 0.78}
        return max((factors[event["severity"]] for event in events
                    if event["name"] == name and event["area"] in (*areas, "Whole factory")), default=0.0)

    @staticmethod
    def _mechanical_damage_rate_per_s(severity_factor: float) -> float:
        """Translate event severity to latent damage; large failures trip quickly."""
        if severity_factor <= 0.0:
            return 0.0
        return 0.25 / 3_600.0 * (severity_factor / 0.78) ** 2

    def _equipment_fault_severity(self, events: list[dict], equipment: str,
                                  area: str) -> float:
        """Resolve an area fault or an explicitly targeted mechanical fault."""
        label = self.MAINTAINABLE_EQUIPMENT[equipment][0]
        aliases = self.EQUIPMENT_TARGET_ALIASES.get(equipment, ())
        factors = {"Small": 0.18, "Medium": 0.42, "Large": 0.78}
        return max((factors[event["severity"]] for event in events
                    if event["name"] == "Mechanical degradation"
                    and event["area"] in (area, "Whole factory")
                    and (not event.get("target_asset") or
                         event["target_asset"].casefold() in
                         {equipment.casefold(), label.casefold(), *(alias.casefold() for alias in aliases)})),
                   default=0.0)

    def perform_maintenance(self, state: dict, equipment_name: str, *,
                            equipment_stopped: bool, fault_active: bool = False) -> float:
        """Restore one process asset to healthy condition while it is safely stopped."""
        match = next((key for key, (label, _) in self.MAINTAINABLE_EQUIPMENT.items()
                      if equipment_name.casefold() in (key.casefold(), label.casefold())), None)
        if match is None:
            raise ValueError(f"Unknown maintainable equipment: {equipment_name!r}")
        if not equipment_stopped:
            raise ValueError("Stop the equipment before carrying out maintenance")
        if fault_active:
            raise ValueError("Clear the active mechanical fault before carrying out maintenance")
        state.setdefault("equipment_health", {}).setdefault(match, 1.0)
        previous = state["equipment_health"][match]
        state["equipment_health"][match] = 1.0
        return previous

    @staticmethod
    def _smooth(old: float, requested: float, seconds: int = 90) -> float:
        return old + (requested - old) / max(seconds, 1)

    def _advance_geological_blocks(self, state: dict, sim_seconds: int, seed: int,
                                   excavated_t: float) -> None:
        """Advance mine domains by tonnes actually loaded at the shovel."""
        block_progress_t = state["mine_block_mined_t"] + max(0.0, excavated_t)
        while block_progress_t >= state["mine_block_target_t"]:
            block_progress_t -= state["mine_block_target_t"]
            block_index = state["mine_block_index"] + 1
            rng = random.Random(seed + block_index * 1_000_003)
            domain = rng.choices(
                self.GEOLOGICAL_DOMAINS,
                weights=[item[1] for item in self.GEOLOGICAL_DOMAINS], k=1)[0]
            domain_name, _weight, grade_mean, grade_sd, hardness_mean, hardness_sd = domain
            block_start = sim_seconds
            target_tonnage = rng.uniform(self.MIN_BLOCK_TONNAGE_T, self.MAX_BLOCK_TONNAGE_T)
            state.update({
                "mine_block_index": block_index,
                "mine_block_domain": domain_name,
                "mine_block_domain_code": self.GEOLOGICAL_DOMAIN_CODES[domain_name],
                "mine_block_start_s": block_start,
                "mine_block_target_t": target_tonnage,
                "mine_block_grade_pct": min(1.2, max(0.25, rng.gauss(grade_mean, grade_sd))),
                "mine_block_hardness_factor": min(1.42, max(0.78,
                    rng.gauss(hardness_mean, hardness_sd))),
            })
        state["mine_block_mined_t"] = block_progress_t
        state["mine_block_remaining_t"] = max(0.0,
            state["mine_block_target_t"] - block_progress_t)

    @staticmethod
    def _motor_current_a(power_mw: float, voltage_kv: float = 6.6,
                         power_factor: float = 0.90, efficiency: float = 0.95) -> float:
        """Estimate balanced three-phase line current from real motor input power."""
        denominator = math.sqrt(3.0) * voltage_kv * power_factor * efficiency
        return max(0.0, power_mw) * 1_000.0 / max(denominator, 1e-9)

    @staticmethod
    def _log_size_percentile(class_fractions: tuple[float, float, float], percentile: float = 0.80) -> float:
        """Estimate a size percentile from fine, intermediate and coarse log bins."""
        cumulative = 0.0
        for fraction, low, high in zip(class_fractions, (20.0, 75.0, 200.0), (75.0, 200.0, 1_500.0)):
            if cumulative + fraction >= percentile:
                position = min(1.0, max(0.0, (percentile - cumulative) / max(fraction, 1e-9)))
                return low * (high / low) ** position
            cumulative += fraction
        return 1_500.0

    @staticmethod
    def _mill_cyclone_pass(class_fractions: tuple[float, float, float], intensity: float) -> tuple:
        """Break coarse solids, classify by size, and mix cyclone rejects with fresh ore."""
        coarse_breakage = 1.0 - math.exp(-1.6 * intensity)
        intermediate_breakage = 1.0 - math.exp(-0.916290731874 * intensity)
        coarse, intermediate, fine = class_fractions
        mill_discharge = (
            coarse * (1.0 - coarse_breakage),
            intermediate * (1.0 - intermediate_breakage) + coarse * coarse_breakage,
            fine + intermediate * intermediate_breakage,
        )
        partition = (0.08, 0.45, 0.95)
        overflow_by_class = tuple(mass * probability
                                  for mass, probability in zip(mill_discharge, partition))
        overflow_fraction = sum(overflow_by_class)
        product_fractions = (overflow_by_class[2] / overflow_fraction,
                             overflow_by_class[1] / overflow_fraction,
                             overflow_by_class[0] / overflow_fraction)
        rejects = tuple(mass * (1.0 - probability)
                        for mass, probability in zip(mill_discharge, partition))
        # Fresh feed is coarse and replaces exactly the mass that exits cyclone overflow.
        next_feed_fractions = (overflow_fraction + rejects[0], rejects[1], rejects[2])
        return (overflow_fraction, product_fractions, next_feed_fractions,
                coarse_breakage, intermediate_breakage)

    def _advance_flotation_bank(self, state: dict, feed_solids_tph: float,
                                feed_grade_fraction: float, kinetic_rates_per_min: list[float],
                                dt_h: float) -> tuple[float, float]:
        """Advance a six-cell copper balance; return concentrate Cu, tail Cu and recovery."""
        cell_solids_inventory_t = (
            self.FLOTATION_BANK_VOLUME_M3
            / (1.0 / 2.7 + state["water_specific_m3_t"])
            / self.FLOTATION_CELL_COUNT)
        copper_inventory = state["flotation_cell_copper_inventory_t"]
        feed_copper_tph = feed_solids_tph * feed_grade_fraction
        downstream_copper_tph = feed_copper_tph
        product_copper_tph = 0.0
        next_inventory = []
        for inventory_t, cell_rate_per_min in zip(copper_inventory, kinetic_rates_per_min):
            tailings_tph = (max(0.0, feed_solids_tph) * inventory_t
                            / max(cell_solids_inventory_t, 1e-9))
            cell_k_per_h = max(0.0, cell_rate_per_min) * 60.0
            froth_tph = cell_k_per_h * inventory_t
            available_copper_t = inventory_t + downstream_copper_tph * dt_h
            requested_out_t = (tailings_tph + froth_tph) * dt_h
            if requested_out_t > available_copper_t and requested_out_t > 0:
                scale = available_copper_t / requested_out_t
                tailings_tph *= scale
                froth_tph *= scale
                requested_out_t = available_copper_t
            next_inventory.append(max(0.0, available_copper_t - requested_out_t))
            product_copper_tph += froth_tph
            downstream_copper_tph = tailings_tph
        state["flotation_cell_copper_inventory_t"] = next_inventory
        state["flotation_copper_inventory_t"] = sum(next_inventory)
        state["flotation_copper_product_tph"] = product_copper_tph
        state["cumulative_flotation_copper_feed_t"] += feed_copper_tph * dt_h
        state["cumulative_flotation_copper_product_t"] += product_copper_tph * dt_h
        state["cumulative_flotation_copper_tailings_t"] += downstream_copper_tph * dt_h
        apparent_recovery = (100.0 * product_copper_tph / feed_copper_tph
                             if feed_copper_tph > 1e-9 else state["copper_recovery_pct"])
        return (product_copper_tph, downstream_copper_tph,
                min(100.0, max(0.0, apparent_recovery)))

    def receive_flotation_reagent(self, state: dict, liters: float) -> float:
        """Receive a simulated reagent delivery into the finite flotation tank."""
        self.migrate(state)
        if not math.isfinite(liters) or liters <= 0.0:
            raise ValueError("Reagent delivery must be positive and finite")
        headroom = state["reagent_tank_capacity_l"] - state["reagent_inventory_l"]
        if liters > headroom + 1e-6:
            raise ValueError(f"Delivery exceeds reagent tank headroom ({max(0.0, headroom):.0f} L)")
        state["reagent_inventory_l"] += liters
        state["reagent_delivery_total_l"] += liters
        return state["reagent_inventory_l"]

    def advance(self, state: dict, sim_seconds: int, events: list[dict], *,
                water_shortage_m3h: float = 0.0, seed: int = 17,
                mine_delivery_t: float | None = None,
                mine_delivery_copper_t: float | None = None,
                mine_excavated_t: float | None = None,
                power_available_fraction: float | None = None,
                equipment_available: dict[str, bool] | None = None) -> None:
        """Advance coupled ore, comminution, flotation, water and power balances."""
        self.migrate(state)
        previous_sim_seconds = state.get("sim_seconds", sim_seconds - 1)
        elapsed_s = max(0, sim_seconds - previous_sim_seconds)
        if mine_excavated_t is None:
            # Standalone process experiments use a nominal mine source; the
            # integrated run supplies the actual tonnes loaded at the shovel.
            mine_excavated_t = 1_120.0 * elapsed_s / 3_600.0
        state["sim_seconds"] = sim_seconds
        state["noise_seed"] = seed
        equipment_available = equipment_available or {
            "tailings_pump": True, "tailings_thickener": True,
            "concentrate_thickener": True, "filter_press": True, "flotation": True, "ball_mill": True,
            "sag_mill": True, "conveyors": True, "screen_deck": True,
            "cone_crusher": True, "secondary_crusher": True, "crusher": True,
        }
        jam = self._severity(events, "Conveyor jam", ("Crushing and screening",))
        upset = self._severity(events, "Process upset", ("Grinding and flotation", "Whole factory"))
        utility = self._severity(events, "Utility loss", ("Mine power network", "Grinding and flotation"))
        measurement = self._severity(events, "Signal drift", ("Grinding and flotation",))
        # Fresh ore carries a gross process-water ratio; thickening and pressing
        # determine what returns. Water shortage constrains throughput separately.
        state["water_specific_m3_t"] = 0.90

        # New blocks change feed grade at the shovel; hardness blends into the
        # mill over the stockpile/circuit residence time rather than teleporting.
        self._advance_geological_blocks(state, sim_seconds, seed, mine_excavated_t)
        state["ore_hardness_factor"] = min(1.42, max(0.78, self._smooth(
            state["ore_hardness_factor"], state["mine_block_hardness_factor"],
            self.ORE_BLEND_RESIDENCE_S)))
        state["ore_hardness_kwh_t"] = 17.0 * state["ore_hardness_factor"]
        mine_grade_pct = state["mine_block_grade_pct"]
        equipment_areas = {"crusher": "Crushing and screening", "conveyors": "Crushing and screening",
                           "secondary_crusher": "Crushing and screening",
                           "cone_crusher": "Crushing and screening", "screen_deck": "Crushing and screening",
                           "sag_mill": "Grinding and flotation", "ball_mill": "Grinding and flotation",
                           "flotation": "Grinding and flotation",
                           "concentrate_thickener": "Concentrate handling",
                           "filter_press": "Concentrate handling",
                           "tailings_thickener": "Tailings and reclaim",
                           "tailings_pump": "Tailings and reclaim",
                           "tailings_decant_pump": "Tailings and reclaim"}
        equipment_faults = {name: self._equipment_fault_severity(events, name, area)
                            for name, area in equipment_areas.items()}
        health = state["equipment_health"]
        for name, fault_factor in equipment_faults.items():
            if equipment_available.get(name, True):
                health[name] = max(0.35, health[name]
                                   - self._mechanical_damage_rate_per_s(fault_factor))
        state["crusher_condition"] = health["crusher"] * (1.0 - equipment_faults["crusher"] * 0.55)
        state["conveyors_condition"] = health["conveyors"] * (1.0 - equipment_faults["conveyors"] * 0.25)
        state["secondary_crusher_condition"] = health["secondary_crusher"] * (
            1.0 - equipment_faults["secondary_crusher"] * 0.45)
        state["cone_crusher_condition"] = health["cone_crusher"] * (
            1.0 - equipment_faults["cone_crusher"] * 0.45)
        state["screen_deck_condition"] = health["screen_deck"] * (
            1.0 - equipment_faults["screen_deck"] * 0.35)
        state["sag_condition"] = health["sag_mill"] * (1.0 - equipment_faults["sag_mill"] * 0.48)
        state["ball_condition"] = health["ball_mill"] * (1.0 - equipment_faults["ball_mill"] * 0.40)
        state["flotation_condition"] = health["flotation"] * (1.0 - equipment_faults["flotation"] * 0.28)
        reagent_pump_fault = self._severity(events, "Mechanical degradation",
                                            ("Grinding and flotation",))
        state["reagent_pump_condition"] = 1.0 - reagent_pump_fault * 0.65
        state["tailings_condition"] = (health["tailings_pump"] * health["tailings_thickener"]
                                        * (1.0 - max(equipment_faults["tailings_pump"],
                                                     equipment_faults["tailings_thickener"]) * 0.65))
        state["tailings_decant_condition"] = (
            health["tailings_decant_pump"]
            * (1.0 - equipment_faults["tailings_decant_pump"] * 0.65))
        state["tailings_decant_vibration_mm_s"] = (
            1.8 + equipment_faults["tailings_decant_pump"] * 11
            + (1.0 - health["tailings_decant_pump"]) * 12)
        state["concentrate_condition"] = (health["concentrate_thickener"] * health["filter_press"]
                                           * (1.0 - max(equipment_faults["concentrate_thickener"],
                                                        equipment_faults["filter_press"]) * 0.35))
        power_factor = (max(0.0, min(1.0, power_available_fraction))
                        if power_available_fraction is not None else max(0.0, 1.0 - utility * 0.82))

        # ROM receiving is finite storage. Integrated runs receive discrete truck
        # payloads; standalone model experiments retain a smooth mine-source input.
        scheduled_rom_feed = 1_120.0 * (1 + 0.07 * math.sin(sim_seconds / 140) + 0.025 * math.sin(sim_seconds / 23))
        jam_factor = 0.0 if jam >= 0.78 else 1.0 - jam
        crusher_running = all(equipment_available.get(name, True) for name in
                              ("crusher", "conveyors", "secondary_crusher", "cone_crusher", "screen_deck"))
        primary_capacity = (1_460.0 * state["crusher_condition"] * state["conveyors_condition"]
                            * jam_factor * power_factor if crusher_running else 0.0)
        screen_efficiency = min(0.94, max(0.68, self.SCREEN_BASE_EFFICIENCY
                                         * state["screen_deck_condition"]))
        state["screen_efficiency_pct"] = 100.0 * screen_efficiency
        # The screen returns oversize to the secondary/cone circuit. Its
        # efficiency therefore multiplies the gross capacity of each closed-
        # circuit stage to give fresh product capacity.
        secondary_capacity = (self.SECONDARY_CRUSHER_CAPACITY_TPH
                             * state["secondary_crusher_condition"] * jam_factor * power_factor
                             if equipment_available.get("secondary_crusher", True) else 0.0)
        cone_capacity = (self.CONE_CRUSHER_CAPACITY_TPH
                         * state["cone_crusher_condition"] * jam_factor * power_factor
                         if equipment_available.get("cone_crusher", True) else 0.0)
        screen_capacity = (self.SCREEN_CAPACITY_TPH * state["screen_deck_condition"]
                           * jam_factor * power_factor
                           if equipment_available.get("screen_deck", True) else 0.0)
        closed_circuit_capacity = min(secondary_capacity, cone_capacity, screen_capacity) * screen_efficiency
        crusher_capacity = min(primary_capacity, closed_circuit_capacity)
        plant_target = 900.0 * (1.0 - upset * 0.18)
        water_factor = min(1.0, max(0.0, 1.0 - water_shortage_m3h / max(plant_target * 0.30, 1.0)))
        dt_h = 1.0 / 3600.0
        tailings_capacity_t = state["tailings_thickener_capacity_t"]
        tailings_storage_capacity_t = state["tailings_storage_capacity_t"]
        storage_space_t = max(0.0, tailings_storage_capacity_t
                              - state["tailings_storage_inventory_t"])
        tailings_storage_backpressure = min(1.0, max(0.0,
            storage_space_t / max(tailings_storage_capacity_t * 0.02, 1.0)))
        tailings_pond_derate_range = max(
            1.0, state["tailings_pond_capacity_m3"]
            * (self.TAILINGS_POND_DERATE_STOP_FRACTION
               - self.TAILINGS_POND_DERATE_START_FRACTION))
        tailings_pond_backpressure = min(1.0, max(0.0,
            (self.TAILINGS_POND_DERATE_STOP_FRACTION * state["tailings_pond_capacity_m3"]
             - state["tailings_pond_inventory_m3"]) / tailings_pond_derate_range))
        state["tailings_pond_backpressure"] = tailings_pond_backpressure
        tailings_sink_ready = (power_factor > 0.05 and equipment_available.get("tailings_thickener", True)
                               and equipment_available.get("tailings_pump", True))
        effective_tailings_pump_capacity = (
            1_400.0 * state["tailings_condition"] * power_factor
            if tailings_sink_ready else 0.0)
        tailings_buffer_headroom_t = max(
            0.0, 0.94 * tailings_capacity_t - state["tailings_thickener_inventory_t"])
        # Let the thickener buffer absorb a bounded one-hour pump/feed mismatch;
        # derate flotation as the effective pump capacity and remaining headroom
        # can no longer accept the expected tailings solids rate.
        tailings_feed_capacity = effective_tailings_pump_capacity + tailings_buffer_headroom_t
        # The plant target is a conservative upper bound for tailings solids
        # (concentrate solids leave the circuit), avoiding a cap-clamped mass loss.
        expected_tailings_feed = max(1.0, plant_target)
        tailings_backpressure = min(
            tailings_storage_backpressure,
            tailings_pond_backpressure,
            max(0.0, min(1.0, tailings_feed_capacity / expected_tailings_feed)))
        if not tailings_sink_ready:
            water_capacity = state["tailings_thickener_water_capacity_m3"]
            water_backpressure = min(1.0, max(0.0,
                (0.94 * water_capacity - state["tailings_thickener_water_inventory_m3"])
                / max(0.14 * water_capacity, 1.0)))
            tailings_backpressure = min(tailings_backpressure, water_backpressure)
        concentrate_capacity_t = state["concentrate_thickener_capacity_t"]
        concentrate_sink_ready = (power_factor > 0.05 and equipment_available.get("concentrate_thickener", True)
                                  and equipment_available.get("filter_press", True))
        concentrate_backpressure = min(1.0, max(0.0,
            (0.94 * concentrate_capacity_t - state["concentrate_thickener_inventory_t"])
            / max(0.14 * concentrate_capacity_t, 1.0)))
        downstream_blocked = (state.get("filter_press_phase") == "DISCHARGING"
                              and state.get("concentrate_cake_inventory_t", 0.0)
                              >= state.get("concentrate_cake_capacity_t", 0.0) - 1e-6)
        if not concentrate_sink_ready or downstream_blocked:
            concentrate_backpressure = 0.0
        plant_target *= min(water_factor, power_factor, tailings_backpressure,
                            concentrate_backpressure)

        sag_power_cap = (21.0 * state["sag_condition"] * power_factor
                         if equipment_available.get("sag_mill", True) else 0.0)
        ball_power_cap = (16.5 * state["ball_condition"] * power_factor
                          if equipment_available.get("ball_mill", True) else 0.0)
        sag_capacity = sag_power_cap * 1_000.0 / max(state["ore_hardness_kwh_t"] * 1.04, 1.0)
        size_fractions = (state["grind_coarse_fraction"], state["grind_intermediate_fraction"],
                          state["grind_fine_fraction"])
        prior_grind_energy_kwh_t = ((state["sag_power_mw"] + state["ball_power_mw"]) * 1_000.0
                                    / max(state["sag_feed_tph"], 100.0))
        grind_intensity = min(1.45, max(0.0,
            prior_grind_energy_kwh_t / max(1.6 * state["ore_hardness_kwh_t"], 1.0)
            * math.sqrt(state["sag_condition"] * state["ball_condition"] * power_factor)))
        (cyclone_overflow_fraction, product_size_fractions, recycled_feed_fractions,
         coarse_breakage, intermediate_breakage) = self._mill_cyclone_pass(size_fractions, grind_intensity)
        cyclone_recirculating_load = (1.0 - cyclone_overflow_fraction) / cyclone_overflow_fraction
        state["cyclone_overflow_fraction"] = cyclone_overflow_fraction
        state["circulating_load_ratio"] = cyclone_recirculating_load
        state["coarse_breakage_per_pass"] = coarse_breakage
        state["intermediate_breakage_per_pass"] = intermediate_breakage
        # Ball-mill power is calibrated against the circulating solids stream:
        # the nominal 5.15 kWh/t is calibrated to the current 8.7 MW reference load.
        ball_circuit_capacity = (ball_power_cap * 1_000.0
                                 / (self.BALL_SPECIFIC_ENERGY_KWH_T * state["ore_hardness_factor"]))
        ball_capacity = ball_circuit_capacity * cyclone_overflow_fraction
        flotation_capacity = (1_240.0 * state["flotation_condition"] * (0.90 + 0.10 * water_factor)
                              if equipment_available.get("flotation", True) else 0.0)
        requested_feed = min(plant_target, sag_capacity, ball_capacity, flotation_capacity)

        rom_space_t = max(0.0, 45_000.0 - state["rom_stockpile_t"])
        if mine_delivery_t is None:
            rom_feed = scheduled_rom_feed
            rom_received = min(rom_feed, rom_space_t * 3600.0) / 3600.0
            rom_received_copper_t = rom_received * mine_grade_pct / 100.0
            curtailed_t = max(0.0, rom_feed * dt_h - rom_received)
            state["rom_feed_tph"] = self._smooth(state["rom_feed_tph"], rom_received * 3600.0, 30)
            state["rom_feed_curtailed_tph"] = curtailed_t * 3600.0
        else:
            mine_delivery_t = max(0.0, mine_delivery_t)
            rom_received = min(mine_delivery_t, rom_space_t)
            offered_copper_t = (mine_delivery_t * mine_grade_pct / 100.0
                                if mine_delivery_copper_t is None
                                else max(0.0, mine_delivery_copper_t))
            rom_received_copper_t = (offered_copper_t * rom_received / mine_delivery_t
                                     if mine_delivery_t > 1e-9 else 0.0)
            curtailed_t = mine_delivery_t - rom_received
            state["rom_delivery_window_t"].append(rom_received)
            state["rom_curtailed_window_t"].append(curtailed_t)
            state["rom_delivery_window_t"] = state["rom_delivery_window_t"][-600:]
            state["rom_curtailed_window_t"] = state["rom_curtailed_window_t"][-600:]
            rom_feed = sum(state["rom_delivery_window_t"]) * 6.0
            state["rom_feed_tph"] = self._smooth(state["rom_feed_tph"], rom_feed, 30)
            state["rom_feed_curtailed_tph"] = (sum(state["rom_curtailed_window_t"])
                                                * 3_600.0 / max(1, len(state["rom_curtailed_window_t"])))
        state["cumulative_mine_copper_received_t"] += rom_received_copper_t
        sag_feeder_enabled = (equipment_available.get("sag_mill", True)
                              and equipment_available.get("ball_mill", True))
        sag_feeder_rate = (min(900.0, state["crushed_stockpile_t"] * 3600.0,
                               max(0.0, 900.0 - state["sag_bin_t"]) * 3600.0)
                           if sag_feeder_enabled else 0.0)
        # The crusher feeds a finite crushed-ore stockpile. When that pile is
        # full, reclaim into the SAG bin is the only room available for more
        # crusher product; upstream ROM reclaim must stop at that limit.
        planned_crushed_rate = min(state["rom_stockpile_t"] * 3600.0,
                                   crusher_capacity, 1_250.0)
        crushed_space_t = max(0.0, 8_000.0 - state["crushed_stockpile_t"])
        crushed_rate = min(planned_crushed_rate,
                           (crushed_space_t + sag_feeder_rate * dt_h) / dt_h)
        # ROM is a surge stockpile; a jam or a full crushed-ore pile closes its
        # feeder so stored ore remains available for orderly restart.
        rom_discharge = crushed_rate
        sag_available_t = state["sag_bin_t"] + sag_feeder_rate / 3600.0
        sag_rate = min(requested_feed, sag_available_t * 3600.0)
        ball_rate = min(sag_rate / cyclone_overflow_fraction, ball_circuit_capacity)
        cyclone_overflow = min(sag_rate, ball_rate * cyclone_overflow_fraction, flotation_capacity)

        state["rom_feed_curtailed_t"] += curtailed_t
        state["cumulative_rom_received_t"] += rom_received
        state["cumulative_rom_discharge_t"] += rom_discharge * dt_h
        state["cumulative_crushed_ore_t"] += crushed_rate * dt_h
        state["cumulative_sag_feeder_t"] += sag_feeder_rate * dt_h
        state["cumulative_sag_processed_t"] += sag_rate * dt_h
        rom_grade_fraction = (state["rom_copper_inventory_t"] / max(state["rom_stockpile_t"], 1e-9)
                              if state["rom_stockpile_t"] > 1e-9 else mine_grade_pct / 100.0)
        crushed_grade_fraction = (state["crushed_copper_inventory_t"]
                                  / max(state["crushed_stockpile_t"], 1e-9)
                                  if state["crushed_stockpile_t"] > 1e-9 else rom_grade_fraction)
        sag_grade_fraction = (state["sag_bin_copper_inventory_t"] / max(state["sag_bin_t"], 1e-9)
                              if state["sag_bin_t"] > 1e-9 else crushed_grade_fraction)
        rom_copper = (state["rom_copper_inventory_t"] + rom_received_copper_t
                      - rom_discharge * dt_h * rom_grade_fraction)
        crushed_copper = (state["crushed_copper_inventory_t"]
                          + crushed_rate * dt_h * rom_grade_fraction
                          - sag_feeder_rate * dt_h * crushed_grade_fraction)
        sag_copper = (state["sag_bin_copper_inventory_t"]
                      + sag_feeder_rate * dt_h * crushed_grade_fraction
                      - sag_rate * dt_h * sag_grade_fraction)
        state["rom_stockpile_t"] = min(45_000.0, max(0.0,
            state["rom_stockpile_t"] + rom_received - rom_discharge * dt_h))
        state["crushed_stockpile_t"] = min(8_000.0, max(0.0,
            state["crushed_stockpile_t"] + (crushed_rate - sag_feeder_rate) * dt_h))
        state["sag_bin_t"] = min(900.0, max(0.0,
            state["sag_bin_t"] + (sag_feeder_rate - sag_rate) * dt_h))
        state["rom_copper_inventory_t"] = min(state["rom_stockpile_t"] * 0.012,
                                               max(0.0, rom_copper))
        state["crushed_copper_inventory_t"] = min(state["crushed_stockpile_t"] * 0.012,
                                                   max(0.0, crushed_copper))
        state["sag_bin_copper_inventory_t"] = min(state["sag_bin_t"] * 0.012,
                                                  max(0.0, sag_copper))
        state["copper_feed_grade_pct"] = 100.0 * sag_grade_fraction

        # Circuit composition changes over the 15-minute grind/cyclone residence
        # as fresh coarse ore mixes with size-dependent cyclone rejects.
        circuit_running = (equipment_available.get("sag_mill", True)
                           and equipment_available.get("ball_mill", True) and power_factor > 0.01)
        if circuit_running:
            for key, next_fraction in zip(("grind_coarse_fraction", "grind_intermediate_fraction",
                                           "grind_fine_fraction"), recycled_feed_fractions):
                state[key] = self._smooth(state[key], next_fraction,
                                          int(self.GRINDING_CIRCUIT_RESIDENCE_S))
        target_p80 = self._log_size_percentile(product_size_fractions)
        if circuit_running:
            state["p80_microns"] = self._smooth(state["p80_microns"], target_p80, 240)

        flotation_air = (540.0 * (1 - equipment_faults["flotation"] * 0.3) * power_factor
                         if equipment_available.get("flotation", True) else 0.0)
        reagent_requested = (self.FLOTATION_REAGENT_DOSE_L_T * cyclone_overflow
                             * (1 + 0.12 * math.sin(sim_seconds / 600)))
        reagent_pump_factor = (state["reagent_pump_condition"] * power_factor
                               if equipment_available.get("reagent_pump", True) else 0.0)
        reagent_capacity = self.FLOTATION_REAGENT_PUMP_CAPACITY_L_H * reagent_pump_factor
        reagent = min(reagent_requested, reagent_capacity,
                      state["reagent_inventory_l"] / max(dt_h, 1e-12))
        state["reagent_requested_l_h"] = reagent_requested
        state["reagent_dose_l_h"] = reagent
        state["reagent_pump_flow_factor"] = (reagent / reagent_requested
                                             if reagent_requested > 1e-9 else 1.0)
        reagent_consumed_l = reagent * dt_h
        state["reagent_inventory_l"] = max(0.0, state["reagent_inventory_l"] - reagent_consumed_l)
        state["reagent_consumed_l"] += reagent_consumed_l
        pulp_flow_m3h = cyclone_overflow * (1.0 / 2.7 + state["water_specific_m3_t"])
        state["flotation_residence_time_min"] = min(90.0, 540.0 * 60.0 / max(pulp_flow_m3h, 1.0))
        # Collector concentration moves through the six well-mixed cells in
        # series. Each compartment uses its physical pulp residence time; a
        # dose loss therefore reaches the final cell and product gradually.
        prior_collector = state["flotation_cell_collector_factor"][:]
        target_collector = min(1.12, max(0.0,
            reagent / (self.FLOTATION_REAGENT_DOSE_L_T * max(cyclone_overflow, 1.0))))
        cell_tau_s = (state["flotation_residence_time_min"] * 60.0
                      / self.FLOTATION_CELL_COUNT)
        collector_response = -math.expm1(-elapsed_s / max(cell_tau_s, 1.0))
        for index in range(self.FLOTATION_CELL_COUNT):
            inlet = target_collector if index == 0 else prior_collector[index - 1]
            old = prior_collector[index]
            state["flotation_cell_collector_factor"][index] = min(1.12, max(0.0,
                old + (inlet - old) * collector_response))
        product_fine_share = product_size_fractions[0]
        size_floatability = min(1.15, max(0.65, 0.80 + 0.40 * product_fine_share / 0.486))
        air_factor = min(1.15, max(0.0, flotation_air / 540.0))
        kinetic_rate_per_min = (0.10 * size_floatability * air_factor
                                * state["flotation_condition"])
        cell_residence_min = state["flotation_residence_time_min"] / self.FLOTATION_CELL_COUNT
        kinetic_recovery = 100.0 * (1.0 - (1.0 + kinetic_rate_per_min * cell_residence_min) **
                                    -self.FLOTATION_CELL_COUNT)
        kinetic_recovery = min(94.0, max(0.0, kinetic_recovery))
        water_recovery_penalty = (1.0 - water_factor) * 17.0
        target_recovery = min(94.0, max(0.0,
            kinetic_recovery - water_recovery_penalty - upset * 12.0))
        # Reduced air/reagent or upset conditions reduce the first-order
        # flotation rate; derive that rate from the six-cell bank's target yield.
        target_cell_recovery = 1.0 - (1.0 - target_recovery / 100.0) ** (
            1.0 / self.FLOTATION_CELL_COUNT)
        cell_residence_h = max(state["flotation_residence_time_min"]
                               / self.FLOTATION_CELL_COUNT / 60.0, 1e-9)
        adjusted_kinetic_rate_per_min = (
            target_cell_recovery / max(1.0 - target_cell_recovery, 1e-9)
            / cell_residence_h / 60.0)
        cell_kinetic_rates_per_min = [
            adjusted_kinetic_rate_per_min * collector_factor ** 0.30
            for collector_factor in state["flotation_cell_collector_factor"]]
        recovered_copper_tph, tailings_copper_tph, recovery = self._advance_flotation_bank(
            state, cyclone_overflow, state["copper_feed_grade_pct"] / 100.0,
            cell_kinetic_rates_per_min, dt_h)
        state["concentrate_grade_pct"] = min(30.0, max(15.0,
            25.0 - max(0.0, state["p80_microns"] - 150) * 0.03))
        concentrate = recovered_copper_tph / max(state["concentrate_grade_pct"] / 100.0, 1e-9)
        tailings = max(0.0, cyclone_overflow - concentrate)
        state["cumulative_concentrate_produced_t"] += concentrate * dt_h
        state["cumulative_concentrate_copper_produced_t"] += recovered_copper_tph * dt_h
        state["cumulative_tailings_received_t"] += tailings * dt_h
        state["cumulative_tailings_copper_received_t"] += tailings_copper_tph * dt_h

        # Concentrate thickening buffers feed to a cyclic filter press. Filling
        # adds cake, squeezing pauses feed, and cake discharge is a short pulse.
        state["filter_press_feed_tph"] = 0.0
        state["filter_press_cake_tph"] = 0.0
        state["filter_press_filtrate_m3h"] = 0.0
        press_cake_copper_tph = 0.0
        press_ready = (power_factor > 0.05 and equipment_available.get("filter_press", True)
                       and equipment_available.get("concentrate_thickener", True))
        if press_ready:
            if state["filter_press_phase"] == "FILLING":
                remaining_batch_t = max(0.0, state["filter_press_batch_capacity_t"]
                                        - state["filter_press_batch_t"])
                available_concentrate_t = (state["concentrate_thickener_inventory_t"]
                                           + concentrate * dt_h - 10.0)
                press_feed = min(42.0 * state["concentrate_condition"] * power_factor,
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
                                cake_room_t / dt_h)
                state["filter_press_cake_tph"] = cake_rate
                batch_grade_fraction = (state["filter_press_batch_copper_inventory_t"]
                                        / max(state["filter_press_batch_t"], 1e-9))
                press_cake_copper_tph = min(
                    cake_rate * batch_grade_fraction,
                    state["filter_press_batch_copper_inventory_t"] / dt_h)
                state["filter_press_batch_copper_inventory_t"] = max(
                    0.0, state["filter_press_batch_copper_inventory_t"]
                    - press_cake_copper_tph * dt_h)
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
        concentrate_tank_solids_before = state["concentrate_thickener_inventory_t"]
        concentrate_tank_copper_before = state["concentrate_thickener_copper_inventory_t"]
        concentrate_tank_grade = (
            (concentrate_tank_copper_before + recovered_copper_tph * dt_h)
            / max(concentrate_tank_solids_before + concentrate * dt_h, 1e-9))
        press_feed_copper_tph = press_feed * concentrate_tank_grade
        state["concentrate_thickener_copper_inventory_t"] = max(
            0.0, concentrate_tank_copper_before
            + recovered_copper_tph * dt_h - press_feed_copper_tph * dt_h)
        state["filter_press_batch_copper_inventory_t"] = max(
            0.0, state["filter_press_batch_copper_inventory_t"]
            + press_feed_copper_tph * dt_h)
        state["concentrate_thickener_inventory_t"] = min(concentrate_capacity_t, max(0.0,
            state["concentrate_thickener_inventory_t"] + (concentrate - press_feed) * dt_h))
        cake_solids_before_shipping = (state["concentrate_cake_inventory_t"]
                                       + state["filter_press_cake_tph"] * dt_h)
        cake_copper_before_shipping = (state["concentrate_cake_copper_inventory_t"]
                                       + press_cake_copper_tph * dt_h)
        press_cake_t = state["filter_press_cake_tph"] * dt_h
        press_cake_copper_t = press_cake_copper_tph * dt_h
        state["concentrate_cake_copper_inventory_t"] = max(
            0.0, cake_copper_before_shipping)
        state["concentrate_cake_inventory_t"] = min(state["concentrate_cake_capacity_t"], max(0.0,
            cake_solids_before_shipping))
        state["cumulative_filter_press_cake_t"] = (
            state.get("cumulative_filter_press_cake_t", 0.0) + press_cake_t)
        state["cumulative_filter_press_cake_copper_t"] = (
            state.get("cumulative_filter_press_cake_copper_t", 0.0) + press_cake_copper_t)
        state["concentrate_shipped_tph"] = 0.0

        # Tailings are a separate solids stream. A stopped thickener/pump train
        # fills its finite inventory, which eventually backpressures flotation.
        tailings_pump_capacity = (min(
            1_400.0 * state["tailings_condition"] * power_factor * tailings_storage_backpressure,
            storage_space_t / max(dt_h, 1e-9)) if tailings_sink_ready else 0.0)
        pumpable_tailings_t = (state["tailings_thickener_inventory_t"] + tailings * dt_h)
        tailings_inventory_error = max(0.0, state["tailings_thickener_inventory_t"] - 1_100.0)
        tailings_pump_request = tailings + tailings_inventory_error * 0.75
        tailings_pumped = min(tailings_pump_capacity, tailings_pump_request,
                              pumpable_tailings_t / dt_h)
        state["tailings_thickener_feed_tph"] = tailings
        tailings_solids_before = state["tailings_thickener_inventory_t"]
        tailings_copper_before = state["tailings_thickener_copper_inventory_t"]
        tailings_solids_available = tailings_solids_before + tailings * dt_h
        tailings_copper_available = tailings_copper_before + tailings_copper_tph * dt_h
        tailings_grade_fraction = (tailings_copper_available
                                   / max(tailings_solids_available, 1e-9))
        pumped_copper_t = min(tailings_copper_available,
                              tailings_pumped * dt_h * tailings_grade_fraction)
        state["tailings_thickener_copper_inventory_t"] = max(
            0.0, tailings_copper_available - pumped_copper_t)
        state["cumulative_tailings_copper_pumped_t"] += pumped_copper_t
        state["tailings_thickener_inventory_t"] = min(tailings_capacity_t, max(0.0,
            state["tailings_thickener_inventory_t"] + (tailings - tailings_pumped) * dt_h))
        state["cumulative_tailings_pumped_t"] += tailings_pumped * dt_h
        deposited_t = min(tailings_pumped * dt_h,
                          max(0.0, tailings_storage_capacity_t
                              - state["tailings_storage_inventory_t"]))
        state["tailings_storage_inventory_t"] += deposited_t
        state["tailings_storage_copper_inventory_t"] += pumped_copper_t
        state["tailings_deposition_tph"] = deposited_t / dt_h
        state["cumulative_tailings_deposited_t"] += deposited_t
        state["cumulative_tailings_copper_deposited_t"] += pumped_copper_t

        state["crusher_feed_tph"] = self._smooth(state["crusher_feed_tph"], crushed_rate, 45)
        state["crushed_ore_tph"] = state["crusher_feed_tph"]
        closed_circuit_feed = crushed_rate / max(screen_efficiency, 1e-9)
        state["secondary_crusher_feed_tph"] = closed_circuit_feed
        state["cone_crusher_feed_tph"] = closed_circuit_feed
        state["screen_feed_tph"] = closed_circuit_feed
        state["screen_oversize_tph"] = max(0.0, closed_circuit_feed - crushed_rate)
        state["plant_feed_tph"] = self._smooth(state["plant_feed_tph"], cyclone_overflow, 75)
        state["sag_feed_tph"] = self._smooth(state["sag_feed_tph"], sag_rate, 65)
        state["ball_circuit_feed_tph"] = self._smooth(state["ball_circuit_feed_tph"], ball_rate, 75)
        state["cyclone_overflow_tph"] = state["plant_feed_tph"]
        state["tailings_tph"] = self._smooth(state["tailings_tph"], tailings_pumped, 60)
        state["concentrate_tph"] = self._smooth(state["concentrate_tph"], concentrate, 120)
        state["copper_recovery_pct"] = self._smooth(state["copper_recovery_pct"], recovery, 180)
        sag_power = min(sag_power_cap, sag_rate * state["ore_hardness_kwh_t"] * 1.04 / 1_000)
        ball_power = min(ball_power_cap, ball_rate * self.BALL_SPECIFIC_ENERGY_KWH_T
                         * state["ore_hardness_factor"] / 1_000)
        state["sag_power_mw"] = self._smooth(state["sag_power_mw"], sag_power, 15)
        state["ball_power_mw"] = self._smooth(state["ball_power_mw"], ball_power, 15)
        state["crusher_power_mw"] = self._smooth(state["crusher_power_mw"], 2.2 * (crushed_rate / 1_200), 10)
        gross_process_water = cyclone_overflow * state["water_specific_m3_t"]
        total_solids = tailings + concentrate
        tails_fraction = tailings / total_solids if total_solids > 1e-9 else 0.0
        tailings_water_feed = gross_process_water * tails_fraction
        concentrate_water_feed = gross_process_water - tailings_water_feed
        tailings_underflow_solids = min(70.0, max(55.0, 68.0 - upset * 10.0))
        underflow_water_ratio = (100.0 - tailings_underflow_solids) / tailings_underflow_solids
        tailings_underflow_water = tailings_pumped * underflow_water_ratio
        tailings_pore_capacity_m3 = (state["tailings_storage_inventory_t"]
                                     * self.TAILINGS_PORE_WATER_M3_PER_T)
        tailings_pore_water_available_m3 = max(
            0.0, tailings_pore_capacity_m3 - state["tailings_storage_water_inventory_m3"])
        tailings_water_retained_m3 = min(
            tailings_underflow_water * dt_h, tailings_pore_water_available_m3)
        state["tailings_storage_water_inventory_m3"] += tailings_water_retained_m3
        state["cumulative_tailings_water_retained_m3"] += tailings_water_retained_m3
        concentrate_underflow_water = concentrate * (1.0 / 0.60 - 1.0)
        concentrate_overflow = max(0.0, concentrate_water_feed - concentrate_underflow_water)
        concentrate_thickener_return = (concentrate_overflow * 0.92 * power_factor
                                        if equipment_available.get("concentrate_thickener", True) else 0.0)
        filtrate_return = (state["filter_press_filtrate_m3h"]
                           if equipment_available.get("filter_press", True) else 0.0)
        water_inventory_before_overflow = max(0.0,
            state["tailings_thickener_water_inventory_m3"]
            + (tailings_water_feed - tailings_underflow_water) * dt_h)
        target_water_hold_m3 = state["tailings_thickener_inventory_t"] * underflow_water_ratio
        excess_water_m3 = max(0.0, water_inventory_before_overflow - target_water_hold_m3)
        thickener_online = (power_factor > 0.05
                            and equipment_available.get("tailings_thickener", True))
        overflow_capacity_m3 = (state["tailings_thickener_overflow_capacity_m3h"]
                                * power_factor * dt_h
                                if thickener_online else 0.0)
        tailings_overflow_m3 = min(excess_water_m3, overflow_capacity_m3)
        water_after_overflow_m3 = water_inventory_before_overflow - tailings_overflow_m3
        water_spill_m3 = max(0.0, water_after_overflow_m3
                             - state["tailings_thickener_water_capacity_m3"])
        state["tailings_thickener_water_inventory_m3"] = min(
            state["tailings_thickener_water_capacity_m3"], max(0.0, water_after_overflow_m3))
        tailings_overflow_water = tailings_overflow_m3 / dt_h
        tailings_return = (tailings_overflow_water * max(0.0, 0.92 - upset * 0.24)
                           if thickener_online else 0.0)
        other_recovered_water = tailings_return + concentrate_thickener_return + filtrate_return
        pond_available = (state["tailings_pond_inventory_m3"]
                          + tailings_underflow_water * dt_h - tailings_water_retained_m3)
        evaporation_m3 = min(self.TAILINGS_EVAPORATION_M3H * dt_h, pond_available)
        pond_after_evaporation = pond_available - evaporation_m3
        decant_request = max(0.0, (pond_after_evaporation - self.TAILINGS_POND_TARGET_M3)
                             * 3_600.0 / self.TAILINGS_DECANT_LEVELING_S)
        decant_pump_running = (power_factor > 0.05
                               and equipment_available.get("tailings_decant_pump", True))
        decant_capacity = min(self.TAILINGS_DECANT_CAPACITY_M3H,
                              max(0.0, gross_process_water - other_recovered_water))
        decant_capacity *= (state["tailings_decant_condition"]
                            if decant_pump_running else 0.0)
        tailings_decant = min(decant_request, decant_capacity,
                              pond_after_evaporation * 3_600.0)
        pond_after_decant = max(0.0, pond_after_evaporation - tailings_decant * dt_h)
        state["tailings_pond_spill_m3"] = max(
            0.0, pond_after_decant - state["tailings_pond_capacity_m3"])
        state["tailings_pond_inventory_m3"] = min(
            state["tailings_pond_capacity_m3"], pond_after_decant)
        state["tailings_decant_water_m3h"] = tailings_decant
        state["tailings_decant_motor_running"] = float(decant_pump_running)
        state["tailings_decant_pump_power_mw"] = (
            9.81 * self.TAILINGS_DECANT_HEAD_M * tailings_decant
            / (3_600.0 * self.TAILINGS_DECANT_PUMP_EFFICIENCY
               * max(state["tailings_decant_condition"], 0.35) * 1_000.0))
        state["cumulative_tailings_decant_m3"] += tailings_decant * dt_h
        state["cumulative_tailings_pond_spill_m3"] += state["tailings_pond_spill_m3"]
        state["cumulative_tailings_pond_evaporation_m3"] += evaporation_m3
        state["tailings_thickener_overflow_water_m3h"] = tailings_overflow_water
        state["tailings_underflow_water_m3h"] = tailings_underflow_water
        state["tailings_thickener_spill_m3"] = water_spill_m3
        state["cumulative_tailings_water_feed_m3"] += tailings_water_feed * dt_h
        state["cumulative_tailings_underflow_water_m3"] += tailings_underflow_water * dt_h
        state["cumulative_tailings_overflow_water_m3"] += tailings_overflow_m3
        state["cumulative_tailings_water_spill_m3"] += water_spill_m3
        recovered_water = other_recovered_water + tailings_decant
        state["gross_process_water_m3h"] = gross_process_water
        state["tailings_reclaim_water_m3h"] = tailings_return
        state["tailings_reclaim_water_m3h"] += tailings_decant
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
        powered = power_factor > 0.05
        state["crusher_motor_running"] = float(powered and equipment_available.get("crusher", True))
        state["conveyors_motor_running"] = float(powered and equipment_available.get("conveyors", True))
        state["secondary_crusher_motor_running"] = float(
            powered and equipment_available.get("secondary_crusher", True))
        state["cone_crusher_motor_running"] = float(
            powered and equipment_available.get("cone_crusher", True))
        state["screen_deck_motor_running"] = float(
            powered and equipment_available.get("screen_deck", True))
        state["sag_motor_running"] = float(powered and equipment_available.get("sag_mill", True))
        state["ball_motor_running"] = float(powered and equipment_available.get("ball_mill", True))
        state["flotation_motor_running"] = float(powered and equipment_available.get("flotation", True))
        state["reagent_pump_running"] = float(powered and reagent_pump_factor > 0.01 and reagent > 0.0)
        state["tailings_motor_running"] = float(powered and equipment_available.get("tailings_pump", True))
        state["crusher_vibration_mm_s"] = (2.4 + equipment_faults["crusher"] * 10
                                             + (1.0 - health["crusher"]) * 12 + jam * 5)
        state["secondary_crusher_vibration_mm_s"] = (
            2.7 + equipment_faults["secondary_crusher"] * 10
            + (1.0 - health["secondary_crusher"]) * 12 + jam * 4)
        state["cone_crusher_vibration_mm_s"] = (
            2.6 + equipment_faults["cone_crusher"] * 10
            + (1.0 - health["cone_crusher"]) * 12 + jam * 4)
        state["screen_deck_vibration_mm_s"] = (
            2.0 + equipment_faults["screen_deck"] * 8
            + (1.0 - health["screen_deck"]) * 10 + jam * 2)
        state["sag_vibration_mm_s"] = (2.8 + equipment_faults["sag_mill"] * 8
                                       + (1.0 - health["sag_mill"]) * 12)
        state["ball_vibration_mm_s"] = (2.4 + equipment_faults["ball_mill"] * 7
                                        + (1.0 - health["ball_mill"]) * 10)
        state["flotation_vibration_mm_s"] = (1.8 + equipment_faults["flotation"] * 4
                                             + (1.0 - health["flotation"]) * 8)
        tailings_health = min(health["tailings_pump"], health["tailings_thickener"])
        state["tailings_vibration_mm_s"] = (2.1 + max(
            equipment_faults["tailings_pump"], equipment_faults["tailings_thickener"]) * 11
                                            + (1.0 - tailings_health) * 12)
        state["tailings_thickener_motor_running"] = float(
            powered and equipment_available.get("tailings_thickener", True))
        state["concentrate_thickener_motor_running"] = float(
            powered and equipment_available.get("concentrate_thickener", True))
        state["filter_press_motor_running"] = float(powered and equipment_available.get("filter_press", True))
        state["conveyor_power_mw"] = 0.45 * (crushed_rate / 1_200.0) * state["conveyors_motor_running"]
        state["secondary_crusher_power_mw"] = (
            0.75 * state["secondary_crusher_feed_tph"] / 1_000.0
            * state["secondary_crusher_motor_running"])
        state["cone_crusher_power_mw"] = (
            1.05 * state["cone_crusher_feed_tph"] / 1_000.0
            * state["cone_crusher_motor_running"])
        state["screen_power_mw"] = (0.22 * state["screen_feed_tph"] / 1_000.0
                                     * state["screen_deck_motor_running"])
        state["flotation_air_power_mw"] = flotation_air * 60.0 * 0.05 / 1_000.0
        state["flotation_aux_power_mw"] = (0.35 * min(1.0, cyclone_overflow / 900.0)
                                           * state["flotation_motor_running"])
        state["tailings_pump_power_mw"] = (0.62 * min(1.0, tailings_pumped / 1_400.0)
                                            / max(state["tailings_condition"], 0.35)
                                            * state["tailings_motor_running"])
        state["tailings_thickener_power_mw"] = (0.18 * min(1.0, tailings / 900.0)
                                                * state["tailings_thickener_motor_running"])
        state["concentrate_thickener_power_mw"] = (0.11 * min(1.0, concentrate / 26.0)
                                                   * (1.0 + (1.0 - state["concentrate_condition"]))
                                                   * state["concentrate_thickener_motor_running"])
        state["filter_press_power_mw"] = (0.12 * state["filter_press_motor_running"]
                                          * float(state["filter_press_phase"] in ("FILLING", "SQUEEZING")))
        state["process_aux_power_mw"] = 0.50
        state["total_power_mw"] = sum(state[key] for key in (
            "sag_power_mw", "ball_power_mw", "crusher_power_mw", "conveyor_power_mw",
            "secondary_crusher_power_mw", "cone_crusher_power_mw", "screen_power_mw",
            "flotation_air_power_mw", "flotation_aux_power_mw", "tailings_pump_power_mw",
            "tailings_decant_pump_power_mw",
            "tailings_thickener_power_mw", "concentrate_thickener_power_mw",
            "filter_press_power_mw", "process_aux_power_mw"))

        if sim_seconds % 15 == 0:
            state["history"].append({"time": sim_seconds, "rom_stockpile_t": state["rom_stockpile_t"],
                "crushed_stockpile_t": state["crushed_stockpile_t"], "sag_bin_t": state["sag_bin_t"],
                "plant_feed_tph": state["plant_feed_tph"],
                "copper_feed_grade_pct": state["copper_feed_grade_pct"],
                "mine_block_grade_pct": state["mine_block_grade_pct"],
                "copper_recovery_pct": state["copper_recovery_pct"], "p80_microns": state["p80_microns"],
                "flotation_copper_inventory_t": state["flotation_copper_inventory_t"],
                "reagent_inventory_l": state["reagent_inventory_l"],
                "reagent_dose_l_h": state["reagent_dose_l_h"],
                "secondary_crusher_feed_tph": state["secondary_crusher_feed_tph"],
                "cone_crusher_feed_tph": state["cone_crusher_feed_tph"],
                "screen_feed_tph": state["screen_feed_tph"],
                "screen_oversize_tph": state["screen_oversize_tph"],
                "screen_efficiency_pct": state["screen_efficiency_pct"],
                **{f"collector_availability_cell_{index + 1:02d}":
                   100.0 * state["flotation_cell_collector_factor"][index]
                   for index in range(self.FLOTATION_CELL_COUNT)},
                "tailings_thickener_inventory_t": state["tailings_thickener_inventory_t"],
                "tailings_thickener_water_inventory_m3": state["tailings_thickener_water_inventory_m3"],
                "grind_coarse_fraction": state["grind_coarse_fraction"],
                "grind_intermediate_fraction": state["grind_intermediate_fraction"],
                "grind_fine_fraction": state["grind_fine_fraction"],
                "coarse_breakage_per_pass": state["coarse_breakage_per_pass"],
                "intermediate_breakage_per_pass": state["intermediate_breakage_per_pass"],
                "flotation_residence_time_min": state["flotation_residence_time_min"],
                "concentrate_thickener_inventory_t": state["concentrate_thickener_inventory_t"],
                "concentrate_cake_inventory_t": state["concentrate_cake_inventory_t"],
                "water_makeup_m3h": state["water_makeup_m3h"],
                "concentrator_reclaim_m3h": state["concentrator_reclaim_m3h"],
                "power_mw": state["total_power_mw"]})
            state["history"] = state["history"][-1_440:]

        # Measurement drift is applied only to the process quality/reading surface,
        # not the plant's internal mass or energy balances.
        state["measurement_drift"] = measurement

    def telemetry(self, state: dict) -> list[dict]:
        """Translate process truth into stable, noisy SCADA tag records."""
        readings = {
            "Open-pit mining": {
                "Pit sector A": {"ore_rate": (state["rom_feed_tph"], "t/h", 0, 2_000),
                    "ore_grade": (state["mine_block_grade_pct"], "% Cu", 0.2, 1.2),
                    "ore_block_id": (state["mine_block_index"], "block", 0, 100_000),
                    "ore_domain_code": (state["mine_block_domain_code"], "enum", 0,
                                         len(self.GEOLOGICAL_DOMAINS) - 1),
                    "block_hardness_factor": (state["mine_block_hardness_factor"], "pu", 0.5, 1.5),
                    "block_mined": (state["mine_block_mined_t"], "t", 0,
                                    self.MAX_BLOCK_TONNAGE_T),
                    "block_target": (state["mine_block_target_t"], "t", 0,
                                     self.MAX_BLOCK_TONNAGE_T),
                    "block_remaining": (state["mine_block_remaining_t"], "t", 0,
                                        self.MAX_BLOCK_TONNAGE_T),
                    "rom_stockpile_grade": (100.0 * state["rom_copper_inventory_t"]
                                             / max(state["rom_stockpile_t"], 1e-9), "% Cu", 0.0, 1.2),
                    "rom_stockpile": (state["rom_stockpile_t"], "t", 0, 45_000),
                    "mine_feed_curtailed": (state["rom_feed_curtailed_tph"], "t/h", 0, 2_000)},
            },
            "Crushing and screening": {
                "Primary crusher": {"ore_rate": (state["crushed_ore_tph"], "t/h", 0, 2_000),
                    "motor_power": (state["crusher_power_mw"], "MW", 0, 5),
                    "motor_current": (self._motor_current_a(state["crusher_power_mw"]), "A", 0, 500),
                    "vibration_rms": (state["crusher_vibration_mm_s"], "mm/s", 0, 30),
                    "equipment_running": (state.get("crusher_motor_running", state["crushing_running"]), "bool", 0, 1)},
                "Secondary crusher": {"feed_rate": (state["secondary_crusher_feed_tph"], "t/h", 0, 2_500),
                    "motor_power": (state["secondary_crusher_power_mw"], "MW", 0, 3),
                    "motor_current": (self._motor_current_a(state["secondary_crusher_power_mw"]), "A", 0, 500),
                    "vibration_rms": (state["secondary_crusher_vibration_mm_s"], "mm/s", 0, 30),
                    "equipment_running": (state["secondary_crusher_motor_running"], "bool", 0, 1)},
                "Cone crusher 01": {"feed_rate": (state["cone_crusher_feed_tph"], "t/h", 0, 2_500),
                    "motor_power": (state["cone_crusher_power_mw"], "MW", 0, 4),
                    "motor_current": (self._motor_current_a(state["cone_crusher_power_mw"]), "A", 0, 700),
                    "vibration_rms": (state["cone_crusher_vibration_mm_s"], "mm/s", 0, 30),
                    "equipment_running": (state["cone_crusher_motor_running"], "bool", 0, 1)},
                "Screen deck A": {"feed_rate": (state["screen_feed_tph"], "t/h", 0, 2_500),
                    "oversize_return": (state["screen_oversize_tph"], "t/h", 0, 1_000),
                    "screen_efficiency": (state["screen_efficiency_pct"], "%", 50, 100),
                    "motor_power": (state["screen_power_mw"], "MW", 0, 1),
                    "motor_current": (self._motor_current_a(state["screen_power_mw"]), "A", 0, 200),
                    "vibration_rms": (state["screen_deck_vibration_mm_s"], "mm/s", 0, 30),
                    "equipment_running": (state["screen_deck_motor_running"], "bool", 0, 1)},
                "Ore conveyors": {"ore_rate": (state["crushed_ore_tph"], "t/h", 0, 2_000),
                    "stockpile_inventory": (state["crushed_stockpile_t"], "t", 0, 8_000),
                    "motor_power": (state["conveyor_power_mw"], "MW", 0, 1),
                    "belt_speed": (3.1 * state["crushing_running"], "m/s", 0, 8),
                    "equipment_running": (state.get("conveyors_motor_running", state["crushing_running"]), "bool", 0, 1)},
            },
            "Grinding and flotation": {
                "Flotation reagent tank": {
                    "inventory": (state["reagent_inventory_l"], "L", 0, state["reagent_tank_capacity_l"]),
                    "level": (100.0 * state["reagent_inventory_l"] / max(state["reagent_tank_capacity_l"], 1e-9), "%", 0, 100),
                    "cumulative_consumption": (state["reagent_consumed_l"], "L", 0, 1_000_000),
                },
                "Flotation reagent dosing pump": {
                    "dosing_flow": (state["reagent_dose_l_h"], "L/h", 0, 500),
                    "requested_flow": (state["reagent_requested_l_h"], "L/h", 0, 500),
                    "flow_fraction": (100.0 * state["reagent_pump_flow_factor"], "%", 0, 120),
                    "operating_condition": (100.0 * state["reagent_pump_condition"], "%", 0, 100),
                    "equipment_running": (state.get("reagent_pump_running", 0.0), "bool", 0, 1),
                },
                "SAG mill": {"ore_rate": (state["sag_feed_tph"], "t/h", 0, 2_000),
                    "motor_power": (state["sag_power_mw"], "MW", 0, 30),
                    "motor_current": (self._motor_current_a(state["sag_power_mw"]), "A", 0, 2_500),
                    "vibration_rms": (state["sag_vibration_mm_s"], "mm/s", 0, 30),
                    "equipment_running": (state.get("sag_motor_running", state["sag_running"]), "bool", 0, 1),
                    "ore_hardness": (state["ore_hardness_kwh_t"], "kWh/t", 8, 32)},
                "Ball mill 01": {"ore_rate": (state["ball_circuit_feed_tph"], "t/h", 0, 4_000),
                    "motor_power": (state["ball_power_mw"], "MW", 0, 20),
                    "motor_current": (self._motor_current_a(state["ball_power_mw"]), "A", 0, 2_500),
                    "vibration_rms": (state["ball_vibration_mm_s"], "mm/s", 0, 30),
                    "coarse_breakage_per_pass": (100 * state["coarse_breakage_per_pass"], "%", 0, 100),
                    "intermediate_breakage_per_pass": (100 * state["intermediate_breakage_per_pass"], "%", 0, 100),
                    "equipment_running": (state.get("ball_motor_running", state["ball_running"]), "bool", 0, 1)},
                "Cyclone cluster": {"ore_rate": (state["cyclone_overflow_tph"], "t/h", 0, 2_000),
                    "product_p80": (state["p80_microns"], "µm", 50, 400),
                    "circulating_load": (100 * state["circulating_load_ratio"], "%", 0, 300),
                    "coarse_size_fraction": (100 * state["grind_coarse_fraction"], "%", 0, 100),
                    "intermediate_size_fraction": (100 * state["grind_intermediate_fraction"], "%", 0, 100),
                    "fine_size_fraction": (100 * state["grind_fine_fraction"], "%", 0, 100),
                    "cyclone_overflow_partition": (100 * state["cyclone_overflow_fraction"], "%", 0, 100),
                    "equipment_running": (float(state["cyclone_overflow_tph"] > 5), "bool", 0, 1)},
                "Flotation cells": {"ore_rate": (state["plant_feed_tph"], "t/h", 0, 2_000),
                    "feed_grade": (state["copper_feed_grade_pct"], "% Cu", 0.2, 1.2),
                    "copper_recovery": (state["copper_recovery_pct"], "%", 0, 100),
                    "copper_inventory": (state["flotation_copper_inventory_t"], "t Cu", 0, 10),
                    "copper_product_rate": (state["flotation_copper_product_tph"], "t Cu/h", 0, 25),
                    **{f"collector_availability_cell_{index + 1:02d}": (
                        100.0 * state["flotation_cell_collector_factor"][index], "%", 0, 120)
                       for index in range(self.FLOTATION_CELL_COUNT)},
                    "concentrate_grade": (state["concentrate_grade_pct"], "%", 0, 40),
                    "concentrate_rate": (state["concentrate_tph"], "t/h", 0, 100),
                    "air_flow": (state["flotation_air_nm3_min"], "Nm³/min", 0, 1_000),
                    "reagent_rate": (state["reagent_l_h"], "L/h", 0, 1_000),
                    "cell_drive_power": (state["flotation_aux_power_mw"], "MW", 0, 2),
                    "residence_time": (state["flotation_residence_time_min"], "min", 0, 90),
                    "equipment_running": (state.get("flotation_motor_running", state["flotation_running"]), "bool", 0, 1)},
                "Flotation air blower": {"air_flow": (state["flotation_air_nm3_min"], "Nm³/min", 0, 1_000),
                    "motor_power": (state["flotation_air_power_mw"], "MW", 0, 5),
                    "equipment_running": (float(state["flotation_air_nm3_min"] > 1), "bool", 0, 1)},
            },
            "Concentrate handling": {
                "Concentrate thickener": {
                    "feed_rate": (state["concentrate_tph"], "t/h", 0, 100),
                    "copper_inventory": (state["concentrate_thickener_copper_inventory_t"], "t Cu", 0, 40),
                    "tank_level": (8 + state["concentrate_thickener_inventory_t"]
                                   / state["concentrate_thickener_capacity_t"] * 84, "%", 0, 100),
                    "underflow_rate": (state["filter_press_feed_tph"], "t/h", 0, 60),
                    "motor_power": (state["concentrate_thickener_power_mw"], "MW", 0, 1),
                    "equipment_running": (state["concentrate_thickener_motor_running"], "bool", 0, 1)},
                "Filter press": {
                    "feed_rate": (state["filter_press_feed_tph"], "t/h", 0, 60),
                    "cake_rate": (state["filter_press_cake_tph"], "t/h", 0, 700),
                    "cake_stockpile": (state["concentrate_cake_inventory_t"], "t", 0, 500),
                    "cake_copper_inventory": (state["concentrate_cake_copper_inventory_t"], "t Cu", 0, 125),
                    "cake_moisture": (9.0 + (1 - state["concentrate_condition"]) * 12, "%", 0, 40),
                    "motor_power": (state["filter_press_power_mw"], "MW", 0, 1),
                    "cycle_count": (float(state["filter_press_cycle_count"]), "cycles", 0, 100_000),
                    "equipment_running": (state["filter_press_motor_running"], "bool", 0, 1)},
            },
            "Tailings and reclaim": {
                "Tailings thickener": {"feed_rate": (state["tailings_thickener_feed_tph"], "t/h", 0, 2_000),
                    "copper_inventory": (state["tailings_thickener_copper_inventory_t"], "t Cu", 0, 25),
                    "copper_grade": (100.0 * state["tailings_thickener_copper_inventory_t"]
                                     / max(state["tailings_thickener_inventory_t"], 1e-9), "% Cu", 0, 2),
                    "tank_level": (state["tailings_thickener_inventory_t"]
                                   / state["tailings_thickener_capacity_t"] * 100, "%", 0, 100),
                    "motor_power": (state["tailings_thickener_power_mw"], "MW", 0, 1),
                    "water_inventory": (state["tailings_thickener_water_inventory_m3"], "m³", 0,
                                        state["tailings_thickener_water_capacity_m3"]),
                    "water_level": (state["tailings_thickener_water_inventory_m3"]
                                    / state["tailings_thickener_water_capacity_m3"] * 100, "%", 0, 100),
                    "overflow_flow": (state["tailings_thickener_overflow_water_m3h"], "m³/h", 0, 800),
                    "underflow_water": (state["tailings_underflow_water_m3h"], "m³/h", 0, 800),
                    "water_spill": (state["tailings_thickener_spill_m3"], "m³", 0, 20),
                    "underflow_rate": (state["tailings_tph"], "t/h", 0, 2_000),
                    "equipment_running": (state["tailings_thickener_motor_running"], "bool", 0, 1)},
                "Tailings pump station A": {"ore_rate": (state["tailings_tph"], "t/h", 0, 2_000),
                    "motor_power": (state["tailings_pump_power_mw"], "MW", 0, 2),
                    "motor_current": (self._motor_current_a(state["tailings_pump_power_mw"]), "A", 0, 250),
                    "vibration_rms": (state["tailings_vibration_mm_s"], "mm/s", 0, 30),
                    "equipment_running": (state.get("tailings_motor_running", float(state["tailings_tph"] > 5)), "bool", 0, 1)},
                "Tailings storage facility": {
                    "deposition_rate": (state["tailings_deposition_tph"], "t/h", 0, 2_000),
                    "solids_inventory": (state["tailings_storage_inventory_t"], "t", 0,
                                         state["tailings_storage_capacity_t"]),
                    "solids_occupancy": (100.0 * state["tailings_storage_inventory_t"]
                                         / max(state["tailings_storage_capacity_t"], 1e-9), "%", 0, 100),
                    "copper_inventory": (state["tailings_storage_copper_inventory_t"], "t Cu", 0, 30),
                    "retained_water_inventory": (state["tailings_storage_water_inventory_m3"], "m³", 0,
                                                 self.TAILINGS_STORAGE_CAPACITY_T
                                                 * self.TAILINGS_PORE_WATER_M3_PER_T),
                    "decant_pond_inventory": (state["tailings_pond_inventory_m3"], "m³", 0,
                                               state["tailings_pond_capacity_m3"]),
                    "decant_pond_level": (100.0 * state["tailings_pond_inventory_m3"]
                                           / max(state["tailings_pond_capacity_m3"], 1e-9), "%", 0, 100),
                    "high_level_feed_factor": (state["tailings_pond_backpressure"], "pu", 0, 1),
                    "pond_spill": (state["tailings_pond_spill_m3"], "m³", 0, 20),
                },
                "Tailings decant pump": {
                    "return_flow": (state["tailings_decant_water_m3h"], "m³/h", 0, 600),
                    "motor_power": (state["tailings_decant_pump_power_mw"], "MW", 0, 0.2),
                    "motor_current": (
                        self._motor_current_a(state["tailings_decant_pump_power_mw"], voltage_kv=0.4),
                        "A", 0, 100),
                    "vibration_rms": (state["tailings_decant_vibration_mm_s"], "mm/s", 0, 30),
                    "equipment_running": (state["tailings_decant_motor_running"], "bool", 0, 1),
                    "operating_condition": (state["tailings_decant_condition"], "pu", 0, 1),
                },
            },
        }
        rows = []
        for area, assets in readings.items():
            for asset, signals in assets.items():
                for signal, (truth, units, low, high) in signals.items():
                    tag = f"{area.lower().replace(' ', '_')}.{asset.lower().replace(' ', '_')}.{signal}"
                    stable_id = sum((i + 1) * ord(char) for i, char in enumerate(tag))
                    rng = random.Random(state["noise_seed"] * 1_000_003 + state["sim_seconds"] * 9_176 + stable_id)
                    noise = (0.0 if units in ("bool", "cycles", "enum", "block") else
                             rng.gauss(0.0, max(high-low, 1e-9) *
                                       (0.0015 if signal in ("product_p80", "copper_recovery") else 0.002)))
                    value = truth + noise
                    if signal in ("ore_rate", "product_p80"):
                        value *= 1.0 + state.get("measurement_drift", 0.0)
                    rows.append({"tag": tag, "area": area, "unit": asset, "sensor": signal, "units": units,
                        "value": round(min(high, max(low, value)), 3), "quality": "GOOD", "low": low,
                        "high": high, "base": (low + high) / 2, "phase": 0.0,
                        "last_change": "Dynamic concentrator process model"})
        for equipment, (label, _controller_key) in self.MAINTAINABLE_EQUIPMENT.items():
            area = self.MAINTENANCE_AREAS[equipment]
            tag = f"{area.lower().replace(' ', '_')}.{label.lower().replace(' ', '_')}.condition"
            rows.append({"tag": tag, "area": area, "unit": label, "sensor": "condition",
                         "units": "%", "value": round(100.0 * state["equipment_health"][equipment], 2),
                         "quality": "GOOD", "low": 0.0, "high": 100.0, "base": 100.0,
                         "phase": 0.0, "last_change": "Persistent asset condition; maintenance restores health"})
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
