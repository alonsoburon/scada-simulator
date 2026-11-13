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
    "mine-grid-support-and-recovery": ScenarioDefinition(
        scenario_id="mine-grid-support-and-recovery",
        name="Mine grid disturbance and reserve dispatch",
        description="A transmission trip islands the mine grid and exercises battery response, diesel start/ramp, process load shedding, and recovery.",
        duration_seconds=5_400,
        steps=(
            ScenarioStep(300, "incident", {"incident_name": "Utility loss", "severity": "Large",
                                             "target_area": "Grid transmission", "duration_seconds": 3_600}),
            ScenarioStep(3_905, "process_control", {"process_action": "RESET_TRIPS"}),
            ScenarioStep(3_910, "process_control", {"process_action": "START"}),
        ),
        expected_observations=(
            "The Large transmission event opens the intertie; the battery responds first, then the diesel generators start and ramp onto the islanded mine bus.",
            "When import and reserves cannot cover demand, noncritical site load sheds first and the process feeder limits concentrator power.",
            "Mine bus frequency and voltage respond to the supply deficit; ordinary process interlocks latch trips, and water storage temporarily buffers pump-power reductions.",
            "After the transmission restriction clears and electrical permissives recover, the replay simulates an operator trip reset and orderly process restart.",
            "The tie-line resumes imports, diesel output ramps down, storage recharges, and throughput recovers after the startup sequence.",
        ),
    ),
    "haulage-crusher-buffer": ScenarioDefinition(
        scenario_id="haulage-crusher-buffer",
        name="Haul truck cycles and crusher stoppage",
        description="Finite truck payloads feed ROM while a crusher conveyor trip forces inventory buffers and dispatch holds to respond.",
        duration_seconds=46_800,
        steps=(
            ScenarioStep(300, "incident", {"incident_name": "Conveyor jam", "severity": "Large",
                                             "target_area": "Crushing and screening", "duration_seconds": 21_600}),
            ScenarioStep(900, "incident", {"incident_name": "Mechanical degradation", "severity": "Medium",
                                             "target_area": "Mobile fleet", "duration_seconds": 2_400}),
        ),
        expected_observations=(
            "Haul trucks deliver finite payloads after loading and loaded trips; return trips carry no ore.",
            "The crusher conveyor trip stops ROM discharge, so incoming truckloads raise the ROM stockpile while downstream buffers continue feeding the concentrator.",
            "During the long stoppage, ROM reaches the dispatch hold limit; trucks wait at the shovel while downstream buffers feed the plant.",
            "After crushing restarts, ROM inventory declines through the resume threshold and loading dispatch resumes with hysteresis.",
            "Mobile-fleet degradation slows travel progress and extends the haul cycle without jumping GPS positions.",
        ),
    ),
    "concentrator-mill-degradation": ScenarioDefinition(
        scenario_id="concentrator-mill-degradation",
        name="Hard ore and grinding circuit degradation",
        description="Ore hardness increases naturally while a mechanical fault degrades grinding; power, circulating load, product size, recovery, water demand, and stockpiles interact.",
        duration_seconds=5_400,
        steps=(
            ScenarioStep(300, "incident", {"incident_name": "Mechanical degradation", "severity": "Medium",
                                             "target_area": "Grinding and flotation", "duration_seconds": 2_400}),
            ScenarioStep(1_800, "incident", {"incident_name": "Utility loss", "severity": "Small",
                                               "target_area": "Mine power network", "duration_seconds": 600}),
        ),
        expected_observations=(
            "The harder ore signal changes mill energy requirement and P80 with residence-time lag.",
            "Grinding degradation limits fresh-feed capacity and shifts power, cyclone recirculating load, and copper recovery.",
            "Mine power disturbance derates the concentrator and the coupled water pump station; inventory buffers delay process effects.",
            "After the faults clear, process variables and inventories recover at different rates.",
        ),
    ),
    "mine-water-pump-and-demand": ScenarioDefinition(
        scenario_id="mine-water-pump-and-demand",
        name="Mine water demand surge and pump degradation",
        description="A process demand surge follows pump degradation; tank inventories and flows respond and recover.",
        duration_seconds=7_200,
        steps=(
            ScenarioStep(300, "incident", {"incident_name": "Process upset", "severity": "Medium",
                                            "target_area": "Water supply network", "duration_seconds": 1_800}),
            ScenarioStep(1_200, "incident", {"incident_name": "Mechanical degradation", "severity": "Medium",
                                              "target_area": "Water supply network", "duration_seconds": 1_500}),
        ),
        expected_observations=(
            "Process demand rises and recovery percentage falls during the process upset.",
            "Pump degradation lowers available head and linked flows; tank inventory responds with a lag.",
            "Demand, pump performance, and tank inventory return toward normal after both events clear.",
        ),
    ),
    "mine-water-treatment-upset-and-recovery": ScenarioDefinition(
        scenario_id="mine-water-treatment-upset-and-recovery",
        name="Water-treatment quality upset and recovery",
        description="A high-turbidity source upset loads clarification and filtration, consumes treatment chemicals, and propagates through delayed transfer into the treated-water tank before quality recovers.",
        duration_seconds=21_600,
        steps=(
            ScenarioStep(300, "incident", {"incident_name": "Process upset", "severity": "Large",
                                             "target_area": "Water treatment", "duration_seconds": 14_400}),
        ),
        expected_observations=(
            "Influent turbidity rises during the raw-water disturbance while coagulant dose and filter loading affect removal performance.",
            "The reclaim-to-treated transfer line delays the quality front; the service-water tank buffers the downstream signal rather than changing instantly.",
            "Filter differential pressure and chemical inventories trend with solids capture and treated-water flow.",
            "After the source upset clears, the clarifier/filter output improves first and the finite treated-water tank quality recovers more slowly.",
        ),
    ),
    "boiler-header-pressure-and-recovery": ScenarioDefinition(
        scenario_id="boiler-header-pressure-and-recovery",
        name="Boiler trip, steam header depletion, and recovery",
        description="A boiler-house utility fault drains steam header inventory, cools pasteurizers with thermal lag, then restores heat and condensate return.",
        duration_seconds=3_600,
        steps=(
            ScenarioStep(300, "incident", {"incident_name": "Utility loss", "severity": "Large",
                                            "target_area": "Boiler house", "duration_seconds": 1_200}),
        ),
        expected_observations=(
            "Boiler capacity ramps down; finite header inventory delays the pressure decline.",
            "Low steam pressure reduces pasteurizer heat input, but product temperature falls over several minutes rather than instantly.",
            "Condensate return lags steam use, so feedwater make-up changes after the header event.",
            "When boiler capacity returns, pressure control rebuilds header inventory and pasteurizers warm back toward setpoint.",
        ),
    ),
    "compressed-air-receiver-and-recovery": ScenarioDefinition(
        scenario_id="compressed-air-receiver-and-recovery",
        name="Compressor loss and instrument-air recovery",
        description="A compressor-house utility fault draws down receiver and ring-main air inventory, curtails pneumatic production, and then recovers.",
        duration_seconds=2_400,
        steps=(
            ScenarioStep(300, "incident", {"incident_name": "Utility loss", "severity": "Large",
                                            "target_area": "Compressed air", "duration_seconds": 600}),
        ),
        expected_observations=(
            "The compressors ramp down while the receiver and ring-main linepack buffer pressure for a short time.",
            "As pressure crosses the instrument-air operating band, filler and case-packer rates fall together.",
            "Loss of dryer power degrades dew point; the measurement responds more slowly than the pressure drop.",
            "After compressor capacity returns, the pressure controller recharges the receiver and packaging rates recover.",
        ),
    ),
    "packaging-jam-buffer-and-recovery": ScenarioDefinition(
        scenario_id="packaging-jam-buffer-and-recovery",
        name="Packaging jam, buffer saturation, and line recovery",
        description="A case-packer jam first fills the accumulation conveyor, then backs up the filler and pasteurized-product tank. The restart clears the jam and downstream pull empties both buffers.",
        duration_seconds=2_400,
        steps=(
            ScenarioStep(300, "incident", {"incident_name": "Conveyor jam", "severity": "Large",
                                            "target_area": "Packaging", "duration_seconds": 600}),
        ),
        expected_observations=(
            "The case packer slows with its drive ramp instead of instantaneously stopping.",
            "The accumulation conveyor buffers product briefly; when full, the filler slows.",
            "The pasteurized-product tank then fills and causes upstream product diversion, preserving finite inventory.",
            "After the jam clears, the packer ramps up and drains buffers before the line returns to steady output.",
        ),
    ),
    "cip-water-steam-and-production-recovery": ScenarioDefinition(
        scenario_id="cip-water-steam-and-production-recovery",
        name="CIP water and steam demand with production recovery",
        description="A pasteurizer enters a seven-stage clean-in-place cycle that consumes plant water and boiler steam, diverts wastewater, and lets the rest of the product line buffer the outage before it recovers.",
        duration_seconds=2_400,
        steps=(
            ScenarioStep(300, "cip_start", {"target_unit": "pasteurizer_01"}),
        ),
        expected_observations=(
            "The selected pasteurizer stops production while the parallel line keeps running.",
            "Pre-rinse, heated caustic, hot sanitation, and final-rinse phases produce different water, steam, temperature, pH, and conductivity signals.",
            "The CIP water request enters the shared process-water network; lower water availability pauses rinse and wash phase timers.",
            "Steam demand during heated phases consumes header inventory, and cleaner temperature rises at a thermal rate instead of instantly.",
            "CIP wastewater appears as a separate effluent flow; the product buffer delays filler slowdown, and normal output returns after release.",
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
