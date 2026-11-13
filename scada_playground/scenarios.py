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
            "Each sequential motor start adds a short, size-dependent load pulse; compare the starting-load tag with mine-bus voltage, frequency, and battery response.",
            "The tie-line resumes imports, diesel output ramps down, storage recharges, and throughput recovers after the startup sequence.",
        ),
    ),
    "haulage-crusher-buffer": ScenarioDefinition(
        scenario_id="haulage-crusher-buffer",
        name="Haul truck cycles and crusher stoppage",
        description="Finite truck payloads feed ROM while a crusher conveyor trip forces inventory buffers and dispatch holds to respond.",
        duration_seconds=72_000,
        steps=(
            ScenarioStep(300, "incident", {"incident_name": "Conveyor jam", "severity": "Large",
                                             "target_area": "Crushing and screening", "duration_seconds": 21_600}),
            ScenarioStep(900, "incident", {"incident_name": "Mechanical degradation", "severity": "Medium",
                                             "target_area": "Mobile fleet", "duration_seconds": 2_400}),
        ),
        expected_observations=(
            "Haul trucks deliver finite payloads after loading and loaded trips; return trips carry no ore.",
            "A single shovel and crusher tip serve FIFO queues; watch queue length and per-truck wait tags as arrival bunching changes cycle time.",
            "Waypoints have a fictional elevation profile: loaded climbs slow and burn more fuel, while empty return grades produce a different speed response.",
            "Dispatch holds against ROM plus payload already committed on loaded trucks, reducing stockpile overshoot while late trucks finish their trips.",
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
    "water-distribution-outage-and-demand-recovery": ScenarioDefinition(
        scenario_id="water-distribution-outage-and-demand-recovery",
        name="District water outage, pressure loss, and refill recovery",
        description="A district-pump feeder outage removes elevated-tank refill while customers continue drawing water; a demand upset accelerates storage loss, lowers pressure in the high district, and tests recovery after power returns.",
        duration_seconds=28_800,
        steps=(
            ScenarioStep(300, "incident", {"incident_name": "Utility loss", "severity": "Large",
                                             "target_area": "Water distribution", "duration_seconds": 18_000}),
            ScenarioStep(1_800, "incident", {"incident_name": "Process upset", "severity": "Medium",
                                               "target_area": "Water distribution", "duration_seconds": 3_600}),
        ),
        expected_observations=(
            "The elevated tank buffers district demand after its refill pump loses power; tank level declines while the treated-water plant remains available.",
            "A customer-demand surge and pressure-scaled leaks draw down storage faster; the highest district loses pressure and customer service before the lower districts.",
            "Pressure-dependent demand curtails service as pressure approaches the district minimum, while chlorine continues to decay with water age.",
            "When feeder power returns, the VFD refill flow ramps up, the elevated tank rebuilds inventory, and district pressure and served demand recover with different lags.",
            "Compare district meters with total leakage and non-revenue water to distinguish customer demand from network loss.",
        ),
    ),
    "chilled-water-loss-and-recovery": ScenarioDefinition(
        scenario_id="chilled-water-loss-and-recovery",
        name="Chilled-water failure, thermal reserve, and production recovery",
        description="A plant-wide chilled-water feeder loss draws down thermal storage; once cold reserve is exhausted, pasteurized product is diverted, the product buffer drains, and filling slows until the chillers recover.",
        duration_seconds=10_800,
        steps=(
            ScenarioStep(300, "incident", {"incident_name": "Utility loss", "severity": "Large",
                                             "target_area": "Chilled water", "duration_seconds": 7_200}),
        ),
        expected_observations=(
            "The chillers ramp down after feeder loss while the chilled-water reserve temporarily carries the pasteurizer cooler load.",
            "Once thermal storage is depleted, cooling service falls and the product cooler diverts hot product instead of sending it to the balance tank.",
            "The finite product buffer delays filler starvation; compare cooling service, product temperature, tank inventory, and filler rate.",
            "After power returns, chiller output ramps up and the product buffer refills before packaging fully recovers.",
        ),
    ),
    "mine-water-level-transmitter-drift": ScenarioDefinition(
        scenario_id="mine-water-level-transmitter-drift",
        name="Water-tank transmitter drift",
        description="A level transmitter slowly biases; its automatic pump controller follows the bad reading, creating a real inventory and flow deviation that independent measurements reveal.",
        duration_seconds=7_200,
        steps=(ScenarioStep(300, "incident", {"incident_name": "Signal drift", "severity": "Large",
                                                "target_area": "Water supply network",
                                                "target_tag": "water_supply_network.raw_tank.tank_level",
                                                "duration_seconds": 1_200}),),
        expected_observations=(
            "The raw-tank level transmitter ramps away from the real level; the intake pump PI loop follows the biased measurement and quickly changes source flow.",
            "Full-pipe linepack delays the source-flow disturbance before it reaches the raw tank; then delivered flow and stored volume respond, while the independent volume reading exposes transmitter disagreement.",
            "The biased reading remains GOOD; after the fault expires, normal source flow returns first and the tank inventory recovers only after the transport delay.",
        ),
    ),
    "site-buildings-hvac-outage-and-recovery": ScenarioDefinition(
        scenario_id="site-buildings-hvac-outage-and-recovery",
        name="Site-building HVAC outage and recovery",
        description="A building-feeder outage stops local HVAC and ventilation; different thermal masses, occupancy schedules, and room uses produce distinct temperature, humidity, and CO₂ responses before staggered recovery.",
        duration_seconds=21_600,
        steps=(ScenarioStep(600, "incident", {"incident_name": "Utility loss", "severity": "Large",
                                                "target_area": "Site buildings",
                                                "duration_seconds": 3_600}),),
        expected_observations=(
            "HVAC compressors and ventilation fans lose building power; room temperature responds according to envelope loss, thermal mass, solar gain, and door openings.",
            "Occupancy schedules and reduced outdoor-air flow cause CO₂ and humidity to evolve differently in the control room, workshop, laboratory, and offices.",
            "The building power submeter drops during the outage and contributes less demand to the mine site auxiliary load.",
            "When power returns, compressors wait through a restart delay and spaces recover at different rates; filter loading and differential pressure persist.",
        ),
    ),
    "underground-ventilation-loss-and-recovery": ScenarioDefinition(
        scenario_id="underground-ventilation-loss-and-recovery",
        name="Underground ventilation loss and recovery",
        description="A ventilation-feeder outage removes main-fan flow, while branch airflow, methane dilution, oxygen, dust, and heat respond with different delays before the fans restart.",
        duration_seconds=14_400,
        steps=(ScenarioStep(600, "incident", {"incident_name": "Utility loss", "severity": "Large",
                                                "target_area": "Underground mining",
                                                "duration_seconds": 3_600}),
               ScenarioStep(1_800, "incident", {"incident_name": "Process upset", "severity": "Large",
                                                   "target_area": "Underground mining",
                                                   "duration_seconds": 1_800})),
        expected_observations=(
            "Both main fans stop on loss of the underground feeder; branch airflow falls below the simulated ventilation requirement and alarms before contaminants accumulate.",
            "Methane, diesel NOx, dust, oxygen, and temperature evolve through finite district volumes rather than changing instantly with the incident.",
            "The process upset increases gas release while ventilation is unavailable; compare airflow and pressure with delayed gas measurements.",
            "After feeder restoration, fan speed ramps, fresh air clears district contaminants over their air-change time, and the alarm clears with hysteresis.",
            "The fan motors are included in mine process demand, so their power draw feeds back into grid dispatch and available fan speed.",
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
    "cip-wastewater-quality-and-reuse": ScenarioDefinition(
        scenario_id="cip-wastewater-quality-and-reuse",
        name="CIP effluent treatment and reuse quality",
        description="A CIP cleaning cycle sends variable-pH return to a shared wastewater treatment train while a local outage pauses treatment, then recovery tests neutralization, solids removal, and quality-gated reuse.",
        duration_seconds=4_200,
        steps=(
            ScenarioStep(180, "incident", {"incident_name": "Utility loss", "severity": "Large",
                                               "target_area": "Wastewater", "duration_seconds": 900}),
            ScenarioStep(300, "cip_start", {"target_unit": "pasteurizer_01"}),
        ),
        expected_observations=(
            "CIP return flow carries pH and conductivity from the active cleaning phase into a shared equalization tank.",
            "The wastewater power loss stops treatment while incoming CIP return raises equalization inventory.",
            "After power recovers, neutralization, solids removal, sludge pressing, chemical inventories, and effluent quality respond at different rates.",
            "Only compliant effluent is reused to the mine reclaim pond; the rest leaves through the modeled outfall.",
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
    "distribution-overcurrent-reclose": ScenarioDefinition(
        scenario_id="distribution-overcurrent-reclose",
        name="Distribution feeder overcurrent and reclose",
        description="A feeder fault trips an inverse-time relay and clears for one successful automatic reclose; a second fault retrips and locks out the branch.",
        duration_seconds=120,
        steps=(
            ScenarioStep(10, "incident", {"incident_name": "Electrical fault", "severity": "Large",
                "target_area": "Grid distribution", "target_asset": "Feeder north", "duration_seconds": 8}),
            ScenarioStep(45, "incident", {"incident_name": "Electrical fault", "severity": "Large",
                "target_area": "Grid distribution", "target_asset": "Feeder north",
                "fault_location_pct": 90.0, "primary_breaker_failure": True, "duration_seconds": 20}),
            ScenarioStep(80, "feeder_protection_reset", {"target_asset": "Distribution incomer"}),
        ),
        expected_observations=(
            "The north feeder relay current rises above pickup and its standard-inverse timer trips the breaker.",
            "Calculated three-phase current depends on source short-circuit strength, feeder R/X impedance, fault resistance, and fault location.",
            "The open feeder stops carrying current and disconnects its process service while other feeders remain closed.",
            "After a 15-second dead time, the breaker recloses once because the first fault is clear; the trip and reclose counters remain visible.",
            "The second fault is configured with a failed feeder breaker; its primary relay operates, then the slower incomer backup trips after a coordination delay and isolates all distribution feeders.",
            "After the second fault clears, the replay simulates an operator incomer reset and re-energizes the distribution bus without clearing the trip history.",
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
