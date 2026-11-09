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
import streamlit as st

from process_model import FactoryProcessModel
from protocol_faults import FAULT_MODES, PROTOCOLS, ProtocolFaultInjector
from scenarios import SCENARIOS, ScenarioRunner, ScenarioStep


AREAS = {
    "Open-pit mining": ["Pit sector A", "Pit sector B", "Shovel fleet", "Haul truck fleet", "Dewatering field"],
    "Underground mining": ["Decline level 1", "Decline level 2", "Ventilation fans", "Ore pass system", "Underground pumps"],
    "Crushing and screening": ["Primary crusher", "Secondary crusher", "Cone crusher 01", "Screen deck A", "Ore conveyors"],
    "Grinding and flotation": ["SAG mill", "Ball mill 01", "Ball mill 02", "Cyclone cluster", "Flotation cells"],
    "Concentrate handling": ["Thickener", "Filter press", "Concentrate dryer", "Loadout silo", "Rail loadout"],
    "Tailings and reclaim": ["Tailings pump station A", "Tailings pump station B", "Deposition line", "Reclaim pond", "Reclaim pumps"],
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
        st.session_state.sim = {"tags": tags, "running": False, "tick": 0, "seed": seed,
                                "simulation_id": uuid.uuid4().hex,
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


def publish_snapshot() -> dict:
    """Publish one atomic local file snapshot for the separate protocol gateway."""
    sim = st.session_state.sim
    fleet_units = sorted({t["unit"] for t in sim["tags"] if t["area"] == "Mobile fleet"})
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
        "tags": [{"tag": t["tag"], "area": t["area"], "asset": t["unit"], "sensor": t["sensor"],
                  "value": t["value"], "units": t["units"], "quality": t["quality"],
                  "low": t["low"], "high": t["high"], "last_change": t["last_change"]} for t in sim["tags"]],
        "alarms": sim["alarms"],
        "fleet": [{"unit": unit, "vehicle_type": unit.rsplit(" ", 1)[0],
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
    SCENARIO_RUNNER.after_tick(sim)
    PROTOCOL_FAULTS.expire(sim)


st.set_page_config(page_title="Factory SCADA Playground", page_icon="🏭", layout="wide")
initialize()
sim = st.session_state.sim
publish_snapshot()
st.title("🏭 Factory SCADA Playground")
st.caption(f"Synthetic multi-industry training world · {len(AREAS)} systems · {sum(map(len, AREAS.values()))} equipment units · {len(st.session_state.sim['tags'])} tags · local simulation only")

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

    tabs = st.tabs(["Factory overview", "Area detail", "Mobile fleet", "Tag explorer", "Alarm console", "Learning missions"])
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
    with tabs[2]:
        fleet = tags_df[tags_df.area == "Mobile fleet"].copy()
        st.caption("Fleet GPS is streamed on the live map above. This view lists the vehicle process and instrumentation tags.")
        st.subheader("Vehicle telemetry")
        st.dataframe(fleet[~fleet.sensor.str.startswith("gps_")][["tag", "value", "units", "quality", "last_change"]],
                     hide_index=True, width="stretch")
        st.subheader("Building and site telemetry")
        buildings = tags_df[tags_df.area == "Site buildings"]
        st.dataframe(buildings[["tag", "value", "units", "quality", "last_change"]], hide_index=True, width="stretch")
    with tabs[3]:
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
    with tabs[4]:
        if alarms_df.empty:
            st.info("No alarms yet. Inject an incident to see alarm behavior.")
        else:
            st.dataframe(alarms_df, hide_index=True, width="stretch")
            if st.button("Acknowledge active alarms"):
                sim["alarms"] = [{**a, "state": "ACTIVE / ACK" if a["state"] == "ACTIVE / UNACK" else a["state"]} for a in sim["alarms"]]
                st.rerun(scope="fragment")
    with tabs[5]:
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
