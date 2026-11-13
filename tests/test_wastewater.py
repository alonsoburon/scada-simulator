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

    def test_equalization_volume_balance_includes_reuse_outfall_bypass_and_cake(self):
        for tick in range(1, 3_601):
            self.model.advance(self.state, tick, cip_flow_m3h=18.0, cip_ph=8.0,
                               cip_conductivity_us_cm=1_000.0,
                               backwash_flow_m3h=20.0, backwash_solids_kg_h=12.0)
        outputs = (self.state["cumulative_reuse_m3"] + self.state["cumulative_outfall_m3"]
                   + self.state["cumulative_bypass_m3"]
                   + self.state["cumulative_sludge_cake_water_m3"])
        self.assertAlmostEqual(self.state["equalization_volume_m3"] + outputs,
                               self.state["cumulative_influent_m3"], places=6)
        self.assertGreater(self.state["cumulative_sludge_solids_kg"], 0.0)

    def test_full_equalization_tank_reports_bypass_without_inventing_storage(self):
        self.state["equalization_volume_m3"] = self.model.EQ_CAPACITY_M3
        outage = [{"name": "Utility loss", "area": "Wastewater", "severity": "Large"}]
        self.model.advance(self.state, 1, cip_flow_m3h=18.0, events=outage)
        self.assertGreater(self.state["bypass_flow_m3h"], 0.0)
        self.assertEqual(self.state["equalization_volume_m3"], self.model.EQ_CAPACITY_M3)
        self.assertAlmostEqual(self.state["cumulative_bypass_m3"], 18.0 / 3600.0, places=9)


if __name__ == "__main__":
    unittest.main()
