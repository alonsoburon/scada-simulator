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

MCP tag reads show the canonical simulator snapshot. Protocol faults are applied independently inside each protocol adapter, so inspect Modbus/OPC UA/MQTT to observe a protocol fault's effect.

## MCP tools

- `get_plant_status`, `list_systems`, `list_scenarios`: discover the simulator, valid incidents, and built-in replays.
- `search_tags`: filter tags by substring, area, asset, or quality; paginated.
- `read_tags`: read up to 200 exact tag IDs from one snapshot. Each sample includes units, engineering limits, quality, and timestamp.
- `get_alarms`, `get_fleet_positions`: read alarm records and simulated vehicle locations/state.
- `get_water_network_state`: read water tank inventories, pipe flows, pressure, demand, and spill.
- `control_simulation`: `start`, `stop`, `reset`, `acknowledge_alarms`, or `inject_incident` with a selected type, severity, and area.
- `run_scenario`: reset and replay a named scenario from a seed; same seed replays the same measurements and timing.
- `step_simulation`: advance a paused run by an exact number of simulated seconds.
- `inject_protocol_fault`, `list_protocol_faults`, `clear_protocol_faults`: inject and manage per-protocol telemetry faults.
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

## Deterministic scenarios and edge cases

The sidebar's **Scenario replay** panel and the MCP `run_scenario` tool run a named scenario from a selected seed. The simulator resets first, records the seed and scenario timeline, and stops at the scenario's end. Stop to pause, use **Step +1 simulated second** (or `step_simulation`) to inspect a boundary, then run the same scenario and seed to replay it.

Built-in scenarios:

- `mine-water-pump-and-demand`: a process demand surge is followed by pump degradation; tank inventory, return flows, alarms, and recovery respond over time.
- `grid-comms-recovery`: simulated network partition in Grid distribution, stale quality, then recovery.
- `frozen-signal`: freeze one signal while simulation time and other values continue.
- `mqtt-tag-gap`: omit one tag from MQTT batches temporarily, then restore it.

The protocol fault injector supports `stale`, `bad`, `freeze`, and `suppress`, targeted at an area or exact tag and scoped to Modbus TCP, OPC UA, MQTT, or all. Durations are in simulated seconds; `0` means keep the fault active until cleared. `freeze` holds the value while retaining GOOD quality, which is useful for stuck-sensor detection. `suppress` omits a tag from MQTT batches. Because Modbus register addresses and OPC UA nodes are stable, those two protocols keep the tag present and report BAD quality for a suppressed tag. Faults change protocol output only; the canonical process values remain untouched.

For an edge-case drill, run `mqtt-tag-gap` with seed `17`, subscribe to the Grid distribution area topic, and confirm the selected tag disappears and later reappears. Run `frozen-signal`, compare the selected tag through all three protocols while the simulation clock advances, and confirm that clearing the fault lets its value move again.

## Scenarios and tag data

The included incident types are Signal drift, Network partition, Utility loss, Mechanical degradation, Process upset, and Conveyor jam. Each has Small, Medium, and Large severity and can target a plant area or `Whole factory`. Incidents create alarm records and change synthetic values or quality. A network partition marks affected readings `STALE`; the effect clears when its simulated duration expires or the simulation is reset.

## Mine water flow simulation

The **Water network** tab runs a separate, modular flow model for a north reservoir, raw-water tank, process-water tank, reclaim pond, and treated-water tank. It advances at one-second simulation steps. Link flow responds to pump speed and availability, valve opening, elevation head, a simplified pipe resistance, and finite upstream storage. Tank volumes follow an explicit inflow/outflow mass balance; process demand, water recovery, unmet demand, and spill are tracked. Medium and Large utility-loss incidents stop pumps, pump degradation lowers available head, and a process upset increases demand while lowering recovery. Level and demand alarms are raised and cleared as conditions cross their thresholds. Pump state, speed, efficiency, valve position, tank levels, pressure, and flow are exposed as regular SCADA tags.

Use the simulated pump and valve controls to create operating changes. The diagram encodes flow with line width and direction; trends show storage and flows over simulated time. Scenario `mine-water-pump-and-demand` provides a deterministic demand and pump degradation replay.

This is a transparent, lumped-parameter learning model. It does not solve transient water hammer, detailed friction-factor hydraulics, cavitation, pump curves, treatment chemistry, or calibrated site behavior. Its purpose is to create coherent, inspectable flow propagation for SCADA integration experiments, not engineering design.

Tag IDs use `area.asset.signal`, for example `grid_distribution.feeder_north.line_voltage`. Values have explicit engineering units, limits, quality, last-change reason, and snapshot timestamp. GPS coordinates and roads are fictional and geographically positioned near Escondida only for context.

## Configuration and development

- `./setup.sh` creates `.venv` and installs the runtime dependencies.
- `./start_playground.sh` starts the Streamlit UI, protocol gateway, live map, and HTTP MCP server.
- `SCADA_SNAPSHOT_PATH` can point the stdio MCP server at a snapshot in a different checkout.
- `SCADA_COMMAND_DIR` can override the MCP command-mailbox directory.
- Runtime snapshots, register maps, command files, virtual environments, and secrets are ignored by Git.
- Run the fast deterministic model/fault tests with `./.venv/bin/python -m unittest discover -s tests -v`.

The runtime is split into focused modules: `process_model.py` advances synthetic plant state, `water_network.py` handles the explicit mine-water mass balance, `scenarios.py` schedules deterministic runs, `protocol_faults.py` transforms each protocol's view, `protocol_gateway.py` serves the industrial interfaces, `mcp_server.py` provides MCP tools, and `app.py` supplies the UI. Other factory areas still use generated behavioral heuristics, and the simulator does not implement a control system or industrial security stack.

## Safety and network scope

The services are unauthenticated and bind to loopback. Do not expose these demo ports to a LAN, the internet, or a real industrial network. Do not connect this simulator to real actuators, production systems, or safety functions. A dashboard stop/reset control affects only in-memory synthetic state.

## License

MIT. See [LICENSE](LICENSE).
