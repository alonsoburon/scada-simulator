"""A transparent mine-water model with pipe loss, pump curves and storage.

Units are explicit: storage is m³, level is m, flow is m³/h, head/pressure
are m/kPa. It is an educational network approximation, not a calibrated plant model.
"""
from __future__ import annotations

import math
import random
from collections import deque


class MineWaterNetwork:
    """Mine-water storage and delayed links, advanced at one-second steps."""

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
        "concentrator_reclaim_sump": {"label": "Concentrator reclaim sump", "capacity_m3": 300.0,
                                      "area_m2": 30.0, "elevation_m": 118.0, "initial_m3": 0.0},
    }
    PUMPS = {
        "Intake pump": {"shutoff_head_m": 175.0, "rated_flow_m3h": 760.0,
                        "motor_efficiency": 0.91, "hydraulic_efficiency": 0.76,
                        "ramp_pct_per_s": 0.35, "controlled_node": "raw_tank", "setpoint_pct": 55.0},
        "Process feed pump": {"shutoff_head_m": 120.0, "rated_flow_m3h": 760.0,
                              "motor_efficiency": 0.92, "hydraulic_efficiency": 0.78,
                              "ramp_pct_per_s": 0.50, "controlled_node": "process_tank", "setpoint_pct": 60.0},
        "Reclaim pump": {"shutoff_head_m": 190.0, "rated_flow_m3h": 440.0,
                         "motor_efficiency": 0.90, "hydraulic_efficiency": 0.73,
                         "ramp_pct_per_s": 0.30, "controlled_node": "treated_tank", "setpoint_pct": 68.0},
        "Concentrator reclaim pump": {"shutoff_head_m": 95.0, "rated_flow_m3h": 360.0,
                                      "motor_efficiency": 0.91, "hydraulic_efficiency": 0.74,
                                      "ramp_pct_per_s": 0.45, "controlled_node": "concentrator_reclaim_sump",
                                      "setpoint_pct": 45.0},
    }
    LINKS = {
        "raw_transfer": {"label": "Reservoir → raw tank", "from": "north_reservoir", "to": "raw_tank",
                         "length_m": 1_100.0, "diameter_m": 0.60, "roughness_m": 0.00015,
                         "minor_loss_k": 12.0, "valve_loss_k": 1.5, "max_flow_m3h": 720.0,
                         "pump": "Intake pump", "valve": "Raw transfer valve"},
        "process_feed": {"label": "Raw tank → process", "from": "raw_tank", "to": "process_tank",
                         "length_m": 480.0, "diameter_m": 0.40, "roughness_m": 0.00015,
                         "minor_loss_k": 16.0, "valve_loss_k": 1.8, "max_flow_m3h": 720.0,
                         "pump": "Process feed pump", "valve": "Process feed valve"},
        "reclaim_return": {"label": "Process → reclaim", "from": "process_tank", "to": "reclaim_pond",
                            "length_m": 360.0, "diameter_m": 0.30, "roughness_m": 0.00015,
                            "minor_loss_k": 22.0, "valve_loss_k": 2.0, "max_flow_m3h": 300.0,
                            "pump": None, "valve": "Reclaim return valve"},
        "reclaim_transfer": {"label": "Reclaim → treated tank", "from": "reclaim_pond", "to": "treated_tank",
                             "length_m": 520.0, "diameter_m": 0.35, "roughness_m": 0.00015,
                             "minor_loss_k": 14.0, "valve_loss_k": 1.8, "max_flow_m3h": 420.0,
                             "pump": "Reclaim pump", "valve": "Reclaim transfer valve"},
        "concentrator_reclaim": {"label": "Concentrator reclaim → process tank",
                                 "from": "concentrator_reclaim_sump", "to": "process_tank",
                                 "length_m": 380.0, "diameter_m": 0.35, "roughness_m": 0.00015,
                                 "minor_loss_k": 18.0, "valve_loss_k": 1.8, "max_flow_m3h": 340.0,
                                 "pump": "Concentrator reclaim pump", "valve": "Concentrator reclaim valve"},
    }

    def initialize(self) -> dict:
        nodes = {}
        for key, spec in self.NODES.items():
            nodes[key] = {**spec, "volume_m3": spec["initial_m3"], "inflow_m3h": 0.0,
                          "outflow_m3h": 0.0, "overflow_m3": 0.0}
            nodes[key]["level_m"] = spec["initial_m3"] / spec["area_m2"]
            nodes[key]["level_pct"] = 100.0 * spec["initial_m3"] / spec["capacity_m3"]
        pumps = {}
        controllers = {}
        for name, spec in self.PUMPS.items():
            initial_speed = 66.0
            pumps[name] = {**spec, "enabled": True, "running": True, "speed_pct": initial_speed,
                           "speed_command_pct": initial_speed, "manual_speed_pct": initial_speed,
                           "condition_factor": 1.0, "efficiency": 1.0, "current_a": 0.0,
                           "power_kw": 0.0, "pump_head_m": 0.0, "vibration_mm_s": 1.2}
            gains = {"Intake pump": (1.0, 0.0003),
                     "Process feed pump": (1.2, 0.0010),
                     "Reclaim pump": (1.2, 0.0010),
                     "Concentrator reclaim pump": (1.2, 0.0010)}
            kp, ki = gains[name]
            controllers[name] = {"mode": "AUTO", "setpoint_pct": spec["setpoint_pct"],
                                 "bias_pct": initial_speed, "kp": kp, "ki": ki,
                                 "integral": 0.0, "output_pct": initial_speed}
        state = {"nodes": nodes,
                "pumps": pumps, "controllers": controllers,
                "valves": {name: 92.0 for name in ("Raw transfer valve", "Process feed valve",
                                                      "Reclaim return valve", "Reclaim transfer valve",
                                                      "Concentrator reclaim valve")},
                "links": {key: {"flow_m3h": 0.0, "pressure_kpa": 0.0, "available": True}
                          for key in self.LINKS},
                "base_demand_m3h": 240.0, "demand_m3h": 240.0, "concentrator_demand_m3h": 0.0,
                "factory_cip_demand_m3h": 0.0, "factory_cip_effluent_m3h": 0.0,
                "factory_cip_served_m3h": 0.0, "factory_water_available_fraction": 1.0,
                "cumulative_factory_cip_effluent_m3": 0.0,
                "concentrator_reclaim_m3h": 0.0,
                "treated_demand_m3h": 96.0,
                "unmet_demand_m3h": 0.0, "unmet_treated_demand_m3h": 0.0,
                "water_recovery_pct": 68.0, "concentrator_recovery_pct": 0.0,
                "history": [], "total_spilled_m3": 0.0, "cumulative_source_withdrawal_m3": 0.0,
                "cumulative_process_consumption_m3": 0.0, "cumulative_treated_delivery_m3": 0.0,
                "cumulative_concentrator_return_m3": 0.0,
                "sensor_rng_seed": 17, "sim_seconds": 0, "measurement_noise": {}}
        for key, spec in self.LINKS.items():
            flow, pressure = self._link_reading(state, key, spec)
            if key == "reclaim_return":
                flow = min(flow, state["demand_m3h"] * state["water_recovery_pct"] / 100.0)
            if key == "concentrator_reclaim":
                flow = 0.0
            pipe_volume = math.pi * spec["diameter_m"]**2 / 4 * spec["length_m"]
            design_flow = (self.PUMPS[spec["pump"]]["rated_flow_m3h"] * 0.6
                           if key == "concentrator_reclaim" else flow)
            delay = max(1, math.ceil(pipe_volume / max(design_flow / 3600.0, 1e-6)))
            delay = min(delay, 21_600)
            state["links"][key].update(flow_m3h=flow, inlet_flow_m3h=flow, outlet_flow_m3h=flow,
                                        pressure_kpa=pressure, available=flow > 0.1,
                                        pipe_volume_m3=pipe_volume, transport_delay_s=delay,
                                        transport_queue=deque([flow / 3600.0] * delay))
        for name, pump in state["pumps"].items():
            link_key = next(key for key, spec in self.LINKS.items() if spec["pump"] == name)
            flow_m3s = state["links"][link_key]["inlet_flow_m3h"] / 3600.0
            hydraulic_kw = 1000.0 * 9.80665 * flow_m3s * pump["pump_head_m"] / 1000.0
            electrical_kw = hydraulic_kw / max(self.PUMPS[name]["hydraulic_efficiency"], 0.1)
            pump["power_kw"] = electrical_kw
            pump["current_a"] = electrical_kw * 1000.0 / (math.sqrt(3) * 690.0 * 0.86)
        return state

    def migrate(self, state: dict) -> None:
        """Add the concentrator reclaim circuit to sessions created by older builds."""
        defaults = self.initialize()
        for key in ("nodes", "pumps", "controllers", "valves", "links"):
            state.setdefault(key, {})
            for item, value in defaults[key].items():
                state[key].setdefault(item, value)
        for key, value in defaults.items():
            if key not in {"nodes", "pumps", "controllers", "valves", "links"}:
                state.setdefault(key, value)
        for sample in state["history"]:
            sample.setdefault("concentrator_reclaim_sump_level_pct", 0.0)
            sample.setdefault("concentrator_gross_demand_m3h", 0.0)
            sample.setdefault("concentrator_reclaim_m3h", 0.0)
            sample.setdefault("concentrator_makeup_m3h", 0.0)

    @staticmethod
    def _level(node: dict) -> float:
        return node["volume_m3"] / node["area_m2"]

    @staticmethod
    def pipe_inventory_m3(state: dict) -> float:
        return sum(sum(link["transport_queue"]) for link in state["links"].values())

    def total_inventory_m3(self, state: dict) -> float:
        return sum(node["volume_m3"] for node in state["nodes"].values()) + self.pipe_inventory_m3(state)

    def _link_reading(self, state: dict, key: str, spec: dict) -> tuple[float, float]:
        opening_pct = state["valves"][spec["valve"]]
        if opening_pct <= 0:
            return 0.0, 0.0
        if spec["pump"]:
            pump = state["pumps"][spec["pump"]]
            pump["pump_head_m"] = 0.0
            speed = pump["speed_pct"] / 100.0 if pump["running"] else 0.0
            pump_spec = self.PUMPS[spec["pump"]]
            shutoff_head = pump_spec["shutoff_head_m"] * pump["condition_factor"]
            pump_head_at_zero = shutoff_head * speed * speed
            rated_flow_m3s = pump_spec["rated_flow_m3h"] / 3600.0
            pump_curve_slope = shutoff_head / max(rated_flow_m3s**2, 1e-12)
        else:
            speed = 0.0
            pump_head_at_zero = 0.0
            pump_curve_slope = 0.0
        source, destination = state["nodes"][spec["from"]], state["nodes"][spec["to"]]
        static_head = self.NODES[spec["from"]]["elevation_m"] + self._level(source)
        destination_head = self.NODES[spec["to"]]["elevation_m"] + self._level(destination)
        head_at_zero = pump_head_at_zero + static_head - destination_head
        if head_at_zero <= 0:
            return 0.0, 0.0

        valve_k = spec["valve_loss_k"] / max((opening_pct / 100.0) ** 2, 1e-6)
        area = math.pi * spec["diameter_m"] ** 2 / 4.0

        def losses(flow_m3s: float) -> float:
            if flow_m3s <= 1e-12:
                return 0.0
            velocity = flow_m3s / area
            reynolds = velocity * spec["diameter_m"] / 1.004e-6
            if reynolds < 2_300:
                friction = 64.0 / max(reynolds, 1.0)
            else:
                relative_roughness = spec["roughness_m"] / (3.7 * spec["diameter_m"])
                friction = 0.25 / math.log10(relative_roughness + 5.74 / reynolds**0.9) ** 2
            coefficient = friction * spec["length_m"] / spec["diameter_m"] + spec["minor_loss_k"] + valve_k
            return coefficient * velocity**2 / (2.0 * 9.80665)

        def residual(flow_m3s: float) -> float:
            pump_head = pump_head_at_zero - pump_curve_slope * flow_m3s**2
            return head_at_zero + pump_head - pump_head_at_zero - losses(flow_m3s)

        upper = spec["max_flow_m3h"] / 3600.0
        if speed and spec["pump"]:
            upper = min(upper, self.PUMPS[spec["pump"]]["rated_flow_m3h"] / 3600.0 * speed)
        if residual(upper) >= 0:
            flow_m3s = upper
        else:
            low, high = 0.0, upper
            for _ in range(28):
                midpoint = (low + high) / 2.0
                if residual(midpoint) > 0:
                    low = midpoint
                else:
                    high = midpoint
            flow_m3s = (low + high) / 2.0
        flow_m3h = flow_m3s * 3600.0
        pressure_drop_kpa = losses(flow_m3s) * 9.80665
        if spec["pump"]:
            pump_head = pump_head_at_zero - pump_curve_slope * flow_m3s**2
            state["pumps"][spec["pump"]]["pump_head_m"] = max(0.0, pump_head)
        return flow_m3h, pressure_drop_kpa

    def advance(self, state: dict, sim_seconds: int, active_events: list[dict], seed: int = 17,
                concentrator_demand_m3h: float = 0.0, concentrator_reclaim_m3h: float = 0.0,
                factory_cip_demand_m3h: float = 0.0, factory_cip_effluent_m3h: float = 0.0,
                power_available_fraction: float | None = None) -> None:
        """Advance one second with PI level controls, hydraulic links and delayed transport."""
        self.migrate(state)
        state["sim_seconds"] = sim_seconds
        state["sensor_rng_seed"] = seed
        stress = max(({"Small": 1.2, "Medium": 1.6, "Large": 2.1}[e["severity"]]
                      for e in active_events if e["name"] == "Process upset"), default=1.0)
        state["concentrator_demand_m3h"] = max(0.0, concentrator_demand_m3h)
        state["concentrator_reclaim_m3h"] = max(0.0, concentrator_reclaim_m3h)
        state["factory_cip_demand_m3h"] = max(0.0, factory_cip_demand_m3h)
        state["factory_cip_effluent_m3h"] = max(0.0, factory_cip_effluent_m3h)
        state["concentrator_recovery_pct"] = (100.0 * state["concentrator_reclaim_m3h"] /
                                                state["concentrator_demand_m3h"]
                                                if state["concentrator_demand_m3h"] > 1e-9 else 0.0)
        state["base_demand_m3h"] = 240.0 * stress * (1 + 0.08 * math.sin(sim_seconds / 95.0))
        state["demand_m3h"] = (state["base_demand_m3h"] + state["concentrator_demand_m3h"]
                               + state["factory_cip_demand_m3h"])
        state["treated_demand_m3h"] = 96.0 * (1 + 0.12 * math.sin(sim_seconds / 137.0 + 1.2))
        state["water_recovery_pct"] = 68.0 if stress == 1.0 else max(35.0, 68.0 - (stress - 1.0) * 18)
        degradation = max(({"Small": 0.90, "Medium": 0.72, "Large": 0.52}[e["severity"]]
                           for e in active_events if e["name"] == "Mechanical degradation"), default=1.0)
        utility_factor = (max(0.0, min(1.0, power_available_fraction))
                          if power_available_fraction is not None else
                          min(({"Small": 0.75, "Medium": 0.42, "Large": 0.0}[e["severity"]]
                               for e in active_events if e["name"] == "Utility loss"), default=1.0))

        # PI loops regulate the three pumped tank inlets. Output moves through a VFD ramp limit.
        for name, pump in state["pumps"].items():
            pump["condition_factor"] = degradation
            pump["efficiency"] = degradation
            pump["running"] = pump["enabled"] and utility_factor > 0.01
            controller = state["controllers"][name]
            if controller["mode"] == "AUTO":
                level = state["nodes"][pump["controlled_node"]]["level_pct"]
                error = controller["setpoint_pct"] - level
                proposed_integral = controller["integral"] + controller["ki"] * error
                unconstrained = controller["bias_pct"] + controller["kp"] * error + proposed_integral
                output = min(100.0, max(0.0, unconstrained))
                if output == unconstrained or (output >= 100.0 and error < 0) or (output <= 0.0 and error > 0):
                    controller["integral"] = proposed_integral
                controller["output_pct"] = output
                pump["speed_command_pct"] = output * utility_factor
            else:
                pump["speed_command_pct"] = pump["manual_speed_pct"] * utility_factor
            if not pump["enabled"]:
                pump["speed_command_pct"] = 0.0
            ramp = pump["ramp_pct_per_s"]
            delta = pump["speed_command_pct"] - pump["speed_pct"]
            pump["speed_pct"] += min(ramp, max(-ramp, delta))

        requested = {}
        pressure = {}
        for key, spec in self.LINKS.items():
            requested[key], pressure[key] = self._link_reading(state, key, spec)

        # Transfer plug-flow packets through each full pipe. The bounded deque is a
        # lightweight transport delay: one volumetric sample in and one out per step.
        volumes = {key: node["volume_m3"] for key, node in state["nodes"].items()}
        inlet = {}
        outlet = {}

        def transfer(key: str, available_m3: float = float("inf")) -> None:
            link = state["links"][key]
            incoming = min(requested[key], max(0.0, available_m3) * 3600.0)
            link["transport_queue"].append(incoming / 3600.0)
            delivered = link["transport_queue"].popleft() * 3600.0
            inlet[key], outlet[key] = incoming, delivered
            link.update(inlet_flow_m3h=incoming, outlet_flow_m3h=delivered,
                        flow_m3h=delivered, pressure_kpa=pressure[key], available=incoming > 0.1)

        transfer("raw_transfer", volumes["north_reservoir"])
        raw_available = volumes["raw_tank"] + outlet["raw_transfer"] / 3600.0
        transfer("process_feed", raw_available)
        reclaim_sump_available = volumes["concentrator_reclaim_sump"] + state["concentrator_reclaim_m3h"] / 3600.0
        requested["concentrator_reclaim"] = min(requested["concentrator_reclaim"],
                                                 reclaim_sump_available * 3600.0)
        transfer("concentrator_reclaim", reclaim_sump_available)
        process_available_m3 = volumes["process_tank"] + outlet["process_feed"] / 3600.0
        served = min(state["demand_m3h"], process_available_m3 * 3600.0)
        state["unmet_demand_m3h"] = state["demand_m3h"] - served
        # Preserve normal site and concentrator water before low-priority CIP cleaning.
        base_served = min(state["base_demand_m3h"], served)
        concentrator_served = min(state["concentrator_demand_m3h"], max(0.0, served - base_served))
        cip_served = min(state["factory_cip_demand_m3h"],
                         max(0.0, served - base_served - concentrator_served))
        state["base_unmet_m3h"] = state["base_demand_m3h"] - base_served
        state["concentrator_served_m3h"] = concentrator_served
        state["concentrator_unmet_m3h"] = state["concentrator_demand_m3h"] - concentrator_served
        state["factory_cip_served_m3h"] = cip_served
        state["factory_water_available_fraction"] = (cip_served / state["factory_cip_demand_m3h"]
            if state["factory_cip_demand_m3h"] > 1e-9 else 1.0)
        actual_cip_effluent = (state["factory_cip_effluent_m3h"]
            * state["factory_water_available_fraction"])
        state["factory_cip_effluent_m3h"] = actual_cip_effluent
        state["cumulative_factory_cip_effluent_m3"] += actual_cip_effluent / 3600.0
        # Reclaim applies to the baseline circulating process stream. Concentrator
        # makeup is consumed fresh water and is not falsely counted as return flow.
        circulating_served = min(served, state["base_demand_m3h"])
        return_request = min(requested["reclaim_return"],
                             circulating_served * state["water_recovery_pct"] / 100.0)
        requested["reclaim_return"] = return_request
        transfer("reclaim_return", process_available_m3)
        reclaim_available = volumes["reclaim_pond"] + outlet["reclaim_return"] / 3600.0
        transfer("reclaim_transfer", reclaim_available)
        treated_available_m3 = volumes["treated_tank"] + outlet["reclaim_transfer"] / 3600.0
        treated_served = min(state["treated_demand_m3h"], treated_available_m3 * 3600.0)
        state["unmet_treated_demand_m3h"] = state["treated_demand_m3h"] - treated_served

        net_m3h = {key: 0.0 for key in self.NODES}
        net_m3h["concentrator_reclaim_sump"] += state["concentrator_reclaim_m3h"]
        for link_key, link in self.LINKS.items():
            net_m3h[link["from"]] -= inlet[link_key]
            net_m3h[link["to"]] += outlet[link_key]
        process_consumption = max(0.0, served - inlet["reclaim_return"])
        net_m3h["process_tank"] -= process_consumption
        net_m3h["treated_tank"] -= treated_served
        spilled = 0.0
        for key, node in state["nodes"].items():
            node["inflow_m3h"] = sum(outlet[link_key] for link_key, link in self.LINKS.items()
                                      if link["to"] == key)
            node["outflow_m3h"] = sum(inlet[link_key] for link_key, link in self.LINKS.items()
                                       if link["from"] == key)
            if key == "process_tank":
                node["outflow_m3h"] += process_consumption
            elif key == "treated_tank":
                node["outflow_m3h"] += treated_served
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
        state["cumulative_source_withdrawal_m3"] += inlet["raw_transfer"] / 3600.0
        state["cumulative_process_consumption_m3"] += process_consumption / 3600.0
        state["cumulative_treated_delivery_m3"] += treated_served / 3600.0
        state["cumulative_concentrator_return_m3"] += state["concentrator_reclaim_m3h"] / 3600.0

        # Derive shaft load and current from the operating point, then add a small
        # repeatable transmitter noise when tags are published.
        for name, pump in state["pumps"].items():
            link_key = next(key for key, spec in self.LINKS.items() if spec["pump"] == name)
            flow_m3s = inlet[link_key] / 3600.0
            hydraulic_kw = 1000.0 * 9.80665 * flow_m3s * pump["pump_head_m"] / 1000.0
            electrical_kw = hydraulic_kw / max(self.PUMPS[name]["hydraulic_efficiency"] * pump["efficiency"], 0.1)
            pump["power_kw"] = electrical_kw if pump["running"] else 0.0
            pump["current_a"] = (electrical_kw * 1000.0 / (math.sqrt(3) * 690.0 * 0.86)
                                  if pump["running"] else 0.0)
            pump["vibration_mm_s"] = 1.2 + (1.0 - pump["efficiency"]) * 9.0 + max(0.0, 1.0 - flow_m3s * 3600 / self.PUMPS[name]["rated_flow_m3h"]) * 0.4

        if sim_seconds % 15 == 0:
            state["history"].append({"time": sim_seconds, "raw_tank_level_pct": state["nodes"]["raw_tank"]["level_pct"],
                                     "process_tank_level_pct": state["nodes"]["process_tank"]["level_pct"],
                                     "reclaim_pond_level_pct": state["nodes"]["reclaim_pond"]["level_pct"],
                                     "treated_tank_level_pct": state["nodes"]["treated_tank"]["level_pct"],
                                     "concentrator_reclaim_sump_level_pct": state["nodes"]["concentrator_reclaim_sump"]["level_pct"],
                                     "intake_flow_m3h": inlet["raw_transfer"],
                                     "intake_arrival_m3h": outlet["raw_transfer"],
                                     "process_feed_m3h": outlet["process_feed"],
                                     "process_demand_m3h": state["demand_m3h"],
                                     "concentrator_gross_demand_m3h": state["concentrator_demand_m3h"],
                                     "factory_cip_demand_m3h": state["factory_cip_demand_m3h"],
                                     "factory_water_available_fraction": state["factory_water_available_fraction"],
                                     "factory_cip_effluent_m3h": actual_cip_effluent,
                                     "concentrator_reclaim_m3h": state["concentrator_reclaim_m3h"],
                                     "concentrator_makeup_m3h": max(
                                         0.0, state["concentrator_demand_m3h"]
                                         - state["concentrator_reclaim_m3h"]),
                                     "treated_demand_m3h": state["treated_demand_m3h"],
                                     "intake_pump_speed_pct": state["pumps"]["Intake pump"]["speed_pct"],
                                     "process_pump_speed_pct": state["pumps"]["Process feed pump"]["speed_pct"]})
            state["history"] = state["history"][-1_440:]

    def telemetry(self, state: dict) -> tuple[list[dict], list[dict]]:
        """Return noisy deterministic instrument tags and the process truth diagram."""
        rows = []

        def add(asset: str, signal: str, value: float, units: str, low: float, high: float,
                noise_fraction: float = 0.0, last_change: str = "Water network instrument") -> None:
            tag_name = f"water_supply_network.{asset}.{signal}"
            if noise_fraction > 0:
                stable_id = sum((index + 1) * ord(char) for index, char in enumerate(tag_name))
                rng = random.Random(state["sensor_rng_seed"] * 1_000_003 +
                                    state["sim_seconds"] * 9_176 + stable_id)
                value += rng.gauss(0.0, max(high - low, 1e-9) * noise_fraction)
            value = min(high, max(low, value))
            rows.append({"tag": tag_name, "area": self.AREA,
                         "unit": asset.replace("_", " ").title(), "sensor": signal, "units": units,
                         "value": round(value, 3), "quality": "GOOD", "low": low, "high": high,
                         "base": (low + high) / 2, "phase": 0.0, "last_change": last_change})

        for key, node in state["nodes"].items():
            add(key, "tank_level", node["level_pct"], "%", 0, 100, 0.0015)
            add(key, "stored_volume", node["volume_m3"], "m³", 0, node["capacity_m3"], 0.0015)
        for key, link in self.LINKS.items():
            reading = state["links"][key]
            area = math.pi * link["diameter_m"]**2 / 4
            add(key, "flow_rate", reading["inlet_flow_m3h"], "m³/h", 0, link["max_flow_m3h"], 0.01)
            add(key, "arrival_flow_rate", reading["outlet_flow_m3h"], "m³/h", 0, link["max_flow_m3h"], 0.01)
            add(key, "pressure_drop", reading["pressure_kpa"], "kPa", 0, 1200, 0.008)
            velocity = reading["inlet_flow_m3h"] / 3600 / area
            add(key, "line_velocity", velocity, "m/s", 0, 5, 0.005)
            add(key, "transport_delay", reading["transport_delay_s"], "s", 0, 21_600)
        for name, pump in state["pumps"].items():
            asset = name.lower().replace(" ", "_")
            add(asset, "pump_running", float(pump["running"]), "bool", 0, 1)
            add(asset, "speed_command", pump["speed_command_pct"], "%", 0, 100)
            add(asset, "speed_feedback", pump["speed_pct"], "%", 0, 100)
            add(asset, "pump_efficiency", self.PUMPS[name]["hydraulic_efficiency"] * pump["efficiency"] * 100.0,
                "%", 0, 100)
            add(asset, "pump_condition", pump["condition_factor"] * 100.0, "%", 0, 100)
            add(asset, "pump_head", pump["pump_head_m"], "m", 0, 200, 0.006)
            add(asset, "motor_current", pump["current_a"], "A", 0, 800, 0.01)
            add(asset, "motor_power", pump["power_kw"], "kW", 0, 800, 0.01)
            add(asset, "vibration_rms", pump["vibration_mm_s"], "mm/s", 0, 25, 0.02)
            controller = state["controllers"][name]
            add(asset, "level_setpoint", controller["setpoint_pct"], "%", 0, 100)
            add(asset, "controller_output", controller["output_pct"], "%", 0, 100)
            add(asset, "controller_auto", float(controller["mode"] == "AUTO"), "bool", 0, 1)
        for name, opening in state["valves"].items():
            add(name.lower().replace(" ", "_"), "valve_position", opening, "%", 0, 100)
        add("process_demand", "water_demand", state["demand_m3h"], "m³/h", 0, 1_200, 0.005)
        add("process_demand", "unmet_demand", state["unmet_demand_m3h"], "m³/h", 0, 600, 0.005)
        add("process_demand", "water_recovery", state["water_recovery_pct"], "%", 0, 100, 0.002)
        add("process_demand", "concentrator_recovery", state["concentrator_recovery_pct"], "%", 0, 100, 0.002)
        add("process_demand", "concentrator_return", state["concentrator_reclaim_m3h"], "m³/h", 0, 700, 0.005)
        add("process_demand", "factory_cip_demand", state["factory_cip_demand_m3h"], "m³/h", 0, 40, 0.005)
        add("process_demand", "factory_cip_served", state["factory_cip_served_m3h"], "m³/h", 0, 40, 0.005)
        add("process_demand", "concentrator_unmet_demand", state.get("concentrator_unmet_m3h", 0.0), "m³/h", 0, 800, 0.005)
        add("process_demand", "factory_cip_water_availability", state["factory_water_available_fraction"] * 100, "%", 0, 100)
        add("process_demand", "factory_cip_effluent", state["factory_cip_effluent_m3h"], "m³/h", 0, 40, 0.005)
        add("process_demand", "treated_water_demand", state["treated_demand_m3h"], "m³/h", 0, 300, 0.005)
        add("process_demand", "unmet_treated_water_demand", state["unmet_treated_demand_m3h"], "m³/h", 0, 300, 0.005)
        nodes = [{"id": key, "label": spec["label"], "level_pct": state["nodes"][key]["level_pct"],
                  "volume_m3": state["nodes"][key]["volume_m3"], "capacity_m3": spec["capacity_m3"]}
                 for key, spec in self.NODES.items()]
        links = [{"id": key, "label": spec["label"], "from": spec["from"], "to": spec["to"],
                  "flow_m3h": state["links"][key]["outlet_flow_m3h"],
                  "inlet_flow_m3h": state["links"][key]["inlet_flow_m3h"],
                  "pressure_kpa": state["links"][key]["pressure_kpa"],
                  "transport_delay_s": state["links"][key]["transport_delay_s"],
                  "pipe_inventory_m3": sum(state["links"][key]["transport_queue"]),
                  "pump": spec["pump"]} for key, spec in self.LINKS.items()]
        return rows, [{"nodes": nodes, "links": links,
                       "pumps": {name: {"enabled": pump["enabled"], "running": pump["running"],
                                         "mode": state["controllers"][name]["mode"],
                                         "speed_pct": pump["speed_pct"],
                                         "speed_command_pct": pump["speed_command_pct"],
                                         "current_a": pump["current_a"], "power_kw": pump["power_kw"],
                                         "pump_head_m": pump["pump_head_m"],
                                         "condition_factor": pump["condition_factor"]}
                                 for name, pump in state["pumps"].items()},
                       "valves": state["valves"], "controllers": state["controllers"],
                       "demand_m3h": state["demand_m3h"],
                       "unmet_demand_m3h": state["unmet_demand_m3h"],
                       "treated_demand_m3h": state["treated_demand_m3h"],
                       "electrical_load_kw": sum(pump["power_kw"] for pump in state["pumps"].values()),
                       "unmet_treated_demand_m3h": state["unmet_treated_demand_m3h"],
                       "concentrator_recovery_pct": state["concentrator_recovery_pct"],
                       "pipe_inventory_m3": self.pipe_inventory_m3(state),
                       "total_inventory_m3": self.total_inventory_m3(state),
                       "total_spilled_m3": state["total_spilled_m3"]}]
