import unittest

from scada_playground.plant_utilities import FactoryUtilities


class FactoryUtilitiesTests(unittest.TestCase):
    def setUp(self):
        self.model = FactoryUtilities()
        self.state = self.model.initialize()

    def advance(self, start, end, events=None):
        for tick in range(start, end + 1):
            self.model.advance(self.state, tick, events or [], seed=17)

    def test_normal_headers_hold_pressure_and_consumers_run(self):
        self.advance(1, 900)
        self.assertGreater(self.state["steam_header_pressure_bar"], 7.8)
        self.assertGreater(self.state["air_receiver_pressure_bar"], 6.9)
        self.assertEqual(self.state["steam_service_fraction"], 1.0)
        self.assertEqual(self.state["air_service_fraction"], 1.0)
        self.assertAlmostEqual(self.state["pasteurizers"]["pasteurizer_01"]["production_rate"], 90.0)
        self.assertAlmostEqual(self.state["filler_rate_units_min"], 180.0)
        self.assertAlmostEqual(self.state["packer_rate_cases_min"], 60.0)

    def test_steam_header_buffers_a_boiler_loss_and_temperature_recovers_later(self):
        outage = [{"name": "Utility loss", "area": "Boiler house", "severity": "Large"}]
        self.advance(1, 600)
        normal_temperature = self.state["pasteurizers"]["pasteurizer_01"]["temperature_c"]
        self.advance(601, 1_200, outage)
        self.assertLess(self.state["steam_header_pressure_bar"], 6.5)
        self.assertLess(self.state["pasteurizers"]["pasteurizer_01"]["temperature_c"], normal_temperature - 15)
        self.assertGreater(self.state["condensate_return_tph"], 0)
        self.advance(1_201, 2_400)
        self.assertGreater(self.state["steam_header_pressure_bar"], 7.5)
        self.assertGreater(self.state["pasteurizers"]["pasteurizer_01"]["temperature_c"], 78)
        self.assertGreater(self.state["pasteurizers"]["pasteurizer_01"]["production_rate"], 88.0)

    def test_receiver_linepack_buffers_compressor_loss_then_packaging_recovers(self):
        outage = [{"name": "Utility loss", "area": "Compressed air", "severity": "Large"}]
        self.advance(1, 600)
        self.advance(601, 1_200, outage)
        self.assertLess(self.state["air_receiver_pressure_bar"], 5.8)
        self.assertLess(self.state["filler_rate_units_min"], 100)
        self.assertLess(self.state["packer_rate_cases_min"], 40)
        self.assertGreater(self.state["dryer_dew_point_c"], 0)
        self.advance(1_201, 2_400)
        self.assertGreater(self.state["air_receiver_pressure_bar"], 6.5)
        self.assertAlmostEqual(self.state["filler_rate_units_min"], 180.0)
        self.assertAlmostEqual(self.state["packer_rate_cases_min"], 60.0)
        self.assertLess(self.state["dryer_dew_point_c"], -10)

    def test_inventory_balances_and_seeded_tags_are_reproducible(self):
        for tick in range(1, 121):
            prior_steam = self.state["steam_header_inventory_t"]
            prior_air = self.state["air_inventory_nm3"]
            prior_condensate = self.state["condensate_inventory_t"]
            self.model.advance(self.state, tick, [], seed=17)
            self.assertAlmostEqual(self.state["steam_header_inventory_t"] - prior_steam,
                (self.state["steam_generation_tph"] - self.state["steam_consumption_tph"]) / 3600.0,
                places=10)
            self.assertAlmostEqual(self.state["air_inventory_nm3"] - prior_air,
                (self.state["air_supply_nm3_min"] - self.state["air_consumption_nm3_min"]) / 60.0,
                places=10)
            self.assertAlmostEqual(self.state["condensate_inventory_t"] - prior_condensate,
                (self.state["condensate_return_tph"] - self.state["condensate_reuse_tph"]) / 3600.0,
                places=10)
        state = self.state
        rows = self.model.telemetry(state, seed=17)
        identifiers = [row["tag"] for row in rows]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertIn("boiler_house.steam_header.line_pressure", identifiers)
        self.assertIn("compressed_air.receiver_01.receiver_pressure", identifiers)
        self.assertIn("filling.filler_01.production_rate", identifiers)
        self.assertEqual(rows, self.model.telemetry(state, seed=17))
        self.assertTrue(all(row["dynamic_model"] for row in rows))


if __name__ == "__main__":
    unittest.main()
