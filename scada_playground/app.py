"""A deterministic, local-only synthetic factory for SCADA learning."""
from __future__ import annotations

import random
import time
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

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
from plant_utilities import FactoryUtilities
from chilled_water import ChilledWaterPlant
from site_buildings import SiteBuildings
from underground_ventilation import UndergroundVentilation


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
    "Grid distribution": ["Distribution incomer", "Feeder north", "Feeder south", "Feeder east", "Feeder west", "Capacitor bank"],
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
    "Electrical fault": "utility",
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
UTILITY_MODEL = FactoryUtilities()
CHILLED_MODEL = ChilledWaterPlant()
PLANT_CONTROLLER = PlantController()
BUILDING_MODEL = SiteBuildings()
VENTILATION_MODEL = UndergroundVentilation()
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
        # The wastewater treatment model now owns this area's measurements;
        # discard the generic placeholder tags before publishing its asset tree.
        tags = [tag for tag in tags if tag["area"] not in {"Wastewater", "Water distribution", "Chilled water"}]
        concentrator_state = CONCENTRATOR_MODEL.initialize()
        process_control_state = PLANT_CONTROLLER.initialize()
        haulage_state = HAULAGE_MODEL.initialize()
        utility_state = UTILITY_MODEL.initialize()
        chilled_state = CHILLED_MODEL.initialize()
        CHILLED_MODEL.advance(chilled_state, 0, [],
            cooling_demand_kw=UTILITY_MODEL.chilled_water_demand_kw(utility_state), seed=seed)
        UTILITY_MODEL.advance(utility_state, 0, [],
            chilled_water_service_fraction=chilled_state["cooling_service_fraction"],
            chilled_water_supply_temperature_c=chilled_state["supply_temperature_c"], seed=seed)
        buildings_state = BUILDING_MODEL.initialize()
        BUILDING_MODEL.advance(buildings_state, 0, [], seed=seed)
        ventilation_state = VENTILATION_MODEL.initialize()
        VENTILATION_MODEL.advance(ventilation_state, 0, [], seed=seed)
        modeled_assets = {(area, asset) for area, assets in CONCENTRATOR_MODEL.AREA_ASSETS.items() for asset in assets}
        modeled_assets.add(("Concentrate handling", "Thickener"))  # legacy generic asset name
        modeled_assets.update(("Mobile fleet", unit) for unit in HAULAGE_MODEL.truck_names)
        modeled_assets.update(("Mobile fleet", asset) for asset in
                              ("Shovel dispatch", "Crusher tip dispatch", "ROM dispatch"))
        modeled_assets.update(("Site buildings", asset) for asset in BUILDING_MODEL.ASSETS)
        modeled_assets.update(("Chilled water", asset) for asset in
                              ("Chiller A", "Chiller B", "Chilled water header", "Cooling tower", "Thermal storage"))
        modeled_assets.update(("Underground mining", asset) for asset in
                              ("Ventilation fans", "Decline level 1", "Decline level 2", "Ore pass system"))
        tags = [tag for tag in tags if (tag["area"], tag["unit"]) not in modeled_assets]
        modeled_tags = CONCENTRATOR_MODEL.telemetry(concentrator_state) + HAULAGE_MODEL.telemetry(haulage_state, seed)
        for tag in modeled_tags:
            tag["dynamic_model"] = True
        tags.extend(modeled_tags)
        water_state = WATER_MODEL.initialize()
        water_state["concentrator_demand_m3h"] = concentrator_state["gross_process_water_m3h"]
        water_state["concentrator_reclaim_m3h"] = concentrator_state["concentrator_reclaim_m3h"]
        water_state["concentrator_recovery_pct"] = concentrator_state["water_recovery_pct"]
        water_state["demand_m3h"] = (water_state["base_demand_m3h"]
                                     + water_state["concentrator_demand_m3h"])
        tags = [tag for tag in tags
                if not tag["tag"].startswith(("water_supply_network.", "water_treatment.",
                                              "water_distribution.", "wastewater."))]
        water_tags, _water_diagram = WATER_MODEL.telemetry(water_state)
        for tag in water_tags:
            tag["dynamic_model"] = True
        tags.extend(water_tags)
        power_state = POWER_MODEL.initialize()
        initial_process_power_mw = concentrator_state["total_power_mw"]
        initial_water_power_mw = water_state["electrical_load_kw"] / 1000.0
        initial_process_power_mw += ventilation_state["fan_power_kw"] / 1000.0
        POWER_MODEL.advance(power_state, 0, [], process_load_mw=initial_process_power_mw,
                            water_load_mw=initial_water_power_mw,
                            utility_load_mw=utility_state["electric_load_mw"] + chilled_state["electric_load_mw"],
                            site_building_load_mw=buildings_state["electric_load_mw"],
                            site_nonbuilding_request_mw=5.6, seed=seed)
        power_tags = POWER_MODEL.telemetry(power_state)
        power_assets = {(area, asset) for area, assets in POWER_MODEL.AREA_ASSETS.items() for asset in assets}
        tags = [tag for tag in tags if (tag["area"], tag["unit"]) not in power_assets]
        for tag in power_tags:
            tag["dynamic_model"] = True
        tags.extend(power_tags)
        utility_assets = {(area, asset) for area, assets in UTILITY_MODEL.AREA_ASSETS.items()
                          for asset in assets}
        tags = [tag for tag in tags if (tag["area"], tag["unit"]) not in utility_assets]
        tags.extend(UTILITY_MODEL.telemetry(utility_state, seed))
        tags.extend(CHILLED_MODEL.telemetry(chilled_state, seed))
        tags.extend(BUILDING_MODEL.telemetry(buildings_state, seed))
        ventilation_tags = VENTILATION_MODEL.telemetry(ventilation_state, seed)
        for tag in ventilation_tags:
            tag["dynamic_model"] = True
        tags.extend(ventilation_tags)
        st.session_state.sim = {"tags": tags, "running": False, "tick": 0, "seed": seed,
                                "water_network": water_state,
                                "power_network": power_state,
                                "plant_utilities": utility_state,
                                "chilled_water": chilled_state,
                                "site_buildings": buildings_state,
                                "mine_ventilation": ventilation_state,
                                "concentrator": concentrator_state,
                                "process_control": process_control_state,
                                "haulage": haulage_state,
                                "simulation_id": uuid.uuid4().hex,
                                "water_alarm_states": {},
                                "water_distribution_alarm_states": {},
                                "chilled_water_alarm_states": {},
                                "building_alarm_states": {},
                                "ventilation_alarm_states": {},
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
        if "plant_utilities" not in sim:
            sim["plant_utilities"] = UTILITY_MODEL.initialize()
        UTILITY_MODEL.migrate(sim["plant_utilities"])
        if "chilled_water" not in sim:
            sim["chilled_water"] = CHILLED_MODEL.initialize()
            CHILLED_MODEL.advance(sim["chilled_water"], sim.get("tick", 0), [],
                cooling_demand_kw=UTILITY_MODEL.chilled_water_demand_kw(sim["plant_utilities"]),
                seed=sim["seed"])
        CHILLED_MODEL.migrate(sim["chilled_water"])
        if "site_buildings" not in sim:
            sim["site_buildings"] = BUILDING_MODEL.initialize()
            BUILDING_MODEL.advance(sim["site_buildings"], sim.get("tick", 0), [], seed=sim["seed"])
        BUILDING_MODEL.migrate(sim["site_buildings"])
        if "mine_ventilation" not in sim:
            sim["mine_ventilation"] = VENTILATION_MODEL.initialize()
            VENTILATION_MODEL.advance(sim["mine_ventilation"], sim.get("tick", 0), [], seed=sim["seed"])
        modeled_assets = {(area, asset) for area, assets in CONCENTRATOR_MODEL.AREA_ASSETS.items() for asset in assets}
        modeled_assets.add(("Concentrate handling", "Thickener"))  # legacy generic asset name
        modeled_assets.update(("Mobile fleet", unit) for unit in HAULAGE_MODEL.truck_names)
        modeled_assets.update(("Mobile fleet", asset) for asset in
                              ("Shovel dispatch", "Crusher tip dispatch", "ROM dispatch"))
        modeled_assets.update(("Site buildings", asset) for asset in BUILDING_MODEL.ASSETS)
        modeled_assets.update(("Chilled water", asset) for asset in
                              ("Chiller A", "Chiller B", "Chilled water header", "Cooling tower", "Thermal storage"))
        modeled_assets.update(("Underground mining", asset) for asset in
                              ("Ventilation fans", "Decline level 1", "Decline level 2", "Ore pass system"))
        sim["tags"] = [tag for tag in sim["tags"] if (tag["area"], tag["unit"]) not in modeled_assets]
        modeled_tags = CONCENTRATOR_MODEL.telemetry(sim["concentrator"]) + HAULAGE_MODEL.telemetry(sim["haulage"], sim["seed"])
        for tag in modeled_tags:
            tag["dynamic_model"] = True
            sim["tags"].append(tag)
        ventilation_tags = VENTILATION_MODEL.telemetry(sim["mine_ventilation"], sim["seed"])
        for tag in ventilation_tags:
            tag["dynamic_model"] = True
            sim["tags"].append(tag)
        if "water_network" not in sim:
            sim["water_network"] = WATER_MODEL.initialize()
            water_tags, _diagram = WATER_MODEL.telemetry(sim["water_network"])
            sim["tags"].extend(water_tags)
        WATER_MODEL.migrate(sim["water_network"])
        sim["water_network"]["concentrator_demand_m3h"] = sim["concentrator"]["gross_process_water_m3h"]
        sim["water_network"]["concentrator_reclaim_m3h"] = sim["concentrator"]["concentrator_reclaim_m3h"]
        sim["water_network"]["concentrator_recovery_pct"] = sim["concentrator"]["water_recovery_pct"]
        sim["water_network"]["demand_m3h"] = (sim["water_network"]["base_demand_m3h"]
                                                + sim["water_network"]["concentrator_demand_m3h"]
                                                + sim["water_network"].get("factory_cip_demand_m3h", 0.0))
        sim["tags"] = [tag for tag in sim["tags"]
                       if not tag["tag"].startswith(("water_supply_network.", "water_treatment.",
                                                     "water_distribution.", "wastewater."))]
        water_tags, _water_diagram = WATER_MODEL.telemetry(sim["water_network"])
        for tag in water_tags:
            tag["dynamic_model"] = True
            sim["tags"].append(tag)
        if "power_network" not in sim:
            sim["power_network"] = POWER_MODEL.initialize()
            process_load_mw = (sim["concentrator"]["total_power_mw"]
                               + sim["mine_ventilation"]["fan_power_kw"] / 1000.0)
            water_load_mw = sim["water_network"]["electrical_load_kw"] / 1000.0
            POWER_MODEL.advance(sim["power_network"], sim["tick"], [], process_load_mw=process_load_mw,
                                water_load_mw=water_load_mw,
                                utility_load_mw=(sim["plant_utilities"]["electric_load_mw"]
                                                 + sim["chilled_water"]["electric_load_mw"]),
                                site_building_load_mw=sim["site_buildings"]["electric_load_mw"],
                                site_nonbuilding_request_mw=5.6,
                                seed=sim["seed"])
        utility_power_demand = sim["plant_utilities"]["electric_load_mw"]
        utility_power_demand += sim["chilled_water"]["electric_load_mw"]
        sim["power_network"].setdefault("factory_utilities_requested_mw", utility_power_demand)
        sim["power_network"].setdefault("factory_utilities_load_mw", utility_power_demand)
        sim["power_network"].setdefault("factory_utilities_power_fraction", 1.0)
        power_assets = {(area, asset) for area, assets in POWER_MODEL.AREA_ASSETS.items() for asset in assets}
        sim["tags"] = [tag for tag in sim["tags"] if (tag["area"], tag["unit"]) not in power_assets]
        for tag in POWER_MODEL.telemetry(sim["power_network"]):
            tag["dynamic_model"] = True
            sim["tags"].append(tag)
        utility_assets = {(area, asset) for area, assets in UTILITY_MODEL.AREA_ASSETS.items()
                          for asset in assets}
        sim["tags"] = [tag for tag in sim["tags"]
                       if (tag["area"], tag["unit"]) not in utility_assets]
        sim["tags"].extend(UTILITY_MODEL.telemetry(sim["plant_utilities"], sim["seed"]))
        sim["tags"] = [tag for tag in sim["tags"] if tag["area"] != "Chilled water"]
        sim["tags"].extend(CHILLED_MODEL.telemetry(sim["chilled_water"], sim["seed"]))
        building_tags = BUILDING_MODEL.telemetry(sim["site_buildings"], sim["seed"])
        sim["tags"].extend(building_tags)
    unique_tags = {}
    for tag in st.session_state.sim["tags"]:
        unique_tags[tag["tag"]] = tag
    st.session_state.sim["tags"] = list(unique_tags.values())


def publish_snapshot() -> dict:
    """Publish one atomic local file snapshot for the separate protocol gateway."""
    sim = st.session_state.sim
    fleet_units = sorted({t["unit"] for t in sim["tags"]
                          if t["area"] == "Mobile fleet"
                          and t["unit"].rsplit(" ", 1)[0] in FLEET_ROUTES})
    water_tags, water_diagram = WATER_MODEL.telemetry(sim["water_network"])
    concentrator_tags = CONCENTRATOR_MODEL.telemetry(sim["concentrator"])
    power_tags = POWER_MODEL.telemetry(sim["power_network"])
    utility_tags = UTILITY_MODEL.telemetry(sim["plant_utilities"], sim["seed"])
    chilled_tags = CHILLED_MODEL.telemetry(sim["chilled_water"], sim["seed"])
    building_tags = BUILDING_MODEL.telemetry(sim["site_buildings"], sim["seed"])
    ventilation_tags = VENTILATION_MODEL.telemetry(sim["mine_ventilation"], sim["seed"])
    haulage_tags = HAULAGE_MODEL.telemetry(sim["haulage"], sim["seed"])
    haulage_frames = {frame["unit"]: frame for frame in HAULAGE_MODEL.positions(sim["haulage"])}
    concentrator_by_tag = {tag["tag"]: tag for tag in concentrator_tags + haulage_tags}
    water_by_tag = {tag["tag"]: tag for tag in water_tags}
    power_by_tag = {tag["tag"]: tag for tag in power_tags}
    utility_by_tag = {tag["tag"]: tag for tag in utility_tags}
    chilled_by_tag = {tag["tag"]: tag for tag in chilled_tags}
    building_by_tag = {tag["tag"]: tag for tag in building_tags}
    ventilation_by_tag = {tag["tag"]: tag for tag in ventilation_tags}
    for tag in sim["tags"]:
        if tag["tag"] in water_by_tag:
            tag.update(water_by_tag[tag["tag"]])
        elif tag["tag"] in power_by_tag:
            tag.update(power_by_tag[tag["tag"]])
        elif tag["tag"] in utility_by_tag:
            tag.update(utility_by_tag[tag["tag"]])
        elif tag["tag"] in chilled_by_tag:
            tag.update(chilled_by_tag[tag["tag"]])
        elif tag["tag"] in building_by_tag:
            tag.update(building_by_tag[tag["tag"]])
        elif tag["tag"] in ventilation_by_tag:
            tag.update(ventilation_by_tag[tag["tag"]])
        elif tag["tag"] in concentrator_by_tag:
            tag.update(concentrator_by_tag[tag["tag"]])
    PROCESS_MODEL.apply_instrument_faults(sim["tags"], sim["events"], sim["tick"])
    for tag in sim["tags"]:
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
        "plant_utilities": {key: value for key, value in sim["plant_utilities"].items() if key != "history"},
        "chilled_water": CHILLED_MODEL.public_state(sim["chilled_water"]),
        "site_buildings": BUILDING_MODEL.public_state(sim["site_buildings"]),
        "mine_ventilation": VENTILATION_MODEL.public_state(sim["mine_ventilation"]),
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
    # Separate UI sessions can publish at once; use a per-simulation temp file
    # so one writer cannot replace another writer's temporary snapshot.
    tmp_path = SNAPSHOT_PATH.with_name(
        f"{SNAPSHOT_PATH.stem}.{sim['simulation_id']}.tmp")
    tmp_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp_path, SNAPSHOT_PATH)
    return payload


@st.cache_data(ttl=5, show_spinner=False)
def live_map_available() -> bool:
    """Detect whether the separate protocol gateway is serving the live map."""
    try:
        with urlopen("http://127.0.0.1:8502/map", timeout=0.3) as response:
            return response.status == 200
    except (OSError, URLError):
        return False


def apply_scenario_action(sim: dict, step: ScenarioStep) -> None:
    params = step.parameters
    if step.action == "incident":
        PROCESS_MODEL.inject(sim, params["incident_name"], params["target_area"], params["severity"],
                             params.get("duration_seconds"), params.get("target_asset"),
                             params.get("fault_location_pct", 90.0),
                             params.get("primary_breaker_failure", False),
                             params.get("target_tag"))
    elif step.action == "protocol_fault":
        PROTOCOL_FAULTS.add(sim, params["protocol"], params["mode"], params["selector_type"],
                            params["target"], params["duration_seconds"])
    elif step.action == "process_control":
        control = sim["process_control"]
        process_action = params["process_action"].upper()
        if process_action == "RESET_TRIPS":
            power = sim["power_network"]
            PLANT_CONTROLLER.reset_trips(
                control, events=[event.__dict__ for event in sim["events"]],
                power_fraction=power["process_power_fraction"],
                voltage_pu=power["voltage_pu"], frequency_hz=power["frequency_hz"])
        else:
            PLANT_CONTROLLER.request(control, process_action)
    elif step.action == "cip_start":
        started = UTILITY_MODEL.CIP_MODEL.start(
            sim["plant_utilities"]["cip"], params.get("target_unit", "pasteurizer_01"), sim["tick"])
        if not started:
            raise RuntimeError(sim["plant_utilities"]["cip"]["start_block_reason"])
    elif step.action == "feeder_protection_reset":
        reset_feeder_protection(sim, params["target_asset"])
    else:
        raise ValueError(f"Scenario action is not supported: {step.action}")


def inject(name: str, area: str, severity: str, duration_seconds: int | None = None,
           target_asset: str | None = None, fault_location_pct: float = 90.0,
           primary_breaker_failure: bool = False, target_tag: str | None = None) -> None:
    PROCESS_MODEL.inject(st.session_state.sim, name, area, severity, duration_seconds, target_asset,
                         fault_location_pct, primary_breaker_failure, target_tag)


def reset_feeder_protection(sim: dict, target_asset: str) -> None:
    feeder_name = ("distribution_incomer" if target_asset == "Distribution incomer"
                   else target_asset.removeprefix("Feeder ").lower())
    fault_active = any(event.name == "Electrical fault" and
                       (event.target_asset == target_asset or target_asset == "Distribution incomer")
                       for event in sim["events"])
    POWER_MODEL.reset_feeder_protection(sim["power_network"], feeder_name,
                                        fault_active=fault_active)


def maintain_process_equipment(sim: dict, target_asset: str) -> float:
    """Perform a simulated repair only after the selected process asset is isolated."""
    match = next(((key, values) for key, values in CONCENTRATOR_MODEL.MAINTAINABLE_EQUIPMENT.items()
                  if target_asset.casefold() in (key.casefold(), values[0].casefold())), None)
    if match is None:
        raise ValueError(f"Unknown process equipment: {target_asset!r}")
    equipment, (label, controller_key) = match
    area = CONCENTRATOR_MODEL.MAINTENANCE_AREAS[equipment]
    active_fault = any(event.name == "Mechanical degradation"
                       and event.area in (area, "Whole factory") for event in sim["events"])
    equipment_stopped = (sim["process_control"]["equipment"][controller_key]["state"] == "STOPPED")
    previous = CONCENTRATOR_MODEL.perform_maintenance(
        sim["concentrator"], label, equipment_stopped=equipment_stopped, fault_active=active_fault)
    sim["alarms"].insert(0, {"time": sim["tick"], "area": area, "severity": "INFO",
        "message": f"Maintenance completed on {label}; condition restored from {previous * 100:.1f}% to 100%",
        "state": "ACTIVE / UNACK"})
    return previous


def maintain_water_pump(sim: dict, pump_name: str) -> float:
    """Repair a water pump after fault clearance and physical coastdown."""
    fault_active = any(event.name == "Mechanical degradation"
                       and event.area in ("Water supply network", "Whole factory")
                       for event in sim["events"])
    previous = WATER_MODEL.maintain_pump(sim["water_network"], pump_name,
                                         fault_active=fault_active)
    sim["alarms"].insert(0, {"time": sim["tick"], "area": "Water supply network",
        "severity": "INFO", "message": f"Maintenance completed on {pump_name}; condition restored "
        f"from {previous * 100:.1f}% to 100%", "state": "ACTIVE / UNACK"})
    return previous


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
                inject(name, area, severity, duration, command.get("target_asset"),
                       command.get("fault_location_pct", 90.0),
                       command.get("primary_breaker_failure", False), command.get("target_tag"))
                target_note = (f" on {command.get('target_tag')}" if command.get("target_tag")
                               else f" on {command.get('target_asset')}" if command.get("target_asset") else "")
                message = f"{severity} {name} injected in {area}{target_note}. Start the factory to evolve its simulated process effects."
            elif action == "reset_feeder_protection":
                target_asset = command.get("target_asset", "")
                reset_feeder_protection(sim, target_asset)
                message = f"Feeder protection lockout manually reset for {target_asset}; trip history was retained."
            elif action == "maintain_equipment":
                target_asset = command.get("target_asset", "")
                previous = maintain_process_equipment(sim, target_asset)
                message = (f"Maintenance completed on {target_asset}; equipment condition restored "
                           f"from {previous * 100:.1f}% to 100%.")
            elif action == "maintain_water_pump":
                target_asset = command.get("target_asset", "")
                previous = maintain_water_pump(sim, target_asset)
                message = (f"Maintenance completed on {target_asset}; pump condition restored "
                           f"from {previous * 100:.1f}% to 100%.")
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
            elif action == "start_cip":
                cip = sim["plant_utilities"]["cip"]
                target_unit = command.get("target_unit", "pasteurizer_01")
                if not UTILITY_MODEL.CIP_MODEL.start(cip, target_unit, sim["tick"]):
                    raise ValueError(cip["start_block_reason"])
                message = f"CIP cleaning started for {target_unit}."
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
    utility_events = [event.__dict__ for event in sim["events"]]
    prior_factory_water_availability = sim["water_network"].get("factory_water_available_fraction", 1.0)
    prior_wastewater_availability = sim["water_network"].get("wastewater_available_fraction", 1.0)
    CHILLED_MODEL.advance(
        sim["chilled_water"], sim["tick"], utility_events,
        cooling_demand_kw=UTILITY_MODEL.chilled_water_demand_kw(sim["plant_utilities"]),
        power_available_fraction=sim["power_network"].get("factory_utilities_power_fraction", 1.0),
        seed=sim["seed"],
    )
    UTILITY_MODEL.advance(
        sim["plant_utilities"], sim["tick"], utility_events,
        power_available_fraction=sim["power_network"].get("factory_utilities_power_fraction", 1.0),
        water_available_fraction=prior_factory_water_availability,
        wastewater_available_fraction=prior_wastewater_availability,
        chilled_water_service_fraction=sim["chilled_water"]["cooling_service_fraction"],
        chilled_water_supply_temperature_c=sim["chilled_water"]["supply_temperature_c"],
        seed=sim["seed"],
    )
    water_events = [event.__dict__ for event in sim["events"]
                    if event.area in ("Water supply network", "Water distribution", "Water treatment",
                                      "Wastewater", "Mine power network", "Whole factory")]
    concentrator_events = [event.__dict__ for event in sim["events"]]
    process_power_load_mw = (sim["concentrator"]["total_power_mw"]
                             + sim["mine_ventilation"]["fan_power_kw"] / 1000.0)
    water_power_load_mw = sim["water_network"]["electrical_load_kw"] / 1000.0
    POWER_MODEL.advance(sim["power_network"], sim["tick"], concentrator_events,
                        process_load_mw=process_power_load_mw,
                        water_load_mw=water_power_load_mw,
                        utility_load_mw=(sim["plant_utilities"]["electric_load_mw"]
                                         + sim["chilled_water"]["electric_load_mw"]),
                        site_building_load_mw=sim["site_buildings"]["electric_load_mw"],
                        site_nonbuilding_request_mw=5.6,
                        motor_starting_load_mw=sim["process_control"]["motor_starting_load_mw"],
                        seed=sim["seed"])
    BUILDING_MODEL.advance(
        sim["site_buildings"], sim["tick"], utility_events, seed=sim["seed"],
        power_available_fraction=sim["power_network"]["site_flexible_power_fraction"])
    moving_trucks = sum(1 for truck in sim["haulage"]["trucks"].values()
                        if truck["frame"]["speed_kmh"] > 2.0
                        or truck["frame"]["cycle_state"] == "loading")
    ventilation_activity = moving_trucks / max(1, len(sim["haulage"]["trucks"]))
    VENTILATION_MODEL.advance(
        sim["mine_ventilation"], sim["tick"], utility_events, seed=sim["seed"],
        power_available_fraction=sim["power_network"]["process_power_fraction"],
        haulage_activity=ventilation_activity,
    )
    ventilation_alarm_states = sim.setdefault("ventilation_alarm_states", {})
    peak_methane = max(zone["methane_pct"] for zone in sim["mine_ventilation"]["branches"].values())
    min_oxygen = min(zone["oxygen_pct"] for zone in sim["mine_ventilation"]["branches"].values())
    ventilation_conditions = (
        ("airflow", sim["mine_ventilation"]["total_airflow_m3s"]
         < 0.80 * sim["mine_ventilation"]["required_airflow_m3s"],
         sim["mine_ventilation"]["total_airflow_m3s"]
         > 0.90 * sim["mine_ventilation"]["required_airflow_m3s"],
         "Mine ventilation airflow is below the operating requirement"),
        ("methane", peak_methane > 1.0, peak_methane < 0.8,
         "Methane concentration is elevated; inspect ventilation and gas sources"),
        ("oxygen", min_oxygen < 19.5, min_oxygen > 19.8,
         "Mine oxygen concentration is below the simulated operating limit"),
    )
    for condition, active, cleared, message in ventilation_conditions:
        if active and not ventilation_alarm_states.get(condition, False):
            sim["alarms"].insert(0, {"time": sim["tick"], "area": "Underground mining",
                "severity": "HIGH", "message": message, "state": "ACTIVE / UNACK"})
            ventilation_alarm_states[condition] = True
        elif ventilation_alarm_states.get(condition, False) and cleared:
            sim["alarms"].insert(0, {"time": sim["tick"], "area": "Underground mining",
                "severity": "INFO", "message": f"{message} cleared", "state": "CLEARED"})
            ventilation_alarm_states[condition] = False
    building_alarm_states = sim.setdefault("building_alarm_states", {})
    for name, zone in sim["site_buildings"]["zones"].items():
        slug = name.lower().replace(" ", "_")
        conditions = (
            ("temperature", zone["temperature_c"] > 32.0 or zone["temperature_c"] < 8.0,
             28.0 >= zone["temperature_c"] >= 12.0,
             f"{name} room temperature outside the operating band"),
            ("co2", zone["co2_ppm"] > 1_500.0, zone["co2_ppm"] < 1_200.0,
             f"{name} indoor CO₂ is high; inspect occupancy and ventilation"),
            ("filter_dp", zone["filter_dp_pa"] > 500.0, zone["filter_dp_pa"] < 420.0,
             f"{name} HVAC filter differential pressure is high"),
        )
        for condition, active, cleared, message in conditions:
            key = f"{slug}_{condition}"
            if active and not building_alarm_states.get(key, False):
                sim["alarms"].insert(0, {"time": sim["tick"], "area": "Site buildings",
                    "severity": "HIGH", "message": message, "state": "ACTIVE / UNACK"})
                building_alarm_states[key] = True
            elif building_alarm_states.get(key, False) and cleared:
                sim["alarms"].insert(0, {"time": sim["tick"], "area": "Site buildings",
                    "severity": "INFO", "message": f"{message} cleared", "state": "CLEARED"})
                building_alarm_states[key] = False
    # The concentrator PLC sees the last scanned tank transmitter value, not
    # the water model's hidden process truth. This lets instrument drift affect
    # ordinary startup permissives just as it affects the tank-level PI loop.
    water_level = sim["water_network"]["controllers"]["Process feed pump"]["process_value_pct"]
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
                                             sim["concentrator"]["rom_stockpile_t"],
                                             ore_grade_pct=sim["concentrator"]["mine_block_grade_pct"])
    prior_water_shortage = sim["water_network"].get("concentrator_unmet_m3h",
                                                      sim["water_network"].get("unmet_demand_m3h", 0.0))
    CONCENTRATOR_MODEL.advance(sim["concentrator"], sim["tick"], concentrator_events,
                               water_shortage_m3h=prior_water_shortage,
                               seed=sim["seed"], mine_delivery_t=mine_delivery_t,
                               mine_delivery_copper_t=sim["haulage"]["last_delivery_copper_t"],
                               power_available_fraction=sim["power_network"]["process_power_fraction"],
                               equipment_available=equipment_available)
    WATER_MODEL.advance(sim["water_network"], sim["tick"], water_events, seed=sim["seed"],
                        concentrator_demand_m3h=sim["concentrator"]["gross_process_water_m3h"],
                        concentrator_reclaim_m3h=sim["concentrator"]["concentrator_reclaim_m3h"],
                        factory_cip_demand_m3h=sim["plant_utilities"]["cip_water_demand_m3h"],
                        factory_cip_effluent_m3h=sim["plant_utilities"]["cip_effluent_m3h"],
                        factory_cip_return_ph=sim["plant_utilities"]["cip"]["return_ph"],
                        factory_cip_conductivity_us_cm=sim["plant_utilities"]["cip"]["return_conductivity_us_cm"],
                        power_available_fraction=sim["power_network"]["water_power_fraction"])
    water = sim["water_network"]
    conditions = {
        "process_tank_low": ("Water supply network", water["nodes"]["process_tank"]["level_pct"] < 25,
                              "Process water tank low level", "HIGH"),
        "unmet_process_demand": ("Water supply network", water["unmet_demand_m3h"] > 1,
                                  "Process water demand is not being met", "CRITICAL"),
        "unmet_treated_water_demand": ("Water distribution", water["unmet_treated_demand_m3h"] > 1,
                                        "Treated water distribution demand is not being met", "HIGH"),
        "treated_water_turbidity_high": ("Water treatment", water["nodes"]["treated_tank"]["turbidity_ntu"] > 5.0,
                                           "Treated service-water turbidity exceeds the operating target", "HIGH"),
        "treated_water_chlorine_low": ("Water treatment", water["nodes"]["treated_tank"]["chlorine_residual_mg_l"] < 0.2,
                                        "Treated service-water chlorine residual is below the operating target", "HIGH"),
        "water_filter_backwash": ("Water treatment", water["treatment"]["mode"] == "BACKWASH",
                                  "Service-water filter is in backwash; treated-water transfer is temporarily isolated", "MEDIUM"),
        "water_filter_high_dp": ("Water treatment", water["treatment"]["filter_differential_pressure_kpa"] > 55,
                                  "Service-water filter differential pressure is high; transfer capacity is reduced", "HIGH"),
        "water_treatment_power_hold": ("Water treatment", water["treatment"]["mode"] == "POWER_HOLD",
                                        "Service-water treatment is paused by low electrical supply", "HIGH"),
        "water_treatment_chemical_low": ("Water treatment", water["treatment"]["coagulant_inventory_kg"] < 40
                                          or water["treatment"]["chlorine_inventory_kg"] < 5,
                                          "Water-treatment chemical inventory is low", "MEDIUM"),
        "wastewater_equalization_high": ("Wastewater", water["wastewater"]["equalization_level_pct"] > 85,
                                           "Wastewater equalization tank is high; CIP and backwash headroom is limited", "HIGH"),
        "wastewater_reaction_tank_high": ("Wastewater", water["wastewater"]["treatment_tank_level_pct"] > 90,
                                            "Wastewater reaction tank is near capacity; equalization draw may slow", "HIGH"),
        "wastewater_overflow": ("Wastewater", water["wastewater"]["equalization_overflow_m3"] > 0,
                                 "Wastewater equalization overflow is bypassing treatment", "CRITICAL"),
        "wastewater_effluent_quality": ("Wastewater",
            water["wastewater"]["treatment_flow_m3h"] > 0 and not water["wastewater"]["effluent_compliant"],
            "Wastewater effluent is outside reuse quality limits; reuse is blocked", "HIGH"),
        "wastewater_chemicals_low": ("Wastewater", min(water["wastewater"]["acid_inventory_kg"],
            water["wastewater"]["caustic_inventory_kg"], water["wastewater"]["coagulant_inventory_kg"],
            water["wastewater"]["polymer_inventory_kg"]) < 20,
            "One or more wastewater treatment chemical inventories are low", "MEDIUM"),
        "wastewater_sludge_high": ("Wastewater", water["wastewater"]["sludge_hopper_kg"] > 450,
                                    "Wastewater sludge hopper is near capacity", "HIGH"),
        "raw_tank_high": ("Water supply network", water["nodes"]["raw_tank"]["level_pct"] > 90,
                           "Raw water tank high level", "HIGH"),
        "tank_spill": ("Water supply network", any(node["overflow_m3"] > 0 for node in water["nodes"].values()),
                       "Water tank overflow detected", "HIGH"),
        "pipe_surge_overpressure": ("Water supply network", any(
            water["pumps"][WATER_MODEL.LINKS[key]["pump"]]["pump_head_m"] * 9.80665
            + link["surge_peak_scan_kpa"] > link["pressure_rating_kpa"]
            for key, link in water["links"].items()
            if WATER_MODEL.LINKS[key]["pump"]),
            "Estimated pipe transient pressure exceeds line rating", "CRITICAL"),
        "pipe_surge_low_pressure": ("Water supply network", any(
            WATER_MODEL.SITE_ATMOSPHERIC_PRESSURE_KPA
            + water["pumps"][WATER_MODEL.LINKS[key]["pump"]]["pump_head_m"] * 9.80665
            + link["surge_min_scan_kpa"] <= WATER_MODEL.WATER_VAPOR_PRESSURE_KPA
            for key, link in water["links"].items() if WATER_MODEL.LINKS[key]["pump"]),
            "Estimated absolute pressure reached the water vapor-pressure floor; column-separation risk", "HIGH"),
    }
    prior_states = sim.setdefault("water_alarm_states", {})
    for alarm_key, (area, active, message, severity) in conditions.items():
        was_active = prior_states.get(alarm_key, False)
        if active and not was_active:
            sim["alarms"].insert(0, {"time": sim["tick"], "area": area,
                                     "severity": severity, "message": message,
                                     "state": "ACTIVE / UNACK"})
        elif was_active and not active:
            sim["alarms"].insert(0, {"time": sim["tick"], "area": area,
                                     "severity": "INFO", "message": f"{message} cleared",
                                     "state": "CLEARED"})
        prior_states[alarm_key] = active
    distribution_alarm_states = sim.setdefault("water_distribution_alarm_states", {})
    distribution = water["distribution"]
    distribution_conditions = [("elevated_tank_low",
        distribution["tank_level_pct"] < 20.0, distribution["tank_level_pct"] > 30.0,
        "Elevated service tank is low; district pressure reserve is shrinking", "HIGH")]
    for zone_name, zone in distribution["zones"].items():
        key = zone_name.lower().replace(" ", "_") + "_low_pressure"
        distribution_conditions.append((key,
            zone["pressure_kpa"] < zone["minimum_pressure_kpa"],
            zone["pressure_kpa"] > zone["minimum_pressure_kpa"] + 15.0,
            f"{zone_name} pressure is below its minimum service threshold", "CRITICAL"))
    distribution_conditions.append(("chlorine_residual_low",
        distribution["chlorine_residual_mg_l"] < 0.20,
        distribution["chlorine_residual_mg_l"] > 0.30,
        "Elevated-tank chlorine residual is low; inspect water age and dosing", "HIGH"))
    distribution_conditions.append(("turbidity_high",
        distribution["turbidity_ntu"] > 5.0,
        distribution["turbidity_ntu"] < 2.0,
        "Elevated-tank turbidity is high; inspect treatment breakthrough or resuspension", "HIGH"))
    for zone_name, zone in distribution["zones"].items():
        key = zone_name.lower().replace(" ", "_") + "_chlorine_low"
        distribution_conditions.append((key,
            zone["chlorine_residual_mg_l"] < 0.20,
            zone["chlorine_residual_mg_l"] > 0.30,
            f"{zone_name} chlorine residual is low after estimated distribution residence time", "HIGH"))
    for alarm_key, active, cleared, message, severity in distribution_conditions:
        was_active = distribution_alarm_states.get(alarm_key, False)
        if active and not was_active:
            sim["alarms"].insert(0, {"time": sim["tick"], "area": "Water distribution",
                "severity": severity, "message": message, "state": "ACTIVE / UNACK"})
        elif was_active and cleared:
            sim["alarms"].insert(0, {"time": sim["tick"], "area": "Water distribution",
                "severity": "INFO", "message": f"{message} cleared", "state": "CLEARED"})
        distribution_alarm_states[alarm_key] = active if not was_active else not cleared
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
        "factory_utility_feeder_limited": (
            power_state["factory_utilities_power_fraction"] < 0.95,
            power_state["factory_utilities_power_fraction"] > 0.98,
            "Factory steam and compressed-air electrical supply curtailed", "HIGH"),
        "battery_reserve": (power_state["battery_soc_pct"] < 20, power_state["battery_soc_pct"] > 30,
                            "Grid support battery reserve low", "HIGH"),
    }
    for feeder_name, feeder in power_state["distribution_feeders"].items():
        power_conditions[f"feeder_{feeder_name}_overcurrent"] = (
            feeder["relay_pickup"], not feeder["relay_pickup"],
            f"Distribution feeder {feeder_name} overcurrent relay pickup at {feeder['relay_current_multiple']:.2f}×",
            "HIGH")
        power_conditions[f"feeder_{feeder_name}_open"] = (
            not feeder["breaker_closed"], feeder["breaker_closed"],
            f"Distribution feeder {feeder_name} breaker open: {feeder['last_trip_reason'] or 'manual isolation'}",
            "CRITICAL")
        power_conditions[f"feeder_{feeder_name}_undervoltage"] = (
            feeder["energized"] and feeder["voltage_pu"] < 0.92,
            not feeder["energized"] or feeder["voltage_pu"] > 0.96,
            f"Distribution feeder {feeder_name} voltage depressed", "HIGH")
    incomer = power_state["distribution_incomer"]
    power_conditions["distribution_incomer_backup_pickup"] = (
        incomer["relay_pickup"], not incomer["relay_pickup"],
        f"Distribution incomer backup relay pickup at {incomer['relay_current_multiple']:.2f}×",
        "CRITICAL")
    power_conditions["distribution_incomer_lockout"] = (
        incomer["lockout"], not incomer["lockout"],
        f"Distribution incomer locked out: {incomer['last_trip_reason'] or 'backup protection operation'}",
        "CRITICAL")
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
    utilities = sim["plant_utilities"]
    utility_alarm_states = sim.setdefault("utility_alarm_states", {})
    utility_conditions = {
        "low_steam_header": ("Boiler house", utilities["steam_header_pressure_bar"] < 6.5,
            utilities["steam_header_pressure_bar"] > 7.0, "Steam header pressure below operating band", "HIGH"),
        "low_instrument_air": ("Compressed air", utilities["air_receiver_pressure_bar"] < 5.8,
            utilities["air_receiver_pressure_bar"] > 6.2, "Instrument-air ring pressure below operating band", "HIGH"),
        "wet_instrument_air": ("Compressed air", utilities["dryer_dew_point_c"] > 0,
            utilities["dryer_dew_point_c"] < -10, "Instrument-air dryer dew point is high", "HIGH"),
        "low_condensate_inventory": ("Boiler house", utilities["condensate_inventory_t"] < 3,
            utilities["condensate_inventory_t"] > 5, "Condensate receiver inventory low", "MEDIUM"),
        "pasteurizer_product_diverted": ("Thermal processing",
            sum(p["diverted_rate"] for p in utilities["pasteurizers"].values()) > 1,
            sum(p["diverted_rate"] for p in utilities["pasteurizers"].values()) < 0.1,
            "Pasteurizer product is being diverted below the temperature limit", "HIGH"),
        "packaging_accumulation_high": ("Packaging",
            utilities["filling_buffer_inventory_units"] > UTILITY_MODEL.PACKAGE_BUFFER_CAPACITY_UNITS * 0.9,
            utilities["filling_buffer_inventory_units"] < UTILITY_MODEL.PACKAGE_BUFFER_CAPACITY_UNITS * 0.7,
            "Case-packer accumulation conveyor nearing capacity", "HIGH"),
        "product_tank_high": ("Thermal processing",
            utilities["product_tank_inventory_units"] > UTILITY_MODEL.PRODUCT_TANK_CAPACITY_UNITS * 0.9,
            utilities["product_tank_inventory_units"] < UTILITY_MODEL.PRODUCT_TANK_CAPACITY_UNITS * 0.7,
            "Pasteurized-product tank nearing capacity", "MEDIUM"),
        "cip_water_starved": ("CIP", utilities["cip"]["active"]
            and utilities["cip"]["water_starved_seconds"] >= 30,
            not utilities["cip"]["active"] or utilities["cip"]["water_starved_seconds"] < 5,
            "CIP phase paused because process water supply is insufficient", "HIGH"),
        "cip_chemical_low": ("CIP",
            utilities["cip"]["caustic_inventory_l"] < 80 or utilities["cip"]["sanitizer_inventory_l"] < 50,
            utilities["cip"]["caustic_inventory_l"] > 100 and utilities["cip"]["sanitizer_inventory_l"] > 70,
            "CIP chemical inventory low; a complete cleaning cycle may be unavailable", "MEDIUM"),
    }
    for key, (area, active, cleared, message, severity) in utility_conditions.items():
        was_active = utility_alarm_states.get(key, False)
        if active and not was_active:
            sim["alarms"].insert(0, {"time": sim["tick"], "area": area,
                "severity": severity, "message": message, "state": "ACTIVE / UNACK"})
            utility_alarm_states[key] = True
        elif was_active and cleared:
            sim["alarms"].insert(0, {"time": sim["tick"], "area": area,
                "severity": "INFO", "message": f"{message} cleared", "state": "CLEARED"})
            utility_alarm_states[key] = False
    chilled = sim["chilled_water"]
    chilled_alarm_states = sim.setdefault("chilled_water_alarm_states", {})
    chilled_conditions = (
        ("low_cooling_storage", chilled["storage_mwh"] < 0.20,
         chilled["storage_mwh"] > 0.45,
         "Chilled-water thermal reserve is low; a further chiller loss may warm product cooling", "HIGH"),
        ("warm_chilled_supply", chilled["supply_temperature_c"] > 9.5,
         chilled["supply_temperature_c"] < 8.0,
         "Chilled-water supply temperature is reducing pasteurizer cooler capacity", "HIGH"),
        ("low_product_cooling", utilities["product_cooling_service_fraction"] < 0.90,
         utilities["product_cooling_service_fraction"] > 0.97,
         "Product cooler duty is insufficient; pasteurized product is being diverted", "CRITICAL"),
    )
    for key, active, cleared, message, severity in chilled_conditions:
        was_active = chilled_alarm_states.get(key, False)
        if active and not was_active:
            sim["alarms"].insert(0, {"time": sim["tick"], "area": "Chilled water",
                "severity": severity, "message": message, "state": "ACTIVE / UNACK"})
            chilled_alarm_states[key] = True
        elif was_active and cleared:
            sim["alarms"].insert(0, {"time": sim["tick"], "area": "Chilled water",
                "severity": "INFO", "message": f"{message} cleared", "state": "CLEARED"})
            chilled_alarm_states[key] = False
    water_tags, _diagram = WATER_MODEL.telemetry(sim["water_network"])
    power_tags = POWER_MODEL.telemetry(sim["power_network"])
    utility_tags = UTILITY_MODEL.telemetry(sim["plant_utilities"], sim["seed"])
    chilled_tags = CHILLED_MODEL.telemetry(sim["chilled_water"], sim["seed"])
    building_tags = BUILDING_MODEL.telemetry(sim["site_buildings"], sim["seed"])
    ventilation_tags = VENTILATION_MODEL.telemetry(sim["mine_ventilation"], sim["seed"])
    water_by_tag = {tag["tag"]: tag for tag in water_tags}
    power_by_tag = {tag["tag"]: tag for tag in power_tags}
    utility_by_tag = {tag["tag"]: tag for tag in utility_tags}
    chilled_by_tag = {tag["tag"]: tag for tag in chilled_tags}
    building_by_tag = {tag["tag"]: tag for tag in building_tags}
    ventilation_by_tag = {tag["tag"]: tag for tag in ventilation_tags}
    concentrator_by_tag = {tag["tag"]: tag for tag in
                           CONCENTRATOR_MODEL.telemetry(sim["concentrator"])
                           + HAULAGE_MODEL.telemetry(sim["haulage"], sim["seed"])}
    for tag in sim["tags"]:
        if tag["tag"] in water_by_tag:
            tag.update(water_by_tag[tag["tag"]])
        elif tag["tag"] in power_by_tag:
            tag.update(power_by_tag[tag["tag"]])
        elif tag["tag"] in utility_by_tag:
            tag.update(utility_by_tag[tag["tag"]])
        elif tag["tag"] in chilled_by_tag:
            tag.update(chilled_by_tag[tag["tag"]])
        elif tag["tag"] in building_by_tag:
            tag.update(building_by_tag[tag["tag"]])
        elif tag["tag"] in ventilation_by_tag:
            tag.update(ventilation_by_tag[tag["tag"]])
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
st.caption(f"Synthetic multi-industry training world · {len(AREAS)} areas · {len({(t['area'], t['unit']) for t in sim['tags']})} equipment units · {len(sim['tags'])} tags · local simulation only")

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
    if incident_name == "Electrical fault":
        target = "Grid distribution"
        target_asset = st.selectbox("Faulted feeder", ["Feeder north", "Feeder south", "Feeder east", "Feeder west"])
        fault_location_pct = st.slider("Fault location along feeder", 0, 100, 90,
                                       help="0% is at the source end; 100% is at the far end.")
        primary_breaker_failure = st.checkbox(
            "Simulate failed feeder breaker (test upstream backup relay)", value=False)
    else:
        target = st.selectbox("Target area", ["Whole factory", *AREAS.keys()])
        target_asset = None
        fault_location_pct = 90.0
        primary_breaker_failure = False
    target_tag = None
    if incident_name == "Signal drift":
        candidates = [tag for tag in sim["tags"]
                      if tag.get("dynamic_model")
                      and tag.get("units") not in {"bool", "enum", "cycles"}
                      and (target == "Whole factory" or tag["area"] == target)]
        if candidates:
            labels = {tag["tag"]: f"{tag['unit']} · {tag['sensor']} ({tag['units']})"
                      for tag in candidates}
            target_tag = st.selectbox("Drifting instrument", list(labels),
                                      format_func=lambda tag_name: labels[tag_name],
                                      key="signal_drift_target")
    if st.button("⚠ Inject incident", width="stretch"):
        inject(incident_name, target, severity, target_asset=target_asset,
               fault_location_pct=fault_location_pct,
               primary_breaker_failure=primary_breaker_failure, target_tag=target_tag)
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

@st.fragment(run_every="5s")
def render_live_map_status():
    st.subheader("Live fleet map")
    if live_map_available():
        st.link_button("Open live fleet map", "http://127.0.0.1:8502/map", type="primary")
        st.caption("The dedicated map uses WebSockets. It stays in its own page while the dashboard updates.")
    else:
        st.info("The live map and protocol endpoints are not running. Start the full playground with `./start_playground.sh`.")


render_live_map_status()

@st.fragment(run_every="1s")
def render_dashboard():
    mcp_results = process_mcp_commands()
    sim = st.session_state.sim
    # Preserve visibility when Streamlit keeps a paused pre-upgrade session alive.
    power = sim["power_network"]
    power.setdefault("frequency_nadir_hz", power.get("frequency_hz", 50.0))
    power.setdefault("frequency_window_nadir_hz", power.get("frequency_hz", 50.0))
    power.setdefault("distribution_losses_mw", 0.0)
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

    tabs = st.tabs(["Factory overview", "Water network", "Mineral process", "Area detail", "Mobile fleet", "Tag explorer", "Alarm console", "Power network", "Learning missions", "Plant utilities", "Underground ventilation"])
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
        st.caption("Pipe flow uses Darcy–Weisbach friction, minor losses, valve opening, static elevation, and pump affinity curves. PI controllers regulate tank levels; VFD ramps limit speed changes. Full pipes carry delayed flow and water-quality packets, so a pump trip can leave downstream flow running until the line empties. Rapid flow changes launch pressure waves that travel and reflect along the pipe; endpoint pressure changes feed the next one-second flow solve. Source, midpoint, receiver, and within-scan pressure peaks are published as signals.")
        metric_cols = st.columns(6)
        for col, label, value in zip(metric_cols, ["Total water demand", "Concentrator make-up", "Concentrator return",
                                                   "Background reclaim loop", "Water in transit", "Unmet demand"],
                                     [f"{water['demand_m3h']:.0f} m³/h",
                                      f"{sim['concentrator']['water_makeup_m3h']:.0f} m³/h",
                                      f"{sim['concentrator']['concentrator_reclaim_m3h']:.0f} m³/h",
                                      f"{water['water_recovery_pct']:.0f}%", f"{diagram['pipe_inventory_m3']:.0f} m³",
                                      f"{water.get('unmet_demand_m3h', 0) + water.get('unmet_treated_demand_m3h', 0):.1f} m³/h"]):
            col.metric(label, value)
        st.caption(f"Concentrator gross draw: {sim['concentrator']['gross_process_water_m3h']:.0f} m³/h · "
                   f"cumulative spill: {water['total_spilled_m3']:.2f} m³ · total water inventory (tanks + pipes): {diagram['total_inventory_m3']:.0f} m³")
        treatment = water["treatment"]
        treated_tank = water["nodes"]["treated_tank"]
        st.caption(f"Service-water treatment: {treatment['mode']} · treated tank {treated_tank['turbidity_ntu']:.1f} NTU / "
                   f"{treated_tank['chlorine_residual_mg_l']:.2f} mg/L chlorine · filter ΔP "
                   f"{treatment['filter_differential_pressure_kpa']:.1f} kPa · media loading "
                   f"{100*treatment['filter_solids_kg']/WATER_MODEL.TREATMENT.FILTER_CAPACITY_KG:.0f}% · "
                   f"coagulant {treatment['coagulant_inventory_kg']:.0f} kg / chlorine "
                   f"{treatment['chlorine_inventory_kg']:.0f} kg")
        wastewater = water["wastewater"]
        st.markdown("#### Shared wastewater treatment · CIP + filter backwash")
        st.caption("CIP return and filter backwash collect in equalization, then pass through pH neutralization, DAF solids removal, and sludge pressing. Only effluent inside the training quality limits returns to the mine reclaim pond; noncompliant flow goes to the modeled outfall.")
        ww_cols = st.columns(6)
        for col, label, value in zip(ww_cols,
            ["Equalization", "Influent", "Treatment", "Effluent pH / TSS", "Reuse / outfall", "Sludge hopper"],
            [f"{wastewater['equalization_level_pct']:.1f}% · {wastewater['equalization_volume_m3']:.1f} m³",
             f"{wastewater['influent_flow_m3h']:.1f} m³/h",
             f"{wastewater['treatment_mode']} · {wastewater['treatment_tank_level_pct']:.0f}% / {wastewater['treatment_flow_m3h']:.1f} m³/h",
             f"{wastewater['effluent_ph']:.2f} · {wastewater['effluent_tss_mg_l']:.0f} mg/L",
             f"{wastewater['reuse_flow_m3h']:.1f} / {wastewater['outfall_flow_m3h']:.1f} m³/h",
             f"{wastewater['sludge_hopper_kg']:.1f} / {WATER_MODEL.WASTEWATER.SLUDGE_HOPPER_CAPACITY_KG:.0f} kg"]):
            col.metric(label, value)
        with st.expander("Operator controls · simulated equipment only"):
            pump_cols = st.columns(4)
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
            st.markdown("**Pump condition and maintenance**")
            pump_condition_rows = [{"Pump": pump_name,
                                    "Health": f"{pump.get('health_factor', 1.0) * 100:.1f}%",
                                    "Enabled": "Yes" if pump["enabled"] else "No",
                                    "Speed": f"{pump['speed_pct']:.1f}%"}
                                   for pump_name, pump in water["pumps"].items()]
            st.dataframe(pd.DataFrame(pump_condition_rows), hide_index=True, width="stretch")
            maintenance_pump = st.selectbox("Pump for maintenance", list(water["pumps"]),
                key=f"water_maintenance_pump_{sim['simulation_id']}")
            selected_pump = water["pumps"][maintenance_pump]
            water_fault_active = any(event.name == "Mechanical degradation"
                and event.area in ("Water supply network", "Whole factory") for event in sim["events"])
            pump_isolated = (not selected_pump["enabled"] and not selected_pump["running"]
                             and selected_pump["speed_pct"] < 1.0)
            if st.button("Maintain water pump", disabled=not pump_isolated or water_fault_active,
                         key=f"maintain_water_pump_{sim['simulation_id']}"):
                previous = maintain_water_pump(sim, maintenance_pump)
                st.success(f"{maintenance_pump}: condition restored from {previous * 100:.1f}% to 100%.")
            if not pump_isolated:
                st.caption("Disable the pump and wait for VFD feedback to reach zero before maintenance.")
            elif water_fault_active:
                st.caption("Clear the active mechanical fault before maintaining a pump.")
            valve_cols = st.columns(5)
            for col, (valve_name, opening) in zip(valve_cols, list(water["valves"].items())):
                with col:
                    water["valves"][valve_name] = st.slider(f"{valve_name} (%)", 0, 100, int(opening), key=f"water_valve_{valve_name}")
        positions = {"north_reservoir": (0.06, .74), "raw_tank": (.28, .74),
                     "process_tank": (.56, .74), "concentrator_reclaim_sump": (.56, .23),
                     "reclaim_pond": (.79, .23), "treated_tank": (.94, .23)}
        node_x, node_y, node_text = [], [], []
        for node in diagram["nodes"]:
            x, y = positions[node["id"]]
            node_x.append(x); node_y.append(y)
            node_text.append(f"{node['label']}<br>{node['level_pct']:.1f}% · {node['volume_m3']:.0f}/{node['capacity_m3']:.0f} m³")
        network_fig = go.Figure()
        for link in diagram["links"]:
            x0, y0 = positions[link["from"]]; x1, y1 = positions[link["to"]]
            max_flow = WATER_MODEL.LINKS[link["id"]]["max_flow_m3h"]
            flow_ratio = min(1.0, abs(link["flow_m3h"]) / max_flow)
            color = ("#277da1" if link["flow_m3h"] > 1 else
                     "#f9844a" if link["flow_m3h"] < -1 else "#a9b4bf")
            path_x, path_y = ([x0, x1], [y0, y1]) if link["flow_m3h"] >= 0 else ([x1, x0], [y1, y0])
            network_fig.add_trace(go.Scatter(x=path_x, y=path_y, mode="lines",
                                              line={"color": color, "width": 2 + 7 * flow_ratio},
                                              hovertemplate=f"{link['label']} · {link['flow_m3h']:.1f} m³/h · check valve {link['check_valve_state']}<extra></extra>",
                                              showlegend=False))
        network_fig.add_trace(go.Scatter(x=node_x, y=node_y, mode="markers+text",
                                          marker={"size": 24, "color": ["#4c78a8", "#72a0c1", "#f2a541", "#9b5de5", "#43aa8b", "#277da1"],
                                                  "line": {"color": "white", "width": 2}},
                                          text=node_text, textposition="top center", textfont={"size": 11, "color": "#243746"},
                                          hovertemplate="%{text}<extra></extra>", showlegend=False))
        for link in diagram["links"]:
            x0, y0 = positions[link["from"]]; x1, y1 = positions[link["to"]]
            arrow_x, arrow_y, arrow_ax, arrow_ay = ((x1, y1, x0, y0) if link["flow_m3h"] >= 0
                                                     else (x0, y0, x1, y1))
            arrow_color = ("#277da1" if link["flow_m3h"] > 1 else
                           "#f9844a" if link["flow_m3h"] < -1 else "#a9b4bf")
            network_fig.add_annotation(x=arrow_x, y=arrow_y, ax=arrow_ax, ay=arrow_ay,
                                       xref="x", yref="y", axref="x", ayref="y",
                                       showarrow=True, arrowhead=3, arrowsize=1.1, arrowwidth=2,
                                       arrowcolor=arrow_color)
            if link["reverse_flow_m3h"] > 1:
                network_fig.add_annotation(x=x0, y=y0, ax=(x0 + x1) / 2, ay=(y0 + y1) / 2,
                    xref="x", yref="y", axref="x", ayref="y", showarrow=True,
                    arrowhead=3, arrowsize=1.1, arrowwidth=2, arrowcolor="#f9844a")
            network_fig.add_annotation(x=(x0+x1)/2, y=(y0+y1)/2 + .06,
                                       text=f"{link['label']}<br>in {link['inlet_flow_m3h']:.0f} → out {link['flow_m3h']:.0f} m³/h · reverse {link['reverse_flow_m3h']:.0f} m³/h · {link['transport_delay_s']//60} min · ΔP {link['pressure_kpa']:.0f} kPa · check {link['check_valve_state']}",
                                       showarrow=False, font={"size": 10, "color": "#243746"}, bgcolor="rgba(255,255,255,.88)")
        network_fig.update_layout(height=420, margin={"l": 10, "r": 10, "t": 10, "b": 10},
                                  xaxis={"range": [0, 1], "visible": False}, yaxis={"range": [0, 1], "visible": False},
                                  plot_bgcolor="white", paper_bgcolor="white",
                                  font={"color": "#243746"}, uirevision="water-network")
        st.plotly_chart(network_fig, width="stretch")
        distribution = water["distribution"]
        st.markdown("#### District water distribution · pressure, service, and leakage")
        st.caption("The treatment outlet refills a finite elevated tank. Gravity pressure pays elevation and Darcy–Weisbach pipe losses; pressure-dependent demand and pressure-scaled leakage then determine each district's delivered flow. A low-pressure event can therefore leave customer demand unmet even while some stored water remains.")
        district_metrics = st.columns(5)
        for col, label, value in zip(district_metrics,
            ["Elevated tank", "Customer service", "Customer demand", "Network leakage", "Refill pump"],
            [f"{distribution['tank_level_pct']:.1f}% · {distribution['tank_volume_m3']:.0f}/{distribution['tank_capacity_m3']:.0f} m³",
             f"{distribution['service_fraction']*100:.1f}%",
             f"{distribution['customer_served_m3h']:.1f}/{distribution['customer_demand_m3h']:.1f} m³/h",
             f"{distribution['leak_flow_m3h']:.2f} m³/h · {distribution['non_revenue_water_pct']:.1f}%",
             f"{distribution['transfer_received_m3h']:.1f} m³/h · {distribution['pump_power_kw']:.1f} kW"]):
            col.metric(label, value)
        st.caption(f"Elevated tank quality: {distribution['chlorine_residual_mg_l']:.3f} mg/L chlorine · {distribution['turbidity_ntu']:.2f} NTU turbidity. Branch chlorine decays with estimated pipe residence time; the district model is a low-order hydraulic and quality approximation.")
        district_rows = [{"District": name,
                          "Pressure (kPa)": zone["pressure_kpa"],
                          "Minimum (kPa)": zone["minimum_pressure_kpa"],
                          "Demand (m³/h)": zone["customer_demand_m3h"],
                          "Served (m³/h)": zone["customer_served_m3h"],
                          "Unmet (m³/h)": zone["unmet_demand_m3h"],
                          "Leak (m³/h)": zone["leak_flow_m3h"],
                          "PRV (%)": zone["prv_position_pct"],
                          "Water age (h)": zone["water_age_h"],
                          "Chlorine (mg/L)": zone["chlorine_residual_mg_l"],
                          "Turbidity (NTU)": zone["turbidity_ntu"]}
                         for name, zone in distribution["zones"].items()]
        st.dataframe(pd.DataFrame(district_rows).round(2), hide_index=True, width="stretch")
        pressure_colors = {"District zone A": "#277da1", "District zone B": "#e07a5f",
                           "District zone C": "#43aa8b"}
        pressure_fig = go.Figure()
        for name, zone in distribution["zones"].items():
            pressure_fig.add_trace(go.Bar(x=[zone["pressure_kpa"]], y=[name], orientation="h",
                name=name, marker_color=pressure_colors[name],
                hovertemplate=f"{name}: {zone['pressure_kpa']:.1f} kPa<extra></extra>"))
            pressure_fig.add_trace(go.Scatter(x=[zone["minimum_pressure_kpa"]], y=[name],
                mode="markers", name=f"{name} minimum", marker={"symbol": "line-ns-open",
                    "size": 17, "color": "#202833"}, showlegend=False,
                hovertemplate=f"{name} service limit: {zone['minimum_pressure_kpa']:.0f} kPa<extra></extra>"))
        pressure_fig.update_layout(title="District pressure and minimum service threshold",
            xaxis_title="Pressure (kPa)", barmode="overlay", height=280,
            legend={"orientation": "h", "y": 1.12},
            uirevision="water-distribution-pressure")
        st.plotly_chart(pressure_fig, width="stretch")
        distribution_history = pd.DataFrame(distribution["history"])
        if not distribution_history.empty:
            pressure_history = px.line(distribution_history, x="sim_seconds",
                y=["zone_a_pressure_kpa", "zone_b_pressure_kpa", "zone_c_pressure_kpa"],
                labels={"sim_seconds": "Simulated seconds", "value": "Pressure (kPa)",
                        "variable": "District"},
                color_discrete_sequence=["#277da1", "#e07a5f", "#43aa8b"])
            pressure_history.update_layout(title="District pressure through demand and feeder changes",
                uirevision="water-distribution-pressure-history")
            st.plotly_chart(pressure_history, width="stretch")
        wave_history = pd.DataFrame(water.get("pressure_wave_history", []))
        if not wave_history.empty:
            wave_link = st.selectbox("Pressure-wave line", list(WATER_MODEL.LINKS),
                format_func=lambda key: WATER_MODEL.LINKS[key]["label"], key="water_wave_link")
            wave_fig = go.Figure()
            for point, label, color in (("source", "Pump/source", "#277DA1"),
                                        ("midpoint", "Midpoint", "#E76F51"),
                                        ("receiver", "Receiving end", "#2A9D8F")):
                wave_fig.add_trace(go.Scatter(x=wave_history["time"],
                    y=wave_history[f"{wave_link}_{point}_peak_kpa"],
                    name=f"{label} positive peak", line={"color": color}))
                wave_fig.add_trace(go.Scatter(x=wave_history["time"],
                    y=wave_history[f"{wave_link}_{point}_min_kpa"],
                    name=f"{label} negative peak", line={"color": color, "dash": "dash"}))
            wave_fig.update_layout(height=340,
                title="Pressure-wave envelope recorded inside each one-second scan",
                xaxis_title="Simulated seconds", yaxis_title="Transient pressure ΔP (kPa)",
                legend={"orientation": "h", "y": 1.14},
                uirevision=f"water-pressure-wave-{wave_link}")
            st.plotly_chart(wave_fig, width="stretch")
        hist = pd.DataFrame(water["history"])
        if not hist.empty:
            left, right = st.columns(2)
            with left:
                f = px.line(hist, x="time", y=["raw_tank_level_pct", "process_tank_level_pct", "concentrator_reclaim_sump_level_pct", "reclaim_pond_level_pct", "treated_tank_level_pct", "distribution_tank_level_pct"],
                            labels={"time": "Simulated seconds", "value": "Tank level (%)", "variable": "Tank"},
                            color_discrete_sequence=["#277da1", "#f2a541", "#43aa8b", "#9b5de5", "#e07a5f", "#577590"])
                f.update_layout(uirevision="water-level-trend")
                st.plotly_chart(f, width="stretch")
            with right:
                f = px.line(hist, x="time", y=["intake_flow_m3h", "intake_arrival_m3h", "process_feed_m3h",
                                                 "process_demand_m3h", "treated_demand_m3h",
                                                 "treated_customer_demand_m3h"],
                            labels={"time": "Simulated seconds", "value": "Flow (m³/h)", "variable": "Flow"},
                            color_discrete_sequence=["#277da1", "#4cc9f0", "#f2a541", "#43aa8b", "#9b5de5", "#e07a5f"])
                f.update_layout(uirevision="water-flow-trend")
                st.plotly_chart(f, width="stretch")
            st.markdown("**Service-water quality and filter loading**")
            quality_col, filter_col = st.columns(2)
            with quality_col:
                quality_fig = go.Figure()
                quality_fig.add_trace(go.Scatter(x=hist["time"], y=hist["treated_turbidity_ntu"],
                    name="Turbidity · NTU", line={"color": "#2A9D8F"}))
                quality_fig.add_trace(go.Scatter(x=hist["time"], y=hist["treated_chlorine_residual_mg_l"],
                    name="Chlorine residual · mg/L", yaxis="y2", line={"color": "#6A4C93"}))
                quality_fig.update_layout(height=300, xaxis_title="Simulated seconds",
                    yaxis={"title": "Turbidity (NTU)", "title_font": {"color": "#2A9D8F"},
                           "tickfont": {"color": "#2A9D8F"}},
                    yaxis2={"title": "Chlorine residual (mg/L)", "overlaying": "y", "side": "right",
                            "title_font": {"color": "#6A4C93"}, "tickfont": {"color": "#6A4C93"}},
                    legend={"orientation": "h", "y": 1.15}, uirevision="water-quality-trend")
                st.plotly_chart(quality_fig, width="stretch")
            with filter_col:
                filter_fig = go.Figure()
                filter_fig.add_trace(go.Scatter(x=hist["time"], y=hist["filter_differential_pressure_kpa"],
                    name="Filter differential pressure · kPa", line={"color": "#E76F51"}))
                filter_fig.add_trace(go.Scatter(x=hist["time"], y=hist["filter_solids_loading_pct"],
                    name="Media solids loading · %", yaxis="y2", line={"color": "#457B9D"}))
                filter_fig.update_layout(height=300, xaxis_title="Simulated seconds",
                    yaxis={"title": "Differential pressure (kPa)", "title_font": {"color": "#E76F51"},
                           "tickfont": {"color": "#E76F51"}},
                    yaxis2={"title": "Media solids loading (%)", "overlaying": "y", "side": "right",
                            "range": [0, 100], "title_font": {"color": "#457B9D"},
                            "tickfont": {"color": "#457B9D"}},
                    legend={"orientation": "h", "y": 1.15}, uirevision="water-filter-trend")
                st.plotly_chart(filter_fig, width="stretch")
            st.markdown("**Concentrator water balance · simulated flow rates**")
            f = px.line(hist, x="time", y=["concentrator_gross_demand_m3h", "concentrator_makeup_m3h",
                                             "concentrator_reclaim_m3h"],
                        labels={"time": "Simulated seconds", "value": "Flow (m³/h)", "variable": "Water stream"},
                        color_discrete_sequence=["#277da1", "#f2a541", "#43aa8b"])
            f.update_layout(uirevision="concentrator-water-balance-trend")
            st.plotly_chart(f, width="stretch")
            st.markdown("**Wastewater quality and reuse · distinct scale traces**")
            wastewater_fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                vertical_spacing=.10, subplot_titles=("Treatment tank inventory",
                    "Effluent pH", "Effluent suspended solids"))
            wastewater_fig.add_trace(go.Scatter(x=hist["time"], y=hist["wastewater_equalization_level_pct"],
                name="Equalization level · %", line={"color": "#6A4C93"}), row=1, col=1)
            wastewater_fig.add_trace(go.Scatter(x=hist["time"], y=hist["wastewater_treatment_tank_level_pct"],
                name="Reaction tank level · %", line={"color": "#277DA1"}), row=1, col=1)
            wastewater_fig.add_trace(go.Scatter(x=hist["time"], y=hist["wastewater_effluent_ph"],
                name="Effluent pH", line={"color": "#E76F51"}), row=2, col=1)
            wastewater_fig.add_trace(go.Scatter(x=hist["time"], y=hist["wastewater_effluent_tss_mg_l"],
                name="Effluent TSS · mg/L", line={"color": "#2A9D8F"}), row=3, col=1)
            wastewater_fig.update_yaxes(title_text="Level (%)", range=[0, 100], row=1, col=1)
            wastewater_fig.update_yaxes(title_text="pH", range=[0, 14], row=2, col=1)
            wastewater_fig.update_yaxes(title_text="TSS (mg/L)", rangemode="tozero", row=3, col=1)
            wastewater_fig.update_layout(height=680, xaxis3_title="Simulated seconds",
                legend={"orientation": "h", "y": 1.05}, uirevision="wastewater-quality-trend")
            st.plotly_chart(wastewater_fig, width="stretch")
        else:
            st.info("Start or single-step the factory to build level and flow trends.")
        st.markdown("**Fault drills:** inject Medium Mechanical degradation or Utility loss in Water supply network and follow tank levels, pump-linked flows, and unmet demand. A Process upset raises demand and reduces recovery.")
    with tabs[2]:
        plant = sim["concentrator"]
        control = sim["process_control"]
        st.subheader("Copper concentrator · ore-to-tailings process circuit")
        st.caption("Crusher and mill power limits, ore hardness, finite buffers, per-pass breakage across three size classes, cyclone reject recirculation, first-order flotation kinetics, separate concentrate and tailings solids balances, cyclic filter-press batches, mine-water availability, and power demand evolve together. These are simplified supervisory dynamics, not a calibrated comminution or metallurgical model.")
        metric_cols = st.columns(6)
        metric_values = [
            ("Plant feed", f"{plant['plant_feed_tph']:.0f} t/h"),
            ("Copper recovery", f"{plant['copper_recovery_pct']:.1f}%"),
            ("Product P80", f"{plant['p80_microns']:.0f} µm"),
            ("Concentrate", f"{plant['concentrate_tph']:.1f} t/h"),
            ("Concentrator load", f"{plant['total_power_mw']:.1f} MW"),
            ("Water makeup", f"{plant['water_makeup_m3h']:.0f} m³/h"),
        ]
        for col, (label, value) in zip(metric_cols, metric_values):
            col.metric(label, value)
        st.caption(f"Mill feed size classes · coarse {plant['grind_coarse_fraction']*100:.1f}% · intermediate {plant['grind_intermediate_fraction']*100:.1f}% · fine {plant['grind_fine_fraction']*100:.1f}% · breakage/pass {plant['coarse_breakage_per_pass']*100:.0f}% coarse, {plant['intermediate_breakage_per_pass']*100:.0f}% intermediate · cyclone overflow {plant['cyclone_overflow_fraction']*100:.1f}% · circulating load {plant['circulating_load_ratio']*100:.0f}% · flotation residence {plant['flotation_residence_time_min']:.1f} min")
        with st.expander("Process PLC sequence · simulated equipment controls", expanded=True):
            st.caption("This teaching controller sequences process motors, checks utility permissives, and latches simulated equipment trips. Motor starts add a decaying electrical load to the mine grid, so large drives can disturb voltage, frequency, and reserves. These aggregate transients are illustrative, not a certified motor-start or protection study. The factory timeline must be running or single-stepped for sequences to progress.")
            st.metric("Motor acceleration load", f"{control['motor_starting_load_mw']:.2f} MW")
            physical_water_level = sim["water_network"]["nodes"]["process_tank"]["level_pct"]
            st.caption(f"PLC water-level permissive PV {control['process_water_level_feedback_pct']:.2f}% · physical tank level {physical_water_level:.2f}%")
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
            st.markdown("**Equipment condition and maintenance**")
            condition_rows = [{"Equipment": values[0],
                               "Condition": f"{plant['equipment_health'][name] * 100:.1f}%",
                               "Control state": control["equipment"][values[1]]["state"]}
                              for name, values in CONCENTRATOR_MODEL.MAINTAINABLE_EQUIPMENT.items()]
            st.dataframe(pd.DataFrame(condition_rows), hide_index=True, width="stretch")
            maintenance_options = list(CONCENTRATOR_MODEL.MAINTAINABLE_EQUIPMENT)
            maintenance_choice = st.selectbox(
                "Asset for maintenance",
                maintenance_options,
                format_func=lambda key: CONCENTRATOR_MODEL.MAINTAINABLE_EQUIPMENT[key][0],
                key=f"maintenance_asset_{sim['simulation_id']}")
            maintenance_label = CONCENTRATOR_MODEL.MAINTAINABLE_EQUIPMENT[maintenance_choice][0]
            maintenance_area = CONCENTRATOR_MODEL.MAINTENANCE_AREAS[maintenance_choice]
            maintenance_fault_active = any(event.name == "Mechanical degradation"
                and event.area in (maintenance_area, "Whole factory") for event in sim["events"])
            maintenance_stopped = (control["equipment"][maintenance_choice]["state"] == "STOPPED")
            if st.button("Perform maintenance", disabled=not maintenance_stopped or maintenance_fault_active,
                         key=f"perform_maintenance_{sim['simulation_id']}"):
                previous = maintain_process_equipment(sim, maintenance_label)
                st.success(f"{maintenance_label}: condition restored from {previous * 100:.1f}% to 100%.")
            if not maintenance_stopped:
                st.caption("Stop the process first. Maintenance is only available when the selected asset is stopped.")
            elif maintenance_fault_active:
                st.caption("Clear the active mechanical fault before maintaining this asset.")
            equipment_rows = [{"Equipment": PLANT_CONTROLLER.LABELS[name],
                               "State": item["state"], "Elapsed (s)": item["elapsed_s"],
                               "Trips": item["trips"], "Trip reason": item["trip_reason"] or "—"}
                              for name, item in control["equipment"].items()]
            st.dataframe(pd.DataFrame(equipment_rows), hide_index=True, width="stretch")
            st.caption("Permissive: " + control["permissive_reason"])
        st.caption(f"ROM stockpile {plant['rom_stockpile_t']:.0f} t · crushed ore {plant['crushed_stockpile_t']:.0f} t · SAG feed bin {plant['sag_bin_t']:.0f} t · tailings thickener {plant['tailings_thickener_inventory_t']:.0f} t solids + {plant['tailings_thickener_water_inventory_m3']:.0f} m³ water · curtailed mine feed {plant['rom_feed_curtailed_tph']:.1f} t/h · water shortage {plant['fresh_water_shortage_m3h']:.1f} m³/h")
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
                                      specs=[[{"secondary_y": True}], [{"secondary_y": True}]])
                trend.add_trace(go.Scatter(x=history.time, y=history.plant_feed_tph, name="Plant feed",
                                           line={"color": "#277da1"}), row=1, col=1)
                trend.add_trace(go.Scatter(x=history.time, y=history.copper_feed_grade_pct,
                                           name="Cu feed grade",
                                           line={"color": "#9b5de5", "dash": "dot"}),
                                row=1, col=1, secondary_y=True)
                trend.add_trace(go.Scatter(x=history.time, y=history.copper_recovery_pct, name="Cu recovery",
                                           line={"color": "#43aa8b"}), row=2, col=1, secondary_y=False)
                trend.add_trace(go.Scatter(x=history.time, y=history.p80_microns, name="Product P80",
                                           line={"color": "#f2a541"}), row=2, col=1, secondary_y=True)
                trend.update_yaxes(title_text="Feed (t/h)", row=1, col=1, secondary_y=False)
                trend.update_yaxes(title_text="Cu grade (%)", range=[0.2, 1.2],
                                   row=1, col=1, secondary_y=True)
                trend.update_yaxes(title_text="Recovery (%)", row=2, col=1, secondary_y=False)
                trend.update_yaxes(title_text="P80 (µm)", row=2, col=1, secondary_y=True)
                trend.update_xaxes(title_text="Simulated seconds", row=2, col=1)
                trend.update_layout(height=470, uirevision="concentrator-process-trend")
                st.plotly_chart(trend, width="stretch")
                flotation_balance = make_subplots(specs=[[{"secondary_y": True}]])
                flotation_balance.add_trace(go.Scatter(
                    x=history.time, y=history.flotation_copper_inventory_t,
                    name="Copper held in cells", line={"color": "#277da1"}),
                    secondary_y=False)
                flotation_balance.add_trace(go.Scatter(
                    x=history.time, y=history.copper_feed_grade_pct,
                    name="Feed grade", line={"color": "#9b5de5", "dash": "dot"}),
                    secondary_y=True)
                flotation_balance.update_yaxes(title_text="Copper inventory (t Cu)", secondary_y=False)
                flotation_balance.update_yaxes(title_text="Feed grade (% Cu)", secondary_y=True)
                flotation_balance.update_layout(height=300, title="Flotation metal holdup and feed grade",
                                                xaxis_title="Simulated seconds",
                                                uirevision="concentrator-flotation-metal-balance")
                st.plotly_chart(flotation_balance, width="stretch")
                size_trend = go.Figure()
                for key, label, color in (
                    ("grind_coarse_fraction", "Coarse", "#277da1"),
                    ("grind_intermediate_fraction", "Intermediate", "#f2a541"),
                    ("grind_fine_fraction", "Fine", "#43aa8b"),
                ):
                    size_trend.add_trace(go.Scatter(x=history.time, y=history[key] * 100,
                        mode="lines", name=label, line={"color": color}))
                size_trend.update_layout(height=300, title="Grinding size-class fractions",
                    xaxis_title="Simulated seconds", yaxis_title="Circuit feed (%)",
                    yaxis={"range": [0, 100]}, uirevision="grinding-size-class-trend")
                st.plotly_chart(size_trend, width="stretch")
            with process_right:
                trend = px.line(history, x="time", y=["rom_stockpile_t", "crushed_stockpile_t", "sag_bin_t",
                                                        "tailings_thickener_inventory_t",
                                                        "concentrate_thickener_inventory_t",
                                                        "concentrate_cake_inventory_t"],
                    labels={"time": "Simulated seconds", "value": "Inventory (t)", "variable": "Buffer"},
                    color_discrete_sequence=["#277da1", "#e07a5f", "#f2a541", "#43aa8b", "#9b5de5", "#d1495b"])
                trend.update_layout(uirevision="concentrator-inventory-trend")
                st.plotly_chart(trend, width="stretch")
                water_trend = px.line(history, x="time", y="tailings_thickener_water_inventory_m3",
                    labels={"time": "Simulated seconds", "tailings_thickener_water_inventory_m3": "Water held (m³)"},
                    color_discrete_sequence=["#277da1"])
                water_trend.update_layout(height=260, uirevision="tailings-thickener-water-trend",
                                          yaxis={"range": [0, plant["tailings_thickener_water_capacity_m3"]]})
                st.plotly_chart(water_trend, width="stretch")
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
        fleet_summary = HAULAGE_MODEL.summary(sim["haulage"])
        committed_ore_t = sum(
            truck["frame"]["payload_t"] for truck in sim["haulage"]["trucks"].values()
            if truck["frame"]["cycle_state"] in ("travel_loaded", "tip_queue", "dumping"))
        fleet_metrics = st.columns(5)
        fleet_metrics[0].metric("Haul delivery", f"{fleet_summary['delivery_rate_tph']:.0f} t/h")
        fleet_metrics[1].metric("Shovel queue", f"{fleet_summary['shovel_queue_length']} trucks")
        fleet_metrics[2].metric("Crusher queue", f"{fleet_summary['tip_queue_length']} trucks")
        fleet_metrics[3].metric("Committed ore", f"{committed_ore_t:.0f} t")
        fleet_metrics[4].metric("ROM stockpile", f"{sim['concentrator']['rom_stockpile_t']:.0f} t",
                                "dispatch held" if fleet_summary["rom_dispatch_paused"] else "dispatch ready")
        st.caption("Fleet GPS is streamed on the live map above. This view lists the vehicle process and instrumentation tags.")
        st.subheader("Vehicle telemetry")
        st.dataframe(fleet[~fleet.sensor.str.startswith("gps_")][["tag", "value", "units", "quality", "last_change"]],
                     hide_index=True, width="stretch")
        st.subheader("Building and site telemetry")
        building_state = sim["site_buildings"]
        zones = building_state["zones"]
        building_cols = st.columns(4)
        building_cols[0].metric("Outdoor", f"{building_state['ambient_temperature_c']:.1f} °C")
        building_cols[1].metric("Mean indoor", f"{sum(z['temperature_c'] for z in zones.values()) / len(zones):.1f} °C")
        building_cols[2].metric("Peak CO₂", f"{max(z['co2_ppm'] for z in zones.values()):.0f} ppm")
        building_cols[3].metric("HVAC load", f"{building_state['electric_load_mw'] * 1_000:.0f} kW")
        building_rows = [{"Building": name, "Temperature (°C)": round(zone["temperature_c"], 1),
                          "Humidity (%RH)": round(zone["humidity_pct"], 1),
                          "CO₂ (ppm)": round(zone["co2_ppm"]), "People": round(zone["occupancy_count"]),
                          "HVAC": zone["hvac_state"], "Filter ΔP (Pa)": round(zone["filter_dp_pa"]),
                          "Power (kW)": round(zone["electric_power_kw"], 1)}
                         for name, zone in zones.items()]
        st.dataframe(pd.DataFrame(building_rows), hide_index=True, width="stretch")
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
        st.caption("Grid import, solar, diesel reserve, and battery storage supply the mine. Fault current comes from a three-phase Thevenin source plus feeder impedance and fault location. Standard-inverse feeder relays coordinate with a slower distribution-incomer backup; a failed feeder breaker can expose the backup trip. This compact model does not replace an AC short-circuit or protection-coordination study.")
        metrics = st.columns(7)
        for col, label, value in zip(metrics,
            ["Mine bus frequency", "Run frequency nadir", "Lowest feeder voltage", "Grid import", "Diesel generation", "Battery reserve", "Distribution losses"],
            [f"{power['frequency_hz']:.2f} Hz", f"{power['frequency_nadir_hz']:.2f} Hz", f"{power['voltage_pu']:.3f} pu", f"{power['grid_import_mw']:.1f} MW",
             f"{power['diesel_mw']:.1f} MW", f"{power['battery_soc_pct']:.1f}%", f"{power['distribution_losses_mw']:.2f} MW"]):
            col.metric(label, value)
        st.caption(f"Solar {power['solar_mw']:.1f} MW · battery {power['battery_power_mw']:+.1f} MW (positive = discharging) · requested/served plant load {power['requested_load_mw']:.1f}/{power['served_load_mw']:.1f} MW · shed {power['load_shed_mw']:.1f} MW · factory utilities {power['factory_utilities_load_mw']:.2f}/{power['factory_utilities_requested_mw']:.2f} MW ({power['factory_utilities_power_fraction']*100:.0f}%) · process/water feeder limits {power['process_feeder_capacity_mw']:.1f}/{power['water_feeder_capacity_mw']:.1f} MW")

        nodes = {
            "grid": (.08, .78, f"Transmission import<br>{power['grid_import_mw']:.1f} / {power['tie_capacity_mw']:.1f} MW"),
            "solar": (.08, .55, f"Solar<br>{power['solar_mw']:.1f} MW"),
            "diesel": (.08, .32, f"Diesel reserve<br>{power['diesel_mw']:.1f} / {power['diesel_available_mw']:.1f} MW"),
            "battery": (.08, .09, f"Battery<br>{power['battery_power_mw']:+.1f} MW · {power['battery_soc_pct']:.0f}%"),
            "bus": (.50, .48, f"Mine bus<br>{power['frequency_hz']:.2f} Hz · {power['voltage_pu']:.3f} pu"),
            "site": (.91, .78, f"Site auxiliaries<br>{power['site_load_mw']:.1f} MW"),
            "water": (.91, .62, f"Water pumps<br>{power['water_load_mw']:.2f} MW · {power['water_power_fraction']*100:.0f}%"),
            "utilities": (.91, .39, f"Factory utilities<br>{power['factory_utilities_load_mw']:.2f} MW · {power['factory_utilities_power_fraction']*100:.0f}%"),
            "process": (.91, .16, f"Concentrator<br>{power['process_load_mw']:.1f} MW · {power['process_power_fraction']*100:.0f}%"),
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
            ("utilities", power["factory_utilities_load_mw"], "#9b5de5"),
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
        incomer = power["distribution_incomer"]
        st.caption(f"Distribution incomer: {'CLOSED' if incomer['breaker_closed'] else 'LOCKED OUT'} · backup relay {incomer['relay_current_multiple']:.2f}× pickup · timer {incomer['relay_timer_s']:.0f}/{incomer['relay_time_to_trip_s']:.1f} s · trips {incomer['trip_count']}")
        if incomer["lockout"]:
            fault_active = any(event.name == "Electrical fault" for event in sim["events"])
            if st.button("Reset Distribution incomer lockout", disabled=fault_active,
                         key="reset_distribution_incomer"):
                reset_feeder_protection(sim, "Distribution incomer")
                st.success("Distribution incomer reclosed; trip history is retained.")
                st.rerun(scope="fragment")
        feeder_rows = [{"Feeder": name.title(), "Breaker": "Closed" if values["breaker_closed"] else "Open",
            "Energized": values["energized"], "Fault current (A)": values["fault_current_a"],
            "Fault location (%)": values["fault_location_pct"],
            "Relay pickup": values["relay_pickup"], "I / pickup": values["relay_current_multiple"],
            "Relay timer (s)": values["relay_timer_s"], "Relay operations": values["relay_operation_count"],
            "Breaker failures": values["breaker_failure_count"], "Trips": values["trip_count"],
            "Recloses": values["reclose_count"], "Voltage (pu)": values["voltage_pu"],
            "Current (A)": values["current_a"], "Loading (%)": values["loading_pct"],
            "P (MW)": values["active_power_mw"], "Q (MVAr)": values["reactive_power_mvar"],
            "Loss (MW)": values["loss_mw"]}
            for name, values in power["distribution_feeders"].items()]
        st.dataframe(pd.DataFrame(feeder_rows).round(2), hide_index=True, width="stretch")
        locked = [(name, values) for name, values in power["distribution_feeders"].items()
                  if values["lockout"]]
        for feeder_name, _values in locked:
            target_asset = f"Feeder {feeder_name}"
            fault_active = any(event.name == "Electrical fault" and event.target_asset == target_asset
                               for event in sim["events"])
            if st.button(f"Reset {target_asset} lockout", disabled=fault_active,
                         key=f"reset_protection_{feeder_name}"):
                reset_feeder_protection(sim, target_asset)
                st.success(f"{target_asset} reclosed. Relay trip history is retained.")
                st.rerun(scope="fragment")
        history = pd.DataFrame(power["history"])
        if not history.empty:
            if "frequency_window_nadir_hz" not in history:
                history["frequency_window_nadir_hz"] = history["frequency_hz"]
            left, right = st.columns(2)
            with left:
                trend = px.line(history, x="sim_seconds", y=["frequency_hz", "frequency_window_nadir_hz"],
                    labels={"sim_seconds": "Simulated seconds", "value": "Frequency (Hz)",
                            "variable": "Measurement"},
                    color_discrete_sequence=["#277da1", "#e07a5f"])
                trend.add_hline(y=49.5, line_dash="dash", line_color="#d1495b", annotation_text="Underfrequency alarm")
                trend.update_layout(uirevision="power-frequency-trend")
                st.plotly_chart(trend, width="stretch")
            with right:
                trend = px.line(history, x="sim_seconds", y=["grid_import_mw", "solar_mw", "diesel_mw", "battery_soc_pct"],
                    labels={"sim_seconds": "Simulated seconds", "value": "MW / battery %", "variable": "Source"},
                    color_discrete_sequence=["#277da1", "#f2a541", "#e07a5f", "#9b5de5"])
                trend.update_layout(uirevision="power-dispatch-trend")
                st.plotly_chart(trend, width="stretch")
            thermal = go.Figure()
            for name, label, color in (
                ("mine_a", "Substation A oil", "#277da1"),
                ("mine_a", "Substation A winding hot spot", "#43aa8b"),
                ("mine_b", "Substation B oil", "#f2a541"),
                ("mine_b", "Substation B winding hot spot", "#d1495b"),
            ):
                suffix = "oil_c" if label.endswith("oil") else "hotspot_c"
                thermal.add_trace(go.Scatter(x=history["sim_seconds"], y=history[f"{name}_{suffix}"],
                    mode="lines", name=label, line={"color": color}))
            thermal.add_hline(y=110, line_dash="dash", line_color="#6c757d",
                              annotation_text="Thermal derating begins")
            thermal.update_layout(height=300, title="Transformer thermal response",
                xaxis_title="Simulated seconds", yaxis_title="Temperature (°C)",
                uirevision="transformer-thermal-trend")
            st.plotly_chart(thermal, width="stretch")
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

    with tabs[9]:
        utilities = sim["plant_utilities"]
        chilled = sim["chilled_water"]
        st.subheader("Factory utilities · steam, air, and chilled water")
        st.caption("Boilers feed a finite steam header and delayed condensate return; compressors feed a finite receiver and ring main. Two chillers supply the pasteurizer product cooler, and a finite cold-water reserve delays production effects after a chiller trip. If cooler duty falls short, product is diverted before the balance tank; the filler then slows as its finite inventory drains. CIP sequences water, caustic, heated sanitation and release steps; water and steam scarcity hold cleaning phases and temporarily remove the selected pasteurizer from production.")
        chilled_metrics = st.columns(5)
        for col, label, value in zip(chilled_metrics,
            ["Cooling service", "Cooling duty", "Cold reserve", "Supply / return", "Chiller COP"],
            [f"{chilled['cooling_service_fraction']*100:.1f}%",
             f"{chilled['cooling_served_kw']:.0f}/{chilled['cooling_demand_kw']:.0f} kW",
             f"{chilled['storage_mwh']:.2f}/{chilled['storage_capacity_mwh']:.2f} MWh",
             f"{chilled['supply_temperature_c']:.1f}/{chilled['return_temperature_c']:.1f} °C",
             f"{chilled['cop']:.2f}"]):
            col.metric(label, value)
        chiller_rows = [{"Chiller": name.replace("_", " ").title(),
                         "Output (kW)": unit["output_kw"],
                         "Available (kW)": unit["available_capacity_kw"],
                         "Vibration (mm/s)": unit["vibration_mm_s"],
                         "Running": unit["running"]}
                        for name, unit in chilled["chillers"].items()]
        st.dataframe(pd.DataFrame(chiller_rows).round(2), hide_index=True, width="stretch")
        chilled_history = pd.DataFrame(chilled["history"])
        if not chilled_history.empty:
            cooling_fig = px.line(chilled_history, x="sim_seconds",
                y=["cooling_demand_kw", "cooling_served_kw"],
                color_discrete_sequence=["#277da1", "#e07a5f"],
                labels={"sim_seconds": "Simulated seconds", "value": "Cooling load (kW)",
                        "variable": "Duty"})
            cooling_fig.update_layout(title="Product cooler duty and chilled-water supply",
                uirevision="chilled-water-duty")
            st.plotly_chart(cooling_fig, width="stretch")
        utility_metrics = st.columns(6)
        for col, label, value in zip(utility_metrics,
            ["Steam header", "Steam service", "Boiler output", "Instrument air", "Air service", "Compressor power"],
            [f"{utilities['steam_header_pressure_bar']:.2f} bar",
             f"{utilities['steam_service_fraction']*100:.0f}%",
             f"{utilities['steam_generation_tph']:.1f}/{utilities['steam_demand_tph']:.1f} t/h",
             f"{utilities['air_receiver_pressure_bar']:.2f} bar",
             f"{utilities['air_service_fraction']*100:.0f}%",
             f"{utilities['electric_load_mw']:.2f} MW"]):
            col.metric(label, value)
        st.caption(f"Air supply/demand {utilities['air_supply_nm3_min']:.1f}/{utilities['air_demand_nm3_min']:.1f} Nm³/min · dryer dew point {utilities['dryer_dew_point_c']:.1f} °C · condensate return {utilities['condensate_return_tph']:.1f} t/h · feedwater makeup {utilities['feedwater_makeup_tph']:.1f} t/h")
        st.caption(f"Product balance tank {utilities['product_tank_inventory_units']:.0f}/{UTILITY_MODEL.PRODUCT_TANK_CAPACITY_UNITS:.0f} units · filling accumulation {utilities['filling_buffer_inventory_units']:.0f}/{UTILITY_MODEL.PACKAGE_BUFFER_CAPACITY_UNITS:.0f} units · approved line rate {utilities['packer_rate_cases_min']:.1f} cases/min · finished cases {utilities['finished_cases_total']:.0f}")
        cip = utilities["cip"]
        cip_status = (f"CLEANING {cip['target_unit'].replace('_', ' ').title()} · {cip['phase']} · {cip['phase_progress_pct']:.0f}%"
                      if cip["active"] else "IDLE · available")
        st.caption(f"CIP status: {cip_status} · return {cip['return_ph']:.1f} pH / {cip['return_conductivity_us_cm']:.0f} µS/cm · cycle count {cip['cycles_completed']} · CIP water {utilities['cip_water_demand_m3h']:.1f} m³/h · wastewater {utilities['cip_effluent_m3h']:.1f} m³/h")
        cip_columns = st.columns(3)
        cip_target = cip_columns[0].selectbox("Pasteurizer to clean", ["pasteurizer_01", "pasteurizer_02"],
                                               format_func=lambda unit: unit.replace("_", " ").title(), key="cip_target_unit")
        if cip_columns[1].button("Start CIP cycle", disabled=cip["active"], key="start_cip_cycle"):
            if UTILITY_MODEL.CIP_MODEL.start(cip, cip_target, sim["tick"]):
                st.rerun(scope="fragment")
            else:
                st.warning(cip["start_block_reason"])
        cip_columns[2].metric("CIP heat", f"{cip['temperature_c']:.1f} °C")
        equipment_rows = [
            {"Equipment": "Boiler 01", "Output": f"{utilities['boilers']['boiler_01']['output_tph']:.1f} t/h", "Availability": f"{utilities['boilers']['boiler_01']['availability']*100:.0f}%", "Condition": f"{utilities['boilers']['boiler_01']['vibration_mm_s']:.1f} mm/s"},
            {"Equipment": "Boiler 02", "Output": f"{utilities['boilers']['boiler_02']['output_tph']:.1f} t/h", "Availability": f"{utilities['boilers']['boiler_02']['availability']*100:.0f}%", "Condition": f"{utilities['boilers']['boiler_02']['vibration_mm_s']:.1f} mm/s"},
            {"Equipment": "Compressor A", "Output": f"{utilities['compressors']['compressor_a']['output_nm3_min']:.1f} Nm³/min", "Availability": f"{utilities['compressors']['compressor_a']['availability']*100:.0f}%", "Condition": f"{utilities['compressors']['compressor_a']['vibration_mm_s']:.1f} mm/s"},
            {"Equipment": "Compressor B", "Output": f"{utilities['compressors']['compressor_b']['output_nm3_min']:.1f} Nm³/min", "Availability": f"{utilities['compressors']['compressor_b']['availability']*100:.0f}%", "Condition": f"{utilities['compressors']['compressor_b']['vibration_mm_s']:.1f} mm/s"},
            {"Equipment": "Pasteurizer 01", "Output": f"{utilities['pasteurizers']['pasteurizer_01']['production_rate']:.1f} units/min", "Availability": f"{utilities['steam_service_fraction']*100:.0f}% steam service", "Condition": f"{utilities['pasteurizers']['pasteurizer_01']['temperature_c']:.1f} °C"},
            {"Equipment": "Filler 01", "Output": f"{utilities['filler_rate_units_min']:.1f} units/min", "Availability": f"{utilities['filling_air_service']*100:.0f}% air service", "Condition": f"{utilities['air_receiver_pressure_bar']:.2f} bar air"},
            {"Equipment": "Product balance tank", "Output": f"{utilities['product_tank_inventory_units']:.0f} units", "Availability": "Finite buffer", "Condition": f"{100*utilities['product_tank_inventory_units']/UTILITY_MODEL.PRODUCT_TANK_CAPACITY_UNITS:.0f}% full"},
            {"Equipment": "Case packer A", "Output": f"{utilities['packer_rate_cases_min']:.1f} cases/min", "Availability": f"{utilities['case_packer_speed_fraction']*100:.0f}% drive speed", "Condition": f"{utilities['filling_buffer_inventory_units']:.0f} units buffered"},
        ]
        st.dataframe(pd.DataFrame(equipment_rows), hide_index=True, width="stretch")
        history = pd.DataFrame(utilities["history"])
        if history.empty:
            st.info("Start or single-step the factory to build utility pressure and process-response trends.")
        else:
            left, right = st.columns(2)
            with left:
                fig = px.line(history, x="sim_seconds", y="steam_header_pressure_bar",
                    labels={"sim_seconds": "Simulated seconds", "steam_header_pressure_bar": "Steam pressure (bar)"},
                    color_discrete_sequence=["#e07a5f"])
                fig.add_hline(y=6.5, line_dash="dash", line_color="#6c757d", annotation_text="Low-pressure alarm")
                fig.update_layout(title="Steam header pressure", uirevision="steam-header-trend")
                st.plotly_chart(fig, width="stretch")
                fig = px.line(history, x="sim_seconds", y=["steam_generation_tph", "steam_demand_tph"],
                    labels={"sim_seconds": "Simulated seconds", "value": "Flow (t/h)", "variable": "Steam"},
                    color_discrete_sequence=["#277da1", "#f2a541"])
                fig.update_layout(title="Steam production and demand", uirevision="steam-balance-trend")
                st.plotly_chart(fig, width="stretch")
                fig = px.line(history, x="sim_seconds",
                    y=["product_tank_inventory_units", "filling_buffer_inventory_units"],
                    labels={"sim_seconds": "Simulated seconds", "value": "Buffered product (units)", "variable": "Location"},
                    color_discrete_sequence=["#577590", "#f9844a"])
                fig.update_layout(title="Finite production-line buffers", uirevision="production-buffer-trend")
                st.plotly_chart(fig, width="stretch")
            with right:
                fig = px.line(history, x="sim_seconds", y="air_receiver_pressure_bar",
                    labels={"sim_seconds": "Simulated seconds", "air_receiver_pressure_bar": "Receiver pressure (bar)"},
                    color_discrete_sequence=["#43aa8b"])
                fig.add_hline(y=5.8, line_dash="dash", line_color="#6c757d", annotation_text="Low-air alarm")
                fig.update_layout(title="Instrument-air receiver and ring main", uirevision="air-receiver-trend")
                st.plotly_chart(fig, width="stretch")
                fig = px.line(history, x="sim_seconds", y=["air_supply_nm3_min", "air_demand_nm3_min"],
                    labels={"sim_seconds": "Simulated seconds", "value": "Flow (Nm³/min)", "variable": "Air"},
                    color_discrete_sequence=["#9b5de5", "#277da1"])
                fig.update_layout(title="Instrument-air production and demand", uirevision="air-balance-trend")
                st.plotly_chart(fig, width="stretch")
        st.markdown("**Fault drills:** run `boiler-header-pressure-and-recovery` or inject Utility loss in Boiler house. Compare header pressure with the slower pasteurizer temperature response and diverted-product rate. Run `compressed-air-receiver-and-recovery` and follow receiver pressure, dryer dew point, filler rate, and case-packer rate. Run `packaging-jam-buffer-and-recovery` to watch the conveyor buffer fill, filler slow, pasteurized-product tank fill, and both buffers drain after the jam clears.")

    with tabs[10]:
        ventilation = sim["mine_ventilation"]
        st.subheader("Underground mine · primary ventilation and district air quality")
        st.caption("Parallel exhaust fans operate against a shared airway network. Airflow divides across the two decline districts and ore pass according to branch resistance. Gas, diesel exhaust, dust, oxygen, and heat respond through finite district volumes, so their trends lag fan and airflow changes.")
        peak_methane = max(zone["methane_pct"] for zone in ventilation["branches"].values())
        min_oxygen = min(zone["oxygen_pct"] for zone in ventilation["branches"].values())
        metric_cols = st.columns(5)
        for col, label, value in zip(metric_cols,
            ["Total airflow", "Required flow", "Main airway pressure", "Peak methane", "Fan power"],
            [f"{ventilation['total_airflow_m3s']:.1f} m³/s",
             f"{ventilation['required_airflow_m3s']:.0f} m³/s",
             f"{ventilation['network_pressure_pa'] / 1000:.2f} kPa",
             f"{peak_methane:.3f}% vol", f"{ventilation['fan_power_kw']:.0f} kW"]):
            col.metric(label, value)
        fan_rows = [{"Fan": name, "Running": fan["running"],
                     "Speed (%)": fan["speed_pct"], "Flow (m³/s)": fan["flow_m3s"],
                     "Power (kW)": fan["power_kw"], "Condition (%)": 100 * fan["condition"],
                     "Starts": fan["starts"]} for name, fan in ventilation["fans"].items()]
        st.dataframe(pd.DataFrame(fan_rows).round(2), hide_index=True, width="stretch")
        district_rows = [{"District": name, "Airflow (m³/s)": zone["airflow_m3s"],
                          "Airway pressure (Pa)": zone["pressure_pa"],
                          "Methane (% vol)": zone["methane_pct"], "NOx (ppm)": zone["nox_ppm"],
                          "Oxygen (% vol)": zone["oxygen_pct"], "Dust (mg/m³)": zone["dust_mg_m3"],
                          "Air temperature (°C)": zone["temperature_c"]}
                         for name, zone in ventilation["branches"].items()]
        st.dataframe(pd.DataFrame(district_rows).round(3), hide_index=True, width="stretch")
        history = pd.DataFrame(ventilation["history"])
        if history.empty:
            st.info("Start or single-step the factory to build ventilation trends.")
        else:
            left, right = st.columns(2)
            with left:
                airflow = px.line(history, x="sim_seconds", y="airflow_m3s",
                    labels={"sim_seconds": "Simulated seconds", "airflow_m3s": "Airflow (m³/s)"},
                    color_discrete_sequence=["#277da1"])
                airflow.add_hline(y=ventilation["required_airflow_m3s"], line_dash="dash",
                                  line_color="#d1495b", annotation_text="Requirement")
                airflow.update_layout(title="Mine ventilation delivery", uirevision="mine-airflow")
                st.plotly_chart(airflow, width="stretch")
            with right:
                gas = px.line(history, x="sim_seconds", y="methane_peak_pct",
                    labels={"sim_seconds": "Simulated seconds", "methane_peak_pct": "Methane (% vol)"},
                    color_discrete_sequence=["#e07a5f"])
                gas.add_hline(y=1.0, line_dash="dash", line_color="#6c757d",
                              annotation_text="Exercise alarm threshold")
                gas.update_layout(title="Peak district methane", uirevision="mine-methane")
                st.plotly_chart(gas, width="stretch")
        st.caption(f"Minimum district oxygen {min_oxygen:.2f}% vol · electrical demand is included in the mine process load. Exercise alarm limits are illustrative, not safety guidance.")
        st.markdown("**Fault drill:** run `underground-ventilation-loss-and-recovery`. Confirm airflow alarms first; then compare fan restoration with delayed gas, oxygen, dust, and thermal recovery.")



render_dashboard()
