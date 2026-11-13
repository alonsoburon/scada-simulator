import unittest

from scada_playground.chilled_water import ChilledWaterPlant
from scada_playground.plant_utilities import FactoryUtilities


class ChilledWaterPlantTests(unittest.TestCase):
    def setUp(self):
        self.chilled_model = ChilledWaterPlant()
        self.utility_model = FactoryUtilities()
        self.chilled = self.chilled_model.initialize()
        self.utilities = self.utility_model.initialize()

    def advance(self, tick, events=()):
        self.chilled_model.advance(self.chilled, tick, list(events),
            cooling_demand_kw=self.utility_model.chilled_water_demand_kw(self.utilities), seed=17)
        self.utility_model.advance(self.utilities, tick, list(events),
            chilled_water_service_fraction=self.chilled["cooling_service_fraction"],
            chilled_water_supply_temperature_c=self.chilled["supply_temperature_c"], seed=17)

    def test_product_sensible_heat_calculates_realistic_cooling_duty(self):
        duty = self.chilled_model.product_cooling_demand_kw(180.0)
        self.assertAlmostEqual(duty, 879.06, places=2)

    def test_one_chiller_trip_uses_thermal_storage_and_keeps_other_unit_available(self):
        start_storage = self.chilled["storage_mwh"]
        event = [{"name": "Utility loss", "area": "Chilled water",
                  "severity": "Large", "target_asset": "Chiller A"}]
        for tick in range(1, 121):
            self.advance(tick, event)
        self.assertFalse(self.chilled["chillers"]["chiller_a"]["running"])
        self.assertTrue(self.chilled["chillers"]["chiller_b"]["running"])
        self.assertEqual(self.chilled["pump_available_fraction"], 1.0)
        self.assertGreater(self.chilled["cooling_service_fraction"], 0.99)
        self.assertLess(self.chilled["storage_mwh"], start_storage)

    def test_plant_outage_exhausts_cold_reserve_then_diverts_product_and_starves_filler(self):
        outage = [{"name": "Utility loss", "area": "Chilled water", "severity": "Large"}]
        self.advance(1)
        start_storage = self.chilled["storage_mwh"]
        initial_supply_temp = self.chilled["supply_temperature_c"]
        for tick in range(2, 4_500):
            self.advance(tick, outage)
        self.assertLess(self.chilled["storage_mwh"], 1e-6)
        self.assertLess(self.chilled["cooling_service_fraction"], 0.05)
        self.assertGreater(sum(p["diverted_rate"] for p in self.utilities["pasteurizers"].values()), 170.0)
        self.assertLess(self.utilities["product_tank_inventory_units"], 1.0)
        self.assertLess(self.utilities["filler_rate_units_min"], 1.0)
        outage_supply_temp = self.chilled["supply_temperature_c"]
        self.assertGreater(outage_supply_temp, initial_supply_temp)
        self.assertGreater(start_storage, self.chilled["storage_mwh"])

        for tick in range(4_500, 4_800):
            self.advance(tick)
        self.assertGreater(self.chilled["cooling_service_fraction"], 0.99)
        self.assertLess(self.chilled["supply_temperature_c"], outage_supply_temp)
        self.assertGreater(self.utilities["product_tank_inlet_units_min"], 170.0)
        self.assertGreater(self.utilities["filler_rate_units_min"], 170.0)

    def test_equipment_tags_are_dynamic_unique_seeded_and_report_chiller_state(self):
        rows = self.chilled_model.telemetry(self.chilled, seed=17)
        identifiers = [row["tag"] for row in rows]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertIn("chilled_water.chiller_a.cooling_output", identifiers)
        self.assertIn("chilled_water.thermal_storage.state_of_charge", identifiers)
        self.assertEqual(rows, self.chilled_model.telemetry(self.chilled, seed=17))
        self.assertTrue(all(row["dynamic_model"] for row in rows))


if __name__ == "__main__":
    unittest.main()
