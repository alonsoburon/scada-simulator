import unittest

from scada_playground.wastewater import WastewaterTreatmentPlant


class WastewaterTreatmentTests(unittest.TestCase):
    def setUp(self):
        self.model = WastewaterTreatmentPlant()
        self.state = self.model.initialize()

    def test_cip_return_is_neutralized_and_quality_gates_reuse(self):
        for tick in range(1, 601):
            self.model.advance(self.state, tick, cip_flow_m3h=18.0, cip_ph=12.2,
                               cip_conductivity_us_cm=22_000.0)
        self.assertGreater(self.state["cumulative_influent_m3"], 2.0)
        self.assertGreater(self.state["acid_inventory_kg"], 0.0)
        self.assertGreaterEqual(self.state["effluent_ph"], 6.5)
        self.assertLessEqual(self.state["effluent_ph"], 8.5)
        self.assertLessEqual(self.state["reuse_flow_m3h"], self.state["treatment_flow_m3h"])
        if self.state["effluent_conductivity_us_cm"] > 6_000:
            self.assertEqual(self.state["reuse_flow_m3h"], 0.0)
            self.assertGreater(self.state["outfall_flow_m3h"], 0.0)

    def test_power_loss_buffers_influent_without_discarding_water(self):
        outage = [{"name": "Utility loss", "area": "Wastewater", "severity": "Large"}]
        for tick in range(1, 901):
            self.model.advance(self.state, tick, cip_flow_m3h=18.0, cip_ph=12.2,
                               backwash_flow_m3h=20.0, backwash_solids_kg_h=8.0,
                               events=outage)
        self.assertEqual(self.state["treatment_mode"], "POWER_HOLD")
        self.assertGreater(self.state["equalization_volume_m3"], 5.0)
        self.assertEqual(self.state["cumulative_outfall_m3"], 0.0)
        self.assertEqual(self.state["cumulative_bypass_m3"], 0.0)

    def test_treatment_tank_gives_effluent_quality_a_residence_time_lag(self):
        for tick in range(1, 601):
            self.model.advance(self.state, tick, cip_flow_m3h=18.0, cip_ph=12.2,
                               cip_conductivity_us_cm=22_000.0)
        high_quality_front = self.state["effluent_conductivity_us_cm"]
        for tick in range(601, 3_001):
            self.model.advance(self.state, tick, cip_flow_m3h=18.0, cip_ph=7.0,
                               cip_conductivity_us_cm=500.0)
        recovered_quality = self.state["effluent_conductivity_us_cm"]
        self.assertGreater(high_quality_front, 15_000.0)
        self.assertGreater(recovered_quality, 500.0)
        self.assertLess(recovered_quality, high_quality_front)

    def test_equalization_volume_balance_includes_reuse_outfall_bypass_and_cake(self):
        for tick in range(1, 3_601):
            self.model.advance(self.state, tick, cip_flow_m3h=18.0, cip_ph=8.0,
                               cip_conductivity_us_cm=1_000.0,
                               backwash_flow_m3h=20.0, backwash_solids_kg_h=12.0)
        outputs = (self.state["cumulative_reuse_m3"] + self.state["cumulative_outfall_m3"]
                   + self.state["cumulative_bypass_m3"]
                   + self.state["cumulative_sludge_cake_water_m3"])
        self.assertAlmostEqual(self.state["equalization_volume_m3"]
                               + self.state["treatment_tank_volume_m3"] + outputs,
                               self.state["cumulative_influent_m3"], places=6)
        solids_stored_and_discharged = (self.state["equalization_tss_kg"]
            + self.state["treatment_tank_tss_kg"] + self.state["sludge_hopper_kg"]
            + self.state["cumulative_effluent_tss_kg"]
            + self.state["cumulative_sludge_cake_solids_kg"])
        self.assertAlmostEqual(solids_stored_and_discharged,
                               self.state["cumulative_influent_tss_kg"], places=6)
        self.assertGreater(self.state["cumulative_sludge_solids_kg"], 0.0)

    def test_full_equalization_tank_reports_bypass_without_inventing_storage(self):
        self.state["equalization_volume_m3"] = self.model.EQ_CAPACITY_M3
        outage = [{"name": "Utility loss", "area": "Wastewater", "severity": "Large"}]
        self.model.advance(self.state, 1, cip_flow_m3h=18.0, events=outage)
        self.assertGreater(self.state["bypass_flow_m3h"], 0.0)
        self.assertEqual(self.state["equalization_volume_m3"], self.model.EQ_CAPACITY_M3)
        self.assertAlmostEqual(self.state["cumulative_bypass_m3"], 18.0 / 3600.0, places=9)
        self.assertGreater(self.state["cumulative_bypass_tss_kg"], 0.0)

    def test_instrument_noise_is_seeded_and_does_not_change_process_truth(self):
        self.state["equalization_volume_m3"] = 120.0
        self.state["equalization_level_pct"] = 40.0
        expected_truth = self.state["equalization_level_pct"]
        first = self.model.telemetry(self.state, seed=21, sim_seconds=300)
        replay = self.model.telemetry(self.state, seed=21, sim_seconds=300)
        later = self.model.telemetry(self.state, seed=21, sim_seconds=301)
        level = next(row for row in first if row["tag"] == "wastewater.equalization_tank.tank_level")
        later_level = next(row for row in later if row["tag"] == "wastewater.equalization_tank.tank_level")
        self.assertEqual(first, replay)
        self.assertNotEqual(level["value"], later_level["value"])
        self.assertEqual(self.state["equalization_level_pct"], expected_truth)


if __name__ == "__main__":
    unittest.main()
