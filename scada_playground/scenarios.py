"""Seed-replayable simulator scenarios and timeline runner."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ScenarioStep:
    at_seconds: int
    action: str
    parameters: dict


@dataclass(frozen=True)
class ScenarioDefinition:
    scenario_id: str
    name: str
    description: str
    duration_seconds: int
    steps: tuple[ScenarioStep, ...]
    expected_observations: tuple[str, ...]

    def catalog_record(self) -> dict:
        return {"id": self.scenario_id, "name": self.name, "description": self.description,
                "duration_seconds": self.duration_seconds,
                "expected_observations": list(self.expected_observations)}


SCENARIOS = {
    "mine-water-pump-and-demand": ScenarioDefinition(
        scenario_id="mine-water-pump-and-demand",
        name="Mine water demand surge and pump degradation",
        description="A process demand surge follows pump degradation; tank inventories and flows respond and recover.",
        duration_seconds=75,
        steps=(
            ScenarioStep(8, "incident", {"incident_name": "Process upset", "severity": "Medium",
                                          "target_area": "Water supply network", "duration_seconds": 30}),
            ScenarioStep(18, "incident", {"incident_name": "Mechanical degradation", "severity": "Medium",
                                           "target_area": "Water supply network", "duration_seconds": 25}),
        ),
        expected_observations=(
            "Process demand rises and recovery percentage falls during the process upset.",
            "Pump degradation lowers available head and linked flows; tank inventory responds with a lag.",
            "Demand, pump performance, and tank inventory return toward normal after both events clear.",
        ),
    ),
    "grid-comms-recovery": ScenarioDefinition(
        scenario_id="grid-comms-recovery",
        name="Grid communications loss and recovery",
        description="A deterministic network partition makes one area stale, then recovers.",
        duration_seconds=60,
        steps=(ScenarioStep(0, "incident", {"incident_name": "Network partition", "severity": "Medium",
                                             "target_area": "Grid distribution", "duration_seconds": 25}),),
        expected_observations=(
            "Grid distribution tags report STALE while the partition is active.",
            "Quality returns to GOOD after 25 simulated seconds and a clear alarm is recorded.",
        ),
    ),
    "frozen-signal": ScenarioDefinition(
        scenario_id="frozen-signal",
        name="Frozen instrument signal",
        description="One protocol signal freezes while the simulated process continues to change.",
        duration_seconds=50,
        steps=(ScenarioStep(5, "protocol_fault", {"protocol": "all", "mode": "freeze",
                                                   "selector_type": "tag",
                                                   "target": "grid_distribution.feeder_north.line_voltage",
                                                   "duration_seconds": 25}),),
        expected_observations=(
            "The selected tag stops changing while simulation time advances.",
            "The frozen value resumes updating after the fault expires.",
        ),
    ),
    "mqtt-tag-gap": ScenarioDefinition(
        scenario_id="mqtt-tag-gap",
        name="MQTT telemetry gap and recovery",
        description="One tag disappears only from MQTT batches, then reappears after recovery.",
        duration_seconds=40,
        steps=(ScenarioStep(5, "protocol_fault", {"protocol": "MQTT", "mode": "suppress",
                                                   "selector_type": "tag",
                                                   "target": "grid_distribution.feeder_north.line_voltage",
                                                   "duration_seconds": 15}),),
        expected_observations=(
            "The selected tag is absent from MQTT area batches during the gap.",
            "The tag reappears after recovery; Modbus and OPC UA retain their stable tag map.",
        ),
    ),
}


class ScenarioRunner:
    """Track a scenario timeline, apply due actions, and preserve replay metadata."""

    def __init__(self, definitions: dict[str, ScenarioDefinition] | None = None):
        self.definitions = definitions or SCENARIOS

    def catalog(self) -> list[dict]:
        return [definition.catalog_record() for definition in self.definitions.values()]

    def definition(self, scenario_id: str) -> ScenarioDefinition:
        try:
            return self.definitions[scenario_id]
        except KeyError as exc:
            raise ValueError(f"Unknown scenario: {scenario_id}") from exc

    def start(self, sim: dict, scenario_id: str, seed: int,
              apply_action: Callable[[dict, ScenarioStep], None]) -> dict:
        definition = self.definition(scenario_id)
        if not 0 <= seed <= 2**32 - 1:
            raise ValueError("Seed must be between 0 and 4,294,967,295")
        sim["seed"] = seed
        sim["running"] = True
        sim["factory_on"] = True
        sim["scenario_run"] = {"id": scenario_id, "name": definition.name,
                               "start_tick": sim["tick"], "duration_seconds": definition.duration_seconds,
                               "seed": seed, "status": "running", "next_step": 0,
                               "expected_observations": list(definition.expected_observations)}
        self._apply_due(sim, apply_action)
        return sim["scenario_run"]

    def pause(self, sim: dict) -> None:
        if sim.get("scenario_run", {}).get("status") == "running":
            sim["scenario_run"]["status"] = "paused"

    def resume(self, sim: dict) -> None:
        if sim.get("scenario_run", {}).get("status") == "paused":
            sim["scenario_run"]["status"] = "running"

    def before_tick(self, sim: dict, apply_action: Callable[[dict, ScenarioStep], None]) -> None:
        run = sim.get("scenario_run")
        if not run or run["status"] == "completed":
            return
        self._apply_due(sim, apply_action, through_tick=sim["tick"] + 1)

    def after_tick(self, sim: dict) -> None:
        run = sim.get("scenario_run")
        if not run or run["status"] == "completed":
            return
        elapsed = sim["tick"] - run["start_tick"]
        if elapsed >= run["duration_seconds"]:
            run["status"] = "completed"
            sim["running"] = False

    def _apply_due(self, sim: dict, apply_action: Callable[[dict, ScenarioStep], None],
                   through_tick: int | None = None) -> None:
        run = sim["scenario_run"]
        definition = self.definitions[run["id"]]
        target_tick = sim["tick"] if through_tick is None else through_tick
        elapsed = target_tick - run["start_tick"]
        while run["next_step"] < len(definition.steps):
            step = definition.steps[run["next_step"]]
            if step.at_seconds > elapsed:
                break
            apply_action(sim, step)
            run["next_step"] += 1
