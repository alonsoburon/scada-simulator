import unittest

from scada_playground.concentrator import CopperConcentrator
from scada_playground.power_network import MinePowerNetwork
from scada_playground.water_network import MineWaterNetwork


class MinePowerIntegrationTests(unittest.TestCase):
    def test_normal_concentrator_and_water_network_hold_a_coupled_balance(self):
        water_model = MineWaterNetwork()
        concentrator_model = CopperConcentrator()
        water = water_model.initialize()
        concentrator = concentrator_model.initialize()
        for tick in range(1, 3_601):
            concentrator_model.advance(concentrator, tick, [],
                                       water_shortage_m3h=water["unmet_demand_m3h"])
            water_model.advance(water, tick, [],
                                concentrator_demand_m3h=concentrator["gross_process_water_m3h"],
                                concentrator_reclaim_m3h=concentrator["concentrator_reclaim_m3h"])
        self.assertLess(water["unmet_demand_m3h"], 1.0)
        self.assertGreater(water["nodes"]["process_tank"]["level_pct"], 20.0)
        self.assertGreater(concentrator["water_makeup_m3h"], 0.0)
        self.assertGreater(concentrator["concentrator_reclaim_m3h"], 0.0)

    def test_grid_outage_curbs_mill_and_pumps_then_process_recovers(self):
        grid = MinePowerNetwork()
        water_model = MineWaterNetwork()
        concentrator_model = CopperConcentrator()
        power = grid.initialize()
        water = water_model.initialize()
        concentrator = concentrator_model.initialize()
        outage = [{"name": "Utility loss", "area": "Grid transmission", "severity": "Large"}]
        process_fraction_during_outage = 1.0
        minimum_frequency_during_outage = 50.0
        pump_speed_before = water["pumps"]["Intake pump"]["speed_pct"]
        pump_speed_during_outage = pump_speed_before

        for tick in range(1, 1_201):
            events = outage if 120 <= tick < 720 else []
            process_request = (concentrator["sag_power_mw"] + concentrator["ball_power_mw"]
                               + concentrator["crusher_power_mw"] + 1.0)
            water_request = sum(pump["power_kw"] for pump in water["pumps"].values()) / 1000
            grid.advance(power, tick, events, process_load_mw=process_request,
                         water_load_mw=water_request, seed=17)
            concentrator_model.advance(concentrator, tick, events, power_available_fraction=power["process_power_fraction"])
            water_model.advance(water, tick, events,
                                concentrator_demand_m3h=concentrator["gross_process_water_m3h"],
                                concentrator_reclaim_m3h=concentrator["concentrator_reclaim_m3h"],
                                power_available_fraction=power["water_power_fraction"])
            if 120 <= tick < 720:
                process_fraction_during_outage = min(process_fraction_during_outage,
                                                      power["process_power_fraction"])
                minimum_frequency_during_outage = min(minimum_frequency_during_outage,
                                                       power["frequency_hz"])
                pump_speed_during_outage = min(pump_speed_during_outage,
                                                water["pumps"]["Intake pump"]["speed_pct"])

        self.assertLess(process_fraction_during_outage, 0.8)
        self.assertLess(minimum_frequency_during_outage, 49.5)
        self.assertGreater(power["frequency_hz"], minimum_frequency_during_outage)
        self.assertLess(pump_speed_during_outage, pump_speed_before)
        self.assertGreater(power["process_power_fraction"], process_fraction_during_outage)
        self.assertGreater(concentrator["plant_feed_tph"], 0)
        self.assertGreaterEqual(water_model.total_inventory_m3(water), 0)


if __name__ == "__main__":
    unittest.main()
