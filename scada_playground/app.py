"""A deterministic, local-only synthetic factory for SCADA learning."""
from __future__ import annotations

import random
import time
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from process_model import FactoryProcessModel
from protocol_faults import FAULT_MODES, PROTOCOLS, ProtocolFaultInjector
from scenarios import SCENARIOS, ScenarioRunner, ScenarioStep
from water_network import MineWaterNetwork
from concentrator import CopperConcentrator
from haulage import HaulTruckFleet
from power_network import MinePowerNetwork
from plant_controller import PlantController


AREAS = {
    "Open-pit mining": ["Pit sector A", "Pit sector B", "Shovel fleet", "Haul truck fleet", "Dewatering field"],
    "Underground mining": ["Decline level 1", "Decline level 2", "Ventilation fans", "Ore pass system", "Underground pumps"],
    "Crushing and screening": ["Primary crusher", "Secondary crusher", "Cone crusher 01", "Screen deck A", "Ore conveyors"],
    "Grinding and flotation": ["SAG mill", "Ball mill 01", "Ball mill 02", "Cyclone cluster", "Flotation cells"],
    "Concentrate handling": ["Concentrate thickener", "Filter press", "Concentrate dryer", "Loadout silo", "Rail loadout"],
    "Tailings and reclaim": ["Tailings thickener", "Tailings pump station A", "Tailings pump station B", "Deposition line", "Reclaim pond", "Reclaim pumps"],
    "Mine power network": ["Mine substation A", "Mine substation B", "Pit distribution", "Underground feeders", "Emergency generation"],
    "Grid generation": ["Turbine generator 01", "Turbine generator 02", "Solar farm", "Battery storage", "Black start unit"],
    "Grid transmission": ["Transmission line A", "Transmission line B", "Grid substation A", "Grid substation B", "Intertie"],
    "Grid distribution": ["Feeder north", "Feeder south", "Feeder east", "Feeder west", "Capacitor bank"],
    "Water supply network": ["Reservoir north", "Reservoir south", "Trunk main A", "Trunk main B", "Booster station"],
    "Water distribution": ["District zone A", "District zone B", "District zone C", "Elevated tank", "Leak monitoring"],
    "Mobile fleet": [f"Haul truck {i:02}" for i in range(1, 13)] + [f"Service truck {i:02}" for i in range(1, 5)] + [f"Site bus {i:02}" for i in range(1, 4)],
    "Site buildings": ["Control room", "Workshop", "Warehouse", "Laboratory", "Substation building", "Pump house", "Office block", "Gatehouse"],
    "Inbound logistics": ["Truck scale", "Unloading bay", "Conveyor 01", "Conveyor 02", "Dust collector"],
    "Bulk receiving": ["Silo A", "Silo B", "Silo C", "Silo D", "Silo E"],
    "Raw materials": ["Mill 01", "Mill 02", "Screen 01", "Blender 01", "Transfer pumps"],
    "Water intake": ["Intake screen", "Raw water pumps", "Clarifier", "Raw water tank", "Chlorination"],
    "Water treatment": ["Filter train A", "Filter train B", "RO skid", "Treated tank", "Dosing skid"],
    "Boiler house": ["Boiler 01", "Boiler 02", "Feedwater system", "Steam header", "Condensate return"],
    "Compressed air": ["Compressor A", "Compressor B", "Dryer A", "Receiver 01", "Ring main"],
    "Chilled water": ["Chiller A", "Chiller B", "Primary pumps", "Cooling tower", "Supply header"],
    "Ingredient dosing": ["Doser 01", "Doser 02", "Doser 03", "Doser 04", "Micro ingredients"],
    "Batch blending": ["Mixer 01", "Mixer 02", "Batch tank A", "Batch tank B", "Transfer manifold"],
    "Thermal processing": ["Pasteurizer 01", "Pasteurizer 02", "CIP return", "Hot water loop", "Cooling section"],
    "CIP": ["CIP tank A", "CIP tank B", "CIP supply", "CIP return", "Chemical dosing"],
    "Filling": ["Filler 01", "Filler 02", "Capper 01", "Labeler 01", "Inspection station"],
    "Packaging": ["Case packer A", "Case packer B", "Wrapper", "Cartoner", "Case conveyor"],
    "Palletizing": ["Robot cell A", "Robot cell B", "Pallet conveyor", "Stretch wrapper", "Dispatch buffer"],
    "Warehouse": ["Cold room A", "Cold room B", "Dock doors", "AGV fleet", "Energy meters"],
    "Wastewater": ["Equalization tank", "pH neutralization", "DAF unit", "Sludge press", "Effluent monitor"],
    "Electrical": ["Main switchboard", "Transformer A", "Transformer B", "Generator", "Power quality"],
}

SENSORS = [
    ("process_temperature", "°C", 0, 140, 0.48),
    ("line_pressure", "kPa", 0, 1200, 0.37),
    ("tank_level", "%", 0, 100, 0.62),
    ("motor_current", "A", 0, 160, 0.35),
    ("vibration_rms", "mm/s", 0, 25, 0.12),
    ("flow_rate", "m³/h", 0, 240, 0.42),
    ("valve_position", "%", 0, 100, 0.57),
    ("production_rate", "units/min", 0, 240, 0.52),
]
MINING_SENSORS = [
    ("ore_rate", "t/h", 0, 5000, .55), ("belt_speed", "m/s", 0, 8, .6),
    ("vibration_rms", "mm/s", 0, 25, .12), ("motor_current", "A", 0, 1600, .45),
    ("slurry_flow", "m³/h", 0, 2400, .5), ("pulp_density", "kg/m³", 800, 1800, .55),
    ("sump_level", "%", 0, 100, .48), ("dust_opacity", "%", 0, 100, .1),
]
POWER_SENSORS = [
    ("active_power", "MW", 0, 500, .55), ("line_voltage", "kV", 0, 500, .8),
    ("line_current", "A", 0, 3000, .48), ("grid_frequency", "Hz", 45, 55, .5),
    ("transformer_oil_temp", "°C", 0, 150, .4), ("reactive_power", "MVAr", -200, 200, .5),
    ("breaker_state", "%", 0, 1, .95), ("power_quality_thd", "%", 0, 20, .12),
]
WATER_SENSORS = [
    ("flow_rate", "m³/h", 0, 5000, .5), ("line_pressure", "kPa", 0, 1600, .58),
    ("tank_level", "%", 0, 100, .7), ("turbidity", "NTU", 0, 100, .08),
    ("ph", "pH", 0, 14, .51), ("chlorine_residual", "mg/L", 0, 5, .5),
    ("pump_current", "A", 0, 800, .45), ("valve_position", "%", 0, 100, .65),
]
BUILDING_SENSORS = [
    ("indoor_temperature", "°C", 0, 50, .46), ("indoor_humidity", "%RH", 0, 100, .45),
    ("hvac_supply_temp", "°C", 0, 35, .44), ("hvac_filter_dp", "Pa", 0, 1200, .25),
    ("occupancy_count", "people", 0, 250, .12), ("door_state", "%", 0, 1, .95),
    ("utility_power", "kW", 0, 800, .4), ("ambient_noise", "dBA", 0, 140, .25),
]
INCIDENTS = {
    "Signal drift": "measurement",
    "Network partition": "comms",
    "Utility loss": "utility",
    "Mechanical degradation": "mechanical",
    "Process upset": "process",
    "Conveyor jam": "jam",
}

# Fictional road geometry for a teaching site in northern Chile. The coordinate
# frame is near the Escondida region; facility names and roads are invented.
SITE_CENTER = {"lat": -24.277, "lon": -69.067}
ROOT = Path(__file__).resolve().parent
SNAPSHOT_PATH = ROOT / ".runtime" / "factory_snapshot.json"
MCP_COMMAND_DIR = ROOT / ".runtime" / "mcp_commands"
FLEET_ROUTES = {
    "Haul truck": [
        ("Pit loading", -24.2910, -69.0810, 32),
        ("Pit ramp bend", -24.2870, -69.0765, 0),
        ("Haul road junction", -24.2815, -69.0730, 0),
        ("Crusher tip", -24.2780, -69.0670, 24),
        ("East return bend", -24.2810, -69.0605, 0),
        ("South return road", -24.2870, -69.0640, 0),
    ],
    "Service truck": [
        ("Workshop", -24.2730, -69.0700, 24),
        ("Process plant", -24.2780, -69.0670, 12),
        ("Power substation", -24.2735, -69.0590, 26),
        ("Water pump station", -24.2660, -69.0620, 16),
    ],
    "Site bus": [
        ("Camp gate", -24.2685, -69.0730, 35),
        ("Operations building", -24.2740, -69.0690, 18),
        ("Process plant stop", -24.2780, -69.0670, 22),
        ("Pit access stop", -24.2840, -69.0740, 16),
    ],
}


def distance_km(a: tuple, b: tuple) -> float:
    """Approximate ground distance between (lat, lon) pairs."""
    from math import asin, cos, radians, sin, sqrt
    lat1, lon1 = radians(a[0]), radians(a[1])
    lat2, lon2 = radians(b[0]), radians(b[1])
    dlat, dlon = lat2-lat1, lon2-lon1
    h = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
    return 6371 * 2 * asin(sqrt(h))


def fleet_motion(unit: str, sim_seconds: int) -> tuple[float, float, float, float, float, str]:
    """Return lat, lon, speed, heading, payload, and route state for one asset."""
    vehicle_type = unit.rsplit(" ", 1)[0]
    route = FLEET_ROUTES[vehicle_type]
    haul = vehicle_type == "Haul truck"
    cycle = []
    for i, (label, lat, lon, dwell) in enumerate(route):
        nxt = route[(i+1) % len(route)]
        dist = distance_km((lat, lon), (nxt[1], nxt[2]))
        loaded_leg = haul and i < 3
        speed = (22 if loaded_leg else 34) if haul else (25 if vehicle_type == "Service truck" else 38)
        travel = max(1.0, dist / speed * 3600)
        cycle.append((label, lat, lon, dwell, nxt[1], nxt[2], travel, speed, loaded_leg))
    cycle_seconds = sum(x[3] + x[6] for x in cycle)
    phase = ((sum(ord(ch) for ch in unit) * 37) % max(1, int(cycle_seconds)))
    t = (sim_seconds + phase) % cycle_seconds
    for label, lat, lon, dwell, next_lat, next_lon, travel, speed, loaded in cycle:
        if t < dwell:
            return lat, lon, 0.0, 0.0, (210.0 if haul and label != "Crusher tip" else 0.0), f"Stopped · {label}"
        t -= dwell
        if t < travel:
            fraction = t / travel
            bearing = __import__("math").degrees(__import__("math").atan2(next_lon-lon, next_lat-lat)) % 360
            return (lat + fraction*(next_lat-lat), lon + fraction*(next_lon-lon), speed,
                    bearing, 210.0 if loaded else 0.0, "Hauling loaded" if loaded else "Returning empty" if haul else "In transit")
        t -= travel
    return route[0][1], route[0][2], 0.0, 0.0, 0.0, "Stopped"


PROCESS_MODEL = FactoryProcessModel(AREAS, INCIDENTS, fleet_motion)
WATER_MODEL = MineWaterNetwork()
CONCENTRATOR_MODEL = CopperConcentrator()
HAULAGE_MODEL = HaulTruckFleet(FLEET_ROUTES, distance_km)
POWER_MODEL = MinePowerNetwork()
PLANT_CONTROLLER = PlantController()
SCENARIO_RUNNER = ScenarioRunner()
PROTOCOL_FAULTS = ProtocolFaultInjector()


def initialize(seed: int = 17) -> None:
    if "sim" not in st.session_state:
        if not 0 <= int(seed) <= 2**32 - 1:
            raise ValueError("Seed must be between 0 and 4,294,967,295")
        seed = int(seed)
        rng = random.Random(seed)
        tags = []
        for area, units in AREAS.items():
            area_sensors = (MINING_SENSORS if any(k in area.lower() for k in ("mining", "crushing", "grinding", "concentrate", "tailings"))
                            else POWER_SENSORS if "grid" in area.lower() or area == "Electrical" or area == "Mine power network"
                            else BUILDING_SENSORS if area == "Site buildings"
                            else WATER_SENSORS if "water" in area.lower() or area in ("Wastewater", "CIP")
                            else SENSORS)
            for ui, unit in enumerate(units, 1):
                for name, units_name, low, high, base in area_sensors:
                    tag = f"{area.lower().replace(' ', '_')}.{unit.lower().replace(' ', '_')}.{name}"
                    tags.append({"tag": tag, "area": area, "unit": unit, "sensor": name, "units": units_name,
                                 "low": low, "high": high, "base": low + base * (high-low),
                                 "value": low + base * (high-low), "quality": "GOOD", "unit_no": ui,
                                 "phase": rng.random() * 6.28, "last_change": "Normal"})
                if area == "Mobile fleet":
                    # GPS coordinates describe a closed, fictional site route in northern Chile.
                    for gps_name, gps_unit, low, high, gps_base in [
                        ("gps_latitude", "°", -24.4, -24.2, SITE_CENTER["lat"]),
                        ("gps_longitude", "°", -69.2, -68.9, SITE_CENTER["lon"]),
                        ("gps_speed", "km/h", 0, 60, 0),
                        ("heading", "°", 0, 360, 0),
                        ("payload", "t", 0, 250, 0),
                    ]:
                        tags.append({"tag": f"{area.lower().replace(' ', '_')}.{unit.lower().replace(' ', '_')}.{gps_name}",
                                     "area": area, "unit": unit, "sensor": gps_name, "units": gps_unit,
                                     "low": low, "high": high, "base": gps_base, "value": gps_base,
                                     "quality": "GOOD", "unit_no": ui, "phase": rng.random() * 6.28,
                                     "last_change": "Normal"})
        concentrator_state = CONCENTRATOR_MODEL.initialize()
        process_control_state = PLANT_CONTROLLER.initialize()
        haulage_state = HAULAGE_MODEL.initialize()
        modeled_assets = {(area, asset) for area, assets in CONCENTRATOR_MODEL.AREA_ASSETS.items() for asset in assets}
        modeled_assets.add(("Concentrate handling", "Thickener"))  # legacy generic asset name
        modeled_assets.update(("Mobile fleet", unit) for unit in HAULAGE_MODEL.truck_names)
        tags = [tag for tag in tags if (tag["area"], tag["unit"]) not in modeled_assets]
        modeled_tags = CONCENTRATOR_MODEL.telemetry(concentrator_state) + HAULAGE_MODEL.telemetry(haulage_state, seed)
        for tag in modeled_tags:
            tag["dynamic_model"] = True
        tags.extend(modeled_tags)
        water_state = WATER_MODEL.initialize()
        water_tags, _water_diagram = WATER_MODEL.telemetry(water_state)
        tags.extend(water_tags)
        power_state = POWER_MODEL.initialize()
        initial_process_power_mw = (concentrator_state["sag_power_mw"] + concentrator_state["ball_power_mw"]
                                     + concentrator_state["crusher_power_mw"] + 1.0)
        initial_water_power_mw = sum(pump["power_kw"] for pump in water_state["pumps"].values()) / 1000.0
        POWER_MODEL.advance(power_state, 0, [], process_load_mw=initial_process_power_mw,
                            water_load_mw=initial_water_power_mw, seed=seed)
        power_tags = POWER_MODEL.telemetry(power_state)
        power_assets = {(area, asset) for area, assets in POWER_MODEL.AREA_ASSETS.items() for asset in assets}
        tags = [tag for tag in tags if (tag["area"], tag["unit"]) not in power_assets]
        for tag in power_tags:
            tag["dynamic_model"] = True
        tags.extend(power_tags)
        st.session_state.sim = {"tags": tags, "running": False, "tick": 0, "seed": seed,
                                "water_network": water_state,
                                "power_network": power_state,
                                "concentrator": concentrator_state,
                                "process_control": process_control_state,
                                "haulage": haulage_state,
                                "simulation_id": uuid.uuid4().hex,
                                "water_alarm_states": {},
                                "concentrator_alarm_states": {},
                                "haulage_alarm_states": {},
                                "events": [], "history": [], "alarms": [], "next_event": 1,
                                "protocol_faults": [], "next_fault_id": 1, "scenario_run": None,
                                "last_tick": time.time(), "factory_on": False}
    else:
        sim = st.session_state.sim
        sim.setdefault("seed", int(seed))
        sim.setdefault("simulation_id", uuid.uuid4().hex)
        sim.setdefault("protocol_faults", [])
        sim.setdefault("next_fault_id", 1)
        sim.setdefault("scenario_run", None)
        if "concentrator" not in sim:
            sim["concentrator"] = CONCENTRATOR_MODEL.initialize()
        if "process_control" not in sim:
            sim["process_control"] = PLANT_CONTROLLER.initialize()
        PLANT_CONTROLLER.migrate(sim["process_control"])
        CONCENTRATOR_MODEL.migrate(sim["concentrator"])
        if "haulage" not in sim:
            sim["haulage"] = HAULAGE_MODEL.initialize()
        modeled_assets = {(area, asset) for area, assets in CONCENTRATOR_MODEL.AREA_ASSETS.items() for asset in assets}
        modeled_assets.add(("Concentrate handling", "Thickener"))  # legacy generic asset name
        modeled_assets.update(("Mobile fleet", unit) for unit in HAULAGE_MODEL.truck_names)
        sim["tags"] = [tag for tag in sim["tags"] if (tag["area"], tag["unit"]) not in modeled_assets]
        modeled_tags = CONCENTRATOR_MODEL.telemetry(sim["concentrator"]) + HAULAGE_MODEL.telemetry(sim["haulage"], sim["seed"])
        for tag in modeled_tags:
            tag["dynamic_model"] = True
            sim["tags"].append(tag)
        if "water_network" not in sim:
            sim["water_network"] = WATER_MODEL.initialize()
            water_tags, _diagram = WATER_MODEL.telemetry(sim["water_network"])
            sim["tags"].extend(water_tags)
        if "power_network" not in sim:
            sim["power_network"] = POWER_MODEL.initialize()
            process_load_mw = (sim["concentrator"]["sag_power_mw"] + sim["concentrator"]["ball_power_mw"]
                               + sim["concentrator"]["crusher_power_mw"] + 1.0)
            water_load_mw = sum(pump["power_kw"] for pump in sim["water_network"]["pumps"].values()) / 1000.0
            POWER_MODEL.advance(sim["power_network"], sim["tick"], [], process_load_mw=process_load_mw,
                                water_load_mw=water_load_mw, seed=sim["seed"])
        power_assets = {(area, asset) for area, assets in POWER_MODEL.AREA_ASSETS.items() for asset in assets}
        sim["tags"] = [tag for tag in sim["tags"] if (tag["area"], tag["unit"]) not in power_assets]
        for tag in POWER_MODEL.telemetry(sim["power_network"]):
            tag["dynamic_model"] = True
            sim["tags"].append(tag)


def publish_snapshot() -> dict:
    """Publish one atomic local file snapshot for the separate protocol gateway."""
    sim = st.session_state.sim
    fleet_units = sorted({t["unit"] for t in sim["tags"] if t["area"] == "Mobile fleet"})
    water_tags, water_diagram = WATER_MODEL.telemetry(sim["water_network"])
    concentrator_tags = CONCENTRATOR_MODEL.telemetry(sim["concentrator"])
    power_tags = POWER_MODEL.telemetry(sim["power_network"])
    haulage_tags = HAULAGE_MODEL.telemetry(sim["haulage"], sim["seed"])
    haulage_frames = {frame["unit"]: frame for frame in HAULAGE_MODEL.positions(sim["haulage"])}
    concentrator_by_tag = {tag["tag"]: tag for tag in concentrator_tags + haulage_tags}
    water_by_tag = {tag["tag"]: tag for tag in water_tags}
    power_by_tag = {tag["tag"]: tag for tag in power_tags}
    for tag in sim["tags"]:
        if tag["tag"] in water_by_tag:
            tag.update(water_by_tag[tag["tag"]])
        elif tag["tag"] in power_by_tag:
            tag.update(power_by_tag[tag["tag"]])
        elif tag["tag"] in concentrator_by_tag:
            tag.update(concentrator_by_tag[tag["tag"]])
        comms_fault = next((event for event in sim["events"]
                            if event.name == "Network partition"
                            and event.area in (tag["area"], "Whole factory")), None)
        if comms_fault:
            tag["quality"] = "STALE"
            tag["last_change"] = "Network partition"
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sim_seconds": sim["tick"],
        "running": sim["running"],
        "seed": sim["seed"],
        "simulation_id": sim["simulation_id"],
        "available_areas": ["Whole factory", *AREAS.keys()],
        "available_incidents": list(INCIDENTS),
        "available_scenarios": SCENARIO_RUNNER.catalog(),
        "scenario_run": sim["scenario_run"],
        "protocol_faults": sim["protocol_faults"],
        "water_network": water_diagram[0],
        "power_network": {key: value for key, value in sim["power_network"].items() if key != "history"},
        "concentrator": {key: value for key, value in sim["concentrator"].items() if key != "history"},
        "process_control": PLANT_CONTROLLER.public_state(sim["process_control"]),
        "haulage": HAULAGE_MODEL.summary(sim["haulage"]),
        "tags": [{"tag": t["tag"], "area": t["area"], "asset": t["unit"], "sensor": t["sensor"],
                  "value": t["value"], "units": t["units"], "quality": t["quality"],
                  "low": t["low"], "high": t["high"], "last_change": t["last_change"]} for t in sim["tags"]],
        "alarms": sim["alarms"],
        "fleet": [haulage_frames[unit] if unit in haulage_frames else {
                   "unit": unit, "vehicle_type": unit.rsplit(" ", 1)[0],
                   "latitude": fleet_motion(unit, sim["tick"])[0],
                   "longitude": fleet_motion(unit, sim["tick"])[1],
                   "speed_kmh": fleet_motion(unit, sim["tick"])[2],
                   "heading": fleet_motion(unit, sim["tick"])[3],
                   "payload_t": fleet_motion(unit, sim["tick"])[4],
                   "state": fleet_motion(unit, sim["tick"])[5]} for unit in fleet_units],
        "routes": [{"vehicle_type": vehicle_type,
                    "points": [{"name": p[0], "latitude": p[1], "longitude": p[2]} for p in route]}
                   for vehicle_type, route in FLEET_ROUTES.items()],
    }
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = SNAPSHOT_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp_path, SNAPSHOT_PATH)
    return payload


def apply_scenario_action(sim: dict, step: ScenarioStep) -> None:
    params = step.parameters
    if step.action == "incident":
        PROCESS_MODEL.inject(sim, params["incident_name"], params["target_area"], params["severity"],
                             params.get("duration_seconds"))
    elif step.action == "protocol_fault":
        PROTOCOL_FAULTS.add(sim, params["protocol"], params["mode"], params["selector_type"],
                            params["target"], params["duration_seconds"])
    else:
        raise ValueError(f"Scenario action is not supported: {step.action}")


def inject(name: str, area: str, severity: str, duration_seconds: int | None = None) -> None:
    PROCESS_MODEL.inject(st.session_state.sim, name, area, severity, duration_seconds)


def process_mcp_commands() -> list[tuple[str, dict]]:
    """Apply localhost MCP requests inside the simulator's owning Streamlit session."""
    if not MCP_COMMAND_DIR.exists():
        return []
    results = []
    for command_path in sorted(MCP_COMMAND_DIR.glob("*.json")):
        if command_path.name.endswith(".result.json"):
            continue
        command_id = command_path.stem
        try:
            command = json.loads(command_path.read_text(encoding="utf-8"))
            action = command.get("action")
            sim = st.session_state.sim
            if action == "start":
                sim["running"] = sim["factory_on"] = True
                SCENARIO_RUNNER.resume(sim)
                message = "Synthetic factory started."
            elif action == "stop":
                sim["running"] = False
                SCENARIO_RUNNER.pause(sim)
                message = "Synthetic factory stopped."
            elif action == "reset":
                PROTOCOL_FAULTS.clear(sim)
                st.session_state.pop("sim", None)
                initialize(int(command.get("seed", 17)))
                sim = st.session_state.sim
                message = f"Synthetic factory reset with seed {sim['seed']}."
            elif action == "step":
                seconds = int(command.get("seconds", 1))
                if sim["running"]:
                    raise ValueError("Pause the simulator before single-stepping")
                if not 1 <= seconds <= 300:
                    raise ValueError("Single-step size must be between 1 and 300 simulated seconds")
                for _ in range(seconds):
                    advance(force=True)
                sim = st.session_state.sim
                message = f"Advanced exactly {seconds} simulated seconds while paused."
            elif action == "run_scenario":
                scenario_id = command.get("scenario_id", "")
                seed = int(command.get("seed", 17))
                PROTOCOL_FAULTS.clear(sim)
                st.session_state.pop("sim", None)
                initialize(seed)
                sim = st.session_state.sim
                SCENARIO_RUNNER.start(sim, scenario_id, seed, apply_scenario_action)
                message = f"Started/replayed scenario '{SCENARIO_RUNNER.definition(scenario_id).name}' with seed {seed}."
            elif action == "inject_incident":
                name = command.get("incident_name", "")
                severity = command.get("severity", "")
                area = command.get("target_area", "")
                duration = command.get("duration_seconds")
                inject(name, area, severity, duration)
                message = f"{severity} {name} injected in {area}. Start the factory to evolve its simulated process effects."
            elif action == "process_control":
                control = sim["process_control"]
                process_action = command.get("process_action", "").upper()
                if process_action in {"START", "STOP"}:
                    PLANT_CONTROLLER.request(control, process_action)
                    message = f"Orderly process {process_action.lower()} sequence requested."
                elif process_action == "RESET_TRIPS":
                    power = sim["power_network"]
                    cleared = PLANT_CONTROLLER.reset_trips(
                        control, events=[event.__dict__ for event in sim["events"]],
                        power_fraction=power["process_power_fraction"],
                        voltage_pu=power["voltage_pu"], frequency_hz=power["frequency_hz"])
                    message = f"Reset {len(cleared)} latched process trip(s); inspect and request START manually."
                else:
                    raise ValueError("process_action must be START, STOP, or RESET_TRIPS")
            elif action == "inject_protocol_fault":
                fault = PROTOCOL_FAULTS.add(
                    sim, command.get("protocol", ""), command.get("mode", ""),
                    command.get("selector_type", ""), command.get("target", ""),
                    int(command.get("duration_seconds", 0)))
                message = f"Protocol fault {fault['id']} injected."
            elif action == "clear_protocol_faults":
                count = PROTOCOL_FAULTS.clear(sim, command.get("fault_id", ""))
                message = f"Cleared {count} protocol fault(s)."
            elif action == "acknowledge_alarms":
                sim["alarms"] = [{**alarm, "state": "ACTIVE / ACK" if alarm["state"] == "ACTIVE / UNACK" else alarm["state"]}
                                  for alarm in sim["alarms"]]
                message = "All active simulator alarms acknowledged."
            else:
                raise ValueError("Unsupported action")
            result = {"ok": True, "command_id": command_id, "action": action, "message": message}
        except Exception as exc:
            result = {"ok": False, "command_id": command_id, "error": str(exc)}
        results.append((command_id, result))
        command_path.unlink(missing_ok=True)
    return results


def publish_mcp_results(results: list[tuple[str, dict]], published_snapshot: dict) -> None:
    """Reply only after the updated snapshot is visible to all protocol readers."""
    for command_id, result in results:
        result_path = MCP_COMMAND_DIR / f"{command_id}.result.json"
        result.update({"running": published_snapshot["running"],
                       "sim_seconds": published_snapshot["sim_seconds"],
                       "snapshot_timestamp": published_snapshot["timestamp"]})
        temporary_path = result_path.with_suffix(".tmp")
        temporary_path.write_text(json.dumps(result), encoding="utf-8")
        os.replace(temporary_path, result_path)


def advance(force: bool = False) -> None:
    sim = st.session_state.sim
    if not sim["running"] and not force:
        return
    SCENARIO_RUNNER.before_tick(sim, apply_scenario_action)
    PROCESS_MODEL.advance(sim, force=force)
    water_events = [event.__dict__ for event in sim["events"]
                    if event.area in ("Water supply network", "Mine power network", "Whole factory")]
    concentrator_events = [event.__dict__ for event in sim["events"]]
    process_power_load_mw = (sim["concentrator"]["sag_power_mw"] + sim["concentrator"]["ball_power_mw"]
                             + sim["concentrator"]["crusher_power_mw"] + 1.0)
    water_power_load_mw = sum(pump["power_kw"] for pump in sim["water_network"]["pumps"].values()) / 1000.0
    POWER_MODEL.advance(sim["power_network"], sim["tick"], concentrator_events,
                        process_load_mw=process_power_load_mw,
                        water_load_mw=water_power_load_mw, seed=sim["seed"])
    water_level = sim["water_network"]["nodes"]["process_tank"]["level_pct"]
    trips_before = sim["process_control"]["trip_count"]
    PLANT_CONTROLLER.advance(
        sim["process_control"], sim["tick"], concentrator_events,
        process_power_fraction=sim["power_network"]["process_power_fraction"],
        water_power_fraction=sim["power_network"]["water_power_fraction"],
        process_water_level_pct=water_level,
        voltage_pu=sim["power_network"]["voltage_pu"],
        frequency_hz=sim["power_network"]["frequency_hz"])
    new_trips = sim["process_control"]["trip_count"] - trips_before
    if new_trips > 0:
        sim["alarms"].insert(0, {"time": sim["tick"], "area": "Grinding and flotation",
            "severity": "CRITICAL", "message": sim["process_control"]["last_transition"],
            "state": "ACTIVE / UNACK"})
    equipment_available = PLANT_CONTROLLER.availability(sim["process_control"])
    mine_delivery_t = HAULAGE_MODEL.advance(sim["haulage"], sim["tick"], concentrator_events,
                                             sim["concentrator"]["rom_stockpile_t"])
    prior_water_shortage = sim["water_network"].get("unmet_demand_m3h", 0.0)
    concentrator_share = sim["concentrator"]["water_makeup_m3h"] / max(sim["water_network"].get("demand_m3h", 1), 1)
    CONCENTRATOR_MODEL.advance(sim["concentrator"], sim["tick"], concentrator_events,
                               water_shortage_m3h=prior_water_shortage * min(1.0, concentrator_share),
                               seed=sim["seed"], mine_delivery_t=mine_delivery_t,
                               power_available_fraction=sim["power_network"]["process_power_fraction"],
                               equipment_available=equipment_available)
    WATER_MODEL.advance(sim["water_network"], sim["tick"], water_events, seed=sim["seed"],
                        concentrator_demand_m3h=sim["concentrator"]["water_makeup_m3h"],
                        power_available_fraction=sim["power_network"]["water_power_fraction"])
    water = sim["water_network"]
    conditions = {
        "process_tank_low": (water["nodes"]["process_tank"]["level_pct"] < 25,
                              "Process water tank low level", "HIGH"),
        "unmet_process_demand": (water["unmet_demand_m3h"] > 1,
                                  "Process water demand is not being met", "CRITICAL"),
        "unmet_treated_water_demand": (water["unmet_treated_demand_m3h"] > 1,
                                        "Treated water distribution demand is not being met", "HIGH"),
        "raw_tank_high": (water["nodes"]["raw_tank"]["level_pct"] > 90,
                           "Raw water tank high level", "HIGH"),
        "tank_spill": (any(node["overflow_m3"] > 0 for node in water["nodes"].values()),
                       "Water tank overflow detected", "HIGH"),
    }
    prior_states = sim.setdefault("water_alarm_states", {})
    for alarm_key, (active, message, severity) in conditions.items():
        was_active = prior_states.get(alarm_key, False)
        if active and not was_active:
            sim["alarms"].insert(0, {"time": sim["tick"], "area": "Water supply network",
                                     "severity": severity, "message": message,
                                     "state": "ACTIVE / UNACK"})
        elif was_active and not active:
            sim["alarms"].insert(0, {"time": sim["tick"], "area": "Water supply network",
                                     "severity": "INFO", "message": f"{message} cleared",
                                     "state": "CLEARED"})
        prior_states[alarm_key] = active
    plant = sim["concentrator"]
    concentrator_conditions = {
        "low_crushed_inventory": ("Grinding and flotation", plant["crushed_stockpile_t"] < 500,
            plant["crushed_stockpile_t"] > 800, "Crushed-ore stockpile low; mill feed buffer is being depleted", "HIGH"),
        "rom_stockpile_high": ("Open-pit mining", plant["rom_stockpile_t"] > 40_000,
            plant["rom_stockpile_t"] < 34_000, "ROM stockpile high; haul dispatch is waiting at the shovel", "HIGH"),
        "sag_bin_high": ("Grinding and flotation", plant["sag_bin_t"] > 820, plant["sag_bin_t"] < 720,
            "SAG feed bin high; upstream feeder should reduce rate", "HIGH"),
        "coarse_grind_product": ("Grinding and flotation", plant["p80_microns"] > 200,
            plant["p80_microns"] < 180, "Grinding product is coarse; flotation liberation may be reduced", "HIGH"),
        "low_copper_recovery": ("Grinding and flotation", plant["copper_recovery_pct"] < 78,
            plant["copper_recovery_pct"] > 82, "Estimated copper recovery below operating target", "HIGH"),
        "tailings_thickener_high": ("Tailings and reclaim",
            plant["tailings_thickener_inventory_t"] > 0.90 * plant["tailings_thickener_capacity_t"],
            plant["tailings_thickener_inventory_t"] < 0.78 * plant["tailings_thickener_capacity_t"],
            "Tailings thickener inventory high; concentrator feed is being curtailed", "HIGH"),
        "concentrate_thickener_high": ("Concentrate handling",
            plant["concentrate_thickener_inventory_t"] > 0.85 * plant["concentrate_thickener_capacity_t"],
            plant["concentrate_thickener_inventory_t"] < 0.70 * plant["concentrate_thickener_capacity_t"],
            "Concentrate thickener inventory high; filter press throughput is limiting", "HIGH"),
        "concentrate_cake_high": ("Concentrate handling",
            plant["concentrate_cake_inventory_t"] > 0.90 * plant["concentrate_cake_capacity_t"],
            plant["concentrate_cake_inventory_t"] < 0.75 * plant["concentrate_cake_capacity_t"],
            "Concentrate cake stockpile high; filter press discharge is backing up", "HIGH"),
        "concentrator_water_shortage": ("Grinding and flotation", plant["fresh_water_shortage_m3h"] > 25,
            plant["fresh_water_shortage_m3h"] < 5, "Concentrator process water shortfall is limiting feed", "CRITICAL"),
    }
    concentration_states = sim.setdefault("concentrator_alarm_states", {})
    for alarm_key, (area, active, clear_now, message, severity) in concentrator_conditions.items():
        was_active = concentration_states.get(alarm_key, False)
        if active and not was_active:
            sim["alarms"].insert(0, {"time": sim["tick"], "area": area,
                "severity": severity, "message": message, "state": "ACTIVE / UNACK"})
            concentration_states[alarm_key] = True
        elif was_active and clear_now:
            sim["alarms"].insert(0, {"time": sim["tick"], "area": area,
                "severity": "INFO", "message": f"{message} cleared", "state": "CLEARED"})
            concentration_states[alarm_key] = False
    haulage_alarm_states = sim.setdefault("haulage_alarm_states", {})
    for tag in sim["tags"]:
        if tag["area"] != "Mobile fleet" or tag["sensor"] != "fuel_level":
            continue
        alarm_key = f"low_fuel:{tag['unit']}"
        low_fuel = tag["value"] < 18
        recovered = tag["value"] > 30
        was_active = haulage_alarm_states.get(alarm_key, False)
        message = f"{tag['unit']} fuel reserve low; refuel at shovel queue"
        if low_fuel and not was_active:
            sim["alarms"].insert(0, {"time": sim["tick"], "area": "Mobile fleet",
                "severity": "HIGH", "message": message, "state": "ACTIVE / UNACK"})
            haulage_alarm_states[alarm_key] = True
        elif was_active and recovered:
            sim["alarms"].insert(0, {"time": sim["tick"], "area": "Mobile fleet",
                "severity": "INFO", "message": f"{message} cleared", "state": "CLEARED"})
            haulage_alarm_states[alarm_key] = False
    power_alarm_states = sim.setdefault("power_alarm_states", {})
    power_state = sim["power_network"]
    power_conditions = {
        "underfrequency": (power_state["frequency_hz"] < 49.5, power_state["frequency_hz"] > 49.8,
                            "Mine bus frequency below operating band", "CRITICAL"),
        "low_voltage": (power_state["voltage_pu"] < 0.92, power_state["voltage_pu"] > 0.96,
                         "Mine bus voltage depressed", "HIGH"),
        "load_shed": (power_state["load_shed_mw"] > 0.1, power_state["load_shed_mw"] < 0.05,
                      "Automatic site auxiliary load shedding active", "HIGH"),
        "process_feeder_limited": (power_state["process_power_fraction"] < 0.95,
                                   power_state["process_power_fraction"] > 0.98,
                                   "Concentrator electrical supply is feeder- or source-limited", "HIGH"),
        "water_feeder_limited": (power_state["water_power_fraction"] < 0.95,
                                 power_state["water_power_fraction"] > 0.98,
                                 "Water pumping electrical supply is feeder- or source-limited", "HIGH"),
        "battery_reserve": (power_state["battery_soc_pct"] < 20, power_state["battery_soc_pct"] > 30,
                            "Grid support battery reserve low", "HIGH"),
    }
    for key, (active, cleared, message, severity) in power_conditions.items():
        was_active = power_alarm_states.get(key, False)
        if active and not was_active:
            sim["alarms"].insert(0, {"time": sim["tick"], "area": "Mine power network",
                                     "severity": severity, "message": message,
                                     "state": "ACTIVE / UNACK"})
            power_alarm_states[key] = True
        elif was_active and cleared:
            sim["alarms"].insert(0, {"time": sim["tick"], "area": "Mine power network",
                                     "severity": "INFO", "message": f"{message} cleared",
                                     "state": "CLEARED"})
            power_alarm_states[key] = False
    water_tags, _diagram = WATER_MODEL.telemetry(sim["water_network"])
    power_tags = POWER_MODEL.telemetry(sim["power_network"])
    water_by_tag = {tag["tag"]: tag for tag in water_tags}
    power_by_tag = {tag["tag"]: tag for tag in power_tags}
    concentrator_by_tag = {tag["tag"]: tag for tag in
                           CONCENTRATOR_MODEL.telemetry(sim["concentrator"])
                           + HAULAGE_MODEL.telemetry(sim["haulage"], sim["seed"])}
    for tag in sim["tags"]:
        if tag["tag"] in water_by_tag:
            tag.update(water_by_tag[tag["tag"]])
        elif tag["tag"] in power_by_tag:
            tag.update(power_by_tag[tag["tag"]])
        elif tag["tag"] in concentrator_by_tag:
            tag.update(concentrator_by_tag[tag["tag"]])
    if sim["tick"] % 15 == 0:
        sim["history"].extend({"time": sim["tick"], "tag": tag["tag"], "value": tag["value"],
                               "quality": tag["quality"]} for tag in sim["tags"])
        sim["history"] = sim["history"][-len(sim["tags"]) * 120:]
    SCENARIO_RUNNER.after_tick(sim)
    PROTOCOL_FAULTS.expire(sim)


st.set_page_config(page_title="Factory SCADA Playground", page_icon="🏭", layout="wide")
initialize()
sim = st.session_state.sim
publish_snapshot()
st.title("🏭 Factory SCADA Playground")
st.caption(f"Synthetic multi-industry training world · {len(AREAS)} systems · {len({(t['area'], t['unit']) for t in sim['tags']})} equipment units · {len(sim['tags'])} tags · local simulation only")

with st.sidebar:
    st.header("Factory controls")
    if st.button("▶ Start factory", type="primary", width="stretch"):
        sim["running"] = True
        sim["factory_on"] = True
        SCENARIO_RUNNER.resume(sim)
    if st.button("⏸ Stop factory", width="stretch"):
        sim["running"] = False
        SCENARIO_RUNNER.pause(sim)
    if st.button("↺ Reset simulation", width="stretch"):
        PROTOCOL_FAULTS.clear(sim)
        del st.session_state["sim"]
        st.rerun()
    if st.button("⏭ Step +1 simulated second", disabled=sim["running"], width="stretch"):
        advance(force=True)
    speed = st.select_slider("Simulation speed", options=[1, 2, 5, 10, 30], value=5, format_func=lambda x: f"{x}×")
    st.divider()
    st.subheader("Scenario replay")
    scenario_id = st.selectbox("Scenario", list(SCENARIOS),
                               format_func=lambda key: SCENARIOS[key].name)
    scenario_seed = st.number_input("Replay seed", min_value=0, max_value=2**32 - 1,
                                    value=int(sim["seed"]), step=1)
    if st.button("▶ Run / replay from reset", width="stretch"):
        PROTOCOL_FAULTS.clear(sim)
        st.session_state.pop("sim", None)
        initialize(int(scenario_seed))
        sim = st.session_state.sim
        SCENARIO_RUNNER.start(sim, scenario_id, int(scenario_seed), apply_scenario_action)
    st.caption("Same seed and scenario replay the same synthetic timeline. Stop to pause; single-step to inspect.")
    st.divider()
    st.subheader("Inject an incident")
    incident_name = st.selectbox("Incident", list(INCIDENTS))
    severity = st.select_slider("Severity", ["Small", "Medium", "Large"], value="Small")
    target = st.selectbox("Target area", ["Whole factory", *AREAS.keys()])
    if st.button("⚠ Inject incident", width="stretch"):
        inject(incident_name, target, severity)
    st.caption("Incidents change simulated sensor behavior and produce alarm events. They never connect to equipment.")
    with st.expander("Protocol fault injector"):
        fault_protocol = st.selectbox("Protocol", PROTOCOLS, key="fault_protocol")
        fault_mode = st.selectbox("Fault mode", FAULT_MODES, key="fault_mode")
        selector_type = st.selectbox("Target type", ["area", "tag"], key="fault_selector_type")
        if selector_type == "area":
            fault_target = st.selectbox("Target area", list(AREAS), key="fault_area_target")
        else:
            fault_target = st.selectbox("Exact tag", [tag["tag"] for tag in sim["tags"]], key="fault_tag_target")
        fault_duration = st.number_input("Duration in simulated seconds (0 = until cleared)",
                                         min_value=0, max_value=86_400, value=30, step=1)
        if st.button("Inject protocol fault", width="stretch"):
            PROTOCOL_FAULTS.add(sim, fault_protocol, fault_mode, selector_type,
                                fault_target, int(fault_duration))
        if sim["protocol_faults"]:
            st.dataframe(pd.DataFrame(sim["protocol_faults"]), hide_index=True, width="stretch")
            if st.button("Clear all protocol faults", width="stretch"):
                PROTOCOL_FAULTS.clear(sim)

st.subheader("Live fleet map")
st.link_button("Open live fleet map", "http://127.0.0.1:8502/map", type="primary")
st.caption("The dedicated map uses WebSockets. It stays in its own page while the dashboard updates.")

@st.fragment(run_every="1s")
def render_dashboard():
    mcp_results = process_mcp_commands()
    sim = st.session_state.sim
    if sim["running"]:
        # Each heartbeat step is one simulated second; the slider controls the multiplier.
        for _ in range(speed):
            advance()
    published_snapshot = publish_snapshot()
    publish_mcp_results(mcp_results, published_snapshot)

    tags_df = pd.DataFrame(sim["tags"])
    alarms_df = pd.DataFrame(sim["alarms"])
    bad = int((tags_df.quality != "GOOD").sum())
    active_alarm_count = int((alarms_df.state == "ACTIVE / UNACK").sum()) if not alarms_df.empty else 0
    cols = st.columns(5)
    for col, label, value in zip(cols, ["Plant state", "Simulated time", "Tags", "Bad / stale quality", "Unacknowledged alarms"],
                                ["RUNNING" if sim["running"] else "STOPPED", f"{sim['tick']//60:02}:{sim['tick']%60:02}", len(tags_df), bad, active_alarm_count]):
        col.metric(label, value)
    scenario_run = sim.get("scenario_run")
    if scenario_run:
        elapsed = max(0, sim["tick"] - scenario_run["start_tick"])
        progress = min(1.0, elapsed / max(1, scenario_run["duration_seconds"]))
        st.caption(f"Scenario: **{scenario_run['name']}** · {scenario_run['status']} · seed {scenario_run['seed']} · {elapsed}/{scenario_run['duration_seconds']} simulated seconds")
        st.progress(progress)
    if sim["protocol_faults"]:
        st.warning(f"{len(sim['protocol_faults'])} protocol fault(s) currently alter downstream telemetry.")

    tabs = st.tabs(["Factory overview", "Water network", "Mineral process", "Area detail", "Mobile fleet", "Tag explorer", "Alarm console", "Power network", "Learning missions"])
    with tabs[0]:
        st.subheader("Plant health by area")
        overview = tags_df.groupby("area").agg(tags=("tag", "count"), stale=("quality", lambda x: int((x != "GOOD").sum())),
                                              average_deviation=("value", lambda x: 0.0)).reset_index()
        dev = tags_df.assign(dev=(tags_df.value-tags_df.base).abs()/(tags_df.high-tags_df.low))
        overview["average_deviation"] = dev.groupby("area").dev.mean().reindex(overview.area).values
        overview["health"] = (100 * (1 - overview.average_deviation.clip(0, .9))).round(1)
        c1, c2 = st.columns([1.2, 1])
        with c1:
            fig = px.bar(overview.sort_values("health"), x="health", y="area", orientation="h", color="health",
                         color_continuous_scale=["#d1495b", "#edae49", "#2a9d8f"], range_color=(40, 100),
                         labels={"health": "Synthetic health score", "area": "Plant area"})
            fig.update_layout(height=600, coloraxis_showscale=False, margin=dict(l=5,r=5,t=10,b=5), uirevision="area-health")
            st.plotly_chart(fig, width="stretch")
        with c2:
            st.markdown("**Factory areas**")
            st.dataframe(overview[["area", "tags", "stale", "health"]].sort_values("area"), hide_index=True, width="stretch", height=560)
    with tabs[1]:
        water = sim["water_network"]
        diagram = published_snapshot["water_network"]
        st.markdown("### Mine water circuit · hydraulic network and level control")
        st.caption("Pipe flow uses Darcy–Weisbach friction, minor losses, valve opening, static elevation, and pump affinity curves. PI controllers regulate tank levels; VFD ramps limit speed changes. Full pipes carry delayed flow packets, so a pump trip can leave downstream flow running until the line empties.")
        metric_cols = st.columns(5)
        for col, label, value in zip(metric_cols, ["Process demand", "Treated demand", "Water recovery", "Water in transit", "Unmet demand"],
                                     [f"{water['demand_m3h']:.0f} m³/h", f"{water['treated_demand_m3h']:.0f} m³/h",
                                      f"{water['water_recovery_pct']:.0f}%", f"{diagram['pipe_inventory_m3']:.0f} m³",
                                      f"{water.get('unmet_demand_m3h', 0) + water.get('unmet_treated_demand_m3h', 0):.1f} m³/h"]):
            col.metric(label, value)
        st.caption(f"Cumulative spill: {water['total_spilled_m3']:.2f} m³ · total water inventory (tanks + pipes): {diagram['total_inventory_m3']:.0f} m³")
        with st.expander("Operator controls · simulated equipment only"):
            pump_cols = st.columns(3)
            for col, (pump_name, pump) in zip(pump_cols, water["pumps"].items()):
                with col:
                    ui_key = sim["simulation_id"]
                    pump["enabled"] = st.toggle(f"{pump_name} enabled", value=pump["enabled"], key=f"water_pump_{ui_key}_{pump_name}")
                    controller = water["controllers"][pump_name]
                    controller["mode"] = st.radio(f"{pump_name} mode", ["AUTO", "MANUAL"],
                                                   index=0 if controller["mode"] == "AUTO" else 1,
                                                   horizontal=True, key=f"water_mode_{ui_key}_{pump_name}")
                    if controller["mode"] == "AUTO":
                        controller["setpoint_pct"] = st.slider(f"{pump['controlled_node'].replace('_', ' ').title()} target (%)",
                                                                 10, 90, int(controller["setpoint_pct"]),
                                                                 key=f"water_setpoint_{ui_key}_{pump_name}")
                    else:
                        pump["manual_speed_pct"] = st.slider(f"{pump_name} speed command (%)", 0, 100,
                                                              int(pump["manual_speed_pct"]),
                                                              key=f"water_manual_speed_{ui_key}_{pump_name}")
                    col.caption(f"VFD feedback {pump['speed_pct']:.1f}% · {pump['current_a']:.1f} A · {pump['power_kw']:.1f} kW")
            valve_cols = st.columns(4)
            for col, (valve_name, opening) in zip(valve_cols, list(water["valves"].items())):
                with col:
                    water["valves"][valve_name] = st.slider(f"{valve_name} (%)", 0, 100, int(opening), key=f"water_valve_{valve_name}")
        positions = {"north_reservoir": (0.08, .72), "raw_tank": (.34, .72),
                     "process_tank": (.62, .72), "reclaim_pond": (.62, .23), "treated_tank": (.9, .23)}
        node_x, node_y, node_text = [], [], []
        for node in diagram["nodes"]:
            x, y = positions[node["id"]]
            node_x.append(x); node_y.append(y)
            node_text.append(f"{node['label']}<br>{node['level_pct']:.1f}% · {node['volume_m3']:.0f}/{node['capacity_m3']:.0f} m³")
        network_fig = go.Figure()
        for link in diagram["links"]:
            x0, y0 = positions[link["from"]]; x1, y1 = positions[link["to"]]
            max_flow = WATER_MODEL.LINKS[link["id"]]["max_flow_m3h"]
            flow_ratio = min(1.0, link["flow_m3h"] / max_flow)
            color = "#277da1" if link["flow_m3h"] > 1 else "#d1495b"
            network_fig.add_trace(go.Scatter(x=[x0, x1], y=[y0, y1], mode="lines",
                                              line={"color": color, "width": 2 + 7 * flow_ratio},
                                              hovertemplate=f"{link['label']} · {link['flow_m3h']:.1f} m³/h<extra></extra>",
                                              showlegend=False))
        network_fig.add_trace(go.Scatter(x=node_x, y=node_y, mode="markers+text",
                                          marker={"size": 24, "color": ["#4c78a8", "#72a0c1", "#f2a541", "#43aa8b", "#277da1"],
                                                  "line": {"color": "white", "width": 2}},
                                          text=node_text, textposition="top center", textfont={"size": 11, "color": "#243746"},
                                          hovertemplate="%{text}<extra></extra>", showlegend=False))
        for link in diagram["links"]:
            x0, y0 = positions[link["from"]]; x1, y1 = positions[link["to"]]
            network_fig.add_annotation(x=x1, y=y1, ax=x0, ay=y0, xref="x", yref="y", axref="x", ayref="y",
                                       showarrow=True, arrowhead=3, arrowsize=1.1, arrowwidth=2,
                                       arrowcolor="#277da1" if link["flow_m3h"] > 1 else "#d1495b")
            network_fig.add_annotation(x=(x0+x1)/2, y=(y0+y1)/2 + .06,
                                       text=f"{link['label']}<br>in {link['inlet_flow_m3h']:.0f} → out {link['flow_m3h']:.0f} m³/h · {link['transport_delay_s']//60} min · ΔP {link['pressure_kpa']:.0f} kPa",
                                       showarrow=False, font={"size": 10, "color": "#243746"}, bgcolor="rgba(255,255,255,.88)")
        network_fig.update_layout(height=420, margin={"l": 10, "r": 10, "t": 10, "b": 10},
                                  xaxis={"range": [0, 1], "visible": False}, yaxis={"range": [0, 1], "visible": False},
                                  plot_bgcolor="white", paper_bgcolor="white",
                                  font={"color": "#243746"}, uirevision="water-network")
        st.plotly_chart(network_fig, width="stretch")
        hist = pd.DataFrame(water["history"])
        if not hist.empty:
            left, right = st.columns(2)
            with left:
                f = px.line(hist, x="time", y=["raw_tank_level_pct", "process_tank_level_pct", "reclaim_pond_level_pct", "treated_tank_level_pct"],
                            labels={"time": "Simulated seconds", "value": "Tank level (%)", "variable": "Tank"},
                            color_discrete_sequence=["#277da1", "#f2a541", "#43aa8b", "#9b5de5"])
                f.update_layout(uirevision="water-level-trend")
                st.plotly_chart(f, width="stretch")
            with right:
                f = px.line(hist, x="time", y=["intake_flow_m3h", "intake_arrival_m3h", "process_feed_m3h",
                                                 "process_demand_m3h", "treated_demand_m3h"],
                            labels={"time": "Simulated seconds", "value": "Flow (m³/h)", "variable": "Flow"},
                            color_discrete_sequence=["#277da1", "#4cc9f0", "#f2a541", "#43aa8b", "#9b5de5"])
                f.update_layout(uirevision="water-flow-trend")
                st.plotly_chart(f, width="stretch")
        else:
            st.info("Start or single-step the factory to build level and flow trends.")
        st.markdown("**Fault drills:** inject Medium Mechanical degradation or Utility loss in Water supply network and follow tank levels, pump-linked flows, and unmet demand. A Process upset raises demand and reduces recovery.")
    with tabs[2]:
        plant = sim["concentrator"]
        control = sim["process_control"]
        st.subheader("Copper concentrator · ore-to-tailings process circuit")
        st.caption("Crusher and mill power limits, ore hardness, finite buffers, cyclone recirculating load, flotation recovery, separate concentrate and tailings solids balances, cyclic filter-press batches, mine-water availability, and power demand evolve together. These are simplified supervisory dynamics, not a calibrated comminution or metallurgical model.")
        metric_cols = st.columns(6)
        metric_values = [
            ("Plant feed", f"{plant['plant_feed_tph']:.0f} t/h"),
            ("Copper recovery", f"{plant['copper_recovery_pct']:.1f}%"),
            ("Product P80", f"{plant['p80_microns']:.0f} µm"),
            ("Concentrate", f"{plant['concentrate_tph']:.1f} t/h"),
            ("Process power", f"{plant['sag_power_mw'] + plant['ball_power_mw'] + plant['crusher_power_mw']:.1f} MW"),
            ("Water makeup", f"{plant['water_makeup_m3h']:.0f} m³/h"),
        ]
        for col, (label, value) in zip(metric_cols, metric_values):
            col.metric(label, value)
        with st.expander("Process PLC sequence · simulated equipment controls", expanded=True):
            st.caption("This teaching controller sequences process motors, checks utility permissives, and latches simulated equipment trips. It is not a certified safety PLC or safety instrumented system. The factory timeline must be running or single-stepped for sequences to progress.")
            state_col, buttons = st.columns([1.1, 2])
            state_col.metric("Process sequence", control["mode"])
            state_col.caption(control["last_transition"])
            with buttons:
                start_col, stop_col, reset_col = st.columns(3)
                if start_col.button("Start process", disabled=(control["requested_mode"] == "RUNNING"
                                    or control["trip_count"] > 0),
                                    key=f"process_start_{sim['simulation_id']}"):
                    PLANT_CONTROLLER.request(control, "START")
                if stop_col.button("Stop process", disabled=control["requested_mode"] == "STOPPED",
                                   key=f"process_stop_{sim['simulation_id']}"):
                    PLANT_CONTROLLER.request(control, "STOP")
                if reset_col.button("Reset trips", disabled=control["trip_count"] == 0,
                                    key=f"process_trip_reset_{sim['simulation_id']}"):
                    try:
                        power = sim["power_network"]
                        cleared = PLANT_CONTROLLER.reset_trips(
                            control, events=[event.__dict__ for event in sim["events"]],
                            power_fraction=power["process_power_fraction"],
                            voltage_pu=power["voltage_pu"], frequency_hz=power["frequency_hz"])
                        st.success(f"Reset {len(cleared)} trip(s). Inspect the condition, then start manually.")
                    except ValueError as error:
                        st.error(str(error))
            equipment_rows = [{"Equipment": PLANT_CONTROLLER.LABELS[name],
                               "State": item["state"], "Elapsed (s)": item["elapsed_s"],
                               "Trips": item["trips"], "Trip reason": item["trip_reason"] or "—"}
                              for name, item in control["equipment"].items()]
            st.dataframe(pd.DataFrame(equipment_rows), hide_index=True, width="stretch")
            st.caption("Permissive: " + control["permissive_reason"])
        st.caption(f"ROM stockpile {plant['rom_stockpile_t']:.0f} t · crushed ore {plant['crushed_stockpile_t']:.0f} t · SAG feed bin {plant['sag_bin_t']:.0f} t · tailings thickener inventory {plant['tailings_thickener_inventory_t']:.0f} t · curtailed mine feed {plant['rom_feed_curtailed_tph']:.1f} t/h · water shortage {plant['fresh_water_shortage_m3h']:.1f} m³/h")
        st.caption(f"Concentrate thickener {plant['concentrate_thickener_inventory_t']:.1f}/{plant['concentrate_thickener_capacity_t']:.0f} t · filter press {plant['filter_press_phase'].lower()} ({plant['filter_press_batch_t']:.2f}/{plant['filter_press_batch_capacity_t']:.1f} t batch) · cake stockpile {plant['concentrate_cake_inventory_t']:.1f}/{plant['concentrate_cake_capacity_t']:.0f} t · concentrate shipped {plant['cumulative_concentrate_shipped_t']:.1f} t")
        flow_stages = [
            ("ROM feed", plant["rom_feed_tph"], "#277da1"),
            ("Crusher", plant["crushed_ore_tph"], "#4c78a8"),
            ("SAG fresh feed", plant["sag_feed_tph"], "#f2a541"),
            ("Ball circuit", plant["ball_circuit_feed_tph"], "#e07a5f"),
            ("Cyclone overflow", plant["cyclone_overflow_tph"], "#43aa8b"),
            ("Tailings pumped", plant["tailings_tph"], "#9b5de5"),
        ]
        process_fig = go.Figure(go.Bar(x=[item[0] for item in flow_stages], y=[item[1] for item in flow_stages],
            marker_color=[item[2] for item in flow_stages], text=[f"{item[1]:.0f} t/h" for item in flow_stages],
            textposition="outside", hovertemplate="%{x}<br>%{y:.1f} t/h<extra></extra>"))
        process_fig.update_layout(height=340, margin={"l": 10, "r": 10, "t": 25, "b": 5},
            yaxis_title="Solids rate (t/h)", xaxis_title="Material flow stage", showlegend=False,
            uirevision="concentrator-material-flow")
        st.plotly_chart(process_fig, width="stretch")
        process_left, process_right = st.columns(2)
        history = pd.DataFrame(plant["history"])
        if not history.empty:
            with process_left:
                trend = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.15,
                                      specs=[[{"secondary_y": False}], [{"secondary_y": True}]])
                trend.add_trace(go.Scatter(x=history.time, y=history.plant_feed_tph, name="Plant feed",
                                           line={"color": "#277da1"}), row=1, col=1)
                trend.add_trace(go.Scatter(x=history.time, y=history.copper_recovery_pct, name="Cu recovery",
                                           line={"color": "#43aa8b"}), row=2, col=1, secondary_y=False)
                trend.add_trace(go.Scatter(x=history.time, y=history.p80_microns, name="Product P80",
                                           line={"color": "#f2a541"}), row=2, col=1, secondary_y=True)
                trend.update_yaxes(title_text="Feed (t/h)", row=1, col=1)
                trend.update_yaxes(title_text="Recovery (%)", row=2, col=1, secondary_y=False)
                trend.update_yaxes(title_text="P80 (µm)", row=2, col=1, secondary_y=True)
                trend.update_xaxes(title_text="Simulated seconds", row=2, col=1)
                trend.update_layout(height=470, uirevision="concentrator-process-trend")
                st.plotly_chart(trend, width="stretch")
            with process_right:
                trend = px.line(history, x="time", y=["rom_stockpile_t", "crushed_stockpile_t", "sag_bin_t",
                                                        "tailings_thickener_inventory_t",
                                                        "concentrate_thickener_inventory_t",
                                                        "concentrate_cake_inventory_t"],
                    labels={"time": "Simulated seconds", "value": "Inventory (t)", "variable": "Buffer"},
                    color_discrete_sequence=["#277da1", "#e07a5f", "#f2a541", "#43aa8b", "#9b5de5", "#d1495b"])
                trend.update_layout(uirevision="concentrator-inventory-trend")
                st.plotly_chart(trend, width="stretch")
        else:
            st.info("Start or single-step the factory to build concentrator trends.")
        st.markdown("**Fault drills:** inject Conveyor jam in Crushing and screening and watch ore buffers absorb the interruption before milling slows. Inject Mechanical degradation in Grinding and flotation to compare vibration, power, circulating load, P80, and recovery. Inject Utility loss in Mine power network to observe power-limited throughput and the coupled water shortfall response.")
    with tabs[3]:
        area = st.selectbox("Choose area", list(AREAS), key="area_detail")
        area_df = tags_df[tags_df.area == area]
        st.write(f"**{area}** · {len(AREAS[area])} equipment units · {len(area_df)} tags")
        unit = st.selectbox("Equipment", sorted(area_df.unit.unique()))
        unit_df = area_df[area_df.unit == unit]
        st.dataframe(unit_df[["tag", "value", "units", "quality", "last_change"]], hide_index=True, width="stretch")
        selected = st.selectbox("Trend tag", unit_df.tag.tolist())
        hist = pd.DataFrame(sim["history"])
        if not hist.empty:
            trend = hist[hist.tag == selected]
            if not trend.empty:
                f = px.line(trend, x="time", y="value", color_discrete_sequence=["#277da1"], labels={"time": "Simulated seconds", "value": "Reading"})
                f.update_layout(uirevision=selected)
                st.plotly_chart(f, width="stretch")
            else:
                st.info("Trend begins after the simulator has collected its first interval.")
        else:
            st.info("Start the factory to collect trend history.")
    with tabs[4]:
        fleet = tags_df[tags_df.area == "Mobile fleet"].copy()
        st.caption("Fleet GPS is streamed on the live map above. This view lists the vehicle process and instrumentation tags.")
        st.subheader("Vehicle telemetry")
        st.dataframe(fleet[~fleet.sensor.str.startswith("gps_")][["tag", "value", "units", "quality", "last_change"]],
                     hide_index=True, width="stretch")
        st.subheader("Building and site telemetry")
        buildings = tags_df[tags_df.area == "Site buildings"]
        st.dataframe(buildings[["tag", "value", "units", "quality", "last_change"]], hide_index=True, width="stretch")
    with tabs[5]:
        q1, q2, q3 = st.columns(3)
        area_filter = q1.selectbox("Area filter", ["All", *AREAS.keys()])
        quality_filter = q2.selectbox("Quality", ["All", "GOOD", "STALE"])
        search = q3.text_input("Find tag", placeholder="e.g. boiler_01")
        filtered = tags_df.copy()
        if area_filter != "All": filtered = filtered[filtered.area == area_filter]
        if quality_filter != "All": filtered = filtered[filtered.quality == quality_filter]
        if search: filtered = filtered[filtered.tag.str.contains(search, case=False, regex=False)]
        st.dataframe(filtered[["tag", "area", "unit", "value", "units", "quality", "last_change"]], hide_index=True, width="stretch", height=550)
        st.download_button("Download current tag snapshot (CSV)", filtered.to_csv(index=False), "factory_tags.csv", "text/csv")
    with tabs[6]:
        if alarms_df.empty:
            st.info("No alarms yet. Inject an incident to see alarm behavior.")
        else:
            st.dataframe(alarms_df, hide_index=True, width="stretch")
            if st.button("Acknowledge active alarms"):
                sim["alarms"] = [{**a, "state": "ACTIVE / ACK" if a["state"] == "ACTIVE / UNACK" else a["state"]} for a in sim["alarms"]]
                st.rerun(scope="fragment")
    with tabs[7]:
        power = sim["power_network"]
        st.subheader("Mine electrical network · dispatch and load response")
        st.caption("Grid import, solar, diesel reserve, and battery storage supply site auxiliaries, water pumps, and the concentrator. Transmission, transformer, and feeder limits constrain delivery; this is a supervisory balance model, not an AC load-flow study.")
        metrics = st.columns(6)
        for col, label, value in zip(metrics,
            ["Mine bus frequency", "Bus voltage", "Grid import", "Diesel generation", "Battery reserve", "Process power available"],
            [f"{power['frequency_hz']:.2f} Hz", f"{power['voltage_pu']:.3f} pu", f"{power['grid_import_mw']:.1f} MW",
             f"{power['diesel_mw']:.1f} MW", f"{power['battery_soc_pct']:.1f}%", f"{power['process_power_fraction']*100:.0f}%"]):
            col.metric(label, value)
        st.caption(f"Solar {power['solar_mw']:.1f} MW · battery {power['battery_power_mw']:+.1f} MW (positive = discharging) · requested/served plant load {power['requested_load_mw']:.1f}/{power['served_load_mw']:.1f} MW · shed {power['load_shed_mw']:.1f} MW · process/water feeder limits {power['process_feeder_capacity_mw']:.1f}/{power['water_feeder_capacity_mw']:.1f} MW")

        nodes = {
            "grid": (.08, .78, f"Transmission import<br>{power['grid_import_mw']:.1f} / {power['tie_capacity_mw']:.1f} MW"),
            "solar": (.08, .55, f"Solar<br>{power['solar_mw']:.1f} MW"),
            "diesel": (.08, .32, f"Diesel reserve<br>{power['diesel_mw']:.1f} / {power['diesel_available_mw']:.1f} MW"),
            "battery": (.08, .09, f"Battery<br>{power['battery_power_mw']:+.1f} MW · {power['battery_soc_pct']:.0f}%"),
            "bus": (.50, .48, f"Mine bus<br>{power['frequency_hz']:.2f} Hz · {power['voltage_pu']:.3f} pu"),
            "site": (.91, .78, f"Site auxiliaries<br>{power['site_load_mw']:.1f} MW"),
            "water": (.91, .50, f"Water pumps<br>{power['water_load_mw']:.2f} MW · {power['water_power_fraction']*100:.0f}%"),
            "process": (.91, .20, f"Concentrator<br>{power['process_load_mw']:.1f} MW · {power['process_power_fraction']*100:.0f}%"),
        }
        fig = go.Figure()
        source_colors = {"grid": "#277da1", "solar": "#f2a541", "diesel": "#e07a5f", "battery": "#9b5de5"}
        source_mw = {"grid": power["grid_import_mw"], "solar": power["solar_mw"],
                     "diesel": power["diesel_mw"], "battery": max(0.0, power["battery_power_mw"])}
        for key, amount in source_mw.items():
            if amount <= 0.02:
                continue
            x0, y0, _ = nodes[key]; x1, y1, _ = nodes["bus"]
            fig.add_trace(go.Scatter(x=[x0+.11, x1-.08], y=[y0, y1], mode="lines",
                line={"color": source_colors[key], "width": 1.5 + min(10, amount / 4)},
                hovertemplate=f"{key.title()} → mine bus · {amount:.2f} MW<extra></extra>", showlegend=False))
        for key, amount, color in [
            ("site", power["site_load_mw"], "#4c78a8"),
            ("water", power["water_load_mw"], "#43aa8b"),
            ("process", power["process_load_mw"], "#e07a5f")]:
            x0, y0, _ = nodes["bus"]
            x1, y1, _ = nodes[key]
            fig.add_trace(go.Scatter(x=[x0+.08, x1-.13], y=[y0, y1], mode="lines",
                line={"color": color, "width": 1.5 + min(10, amount / 4)},
                hovertemplate=f"Mine bus → {key.title()} · {amount:.2f} MW<extra></extra>", showlegend=False))
        for key, (x, y, label) in nodes.items():
            fig.add_annotation(x=x, y=y, text=label, showarrow=False, align="center",
                bgcolor="white", bordercolor="#aab7c4", borderwidth=1, borderpad=7,
                font={"size": 11, "color": "#243746"})
        fig.update_layout(height=390, margin={"l": 8, "r": 8, "t": 8, "b": 8},
            xaxis={"range": [0, 1], "visible": False}, yaxis={"range": [0, 1], "visible": False},
            plot_bgcolor="white", paper_bgcolor="white", uirevision="electrical-network")
        st.plotly_chart(fig, width="stretch")
        history = pd.DataFrame(power["history"])
        if not history.empty:
            left, right = st.columns(2)
            with left:
                trend = px.line(history, x="sim_seconds", y=["frequency_hz"],
                    labels={"sim_seconds": "Simulated seconds", "value": "Frequency (Hz)"},
                    color_discrete_sequence=["#277da1"])
                trend.add_hline(y=49.5, line_dash="dash", line_color="#d1495b", annotation_text="Underfrequency alarm")
                trend.update_layout(uirevision="power-frequency-trend")
                st.plotly_chart(trend, width="stretch")
            with right:
                trend = px.line(history, x="sim_seconds", y=["grid_import_mw", "solar_mw", "diesel_mw", "battery_soc_pct"],
                    labels={"sim_seconds": "Simulated seconds", "value": "MW / battery %", "variable": "Source"},
                    color_discrete_sequence=["#277da1", "#f2a541", "#e07a5f", "#9b5de5"])
                trend.update_layout(uirevision="power-dispatch-trend")
                st.plotly_chart(trend, width="stretch")
        else:
            st.info("Start the factory or run the grid disturbance scenario to build dispatch trends.")
        st.markdown("**Fault drills:** run `mine-grid-support-and-recovery`; compare battery response with diesel start delay, watch import limits and process curtailment, then observe source and process recovery when the event expires.")

    with tabs[8]:
        st.subheader("Investigation missions")
        st.markdown("Use these as exercises: form hypotheses, locate evidence across areas, identify first-out signals, and propose what a unified monitoring layer should show.")
        missions = [
            ("Small · Instrument fault", "Inject Signal drift in one area. Find which tags moved together, separate process change from measurement fault, and propose a quality rule."),
            ("Medium · Utility disturbance", "Inject Utility loss in Boiler house or Compressed air. Trace which downstream areas lose pressure, flow, or production. Identify leading and lagging signals."),
            ("Large · Plant-wide incident", "Inject a Large incident into Whole factory. Rank affected areas by urgency, draft an alarm grouping strategy, and decide what evidence a shift handover needs."),
            ("Integration challenge", "Design a canonical tag model for area, asset, signal, units, range, quality, timestamp, and source. Export a snapshot and sketch how your historian would ingest it."),
            ("Alarm rationalization", "Compare the alarm list with tag quality and trends. Define priority, deadband, delay, shelving rules, and operator response for each failure mode."),
        ]
        for title, description in missions:
            with st.expander(title): st.write(description)



render_dashboard()
