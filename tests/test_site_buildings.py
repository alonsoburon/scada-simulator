from __future__ import annotations

import unittest

from scada_playground.site_buildings import SiteBuildings


class SiteBuildingsTests(unittest.TestCase):
    def test_building_telemetry_exposes_thermal_air_quality_and_hvac_state(self):
        model = SiteBuildings()
        state = model.initialize()
        for tick in range(1, 31):
            model.advance(state, tick, [], seed=21)
        first = model.telemetry(state, seed=21)
        second = model.telemetry(state, seed=21)
        by_tag = {tag["tag"]: tag for tag in first}
        self.assertEqual(first, second)
        self.assertEqual(len(state["zones"]), 8)
        self.assertIn("site_buildings.office_block.indoor_temperature", by_tag)
        self.assertIn("site_buildings.office_block.co2_ppm", by_tag)
        self.assertIn("site_buildings.substation_building.hvac_filter_dp", by_tag)
        self.assertTrue(all(tag["dynamic_model"] for tag in first))

    def test_local_power_loss_stops_hvac_and_causes_room_specific_response(self):
        model = SiteBuildings()
        normal = model.initialize()
        outage = model.initialize()
        for state in (normal, outage):
            office = state["zones"]["Office block"]
            office["temperature_c"] = 30.0
            office["humidity_pct"] = 48.0
            office["co2_ppm"] = 1_000.0
            office["mode_elapsed_s"] = 180
        fault = [{"name": "Utility loss", "area": "Site buildings", "severity": "Large"}]

        for tick in range(54_000, 55_200):
            model.advance(normal, tick, [], seed=17, power_available_fraction=1.0)
            model.advance(outage, tick, fault, seed=17, power_available_fraction=1.0)

        normal_office = normal["zones"]["Office block"]
        outage_office = outage["zones"]["Office block"]
        self.assertEqual(outage_office["hvac_state"], "POWER_LOSS")
        self.assertLess(normal_office["temperature_c"], outage_office["temperature_c"])
        self.assertLess(outage_office["electric_power_kw"], normal_office["electric_power_kw"])
        self.assertGreater(outage_office["co2_ppm"], normal_office["co2_ppm"])

    def test_site_power_fraction_derates_hvac_but_preserves_room_thermal_inertia(self):
        model = SiteBuildings()
        state = model.initialize()
        office = state["zones"]["Office block"]
        office["temperature_c"] = 30.0
        for tick in range(54_000, 54_300):
            model.advance(state, tick, [], seed=8,
                          power_available_fraction=1.0 if tick < 54_150 else 0.25)
        self.assertEqual(state["power_available_fraction"], 0.25)
        self.assertGreater(office["temperature_c"], 20.0)
        self.assertLessEqual(office["electric_power_kw"], office["requested_power_kw"])


if __name__ == "__main__":
    unittest.main()
