"""Low-compute equalization, pH neutralization, DAF, and sludge handling."""
from __future__ import annotations

import math

try:
    from .instrumentation import correlated_sensor_noise
except ImportError:
    from instrumentation import correlated_sensor_noise

class WastewaterTreatmentPlant:
    EQ_CAPACITY_M3 = 300.0
    TREATMENT_TANK_CAPACITY_M3 = 25.0
    DESIGN_HYDRAULIC_RETENTION_MIN = 20.0
    MIN_PROCESS_VOLUME_M3 = 1.0
    HYDRAULIC_CAPACITY_M3H = 75.0
    ACID_EQUIVALENT_KG = 0.03646  # HCl kg per neutralization equivalent.
    BASE_EQUIVALENT_KG = 0.04000  # NaOH kg per neutralization equivalent.
    DAF_SOLIDS_CAPACITY_KGH = 65.0
    SLUDGE_HOPPER_CAPACITY_KG = 500.0
    PRESS_CAPACITY_KGH = 55.0

    def initialize(self) -> dict:
        return {
            "equalization_volume_m3": 0.0, "equalization_capacity_m3": self.EQ_CAPACITY_M3,
            "equalization_level_pct": 0.0, "equalization_ph": 7.0,
            "equalization_alkalinity_eq": 0.0, "equalization_conductivity_us_cm": 800.0,
            "equalization_conductivity_mass": 0.0, "equalization_tss_kg": 0.0,
            "treatment_mode": "STANDBY", "treatment_power_fraction": 1.0,
            "treatment_tank_volume_m3": 0.0,
            "treatment_tank_capacity_m3": self.TREATMENT_TANK_CAPACITY_M3,
            "treatment_tank_level_pct": 0.0,
            "treatment_tank_alkalinity_eq": 0.0,
            "treatment_tank_conductivity_mass": 0.0,
            "treatment_tank_tss_kg": 0.0,
            "influent_flow_m3h": 0.0, "influent_cip_flow_m3h": 0.0,
            "influent_backwash_flow_m3h": 0.0, "treatment_feed_flow_m3h": 0.0,
            "treatment_flow_m3h": 0.0,
            "reuse_flow_m3h": 0.0, "outfall_flow_m3h": 0.0,
            "sludge_cake_water_m3h": 0.0, "bypass_flow_m3h": 0.0,
            "effluent_ph": 7.0, "effluent_tss_mg_l": 0.0,
            "effluent_conductivity_us_cm": 800.0, "effluent_compliant": True,
            "acid_inventory_kg": 1_000.0, "caustic_inventory_kg": 700.0,
            "coagulant_inventory_kg": 500.0, "polymer_inventory_kg": 120.0,
            "acid_dose_kg_h": 0.0, "caustic_dose_kg_h": 0.0,
            "coagulant_dose_mg_l": 24.0, "polymer_dose_mg_l": 1.5,
            "daf_solids_rate_kg_h": 0.0, "daf_removal_fraction": 0.0,
            "sludge_hopper_kg": 0.0, "sludge_press_running": False,
            "sludge_cake_solids_kg_h": 0.0, "sludge_cake_total_kg_h": 0.0,
            "electrical_load_kw": 0.0, "wastewater_available_fraction": 1.0,
            "equalization_overflow_m3": 0.0, "cumulative_influent_m3": 0.0,
            "cumulative_reuse_m3": 0.0, "cumulative_outfall_m3": 0.0,
            "cumulative_bypass_m3": 0.0, "cumulative_sludge_cake_water_m3": 0.0,
            "cumulative_sludge_solids_kg": 0.0, "history": [], "last_advance_seconds": 0,
            "cumulative_influent_tss_kg": 0.0, "cumulative_bypass_tss_kg": 0.0,
            "cumulative_effluent_tss_kg": 0.0, "cumulative_sludge_cake_solids_kg": 0.0,
        }

    def migrate(self, state: dict, sim_seconds: int = 0) -> None:
        missing_time = "last_advance_seconds" not in state
        for key, value in self.initialize().items():
            if key == "history":
                state.setdefault(key, [])
            else:
                state.setdefault(key, value)
        if missing_time:
            state["last_advance_seconds"] = sim_seconds
        state["equalization_capacity_m3"] = self.EQ_CAPACITY_M3
        state["treatment_tank_capacity_m3"] = self.TREATMENT_TANK_CAPACITY_M3
        state["equalization_level_pct"] = (100.0 * state["equalization_volume_m3"]
                                             / self.EQ_CAPACITY_M3)

    @staticmethod
    def _alkalinity_eq_per_m3(ph: float) -> float:
        # Buffered wastewater approximation: 0.8 equivalents/m³ per pH unit.
        return (ph - 7.0) * 0.8

    def available_fraction(self, state: dict) -> float:
        free = max(0.0, self.EQ_CAPACITY_M3 - state.get("equalization_volume_m3", 0.0))
        return min(1.0, free / 20.0)

    def advance(self, state: dict, sim_seconds: int, *, cip_flow_m3h: float = 0.0,
                cip_ph: float = 7.0, cip_conductivity_us_cm: float = 100.0,
                backwash_flow_m3h: float = 0.0, backwash_solids_kg_h: float = 0.0,
                power_available_fraction: float = 1.0,
                events: list[dict] | None = None) -> None:
        self.migrate(state, sim_seconds)
        dt = max(0, sim_seconds - state["last_advance_seconds"])
        state["last_advance_seconds"] = sim_seconds
        events = events or []
        local_power = min(({"Small": .75, "Medium": .42, "Large": 0.0}[e["severity"]]
                           for e in events if e["name"] == "Utility loss"
                           and e["area"] in ("Wastewater", "Whole factory")), default=1.0)
        mechanical = max(({"Small": .15, "Medium": .35, "Large": .65}[e["severity"]]
                          for e in events if e["name"] == "Mechanical degradation"
                          and e["area"] in ("Wastewater", "Whole factory")), default=0.0)
        power = max(0.0, min(1.0, power_available_fraction)) * local_power
        state["treatment_power_fraction"] = power
        cip_flow_m3h = max(0.0, cip_flow_m3h)
        backwash_flow_m3h = max(0.0, backwash_flow_m3h)
        state["influent_cip_flow_m3h"] = cip_flow_m3h
        state["influent_backwash_flow_m3h"] = backwash_flow_m3h
        requested_inflow = cip_flow_m3h + backwash_flow_m3h
        dt_hours = dt / 3600.0
        old_volume = state["equalization_volume_m3"]
        free_volume = max(0.0, self.EQ_CAPACITY_M3 - old_volume)
        incoming_volume = requested_inflow * dt_hours
        accepted_volume = min(incoming_volume, free_volume)
        overflow_volume = incoming_volume - accepted_volume
        actual_inflow = accepted_volume / dt_hours if dt_hours > 0 else 0.0
        state["bypass_flow_m3h"] = overflow_volume / dt_hours if dt_hours > 0 else 0.0
        state["equalization_overflow_m3"] = overflow_volume
        state["cumulative_bypass_m3"] += overflow_volume
        state["cumulative_influent_m3"] += accepted_volume
        state["influent_flow_m3h"] = actual_inflow

        # Alkalinity-equivalent, conductivity, and suspended-solids inventories mix
        # in the equalization tank before the downstream treatment train.
        backwash_volume = backwash_flow_m3h * dt_hours
        cip_volume = cip_flow_m3h * dt_hours
        old_alkalinity = state["equalization_alkalinity_eq"]
        old_conductivity_mass = state["equalization_conductivity_mass"]
        old_tss = state["equalization_tss_kg"]
        accepted_fraction = accepted_volume / incoming_volume if incoming_volume > 1e-12 else 1.0
        cip_accepted = cip_volume * accepted_fraction
        backwash_accepted = backwash_volume * accepted_fraction
        alkalinity_in = (cip_accepted * self._alkalinity_eq_per_m3(cip_ph)
                         + backwash_accepted * self._alkalinity_eq_per_m3(7.2))
        conductivity_in = (cip_accepted * max(0.0, cip_conductivity_us_cm)
                           + backwash_accepted * 450.0)
        cip_tss_mg_l = 45.0 + max(0.0, cip_conductivity_us_cm - 500.0) * .004
        requested_influent_tss = (cip_volume * cip_tss_mg_l / 1_000.0
                                  + max(0.0, backwash_solids_kg_h) * dt_hours)
        cip_tss_kg = cip_accepted * cip_tss_mg_l / 1_000.0
        backwash_tss_kg = max(0.0, backwash_solids_kg_h) * dt_hours * accepted_fraction
        total_influent_tss = cip_tss_kg + backwash_tss_kg
        state["cumulative_influent_tss_kg"] += total_influent_tss
        state["cumulative_bypass_tss_kg"] += max(0.0, requested_influent_tss - total_influent_tss)
        mixed_volume = old_volume + accepted_volume
        mixed_alkalinity = old_alkalinity + alkalinity_in
        mixed_conductivity_mass = old_conductivity_mass + conductivity_in
        mixed_tss = old_tss + cip_tss_kg + backwash_tss_kg

        # The equalization tank feeds a finite mixed treatment tank. Its design
        # volume gives a real residence-time lag: feed enters now, but effluent
        # reflects the chemistry that was already in the train.
        processable_m3 = max(0.0, mixed_volume - self.MIN_PROCESS_VOLUME_M3)
        transfer_capacity_m3 = self.HYDRAULIC_CAPACITY_M3H * power * dt_hours
        reactor_volume = state["treatment_tank_volume_m3"]
        residence_s = self.DESIGN_HYDRAULIC_RETENTION_MIN * 60.0
        output_volume = min(reactor_volume, reactor_volume * dt / max(1.0, residence_s))
        feed_volume = min(transfer_capacity_m3, processable_m3,
                          max(0.0, self.TREATMENT_TANK_CAPACITY_M3 - reactor_volume + output_volume))
        feed_flow = feed_volume / dt_hours if dt_hours > 0 else 0.0
        treatment_flow = output_volume / dt_hours if dt_hours > 0 else 0.0

        eq_alk_conc = mixed_alkalinity / max(mixed_volume, 1e-9)
        eq_cond_conc = mixed_conductivity_mass / max(mixed_volume, 1e-9)
        eq_tss_conc = mixed_tss / max(mixed_volume, 1e-9)
        feed_alk = eq_alk_conc * feed_volume
        feed_cond = eq_cond_conc * feed_volume
        feed_tss = eq_tss_conc * feed_volume
        state["equalization_volume_m3"] = max(0.0, mixed_volume - feed_volume)
        state["equalization_alkalinity_eq"] = mixed_alkalinity - feed_alk
        state["equalization_conductivity_mass"] = max(0.0, mixed_conductivity_mass - feed_cond)
        state["equalization_tss_kg"] = max(0.0, mixed_tss - feed_tss)

        reactor_mixed_volume = reactor_volume + feed_volume
        reactor_alk = state["treatment_tank_alkalinity_eq"] + feed_alk
        reactor_cond = state["treatment_tank_conductivity_mass"] + feed_cond
        reactor_tss = state["treatment_tank_tss_kg"] + feed_tss
        reactor_alk_conc = reactor_alk / max(reactor_mixed_volume, 1e-9)
        reactor_cond_conc = reactor_cond / max(reactor_mixed_volume, 1e-9)
        reactor_tss_conc = reactor_tss / max(reactor_mixed_volume, 1e-9)

        # Feed-forward neutralization uses the mixed-tank load and finite chemical
        # rates. A depleted or undersized dosing system leaves a measurable pH error.
        demanded_eq_h = ((feed_alk / dt_hours if dt_hours > 0 else 0.0)
                         + reactor_alk_conc * treatment_flow)
        if demanded_eq_h > 0:
            acid_need = demanded_eq_h * self.ACID_EQUIVALENT_KG
            state["acid_dose_kg_h"] = min(24.0, acid_need,
                                           state["acid_inventory_kg"] / max(dt_hours, 1e-12))
            state["caustic_dose_kg_h"] = 0.0
            neutralized_eq = min(max(0.0, reactor_alk),
                                  state["acid_dose_kg_h"] * dt_hours / self.ACID_EQUIVALENT_KG)
            state["acid_inventory_kg"] = max(0.0, state["acid_inventory_kg"]
                - state["acid_dose_kg_h"] * dt_hours)
        elif demanded_eq_h < 0:
            base_need = -demanded_eq_h * self.BASE_EQUIVALENT_KG
            state["caustic_dose_kg_h"] = min(18.0, base_need,
                                               state["caustic_inventory_kg"] / max(dt_hours, 1e-12))
            state["acid_dose_kg_h"] = 0.0
            neutralized_eq = -min(max(0.0, -reactor_alk),
                                  state["caustic_dose_kg_h"] * dt_hours / self.BASE_EQUIVALENT_KG)
            state["caustic_inventory_kg"] = max(0.0, state["caustic_inventory_kg"]
                - state["caustic_dose_kg_h"] * dt_hours)
        else:
            neutralized_eq = 0.0
            state["acid_dose_kg_h"] = 0.0
            state["caustic_dose_kg_h"] = 0.0
        reactor_alk = math.copysign(max(0.0, abs(reactor_alk) - abs(neutralized_eq)), reactor_alk) if reactor_alk else 0.0
        reactor_volume_after_reaction = reactor_mixed_volume
        effluent_alk_conc = reactor_alk / max(reactor_volume_after_reaction, 1e-9)
        state["effluent_ph"] = min(14.0, max(0.0, 7.0 + effluent_alk_conc / .8))
        state["effluent_conductivity_us_cm"] = max(0.0, reactor_cond_conc)

        solids_out = min(reactor_tss, reactor_tss_conc * output_volume)
        coagulant_factor = min(1.0, state["coagulant_inventory_kg"] / 8.0)
        polymer_factor = min(1.0, state["polymer_inventory_kg"] / 1.0)
        press_factor = max(.15, 1.0 - state["sludge_hopper_kg"] / self.SLUDGE_HOPPER_CAPACITY_KG)
        daf_fraction = .88 * coagulant_factor * polymer_factor * press_factor * max(0.0, 1.0 - mechanical)
        captured_kg = min(solids_out,
                          max(0.0, self.SLUDGE_HOPPER_CAPACITY_KG - state["sludge_hopper_kg"]),
                          self.DAF_SOLIDS_CAPACITY_KGH * dt_hours,
                          solids_out * daf_fraction)
        state["daf_removal_fraction"] = captured_kg / solids_out if solids_out > 1e-12 else .88
        state["daf_solids_rate_kg_h"] = captured_kg / dt_hours if dt_hours > 0 else 0.0
        state["sludge_hopper_kg"] = min(self.SLUDGE_HOPPER_CAPACITY_KG,
                                         state["sludge_hopper_kg"] + captured_kg)
        effluent_solids_kg = max(0.0, solids_out - captured_kg)
        state["cumulative_effluent_tss_kg"] += effluent_solids_kg
        state["effluent_tss_mg_l"] = (1_000.0 * effluent_solids_kg / output_volume
                                       if output_volume > 1e-12 else 0.0)

        state["coagulant_inventory_kg"] = max(0.0, state["coagulant_inventory_kg"]
            - feed_volume * state["coagulant_dose_mg_l"] / 1_000.0)
        state["polymer_inventory_kg"] = max(0.0, state["polymer_inventory_kg"]
            - feed_volume * state["polymer_dose_mg_l"] / 1_000.0)
        state["cumulative_sludge_solids_kg"] += captured_kg

        press_rate = min(self.PRESS_CAPACITY_KGH, state["sludge_hopper_kg"] / max(dt_hours, 1e-12))
        state["sludge_press_running"] = press_rate > 1e-9
        state["sludge_cake_solids_kg_h"] = press_rate
        pressed_solids = press_rate * dt_hours
        state["cumulative_sludge_cake_solids_kg"] += pressed_solids
        state["sludge_hopper_kg"] = max(0.0, state["sludge_hopper_kg"] - pressed_solids)
        state["sludge_cake_total_kg_h"] = press_rate * 5.0
        cake_water_volume = min(output_volume, press_rate * 4.0 / 1_000.0 * dt_hours)
        state["sludge_cake_water_m3h"] = cake_water_volume / dt_hours if dt_hours > 0 else 0.0

        reactor_out_fraction = output_volume / max(reactor_mixed_volume, 1e-9)
        state["treatment_tank_volume_m3"] = max(0.0, reactor_mixed_volume - output_volume)
        state["treatment_tank_alkalinity_eq"] = reactor_alk * (1.0 - reactor_out_fraction)
        state["treatment_tank_conductivity_mass"] = max(0.0, reactor_cond * (1.0 - reactor_out_fraction))
        state["treatment_tank_tss_kg"] = max(0.0, reactor_tss - solids_out)

        clarified_volume = max(0.0, output_volume - cake_water_volume)
        if output_volume > 1e-12:
            compliant = (6.5 <= state["effluent_ph"] <= 8.5
                         and state["effluent_tss_mg_l"] <= 30.0
                         and state["effluent_conductivity_us_cm"] <= 6_000.0)
            state["effluent_compliant"] = compliant
            reuse_volume = clarified_volume * (.72 if compliant else 0.0)
            outfall_volume = clarified_volume - reuse_volume
            state["reuse_flow_m3h"] = reuse_volume / dt_hours if dt_hours > 0 else 0.0
            state["outfall_flow_m3h"] = outfall_volume / dt_hours if dt_hours > 0 else 0.0
            state["cumulative_reuse_m3"] += reuse_volume
            state["cumulative_outfall_m3"] += outfall_volume
            state["cumulative_sludge_cake_water_m3"] += cake_water_volume
        else:
            state["effluent_compliant"] = False
            state["reuse_flow_m3h"] = 0.0
            state["outfall_flow_m3h"] = 0.0

        state["treatment_flow_m3h"] = treatment_flow
        state["treatment_feed_flow_m3h"] = feed_flow
        state["treatment_tank_level_pct"] = (100.0 * state["treatment_tank_volume_m3"]
                                                / self.TREATMENT_TANK_CAPACITY_M3)
        state["treatment_mode"] = ("TREATING" if treatment_flow > 0 else
                                    "POWER_HOLD" if power < .15 else
                                    "BUFFERING" if mixed_volume > self.MIN_PROCESS_VOLUME_M3 else
                                    "FILLING" if reactor_volume < self.TREATMENT_TANK_CAPACITY_M3 else "STANDBY")

        state["treatment_flow_m3h"] = treatment_flow
        final_volume = state["equalization_volume_m3"]
        state["equalization_level_pct"] = 100.0 * final_volume / self.EQ_CAPACITY_M3
        state["equalization_ph"] = (7.0 + state["equalization_alkalinity_eq"]
                                     / max(final_volume, 1e-9) / .8 if final_volume > 1e-9 else 7.0)
        state["equalization_conductivity_us_cm"] = (state["equalization_conductivity_mass"]
            / max(final_volume, 1e-9) if final_volume > 1e-9 else 800.0)
        state["treatment_power_fraction"] = power
        state["electrical_load_kw"] = 72.0 if treatment_flow > 0 else (8.0 if power < .15 else 22.0)
        state["wastewater_available_fraction"] = self.available_fraction(state)
        state["cumulative_sludge_solids_kg"] = max(0.0, state["cumulative_sludge_solids_kg"])

        if sim_seconds % 15 == 0:
            state["history"].append({"time": sim_seconds,
                "influent_flow_m3h": state["influent_flow_m3h"],
                "cip_effluent_m3h": state["influent_cip_flow_m3h"],
                "backwash_flow_m3h": state["influent_backwash_flow_m3h"],
                "treatment_flow_m3h": state["treatment_flow_m3h"],
                "treatment_feed_flow_m3h": state["treatment_feed_flow_m3h"],
                "treatment_tank_level_pct": state["treatment_tank_level_pct"],
                "reuse_flow_m3h": state["reuse_flow_m3h"],
                "outfall_flow_m3h": state["outfall_flow_m3h"],
                "equalization_level_pct": state["equalization_level_pct"],
                "influent_ph": state["equalization_ph"],
                "effluent_ph": state["effluent_ph"],
                "effluent_tss_mg_l": state["effluent_tss_mg_l"],
                "sludge_hopper_kg": state["sludge_hopper_kg"]})
            state["history"] = state["history"][-1_440:]

    def telemetry(self, state: dict, seed: int = 17, sim_seconds: int | None = None) -> list[dict]:
        signals = {
            ("equalization_tank", "tank_level"): (state["equalization_level_pct"], "%", 0, 100),
            ("equalization_tank", "stored_volume"): (state["equalization_volume_m3"], "m³", 0, self.EQ_CAPACITY_M3),
            ("equalization_tank", "ph"): (state["equalization_ph"], "pH", 0, 14),
            ("equalization_tank", "conductivity"): (state["equalization_conductivity_us_cm"], "µS/cm", 0, 50_000),
            ("influent_header", "flow_rate"): (state["influent_flow_m3h"], "m³/h", 0, 200),
            ("influent_header", "cip_flow"): (state["influent_cip_flow_m3h"], "m³/h", 0, 40),
            ("influent_header", "backwash_flow"): (state["influent_backwash_flow_m3h"], "m³/h", 0, 100),
            ("ph_neutralization", "effluent_ph"): (state["effluent_ph"], "pH", 0, 14),
            ("ph_neutralization", "acid_dose"): (state["acid_dose_kg_h"], "kg/h", 0, 50),
            ("ph_neutralization", "caustic_dose"): (state["caustic_dose_kg_h"], "kg/h", 0, 50),
            ("daf_unit", "effluent_tss"): (state["effluent_tss_mg_l"], "mg/L", 0, 10_000),
            ("daf_unit", "solids_removal"): (100 * state["daf_removal_fraction"], "%", 0, 100),
            ("treatment_outlet", "flow_rate"): (state["treatment_flow_m3h"], "m³/h", 0, 100),
            ("treatment_tank", "level"): (state["treatment_tank_level_pct"], "%", 0, 100),
            ("treatment_tank", "stored_volume"): (state["treatment_tank_volume_m3"], "m³", 0, self.TREATMENT_TANK_CAPACITY_M3),
            ("treatment_tank", "feed_flow"): (state["treatment_feed_flow_m3h"], "m³/h", 0, 100),
            ("treatment_outlet", "reuse_flow"): (state["reuse_flow_m3h"], "m³/h", 0, 100),
            ("treatment_outlet", "outfall_flow"): (state["outfall_flow_m3h"], "m³/h", 0, 100),
            ("treatment_outlet", "compliant"): (float(state["effluent_compliant"]), "bool", 0, 1),
            ("sludge_press", "hopper_inventory"): (state["sludge_hopper_kg"], "kg", 0, self.SLUDGE_HOPPER_CAPACITY_KG),
            ("sludge_press", "cake_rate"): (state["sludge_cake_total_kg_h"], "kg/h", 0, 500),
            ("sludge_press", "press_running"): (float(state["sludge_press_running"]), "bool", 0, 1),
            ("chemicals", "acid_inventory"): (state["acid_inventory_kg"], "kg", 0, 1_200),
            ("chemicals", "caustic_inventory"): (state["caustic_inventory_kg"], "kg", 0, 1_000),
            ("chemicals", "coagulant_inventory"): (state["coagulant_inventory_kg"], "kg", 0, 600),
            ("chemicals", "polymer_inventory"): (state["polymer_inventory_kg"], "kg", 0, 200),
        }
        rows = []
        sample_time = state.get("last_advance_seconds", 0) if sim_seconds is None else sim_seconds
        for (asset, signal), (value, units, low, high) in signals.items():
            tag_id = f"wastewater.{asset}.{signal}"
            noise_fraction = (0.0 if units == "bool" else
                              0.02 if signal == "effluent_tss" else
                              0.005 if signal == "conductivity" else
                              0.002 if units in ("pH", "%") else
                              0.01 if units in ("m³/h", "kg/h") else 0.001)
            measured = value + correlated_sensor_noise(
                state, tag_id, seed, sample_time, max(high - low, 1e-9) * noise_fraction)
            rows.append({"tag": f"wastewater.{asset}.{signal}", "area": "Wastewater",
                "unit": asset.replace("_", " ").title(), "sensor": signal,
                "value": round(min(high, max(low, measured)), 3), "units": units,
                "quality": "GOOD", "low": low, "high": high, "base": (low + high) / 2,
                "phase": 0.0, "last_change": "Wastewater treatment model"})
        return rows
