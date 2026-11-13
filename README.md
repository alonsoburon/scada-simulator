# SCADA Simulator

A local synthetic industrial operations world for developers building, testing, and demoing SCADA monitoring integrations. One simulator publishes the same values through Modbus TCP, OPC UA, MQTT, and MCP, with a small operator UI and a separate live fleet map.

It models mining, power, water, manufacturing, buildings, and GPS-equipped vehicles: **32 areas, about 200 equipment units, and about 1,540 tags**. The mine electrical network, water network, copper concentrator, haulage fleet, and factory steam/instrument-air/production-line loops have explicit dynamic models; the remaining areas still use broad synthetic behavior. Inject incidents, observe alarm and signal-quality changes, and connect your own telemetry readers.

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
- `get_haulage_state`: read truck cycle states, delivery rate, payload/fuel tags, and ROM dispatch holds.
- `get_water_network_state`: read water tank inventories, pipe flows, pressure, demand, and spill.
- `get_wastewater_state`: read equalization, chemical inventories, treatment performance, effluent quality, reuse, outfall, and sludge state.
- `get_concentrator_state`: read ore buffers, grinding limits, product size, recovery, water use, and power.
- `get_process_control_state`, `control_process`: inspect the simulated process sequence and request orderly `START`, `STOP`, or `RESET_TRIPS` after the cause clears.
- `get_power_network_state`: read grid import, diesel/solar/storage dispatch, frequency, voltage, load shedding, and feeder limits.
- `get_plant_utilities_state`: read boiler output, steam-header pressure, condensate return, compressor output, receiver pressure, dryer dew point, CIP phase/chemistry, and downstream production.
- `start_cip_cycle`: request a simulated clean-in-place cycle for either pasteurizer; water, chemical, steam, power, and target availability permissives apply.
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

- `mine-grid-support-and-recovery`: a large transmission trip islands the mine grid, trips the process sequence, then simulates an operator reset and orderly restart after electrical supply recovers.
- `haulage-crusher-buffer`: a long crusher conveyor trip lets discrete truck loads fill ROM and pause dispatch; recovery draws the stockpile down until truck loading resumes.
- `concentrator-mill-degradation`: changing ore hardness, grinding degradation, and a mine power disturbance propagate into grind size, flotation recovery, water demand, and buffered inventories.
- `mine-water-pump-and-demand`: a process demand surge is followed by pump degradation; tank inventory, return flows, alarms, and recovery respond over time.
- `mine-water-treatment-upset-and-recovery`: source turbidity, chemical use, filtration loading, transfer-line delay, and treated-tank quality move at different rates during an upset and recovery.
- `boiler-header-pressure-and-recovery`: boiler capacity loss drains header inventory; pasteurizer temperatures lag the pressure drop and recover after the fault clears.
- `cip-water-steam-and-production-recovery`: a pasteurizer CIP cycle sequences rinse, caustic, hot sanitation, final rinse, and release; shared water or steam shortages hold the phase and production returns after cleaning.
- `cip-wastewater-quality-and-reuse`: CIP return enters equalization and treatment through an outage and recovery; compare neutralization, solids removal, chemical inventories, sludge pressing, quality-gated reuse, and outfall.
- `compressed-air-receiver-and-recovery`: compressor loss draws down receiver pressure, degrades dryer output, and curtails filler and case-packer rates before recovery.
- `packaging-jam-buffer-and-recovery`: a case-packer jam fills the accumulation conveyor, backs the filler up, fills the pasteurized-product tank, and propagates a controlled slowdown back upstream before draining on recovery.
- `grid-comms-recovery`: simulated network partition in Grid distribution, stale quality, then recovery.
- `frozen-signal`: freeze one signal while simulation time and other values continue.
- `mqtt-tag-gap`: omit one tag from MQTT batches temporarily, then restore it.

The protocol fault injector supports `stale`, `bad`, `freeze`, and `suppress`, targeted at an area or exact tag and scoped to Modbus TCP, OPC UA, MQTT, or all. Durations are in simulated seconds; `0` means keep the fault active until cleared. `freeze` holds the value while retaining GOOD quality, which is useful for stuck-sensor detection. `suppress` omits a tag from MQTT batches. Because Modbus register addresses and OPC UA nodes are stable, those two protocols keep the tag present and report BAD quality for a suppressed tag. Faults change protocol output only; the canonical process values remain untouched.

For an edge-case drill, run `mqtt-tag-gap` with seed `17`, subscribe to the Grid distribution area topic, and confirm the selected tag disappears and later reappears. Run `frozen-signal`, compare the selected tag through all three protocols while the simulation clock advances, and confirm that clearing the fault lets its value move again.

## Scenarios and tag data

The included incident types are Signal drift, Network partition, Utility loss, Mechanical degradation, Process upset, and Conveyor jam. Each has Small, Medium, and Large severity and can target a plant area or `Whole factory`. Incidents create alarm records and change synthetic values or quality. A network partition marks affected readings `STALE`; the effect clears when its simulated duration expires or the simulation is reset.

## Mine water flow simulation

The **Water network** tab models a north reservoir, raw-water tank, process-water tank, reclaim pond, treated-water tank, concentrator reclaim sump, and shared wastewater equalization tank. It advances at one-second simulation steps. Pipe flow is solved from Darcy–Weisbach wall friction, minor losses, elevation head, valve loss, and a quadratic pump curve. Full-pipe transport queues add travel-time lag while preserving inventory, including a separate quality front on the reclaim-to-treated transfer. PI level controllers drive pump speed commands through VFD ramp limits; operators can switch each pump between AUTO and MANUAL, change tank setpoints or speed, and disable equipment. Tank volumes follow explicit mass balances; the model tracks site and concentrator gross demand, solids-derived recovery streams, fresh make-up, unmet demand, spill, pipe inventory, pump power, and motor current. Concentrator tailings overflow and filter-press filtrate enter the reclaim sump, then return through a dedicated pump and delayed pipe; fresh make-up is gross process demand minus those returns. Service water passes through chemical conditioning, filtration, and disinfection before it reaches the treated tank. Turbidity, chlorine residual, finite chemical inventory, filter loading, differential pressure, and backwash demand evolve over time; backwash draws water from the finite treated tank and carries modeled filter solids into the wastewater train. Factory CIP return joins backwash water in equalization, pH neutralization, DAF solids removal, sludge pressing, quality-gated reuse to the reclaim pond, and an outfall. Utility loss, mechanical degradation, and process upset alter flows, inventories, and effluent quality. Wastewater power loss and limited equalization headroom can constrain upstream discharges; the `get_wastewater_state` MCP tool exposes the treatment state and the scenario `cip-wastewater-quality-and-reuse` exercises outage and recovery. Measurements receive small seeded noise, so a replay is reproducible.

Use the simulated pump and valve controls to create operating changes. The diagram encodes flow with line width and direction; trends show storage, demand, flow, treated-water quality, and filter loading over simulated time. Scenario `mine-water-pump-and-demand` provides a deterministic demand and pump degradation replay; `mine-water-treatment-upset-and-recovery` shows the delayed response to a source-quality disturbance.

This remains a compact learning model. Pipe friction and pump curves are simplified steady-state relationships; queues approximate plug flow. Service-water and wastewater chemistry, DAF removal, and reuse quality limits are bounded training dynamics, not validated treatment design or discharge-compliance calculations. It does not model water hammer, elevation profiles along each pipe, changing pipe diameter, cavitation, pump efficiency maps, detailed flocculation/settling, or site-calibrated equipment. The mine power feeder includes service-water and wastewater treatment loads alongside the water pumps.

## Factory steam and instrument-air utilities

The **Plant utilities** tab models two boilers feeding a finite steam header, a delayed condensate return, two air compressors, a dryer, a receiver plus effective ring-main linepack, and a coupled production line. Pasteurizers only send product above a simulated minimum temperature; colder output is diverted. A finite pasteurized-product tank buffers the fillers, and a finite accumulation conveyor buffers the case packer. Each scan conserves product across those two inventories; when downstream blockage fills them, filler and pasteurizer output back off. A case contains three simulated units. Header, receiver, and product inventory buffer outages; boiler/compressor/packer output ramps; low pressure curtails pasteurizer temperature and packaging rates with thermal and pneumatic response lags. Condensate reuse and boiler make-up change after the return delay. Compressor electrical demand is included in the site power request, so a mine-grid limitation can also reduce instrument-air capacity. A seven-phase clean-in-place cycle takes one pasteurizer out of service while the parallel line continues; phase timers wait on shared water, steam, and electrical availability. Caustic/sanitizer inventory and return pH/conductivity provide operator signals, CIP effluent is accounted in the water network, and the cycle exposes start permissives and low-inventory/starvation alarms.

These compact inventory and pressure-control models do not calculate combustion, steam tables, compressor maps, air-leak location, water-treatment chemistry, or pipe-network design. CIP dosing, pH, conductivity, and effluent are simplified educational signals, not validated cleaning recipes or wastewater compliance calculations. They provide cross-system failure propagation for SCADA monitoring practice. Run `boiler-header-pressure-and-recovery`, `compressed-air-receiver-and-recovery`, and `cip-water-steam-and-production-recovery` to compare stored inventory with downstream response.

## Copper concentrator process simulation

The **Mineral process** tab follows a copper-ore circuit from ROM feed through primary crushing, crushed-ore stockpile, SAG mill, ball mill/cyclone recirculation, flotation, concentrate thickening, cyclic filter pressing, a concentrate cake stockpile, tailings thickening, and tailings pumping. ROM, crushed-ore, concentrate, cake, and tailings inventories carry material through upstream and downstream stoppages. A bounded change in ore hardness alters specific energy and grind size; available SAG/ball mill power and equipment condition limit fresh throughput; P80 (the particle size below which 80% of measured material falls) and water availability influence estimated flotation recovery. Concentrate tonnage follows a copper feed/recovery/product-grade mass balance. ROM is fed by discrete payloads from the haul truck cycles below. The circuit publishes power demand and make-up water demand as tags; a mine utility loss derates mill and pump capacity, while water shortage constrains feed and recovery.

The model samples every simulated second, smooths slow process measurements, and adds repeatable measurement noise keyed by seed and tag. A Large conveyor jam sharply reduces crushing, but the stockpile temporarily shields the mill; a grinding fault changes mill capacity, vibration, grind size, and recovery. Replay `concentrator-mill-degradation` to observe the effects and recovery. The MCP `get_concentrator_state` tool provides a process summary; regular tag reads expose individual measurements through the same interfaces as the rest of the plant.

The **Process PLC sequence** controls a teaching sequence for tailings pumping and thickening, concentrate thickening and filtration, flotation, grinding, conveyors, and primary crushing. It models downstream-first startup, orderly shutdown, utility permissives, three-second delayed trips, basic dependency interlocks, and manual trip reset. Filter pressing alternates filling, squeezing, and cake discharge; finite concentrate and tailings inventories constrain upstream feed when their outlet equipment is unavailable. Factory timeline start/stop controls only simulation time; process start/stop controls the simulated equipment sequence. Reset clears trips only after the simulated supply recovers and active jam/severe mechanical causes clear; a separate operator start is required afterward. This is a small ordinary process-control model, not a certified safety PLC or safety instrumented system.

This is a low-compute supervisory approximation, not a calibrated comminution or metallurgy model. Three size classes pass through energy-, work-index-, and condition-dependent breakage probabilities, then size-dependent cyclone partitions. Rejected coarse and intermediate material recirculates with fresh coarse feed; the class mix relaxes over a 15-minute circuit residence time. Overflow, circulating load, and P80 are derived from that mill/cyclone pass. Flotation uses an effective cell volume, calculated pulp residence time, and first-order recovery kinetics adjusted by size class, air, reagent dose, and water availability. This compact class balance omits particle fracture distributions, liberation/mineralogy, flotation-cell residence-time distributions, reagent chemistry, water chemistry, and site calibration. The copper mass balance and finite buffers are useful for integration, historian, alarm, and failure-propagation exercises; they are not suitable for production forecasts or equipment selection.

The water circuit uses an explicit first-pass solids/water split. Gross process use is 0.90 m³ per tonne of flotation feed. Water follows the measured concentrate/tailings dry-solids split; the tailings thickener holds a finite liquid inventory, removes underflow water with pumped solids, and releases overflow at a bounded hydraulic rate. It targets 68% underflow solids and returns 92% of overflow water; an outage therefore stores liquid and can create a reclaim surge during restart. The concentrate thickener targets 60% solids, and a filter-press batch retains water at 85% cake solids; filtrate accumulates during filling and is released over the 90-second squeeze. Those streams enter a finite reclaim sump and return through its own VFD pump and delayed pipe. The process tank is charged with gross demand and credits return only when it physically arrives, so a pump trip fills the sump and causes a delayed water shortage. All assumptions are exposed as state or tags and the component return flows close against gross use. The model still omits settling kinetics, particle-size effects, rheology, density variation, and site calibration, so its separation efficiencies are teaching assumptions rather than design values.

## Open-pit haulage

The **Mobile fleet** map and telemetry share a route-driven cycle for twelve haul trucks: shovel queue, loading, loaded travel, crusher queue, tipping, and empty return. Per-truck payload, speed limits, fuel, engine hours, tire/brake temperatures, and loads hauled follow the cycle; smooth movement between road nodes avoids instantaneous GPS jumps. Each tip adds its payload to the concentrator ROM inventory. If ROM reaches 40,000 t, dispatch waits at the shovel; loading resumes below 34,000 t to avoid rapid stop/start chatter. At 15% fuel reserve, trucks refuel at the shovel queue and emit a low-fuel alarm. Fleet mechanical degradation slows travel time, and shovel/power incidents delay loading. Service trucks and buses remain synthetic route vehicles. Run `haulage-crusher-buffer` to observe payload delivery, stockpile buffering, crusher recovery, and dispatch holds; MCP `get_haulage_state` reports fleet states and process tags.

This remains a route abstraction: it does not calculate road grade from elevation, truck traction/braking physics, tire health failure probability, collision avoidance, autonomous dispatch optimization, shovel dig cycles, or calibrated fuel maps. Truck delivery is discrete, while the crusher and downstream process retain their own capacity limits.

## Mine electrical network

Grid generation, two transmission paths, substations, distribution feeders, and the mine substation publish values from a shared dispatch model. It carries about 44 MW of normal import capacity, variable solar output, two diesel units with a 20-second start delay and finite ramp, and a 20 MWh battery with a 6 MW inverter and asymmetric charge/discharge ramp rates. The model serves site auxiliaries, water pumps, factory utilities, and the concentrator subject to transformer and feeder limits. During scarcity it preserves essential controls, then water supply, then steam/compressed-air services, then concentrator process loads, and sheds flexible site auxiliaries last. Served/requested power and a distinct factory-utility feeder tag make this priority visible. Transformer top-oil and winding hot-spot temperatures have separate thermal time constants; staged cooling starts at rising oil temperatures, and a sustained hot spot derates the connected transfer limit. Four radial feeders estimate branch voltage drop, reactive flow, current, loading, and I²R losses; those losses are included in the source-to-load balance. Frequency uses the prior scan's connected load to show a short imbalance transient, and reports rate of change and the run nadir. A Large `Utility loss` in Grid transmission trips the intertie and islands the mine bus; smaller events derate the import path. Targeting Grid distribution, Mine power network, or Grid generation exercises local feeder, transformer, or source constraints. Replay `mine-grid-support-and-recovery` to inspect reserves and recovery.

This is a low-compute balanced network approximation. Feeder voltage uses linearized DistFlow, not an iterative AC load-flow solver. It does not solve voltage angles, protection curves, relay coordination, harmonic propagation, reactive-power capability, generator governors in detail, or actual island resynchronization. Frequency is a damped aggregate response, and an energized utility tie acts as a strong 50 Hz reference; islanding transients are one-second approximations. Source voltage droop is a supervisory proxy. Use it to exercise SCADA source/feeder telemetry, alarms, reserve transitions, and cross-system process response, not to size equipment or validate protection settings.

Tag IDs use `area.asset.signal`, for example `grid_distribution.feeder_north.line_voltage`. Values have explicit engineering units, limits, quality, last-change reason, and snapshot timestamp. GPS coordinates and roads are fictional and geographically positioned near Escondida only for context.

## Configuration and development

- `./setup.sh` creates `.venv` and installs the runtime dependencies.
- `./start_playground.sh` starts the Streamlit UI, protocol gateway, live map, and HTTP MCP server.
- `SCADA_SNAPSHOT_PATH` can point the stdio MCP server at a snapshot in a different checkout.
- `SCADA_COMMAND_DIR` can override the MCP command-mailbox directory.
- Runtime snapshots, register maps, command files, virtual environments, and secrets are ignored by Git.
- Run the fast deterministic model/fault tests with `./.venv/bin/python -m unittest discover -s tests -v`.

The runtime is split into focused modules: `process_model.py` advances unmodeled synthetic areas, `power_network.py` dispatches the mine electrical network, `water_network.py` handles mine-water hydraulics and level controllers, `concentrator.py` models ore processing, `plant_controller.py` executes a limited PLC-style equipment sequence, `haulage.py` models truck cycles, `scenarios.py` schedules deterministic runs, `protocol_faults.py` transforms each protocol's view, `protocol_gateway.py` serves the industrial interfaces, `mcp_server.py` provides MCP tools, and `app.py` supplies the UI. Power, water, haulage, and mineral processing share explicit operating constraints and process connections; other areas still use generated behavioral heuristics. It does not model a full PLC program, complete protection system, or industrial security stack.

## Safety and network scope

The services are unauthenticated and bind to loopback. Do not expose these demo ports to a LAN, the internet, or a real industrial network. Do not connect this simulator to real actuators, production systems, or safety functions. A dashboard stop/reset control affects only in-memory synthetic state.

## License

MIT. See [LICENSE](LICENSE).
