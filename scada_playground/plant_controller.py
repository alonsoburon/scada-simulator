"""Deterministic PLC-style sequence for a small concentrator circuit.

This models ordinary process control and equipment protection only. The
simulated trips are not a certified safety instrumented system.
"""
from __future__ import annotations


class PlantController:
    START_ORDER = ("tailings_pump", "tailings_thickener", "filter_press",
                   "concentrate_thickener", "flotation", "ball_mill",
                   "sag_mill", "conveyors", "crusher")
    STOP_ORDER = ("crusher", "conveyors", "sag_mill", "ball_mill", "flotation",
                  "concentrate_thickener", "filter_press", "tailings_thickener", "tailings_pump")
    START_DELAY_S = {"tailings_pump": 4, "tailings_thickener": 8, "filter_press": 8,
                     "concentrate_thickener": 7, "flotation": 5, "ball_mill": 12,
                     "sag_mill": 12, "conveyors": 5, "crusher": 8}
    STOP_DELAY_S = {"crusher": 3, "conveyors": 5, "sag_mill": 15,
                    "ball_mill": 10, "flotation": 5, "concentrate_thickener": 10,
                    "filter_press": 8, "tailings_thickener": 12, "tailings_pump": 8}
    LABELS = {"tailings_pump": "Tailings pump", "tailings_thickener": "Tailings thickener",
              "filter_press": "Concentrate filter press",
              "concentrate_thickener": "Concentrate thickener", "flotation": "Flotation air and cells",
              "ball_mill": "Ball mill", "sag_mill": "SAG mill",
              "conveyors": "Ore conveyors", "crusher": "Primary crusher"}

    def initialize(self) -> dict:
        # The default dashboard opens on a stable operating plant. Its timeline is
        # paused, but equipment is running until an operator requests a stop.
        return {
            "mode": "RUNNING", "requested_mode": "RUNNING", "sequence_index": len(self.START_ORDER),
            "sequence_timer_s": 0, "last_transition": "Plant initialized in steady operation",
            "permissive_reason": "All startup permissives healthy",
            "trip_count": 0, "history": [], "scan_seconds": 1,
            "equipment": {name: {"state": "RUNNING", "elapsed_s": 0,
                                 "trip_reason": "", "starts": 1, "trips": 0}
                          for name in self.START_ORDER},
            "condition_timers": {},
        }

    def migrate(self, state: dict) -> None:
        """Keep a live Streamlit session valid across controller schema updates."""
        defaults = self.initialize()
        equipment = state.setdefault("equipment", {})
        for name, initial in defaults["equipment"].items():
            equipment.setdefault(name, {**initial})
        for key, value in defaults.items():
            if key in ("equipment", "history", "condition_timers"):
                continue
            state.setdefault(key, value)
        state.setdefault("history", [])
        state.setdefault("condition_timers", {})
        if state["requested_mode"] == "RUNNING" and all(
                item["state"] == "RUNNING" for item in equipment.values()):
            state["sequence_index"] = len(self.START_ORDER)
        elif state["requested_mode"] == "STOPPED":
            state["sequence_index"] = 0

    @staticmethod
    def _active(events: list[dict], name: str, areas: tuple[str, ...]) -> bool:
        return any(event["name"] == name and event["area"] in (*areas, "Whole factory") for event in events)

    def request(self, state: dict, command: str) -> None:
        command = command.strip().upper()
        if command not in {"START", "STOP"}:
            raise ValueError("Process command must be START or STOP")
        requested_mode = "RUNNING" if command == "START" else "STOPPED"
        if state["requested_mode"] == requested_mode:
            return
        if command == "START" and state["mode"] == "STOPPING":
            raise ValueError("Finish the orderly stop sequence before starting again")
        if command == "START" and any(equipment["state"] == "TRIPPED"
                                      for equipment in state["equipment"].values()):
            raise ValueError("Reset all active equipment trips before starting the process")
        state["requested_mode"] = requested_mode
        if command == "STOP" and all(equipment["state"] == "STOPPED"
                                      for equipment in state["equipment"].values()):
            state["mode"] = "STOPPED"
            state["sequence_index"] = len(self.STOP_ORDER)
            state["last_transition"] = "Process already stopped"
            return
        state["mode"] = "STARTING" if command == "START" else "STOPPING"
        state["sequence_index"] = 0
        state["sequence_timer_s"] = 0
        state["last_transition"] = f"Operator requested orderly {command.lower()} sequence"

    def reset_trips(self, state: dict, *, events: list[dict], power_fraction: float,
                    voltage_pu: float, frequency_hz: float) -> list[str]:
        if power_fraction < 0.90 or voltage_pu < 0.92 or frequency_hz < 49.5:
            raise ValueError("Reset blocked: electrical supply has not recovered to reset limits")
        jam_active = self._active(events, "Conveyor jam", ("Crushing and screening",))
        large_mechanical = any(event["name"] == "Mechanical degradation"
                               and event["severity"] == "Large"
                               and event["area"] in ("Crushing and screening", "Grinding and flotation",
                                                     "Concentrate handling", "Tailings and reclaim",
                                                     "Whole factory")
                               for event in events)
        if jam_active or large_mechanical:
            raise ValueError("Reset blocked: clear the active jam or severe mechanical condition first")
        cleared = []
        for name, equipment in state["equipment"].items():
            if equipment["state"] == "TRIPPED":
                equipment.update(state="STOPPED", elapsed_s=0, trip_reason="")
                cleared.append(name)
        if cleared:
            state["trip_count"] = max(0, state["trip_count"] - len(cleared))
            state["mode"] = "STOPPED"
            state["requested_mode"] = "STOPPED"
            state["sequence_index"] = 0
            state["last_transition"] = "Operator reset trips: " + ", ".join(self.LABELS[name] for name in cleared)
        return cleared

    def _trip(self, state: dict, equipment_names: tuple[str, ...], cause: str) -> None:
        # Ordinary process interlocks cascade upstream when a required service
        # disappears. These are control protections, not certified safety trips.
        cascade = {
            "tailings_pump": ("tailings_thickener", "flotation", "ball_mill", "sag_mill"),
            "tailings_thickener": ("flotation", "ball_mill", "sag_mill"),
            "filter_press": ("concentrate_thickener", "flotation", "ball_mill", "sag_mill"),
            "concentrate_thickener": ("flotation", "ball_mill", "sag_mill"),
            "flotation": ("ball_mill", "sag_mill"),
            "ball_mill": ("sag_mill",),
            "conveyors": ("crusher",),
        }
        pending = list(equipment_names)
        expanded = set()
        while pending:
            name = pending.pop()
            if name in expanded:
                continue
            expanded.add(name)
            pending.extend(cascade.get(name, ()))
        tripped_any = False
        for name in self.START_ORDER:
            if name not in expanded:
                continue
            equipment = state["equipment"][name]
            if equipment["state"] in ("TRIPPED", "STOPPED"):
                continue
            equipment.update(state="TRIPPED", elapsed_s=0, trip_reason=cause)
            equipment["trips"] += 1
            state["trip_count"] += 1
            tripped_any = True
        if not tripped_any:
            return
        state["mode"] = "TRIPPED"
        state["requested_mode"] = "STOPPED"
        state["last_transition"] = cause

    def advance(self, state: dict, sim_seconds: int, events: list[dict], *,
                process_power_fraction: float, water_power_fraction: float,
                process_water_level_pct: float, voltage_pu: float,
                frequency_hz: float) -> None:
        """Execute one simulated PLC scan, sequence transitions, and latched trips."""
        state["sim_seconds"] = sim_seconds
        electrical_trip = (process_power_fraction < 0.70 or voltage_pu < 0.82
                           or frequency_hz < 48.0)
        timers = state["condition_timers"]

        def timed_condition(key: str, active: bool) -> bool:
            timers[key] = timers.get(key, 0) + 1 if active else 0
            return timers[key] >= 3

        jam = self._active(events, "Conveyor jam", ("Crushing and screening",))
        area_by_equipment = {
            "crusher": ("Crushing and screening",), "conveyors": ("Crushing and screening",),
            "sag_mill": ("Grinding and flotation",), "ball_mill": ("Grinding and flotation",),
            "flotation": ("Grinding and flotation",),
            "concentrate_thickener": ("Concentrate handling",),
            "filter_press": ("Concentrate handling",),
            "tailings_thickener": ("Tailings and reclaim",),
            "tailings_pump": ("Tailings and reclaim",),
        }
        affected = tuple(name for name, areas in area_by_equipment.items()
                         if any(event["name"] == "Mechanical degradation"
                                and event["severity"] == "Large"
                                and event["area"] in (*areas, "Whole factory") for event in events))
        electrical_trip_ready = timed_condition("electrical_trip", electrical_trip)
        jam_trip_ready = timed_condition("crusher_jam", jam)
        mechanical_trip_ready = timed_condition("mechanical_trip", bool(affected))
        if electrical_trip_ready:
            self._trip(state, self.START_ORDER, "Undervoltage / underfrequency motor protection")
        elif jam_trip_ready:
            self._trip(state, ("crusher", "conveyors"), "Crusher/conveyor jam trip")
        elif mechanical_trip_ready:
            self._trip(state, affected, "High vibration mechanical protection")

        if any(item["state"] == "TRIPPED" for item in state["equipment"].values()):
            state["mode"] = "TRIPPED"
            state["permissive_reason"] = "One or more motor trips are latched; inspect, reset, then restart manually"
            return

        if state["requested_mode"] == "RUNNING":
            state["mode"] = "STARTING"
            if state["sequence_index"] >= len(self.START_ORDER):
                state["mode"] = "RUNNING"
                state["permissive_reason"] = "All equipment running; process permissives healthy"
                return
            name = self.START_ORDER[state["sequence_index"]]
            equipment = state["equipment"][name]
            if name == "tailings_pump":
                permit = process_power_fraction >= 0.90 and voltage_pu >= 0.92
                reason = "Healthy mine bus required for tailings pump start"
            elif name == "flotation":
                permit = (state["equipment"]["tailings_pump"]["state"] == "RUNNING"
                          and state["equipment"]["tailings_thickener"]["state"] == "RUNNING"
                          and state["equipment"]["filter_press"]["state"] == "RUNNING"
                          and state["equipment"]["concentrate_thickener"]["state"] == "RUNNING"
                          and water_power_fraction >= 0.80 and process_water_level_pct >= 20)
                reason = "Concentrate/tailings handling, process water, and electrical supply required for flotation"
            elif name == "tailings_thickener":
                permit = state["equipment"]["tailings_pump"]["state"] == "RUNNING"
                reason = "Tailings pump ready before tailings thickener starts"
            elif name == "filter_press":
                permit = process_power_fraction >= 0.90
                reason = "Healthy process electrical supply required for filter press"
            elif name == "concentrate_thickener":
                permit = state["equipment"]["filter_press"]["state"] == "RUNNING"
                reason = "Filter press ready before concentrate thickener starts"
            elif name in ("ball_mill", "sag_mill"):
                permit = (state["equipment"]["tailings_pump"]["state"] == "RUNNING"
                          and water_power_fraction >= 0.80 and process_water_level_pct >= 20)
                if name == "sag_mill":
                    permit = permit and state["equipment"]["ball_mill"]["state"] == "RUNNING"
                reason = f"Required utilities and upstream mill permissive required for {self.LABELS[name]}"
            elif name == "conveyors":
                permit = (state["equipment"]["sag_mill"]["state"] == "RUNNING"
                          and state["equipment"]["crusher"]["state"] != "TRIPPED")
                reason = "Downstream mill ready before ore conveyors start"
            else:
                permit = (state["equipment"]["conveyors"]["state"] == "RUNNING"
                          and process_power_fraction >= 0.90
                          and not self._active(events, "Conveyor jam", ("Crushing and screening",)))
                reason = "Conveyors, clear chute, and healthy electrical supply required for crusher"
            if not permit:
                state["permissive_reason"] = reason
                return
            state["permissive_reason"] = "Startup permissives healthy"
            if equipment["state"] == "STOPPED":
                equipment.update(state="STARTING", elapsed_s=0)
                state["sequence_timer_s"] = 0
                state["last_transition"] = f"Starting {self.LABELS[name]}"
            equipment["elapsed_s"] += 1
            state["sequence_timer_s"] += 1
            if equipment["elapsed_s"] >= self.START_DELAY_S[name]:
                equipment.update(state="RUNNING", elapsed_s=0)
                equipment["starts"] += 1
                state["sequence_index"] += 1
                state["last_transition"] = f"{self.LABELS[name]} run feedback confirmed"
            return

        state["mode"] = "STOPPING"
        if state["sequence_index"] >= len(self.STOP_ORDER):
            state["mode"] = "STOPPED"
            state["permissive_reason"] = "All process motors stopped in sequence"
            return
        name = self.STOP_ORDER[state["sequence_index"]]
        equipment = state["equipment"][name]
        if equipment["state"] == "STOPPED":
            state["sequence_index"] += 1
            return
        if equipment["state"] == "RUNNING":
            equipment.update(state="STOPPING", elapsed_s=0)
            state["last_transition"] = f"Stopping {self.LABELS[name]} orderly"
        equipment["elapsed_s"] += 1
        state["sequence_timer_s"] += 1
        if equipment["elapsed_s"] >= self.STOP_DELAY_S[name]:
            equipment.update(state="STOPPED", elapsed_s=0)
            state["sequence_index"] += 1
            state["last_transition"] = f"{self.LABELS[name]} stopped"

    @staticmethod
    def availability(state: dict) -> dict[str, bool]:
        equipment = state["equipment"]
        # A commanded stop stays process-available through its modeled coastdown.
        return {name: item["state"] in ("RUNNING", "STOPPING") for name, item in equipment.items()}

    def public_state(self, state: dict) -> dict:
        return {key: value for key, value in state.items() if key not in ("history", "condition_timers")}
