"""A transparent, lumped-parameter mine-water network teaching simulator.

Units are explicit: storage is m³, level is m, flow is m³/h, head/pressure
are m/kPa. This is an educational mass-balance model, not a hydraulic solver.
"""
from __future__ import annotations

import math


class MineWaterNetwork:
    """Five connected storage nodes and four links advanced at one-second steps."""

    AREA = "Water supply network"
    NODES = {
        "north_reservoir": {"label": "North reservoir", "capacity_m3": 12_000.0,
                            "area_m2": 1_200.0, "elevation_m": 80.0, "initial_m3": 8_400.0},
        "raw_tank": {"label": "Raw water tank", "capacity_m3": 2_000.0,
                     "area_m2": 200.0, "elevation_m": 105.0, "initial_m3": 1_100.0},
        "process_tank": {"label": "Process water tank", "capacity_m3": 1_500.0,
                         "area_m2": 150.0, "elevation_m": 130.0, "initial_m3": 900.0},
        "reclaim_pond": {"label": "Reclaim pond", "capacity_m3": 8_000.0,
                         "area_m2": 800.0, "elevation_m": 92.0, "initial_m3": 3_800.0},
        "treated_tank": {"label": "Treated water tank", "capacity_m3": 1_000.0,
                         "area_m2": 100.0, "elevation_m": 145.0, "initial_m3": 680.0},
    }
    LINKS = {
        "raw_transfer": {"label": "Reservoir → raw tank", "from": "north_reservoir", "to": "raw_tank",
                         "pump_head_m": 62.0, "resistance": 0.00042, "max_flow_m3h": 720.0,
                         "pump": "Intake pump", "valve": "Raw transfer valve"},
        "process_feed": {"label": "Raw tank → process", "from": "raw_tank", "to": "process_tank",
                         "pump_head_m": 57.0, "resistance": 0.00022, "max_flow_m3h": 540.0,
                         "pump": "Process feed pump", "valve": "Process feed valve"},
        "reclaim_return": {"label": "Process → reclaim", "from": "process_tank", "to": "reclaim_pond",
                            "pump_head_m": 0.0, "resistance": 0.0008, "max_flow_m3h": 300.0,
                            "pump": None, "valve": "Reclaim return valve"},
        "reclaim_transfer": {"label": "Reclaim → treated tank", "from": "reclaim_pond", "to": "treated_tank",
                             "pump_head_m": 100.0, "resistance": 0.0004, "max_flow_m3h": 420.0,
                             "pump": "Reclaim pump", "valve": "Reclaim transfer valve"},
    }

    def initialize(self) -> dict:
        nodes = {}
        for key, spec in self.NODES.items():
            nodes[key] = {**spec, "volume_m3": spec["initial_m3"], "inflow_m3h": 0.0,
                          "outflow_m3h": 0.0, "overflow_m3": 0.0}
            nodes[key]["level_m"] = spec["initial_m3"] / spec["area_m2"]
            nodes[key]["level_pct"] = 100.0 * spec["initial_m3"] / spec["capacity_m3"]
        state = {"nodes": nodes,
                "pumps": {name: {"running": True, "speed_pct": 76.0, "efficiency": 1.0}
                          for name in ("Intake pump", "Process feed pump", "Reclaim pump")},
                "valves": {name: 92.0 for name in ("Raw transfer valve", "Process feed valve",
                                                      "Reclaim return valve", "Reclaim transfer valve")},
                "links": {key: {"flow_m3h": 0.0, "pressure_kpa": 0.0, "available": True}
                          for key in self.LINKS},
                "demand_m3h": 240.0, "unmet_demand_m3h": 0.0, "water_recovery_pct": 68.0,
                "history": [], "total_spilled_m3": 0.0, "cumulative_source_withdrawal_m3": 0.0,
                "cumulative_process_consumption_m3": 0.0}
        for key, spec in self.LINKS.items():
            flow, pressure = self._link_reading(state, key, spec)
            state["links"][key].update(flow_m3h=flow, pressure_kpa=pressure, available=flow > 0.1)
        return state

    @staticmethod
    def _level(node: dict) -> float:
        return node["volume_m3"] / node["area_m2"]

    def _link_reading(self, state: dict, key: str, spec: dict) -> tuple[float, float]:
        opening = state["valves"][spec["valve"]] / 100.0
        if spec["pump"]:
            pump = state["pumps"][spec["pump"]]
            speed = pump["speed_pct"] / 100.0
            pump_head = spec["pump_head_m"] * speed * speed * pump["efficiency"] if pump["running"] else 0.0
        else:
            pump_head = 0.0
        source, destination = state["nodes"][spec["from"]], state["nodes"][spec["to"]]
        static_head = self.NODES[spec["from"]]["elevation_m"] + self._level(source)
        destination_head = self.NODES[spec["to"]]["elevation_m"] + self._level(destination)
        available_head = pump_head + static_head - destination_head
        flow = (math.sqrt(available_head / spec["resistance"]) * opening
                if available_head > 0 and opening > 0.01 else 0.0)
        return min(spec["max_flow_m3h"], flow), max(0.0, available_head * 9.80665)

    def advance(self, state: dict, sim_seconds: int, active_events: list[dict]) -> None:
        """Advance one simulated second using simultaneous flows and a mass balance."""
        stress = max(({"Small": 1.2, "Medium": 1.6, "Large": 2.1}[e["severity"]]
                      for e in active_events if e["name"] == "Process upset"), default=1.0)
        state["demand_m3h"] = 240.0 * stress * (1 + 0.08 * math.sin(sim_seconds / 95.0))
        state["water_recovery_pct"] = 68.0 if stress == 1.0 else max(35.0, 68.0 - (stress - 1.0) * 18)
        degraded = any(e["name"] == "Mechanical degradation" and e["severity"] in {"Medium", "Large"}
                       for e in active_events)
        utility_lost = any(e["name"] == "Utility loss" and e["severity"] in {"Medium", "Large"}
                           for e in active_events)
        for pump in state["pumps"].values():
            pump["efficiency"] = 0.58 if degraded else 1.0
            pump["running"] = not utility_lost

        flows = {}
        for key, spec in self.LINKS.items():
            flow, pressure = self._link_reading(state, key, spec)
            flows[key] = flow
            state["links"][key].update(flow_m3h=flow, pressure_kpa=pressure,
                                        available=flow > 0.1)

        # Respect available source storage, then carry delivered process demand through recovery.
        volumes = {key: node["volume_m3"] for key, node in state["nodes"].items()}
        flows["raw_transfer"] = min(flows["raw_transfer"], volumes["north_reservoir"] * 3600.0)
        flows["process_feed"] = min(flows["process_feed"], (volumes["raw_tank"] + flows["raw_transfer"] / 3600.0) * 3600.0)
        available_process = volumes["process_tank"] * 3600.0 + flows["process_feed"]
        served = min(state["demand_m3h"], available_process)
        state["unmet_demand_m3h"] = state["demand_m3h"] - served
        flows["reclaim_return"] = min(served * state["water_recovery_pct"] / 100.0,
                                      self.LINKS["reclaim_return"]["max_flow_m3h"])
        flows["reclaim_transfer"] = min(flows["reclaim_transfer"],
                                         (volumes["reclaim_pond"] + flows["reclaim_return"] / 3600.0) * 3600.0)
        net_m3h = {key: 0.0 for key in self.NODES}
        for link_key, flow in flows.items():
            link = self.LINKS[link_key]
            net_m3h[link["from"]] -= flow
            net_m3h[link["to"]] += flow
        net_m3h["process_tank"] -= max(0.0, state["demand_m3h"] - flows["reclaim_return"])
        spilled = 0.0
        for key, node in state["nodes"].items():
            node["inflow_m3h"] = sum(flow for link_key, flow in flows.items()
                                      if self.LINKS[link_key]["to"] == key)
            node["outflow_m3h"] = sum(flow for link_key, flow in flows.items()
                                       if self.LINKS[link_key]["from"] == key)
            if key == "process_tank":
                node["outflow_m3h"] += max(0.0, served - flows["reclaim_return"])
            raw_volume = node["volume_m3"] + net_m3h[key] / 3600.0
            node_spill = 0.0
            if raw_volume > node["capacity_m3"]:
                spill = raw_volume - node["capacity_m3"]
                node_spill = spill
                spilled += spill
                raw_volume = node["capacity_m3"]
            node["volume_m3"] = min(node["capacity_m3"], max(0.0, raw_volume))
            node["level_m"] = self._level(node)
            node["level_pct"] = 100.0 * node["volume_m3"] / node["capacity_m3"]
            node["overflow_m3"] = node_spill
        state["total_spilled_m3"] += spilled
        state["cumulative_source_withdrawal_m3"] += flows["raw_transfer"] / 3600.0
        state["cumulative_process_consumption_m3"] += max(0.0, served - flows["reclaim_return"]) / 3600.0
        if sim_seconds % 5 == 0:
            state["history"].append({"time": sim_seconds, "raw_tank_level_pct": state["nodes"]["raw_tank"]["level_pct"],
                                     "process_tank_level_pct": state["nodes"]["process_tank"]["level_pct"],
                                     "reclaim_pond_level_pct": state["nodes"]["reclaim_pond"]["level_pct"],
                                     "intake_flow_m3h": flows["raw_transfer"],
                                     "process_feed_m3h": flows["process_feed"],
                                     "demand_m3h": state["demand_m3h"]})
            state["history"] = state["history"][-720:]

    def telemetry(self, state: dict) -> tuple[list[dict], list[dict]]:
        """Return tag rows and an edge/node diagram payload for snapshots/UI."""
        rows = []

        def add(asset: str, signal: str, value: float, units: str, low: float, high: float,
                last_change: str = "Water network mass balance") -> None:
            rows.append({"tag": f"water_supply_network.{asset}.{signal}", "area": self.AREA,
                         "unit": asset.replace("_", " ").title(), "sensor": signal, "units": units,
                         "value": round(value, 3), "quality": "GOOD", "low": low, "high": high,
                         "base": (low + high) / 2, "phase": 0.0, "last_change": last_change})

        for key, node in state["nodes"].items():
            add(key, "tank_level", node["level_pct"], "%", 0, 100)
            add(key, "stored_volume", node["volume_m3"], "m³", 0, node["capacity_m3"])
        for key, link in self.LINKS.items():
            reading = state["links"][key]
            add(key, "flow_rate", reading["flow_m3h"], "m³/h", 0, link["max_flow_m3h"])
            add(key, "line_pressure", reading["pressure_kpa"], "kPa", 0, 1200)
        for name, pump in state["pumps"].items():
            asset = name.lower().replace(" ", "_")
            add(asset, "pump_running", float(pump["running"]), "bool", 0, 1)
            add(asset, "speed_command", pump["speed_pct"], "%", 0, 100)
            add(asset, "pump_efficiency", pump["efficiency"] * 100.0, "%", 0, 100)
        for name, opening in state["valves"].items():
            add(name.lower().replace(" ", "_"), "valve_position", opening, "%", 0, 100)
        add("process_demand", "water_demand", state["demand_m3h"], "m³/h", 0, 600)
        add("process_demand", "unmet_demand", state["unmet_demand_m3h"], "m³/h", 0, 600)
        add("process_demand", "water_recovery", state["water_recovery_pct"], "%", 0, 100)
        nodes = [{"id": key, "label": spec["label"], "level_pct": state["nodes"][key]["level_pct"],
                  "volume_m3": state["nodes"][key]["volume_m3"], "capacity_m3": spec["capacity_m3"]}
                 for key, spec in self.NODES.items()]
        links = [{"id": key, "label": spec["label"], "from": spec["from"], "to": spec["to"],
                  "flow_m3h": state["links"][key]["flow_m3h"], "pressure_kpa": state["links"][key]["pressure_kpa"],
                  "pump": spec["pump"]} for key, spec in self.LINKS.items()]
        return rows, [{"nodes": nodes, "links": links, "demand_m3h": state["demand_m3h"],
                       "unmet_demand_m3h": state["unmet_demand_m3h"],
                       "total_spilled_m3": state["total_spilled_m3"]}]
