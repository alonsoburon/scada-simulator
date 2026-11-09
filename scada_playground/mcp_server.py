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
            "target_areas": data.get("available_areas", [])}


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
                       target_area: str = "Whole factory") -> dict[str, Any]:
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
    return submit_command({"action": action, "incident_name": incident_name,
                           "severity": severity, "target_area": target_area})


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
