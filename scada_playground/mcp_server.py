"""MCP adapter for local synthetic factory reads and simulation-only controls.

The default stdio transport is for MCP hosts that launch a local subprocess.
Use ``--transport streamable-http`` to expose the same tools at localhost:8503.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
import uuid
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from protocol_faults import FAULT_MODES, PROTOCOLS
from scenarios import SCENARIOS


ROOT = Path(__file__).resolve().parent
SNAPSHOT = Path(os.environ.get("SCADA_SNAPSHOT_PATH", ROOT / ".runtime" / "factory_snapshot.json"))
COMMAND_DIR = Path(os.environ.get("SCADA_COMMAND_DIR", SNAPSHOT.parent / "mcp_commands"))
mcp = FastMCP(
    "SCADA Factory Playground",
    instructions=(
        "Access to a synthetic, local multi-industry SCADA playground. "
        "Values and incidents are simulated. Always inspect quality and timestamp "
        "before interpreting measurements. Simulation controls affect only this "
        "toy process and can never control physical equipment."
    ),
    port=8503,
)


def snapshot() -> dict[str, Any]:
    """Read one complete atomic snapshot written by the simulator UI."""
    try:
        return json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError("Simulator snapshot is missing. Start ./start_playground.sh first.") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Simulator snapshot cannot be read: {exc}") from exc


def normalized(value: str) -> str:
    return value.strip().casefold()


def tag_record(tag: dict[str, Any], timestamp: str) -> dict[str, Any]:
    return {
        "tag": tag["tag"],
        "area": tag["area"],
        "asset": tag["asset"],
        "sensor": tag["sensor"],
        "value": tag["value"],
        "units": tag["units"],
        "quality": tag["quality"],
        "low_limit": tag.get("low"),
        "high_limit": tag.get("high"),
        "last_change": tag.get("last_change"),
        "timestamp": timestamp,
        "source": "synthetic-simulator",
    }


@mcp.tool()
def get_plant_status() -> dict[str, Any]:
    """Get simulator run state, simulation clock, snapshot time, and counts."""
    data = snapshot()
    qualities: dict[str, int] = {}
    for tag in data["tags"]:
        qualities[tag["quality"]] = qualities.get(tag["quality"], 0) + 1
    return {
        "simulated": True,
        "running": data["running"],
        "sim_seconds": data["sim_seconds"],
        "snapshot_timestamp": data["timestamp"],
        "area_count": len({tag["area"] for tag in data["tags"]}),
        "asset_count": len({(tag["area"], tag["asset"]) for tag in data["tags"]}),
        "tag_count": len(data["tags"]),
        "quality_counts": qualities,
        "alarm_count": len(data["alarms"]),
        "write_scope": "simulation controls only",
        "active_scenario": data.get("scenario_run"),
        "active_protocol_fault_count": len(data.get("protocol_faults", [])),
    }


@mcp.tool()
def list_systems() -> list[dict[str, Any]]:
    """List plant areas and asset/tag counts, useful before searching tags."""
    data = snapshot()
    groups: dict[str, dict[str, Any]] = {}
    for tag in data["tags"]:
        area = groups.setdefault(tag["area"], {"area": tag["area"], "assets": set(), "tag_count": 0})
        area["assets"].add(tag["asset"])
        area["tag_count"] += 1
    return [{"area": area["area"], "asset_count": len(area["assets"]), "tag_count": area["tag_count"]}
            for area in sorted(groups.values(), key=lambda row: row["area"])]


@mcp.tool()
def list_scenarios() -> dict[str, Any]:
    """List valid simulator incident types, severity levels, and target areas."""
    data = snapshot()
    return {"simulated_only": True, "incident_types": data.get("available_incidents", []),
            "severity_levels": ["Small", "Medium", "Large"],
            "target_areas": data.get("available_areas", []),
            "scenarios": data.get("available_scenarios", [])}


@mcp.tool()
def search_tags(query: str = "", area: str = "", asset: str = "", quality: str = "",
               limit: int = 100, offset: int = 0) -> dict[str, Any]:
    """Find tags by substring and optional area, asset, or quality; supports pagination."""
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    if offset < 0:
        raise ValueError("offset must be zero or greater")
    data = snapshot()
    q, area_q, asset_q, quality_q = map(normalized, (query, area, asset, quality))
    matched = [t for t in data["tags"]
               if q in normalized(" ".join((t["tag"], t["sensor"], t["asset"], t["area"])))
               and area_q in normalized(t["area"])
               and asset_q in normalized(t["asset"])
               and (not quality_q or quality_q == normalized(t["quality"]))]
    return {"timestamp": data["timestamp"], "total": len(matched), "offset": offset,
            "limit": limit, "tags": [tag_record(t, data["timestamp"]) for t in matched[offset:offset + limit]]}


@mcp.tool()
def read_tags(tag_names: list[str]) -> dict[str, Any]:
    """Read exact tag IDs in one snapshot, returning value, units, quality, and timestamp."""
    if not tag_names:
        raise ValueError("Provide at least one exact tag ID")
    if len(tag_names) > 200:
        raise ValueError("Read at most 200 tags per request")
    data = snapshot()
    by_name = {tag["tag"].casefold(): tag for tag in data["tags"]}
    values = [tag_record(by_name[name.casefold()], data["timestamp"])
              for name in tag_names if name.casefold() in by_name]
    missing = [name for name in tag_names if name.casefold() not in by_name]
    return {"snapshot_timestamp": data["timestamp"], "sim_seconds": data["sim_seconds"],
            "values": values, "not_found": missing}


@mcp.tool()
def get_alarms(state: str = "", severity: str = "", limit: int = 100) -> dict[str, Any]:
    """Read alarm records, optionally filtering by state or severity."""
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    data = snapshot()
    state_q, severity_q = normalized(state), normalized(severity)
    alarms = [alarm for alarm in data["alarms"]
              if (not state_q or state_q in normalized(alarm.get("state", "")))
              and (not severity_q or severity_q in normalized(alarm.get("severity", "")))]
    return {"snapshot_timestamp": data["timestamp"], "total": len(alarms), "alarms": alarms[:limit]}


@mcp.tool()
def get_fleet_positions(vehicle_type: str = "") -> dict[str, Any]:
    """Read current synthetic vehicle positions, route state, speed, and payload."""
    data = snapshot()
    type_q = normalized(vehicle_type)
    fleet = [v for v in data["fleet"] if type_q in normalized(v["vehicle_type"])]
    return {"snapshot_timestamp": data["timestamp"], "sim_seconds": data["sim_seconds"],
            "simulated": True, "vehicles": fleet}


@mcp.tool()
def get_haulage_state() -> dict[str, Any]:
    """Read truck-cycle states, ROM delivery rate, payloads, fuel, and fleet quality tags."""
    data = snapshot()
    haulage = data.get("haulage")
    if not haulage:
        raise RuntimeError("Haulage model is absent; restart the playground to load its current snapshot schema")
    fleet = [vehicle for vehicle in data["fleet"] if vehicle["vehicle_type"] == "Haul truck"]
    readings = [tag_record(tag, data["timestamp"]) for tag in data["tags"]
                if tag["area"] == "Mobile fleet" and tag["asset"].startswith("Haul truck")]
    return {"simulated": True, "snapshot_timestamp": data["timestamp"],
            "sim_seconds": data["sim_seconds"], "running": data["running"],
            **haulage, "trucks": fleet, "readings": readings}


@mcp.tool()
def get_water_network_state() -> dict[str, Any]:
    """Read mine-water tank inventories, link flows/pressure, and process demand."""
    data = snapshot()
    network = data.get("water_network")
    if not network:
        raise RuntimeError("Mine water network is absent; restart the playground to load its current snapshot schema")
    return {"simulated": True, "snapshot_timestamp": data["timestamp"],
            "sim_seconds": data["sim_seconds"], "running": data["running"], **network}


@mcp.tool()
def get_concentrator_state() -> dict[str, Any]:
    """Read ore buffers, mill limits, grind size, flotation recovery, water use, and power."""
    data = snapshot()
    concentrator = data.get("concentrator")
    if not concentrator:
        raise RuntimeError("Concentrator model is absent; restart the playground to load its current snapshot schema")
    return {"simulated": True, "snapshot_timestamp": data["timestamp"],
            "sim_seconds": data["sim_seconds"], "running": data["running"], **concentrator}


@mcp.tool()
def get_process_control_state() -> dict[str, Any]:
    """Read the simulated concentrator PLC sequence, equipment states, permissive, and latched trips."""
    data = snapshot()
    control = data.get("process_control")
    if not control:
        raise RuntimeError("Process controller is absent; restart the playground to load its current snapshot schema")
    return {"simulated": True, "snapshot_timestamp": data["timestamp"],
            "sim_seconds": data["sim_seconds"], "running": data["running"], **control}


@mcp.tool()
def get_power_network_state() -> dict[str, Any]:
    """Read grid import, generation, storage, frequency, voltage, and feeder capacity."""
    data = snapshot()
    power = data.get("power_network")
    if not power:
        raise RuntimeError("Power network model is absent; restart the playground to load its current snapshot schema")
    return {"simulated": True, "snapshot_timestamp": data["timestamp"],
            "sim_seconds": data["sim_seconds"], "running": data["running"], **power}


@mcp.tool()
def get_plant_utilities_state() -> dict[str, Any]:
    """Read steam generation/header pressure and compressed-air storage state."""
    data = snapshot()
    utilities = data.get("plant_utilities")
    if not utilities:
        raise RuntimeError("Plant utility model is absent; restart the playground to load its current snapshot schema")
    return {"simulated": True, "snapshot_timestamp": data["timestamp"],
            "sim_seconds": data["sim_seconds"], "running": data["running"], **utilities}


@mcp.tool()
def list_protocol_faults() -> dict[str, Any]:
    """List active protocol-output faults and their simulation-time expiry."""
    data = snapshot()
    return {"snapshot_timestamp": data["timestamp"], "sim_seconds": data["sim_seconds"],
            "faults": data.get("protocol_faults", [])}


def submit_command(command: dict[str, Any]) -> dict[str, Any]:
    """Submit a validated simulation-only command and await simulator response."""
    command_id = uuid.uuid4().hex
    COMMAND_DIR.mkdir(parents=True, exist_ok=True)
    command_path = COMMAND_DIR / f"{command_id}.json"
    temporary_path = command_path.with_suffix(".tmp")
    temporary_path.write_text(json.dumps({"id": command_id, **command}), encoding="utf-8")
    temporary_path.replace(command_path)
    result_path = COMMAND_DIR / f"{command_id}.result.json"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if result_path.exists():
            try:
                return json.loads(result_path.read_text(encoding="utf-8"))
            finally:
                result_path.unlink(missing_ok=True)
        time.sleep(0.1)
    command_path.unlink(missing_ok=True)
    raise RuntimeError("Simulator did not process the command within 20 seconds. Is its dashboard open?")


@mcp.tool()
def control_simulation(action: Literal["start", "stop", "reset", "acknowledge_alarms", "inject_incident"],
                       incident_name: str = "", severity: Literal["Small", "Medium", "Large"] = "Small",
                       target_area: str = "Whole factory", duration_seconds: int = 0) -> dict[str, Any]:
    """Control only the synthetic simulator: start, stop, reset, acknowledge alarms, or inject_incident.

    For incident injection, pass a known incident_name, severity Small/Medium/Large,
    and target_area (an area name or Whole factory). No physical device can be controlled.
    """
    allowed = {"start", "stop", "reset", "acknowledge_alarms", "inject_incident"}
    if action not in allowed:
        raise ValueError(f"action must be one of: {', '.join(sorted(allowed))}")
    if action == "inject_incident":
        if severity not in {"Small", "Medium", "Large"}:
            raise ValueError("severity must be Small, Medium, or Large")
        if not incident_name.strip() or not target_area.strip():
            raise ValueError("incident_name and target_area are required for incident injection")
        data = snapshot()
        if incident_name not in data.get("available_incidents", []):
            raise ValueError("Unknown incident_name; use one of the simulator's configured incident types")
        if target_area not in data.get("available_areas", []):
            raise ValueError("Unknown target_area; use a configured plant area or Whole factory")
        if duration_seconds < 0 or duration_seconds > 86_400:
            raise ValueError("duration_seconds must be 0 (severity default) or between 1 and 86,400")
    return submit_command({"action": action, "incident_name": incident_name,
                           "severity": severity, "target_area": target_area,
                           "duration_seconds": duration_seconds})


@mcp.tool()
def control_process(process_action: Literal["START", "STOP", "RESET_TRIPS"]) -> dict[str, Any]:
    """Request orderly simulated concentrator START/STOP or reset trips after their causes clear.

    The command affects only the local synthetic process model. START/STOP sequences
    advance when the factory timeline is running or stepped. Trip reset never restarts
    equipment automatically; inspect state and request START separately.
    """
    if process_action not in {"START", "STOP", "RESET_TRIPS"}:
        raise ValueError("process_action must be START, STOP, or RESET_TRIPS")
    return submit_command({"action": "process_control", "process_action": process_action})


@mcp.tool()
def run_scenario(scenario_id: str, seed: int = 17) -> dict[str, Any]:
    """Reset and run a named deterministic scenario from its start; same seed replays the same timeline."""
    if scenario_id not in SCENARIOS:
        raise ValueError(f"scenario_id must be one of: {', '.join(SCENARIOS)}")
    if not 0 <= seed <= 2**32 - 1:
        raise ValueError("seed must be between 0 and 4,294,967,295")
    return submit_command({"action": "run_scenario", "scenario_id": scenario_id, "seed": seed})


@mcp.tool()
def step_simulation(seconds: int = 1) -> dict[str, Any]:
    """Advance a paused simulation by exactly 1-300 simulated seconds."""
    if not 1 <= seconds <= 300:
        raise ValueError("seconds must be between 1 and 300")
    return submit_command({"action": "step", "seconds": seconds})


@mcp.tool()
def inject_protocol_fault(protocol: Literal["all", "Modbus TCP", "OPC UA", "MQTT"],
                          mode: Literal["stale", "bad", "freeze", "suppress"],
                          selector_type: Literal["area", "tag"], target: str,
                          duration_seconds: int = 0) -> dict[str, Any]:
    """Alter only a selected protocol's telemetry output, never simulator process state.

    Modes: stale/bad override quality; freeze holds a GOOD-quality value. suppress omits
    MQTT tags, while fixed-address Modbus and stable-node OPC UA report BAD quality.
    Duration 0 means indefinite until cleared; other durations use simulation seconds.
    """
    if protocol not in PROTOCOLS or mode not in FAULT_MODES:
        raise ValueError("Unsupported protocol or fault mode")
    if selector_type not in {"area", "tag"}:
        raise ValueError("selector_type must be area or tag")
    if not 0 <= duration_seconds <= 86_400:
        raise ValueError("duration_seconds must be 0 or between 1 and 86,400")
    data = snapshot()
    target_key = target.strip().casefold()
    matches = [tag for tag in data["tags"]
               if (tag["tag"].casefold() == target_key if selector_type == "tag"
                   else tag["area"].casefold() == target_key)]
    if not matches:
        raise ValueError(f"No {selector_type} matches {target!r}")
    return submit_command({"action": "inject_protocol_fault", "protocol": protocol, "mode": mode,
                           "selector_type": selector_type, "target": target,
                           "duration_seconds": duration_seconds})


@mcp.tool()
def clear_protocol_faults(fault_id: str = "") -> dict[str, Any]:
    """Clear one active protocol fault by ID, or all active faults when fault_id is empty."""
    if fault_id:
        active = {fault["id"] for fault in snapshot().get("protocol_faults", [])}
        if fault_id not in active:
            raise ValueError(f"No active protocol fault has ID {fault_id}")
    return submit_command({"action": "clear_protocol_faults", "fault_id": fault_id})


@mcp.resource("scada://plant/status")
def status_resource() -> str:
    """Current simulator status as JSON."""
    return json.dumps(get_plant_status(), indent=2)


@mcp.resource("scada://plant/systems")
def systems_resource() -> str:
    """Current area and asset catalog as JSON."""
    return json.dumps(list_systems(), indent=2)


@mcp.resource("scada://plant/scenarios")
def scenarios_resource() -> str:
    """Available simulation controls and allowed incident inputs as JSON."""
    return json.dumps(list_scenarios(), indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    args = parser.parse_args()
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
