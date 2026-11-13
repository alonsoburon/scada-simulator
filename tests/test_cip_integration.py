import unittest

from scada_playground.plant_utilities import FactoryUtilities
from scada_playground.water_network import MineWaterNetwork


class CIPPlantIntegrationTests(unittest.TestCase):
    def test_cip_consumes_shared_water_and_steam_then_line_recovers(self):
        utilities_model = FactoryUtilities()
        water_model = MineWaterNetwork()
        utilities = utilities_model.initialize()
        water = water_model.initialize()
        initial_water_inventory = water_model.total_inventory_m3(water)
        self.assertTrue(utilities_model.CIP_MODEL.start(
            utilities["cip"], "pasteurizer_01", sim_seconds=0))
        highest_water_demand = 0.0
        highest_steam_demand = 0.0
        minimum_filler_rate = 180.0

        for tick in range(1, 1_801):
            utilities_model.advance(utilities, tick, [],
                                    water_available_fraction=water["factory_water_available_fraction"])
            water_model.advance(
                water, tick, [], concentrator_demand_m3h=200.0,
                concentrator_reclaim_m3h=120.0,
                factory_cip_demand_m3h=utilities["cip_water_demand_m3h"],
                factory_cip_effluent_m3h=utilities["cip_effluent_m3h"],
            )
            highest_water_demand = max(highest_water_demand, water["demand_m3h"])
            highest_steam_demand = max(highest_steam_demand, utilities["steam_demand_tph"])
            minimum_filler_rate = min(minimum_filler_rate, utilities["filler_rate_units_min"])

        expected_inventory = (initial_water_inventory + water["cumulative_concentrator_return_m3"]
            - water["cumulative_process_consumption_m3"] - water["cumulative_treated_delivery_m3"]
            - water["total_spilled_m3"])
        self.assertFalse(utilities["cip"]["active"])
        self.assertEqual(utilities["cip"]["cycles_completed"], 1)
        self.assertGreater(highest_water_demand, water["base_demand_m3h"] + 200.0)
        self.assertGreater(highest_steam_demand, 40.0)
        self.assertGreater(water["cumulative_factory_cip_effluent_m3"], 1.0)
        self.assertLess(minimum_filler_rate, 120.0)
        self.assertAlmostEqual(utilities["filler_rate_units_min"], 180.0, delta=1.0)
        self.assertAlmostEqual(utilities["packer_rate_cases_min"], 60.0, delta=1.0)
        self.assertAlmostEqual(water_model.total_inventory_m3(water), expected_inventory, places=5)


if __name__ == "__main__":
    unittest.main()
