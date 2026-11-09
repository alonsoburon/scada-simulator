# SCADA Simulator

A local synthetic industrial operations world for developers building, testing, and demoing SCADA monitoring integrations. One simulator publishes the same values through Modbus TCP, OPC UA, MQTT, and MCP, with a small operator UI and a separate live fleet map.

It models mining, power, water, manufacturing, buildings, and GPS-equipped vehicles: **32 areas, 177 assets, and about 1,500 tags**. Inject incidents, observe alarm and signal-quality changes, and connect your own telemetry readers.

> Everything here is simulated. It is not a PLC, a process engineering model, a safety system, or a connection to real equipment.

## Quick start

Requirements: Python 3.10+ and a local machine. The map uses public OpenStreetMap tiles, so its basemap needs an internet connection.

```bash
git clone https://github.com/alonsoburon/scada-simulator.git
cd scada-simulator
./setup.sh
./start_playground.sh
```

Open <http://127.0.0.1:8501> and keep the dashboard page open while using MCP simulation controls. The page starts the synthetic plant session; the other services start alongside it. Use the sidebar to start/stop the whole plant, set simulation speed, and inject an incident. Stop all services with Ctrl+C.

## Integration endpoints

All endpoints bind to loopback (`127.0.0.1`) by default. They have no authentication and are intended for isolated local development only.

| Interface | Endpoint | What it exposes |
| --- | --- | --- |
| Modbus TCP | `127.0.0.1:1502`, unit 1 | FC04 input registers; three per tag: IEEE-754 float32 high word, low word, quality code (`0=GOOD`, `1=STALE`, `2=BAD`). The generated tag/address map is at `scada_playground/.runtime/modbus_register_map.csv`. |
| OPC UA | `opc.tcp://127.0.0.1:4840/factory/server/` | `Objects/Factory/<area>/<asset>/<tag>/{Value,Quality,Units}`. |
| MQTT | `127.0.0.1:1883` | Retained area batches at `scada/factory/areas/<area_slug>/telemetry`, plus plant status and alarm snapshots under `scada/factory/`. |
| MCP Streamable HTTP | `http://127.0.0.1:8503/mcp` | Discover/read plant data and trigger simulator-only scenarios. |
| MCP stdio | `scada_playground/mcp_server.py` | Same MCP tools for a host that launches local subprocess servers. |
| Fleet map | `http://127.0.0.1:8502/map` | Independent Leaflet map updated by WebSocket; pan and zoom persist while vehicles move. |

The protocol gateway updates from the same atomic snapshot as the dashboard. For normal SCADA reader development, use Modbus, OPC UA, or MQTT. MCP is a companion surface for compatible agents and applications that need structured discovery, queries, or test-scenario control.

## MCP tools

- `get_plant_status`, `list_systems`, `list_scenarios`: discover the simulator and valid incident inputs.
- `search_tags`: filter tags by substring, area, asset, or quality; paginated.
- `read_tags`: read up to 200 exact tag IDs from one snapshot. Each sample includes units, engineering limits, quality, and timestamp.
- `get_alarms`, `get_fleet_positions`: read alarm records and simulated vehicle locations/state.
- `control_simulation`: `start`, `stop`, `reset`, `acknowledge_alarms`, or `inject_incident` with a selected type, severity, and area.
- Resources: `scada://plant/status`, `scada://plant/systems`, `scada://plant/scenarios`.

Control commands are applied by the active dashboard session and acknowledged only after its next snapshot is published. Keep at least one dashboard tab open; a command times out if no session processes it within 20 seconds. MCP cannot write to any industrial protocol endpoint or physical equipment.

### Connect an MCP client

For Streamable HTTP clients, use `http://127.0.0.1:8503/mcp` while the playground is running. For stdio clients, add a server entry like this to the client configuration, replacing the paths with the absolute path to your checkout:

```json
{
  "mcpServers": {
    "scada-simulator": {
      "command": "/path/to/scada-simulator/.venv/bin/python",
      "args": ["/path/to/scada-simulator/scada_playground/mcp_server.py"]
    }
  }
}
```

The stdio server reads the same snapshot and uses the same command mailbox. The dashboard session must be running to advance the simulation or apply control commands.

## Scenarios and tag data

The included incident types are Signal drift, Network partition, Utility loss, Mechanical degradation, Process upset, and Conveyor jam. Each has Small, Medium, and Large severity and can target a plant area or `Whole factory`. Incidents create alarm records and change synthetic values or quality. A network partition marks affected readings `STALE`; the effect clears when its simulated duration expires or the simulation is reset.

Tag IDs use `area.asset.signal`, for example `grid_distribution.feeder_north.line_voltage`. Values have explicit engineering units, limits, quality, last-change reason, and snapshot timestamp. GPS coordinates and roads are fictional and geographically positioned near Escondida only for context.

## Configuration and development

- `./setup.sh` creates `.venv` and installs the runtime dependencies.
- `./start_playground.sh` starts the Streamlit UI, protocol gateway, live map, and HTTP MCP server.
- `SCADA_SNAPSHOT_PATH` can point the stdio MCP server at a snapshot in a different checkout.
- `SCADA_COMMAND_DIR` can override the MCP command-mailbox directory.
- Runtime snapshots, register maps, command files, virtual environments, and secrets are ignored by Git.

See [the simulator implementation](scada_playground/) for the process/UI, protocol gateway, and MCP server. The simulation uses simplified heuristics rather than calibrated mass/energy models, and it does not implement a control system or industrial security stack.

## Safety and network scope

The services are unauthenticated and bind to loopback. Do not expose these demo ports to a LAN, the internet, or a real industrial network. Do not connect this simulator to real actuators, production systems, or safety functions. A dashboard stop/reset control affects only in-memory synthetic state.

## License

MIT. See [LICENSE](LICENSE).
