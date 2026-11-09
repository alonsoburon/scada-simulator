from __future__ import annotations

import unittest

from scada_playground.process_model import FactoryProcessModel
from scada_playground.protocol_faults import ProtocolFaultInjector
from scada_playground.scenarios import ScenarioRunner


def simulation_state() -> dict:
    return {
        "tags": [{"tag": "grid_distribution.feeder_north.line_voltage",
                  "area": "Grid distribution", "sensor": "line_voltage", "unit": "Feeder north",
                  "value": 400.0, "base": 400.0, "low": 0.0, "high": 500.0,
                  "phase": 0.5, "units": "kV", "quality": "GOOD", "last_change": "Normal"}],
        "tick": 0, "seed": 17, "simulation_id": "test-run-0001", "running": False,
        "events": [], "history": [], "alarms": [], "next_event": 1,
        "protocol_faults": [], "next_fault_id": 1, "scenario_run": None,
    }


class ScenarioRunnerTests(unittest.TestCase):
    def test_scenario_actions_are_dispatched_at_their_simulated_offset(self):
        runner = ScenarioRunner()
        sim = simulation_state()
        actions = []
        runner.start(sim, "frozen-signal", 17,
                     lambda state, step: actions.append((state["tick"], step.action)))
        self.assertEqual(actions, [])
        sim["tick"] = 4
        runner.before_tick(sim, lambda state, step: actions.append((state["tick"] + 1, step.action)))
        self.assertEqual(actions, [(5, "protocol_fault")])

    def test_scenario_completes_and_stops_plant_at_duration(self):
        runner = ScenarioRunner()
        sim = simulation_state()
        runner.start(sim, "mqtt-tag-gap", 23, lambda _state, _step: None)
        sim["tick"] = 40
        runner.after_tick(sim)
        self.assertEqual(sim["scenario_run"]["status"], "completed")
        self.assertFalse(sim["running"])

    def test_invalid_seed_and_scenario_are_rejected(self):
        runner = ScenarioRunner()
        with self.assertRaises(ValueError):
            runner.start(simulation_state(), "missing", 17, lambda _state, _step: None)
        with self.assertRaises(ValueError):
            runner.start(simulation_state(), "frozen-signal", -1, lambda _state, _step: None)


class ProtocolFaultTests(unittest.TestCase):
    def test_fault_modes_transform_only_the_protocol_view(self):
        injector = ProtocolFaultInjector()
        sim = simulation_state()
        original_value = sim["tags"][0]["value"]
        injector.add(sim, "all", "stale", "tag", sim["tags"][0]["tag"])
        canonical = {"tags": sim["tags"], "protocol_faults": sim["protocol_faults"]}
        viewed = injector.apply(canonical, "Modbus TCP")
        self.assertEqual(viewed["tags"][0]["quality"], "STALE")
        self.assertEqual(sim["tags"][0]["quality"], "GOOD")
        self.assertEqual(sim["tags"][0]["value"], original_value)

    def test_freeze_holds_value_and_mqtt_suppress_omits_only_selected_tag(self):
        injector = ProtocolFaultInjector()
        sim = simulation_state()
        tag_name = sim["tags"][0]["tag"]
        injector.add(sim, "all", "freeze", "tag", tag_name)
        source = {"tags": sim["tags"], "protocol_faults": sim["protocol_faults"]}
        first = injector.apply(source, "OPC UA")["tags"][0]["value"]
        sim["tags"][0]["value"] = 417.5
        next_source = {"tags": sim["tags"], "protocol_faults": sim["protocol_faults"]}
        self.assertEqual(injector.apply(next_source, "OPC UA")["tags"][0]["value"], first)

        injector.add(sim, "MQTT", "suppress", "tag", tag_name)
        mqtt_view = injector.apply(next_source, "MQTT")
        self.assertEqual(mqtt_view["tags"], [])
        modbus_view = injector.apply(next_source, "Modbus TCP")
        self.assertEqual(len(modbus_view["tags"]), 1)
        self.assertEqual(modbus_view["tags"][0]["quality"], "GOOD")

    def test_expiring_fault_uses_simulation_clock(self):
        injector = ProtocolFaultInjector()
        sim = simulation_state()
        fault = injector.add(sim, "MQTT", "suppress", "area", "Grid distribution", 10)
        sim["tick"] = 9
        self.assertEqual(injector.expire(sim), [])
        sim["tick"] = 10
        expired = injector.expire(sim)
        self.assertEqual(expired[0]["id"], fault["id"])
        self.assertEqual(sim["protocol_faults"], [])

    def test_unknown_targets_and_modes_are_rejected(self):
        injector = ProtocolFaultInjector()
        sim = simulation_state()
        with self.assertRaises(ValueError):
            injector.add(sim, "MQTT", "freeze", "tag", "not.a.real.tag")
        with self.assertRaises(ValueError):
            injector.add(sim, "MQTT", "unknown", "area", "Grid distribution")


class ProcessDeterminismTests(unittest.TestCase):
    def test_same_seed_reproduces_the_same_process_sample(self):
        model = FactoryProcessModel({"Grid distribution": []}, {}, lambda _unit, _tick: ())
        first, second = simulation_state(), simulation_state()
        first["running"] = second["running"] = True
        model.advance(first)
        model.advance(second)
        self.assertEqual(first["tags"], second["tags"])


if __name__ == "__main__":
    unittest.main()
