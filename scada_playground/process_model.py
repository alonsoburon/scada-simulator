"""Deterministic synthetic process behavior, independent of the Streamlit UI."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Callable


@dataclass
class ProcessEvent:
    name: str
    area: str
    severity: str
    started: int
    duration: int
    event_id: int


class FactoryProcessModel:
    """Apply one deterministic simulated second to the plant state."""

    def __init__(self, areas: dict[str, list[str]], incidents: dict[str, str],
                 fleet_motion: Callable[[str, int], tuple]):
        self.areas = areas
        self.incidents = incidents
        self.fleet_motion = fleet_motion

    def inject(self, sim: dict, name: str, area: str, severity: str,
               duration_seconds: int | None = None) -> ProcessEvent:
        if name not in self.incidents:
            raise ValueError(f"Unknown incident: {name}")
        if severity not in {"Small", "Medium", "Large"}:
            raise ValueError("Severity must be Small, Medium, or Large")
        if area != "Whole factory" and area not in self.areas:
            raise ValueError(f"Unknown target area: {area}")
        duration = duration_seconds or {"Small": 90, "Medium": 240, "Large": 600}[severity]
        if not 1 <= duration <= 86_400:
            raise ValueError("Incident duration must be between 1 and 86,400 simulated seconds")
        event = ProcessEvent(name, area, severity, sim["tick"], duration, sim["next_event"])
        sim["events"].append(event)
        sim["next_event"] += 1
        sim["alarms"].insert(0, {"time": sim["tick"], "area": area, "severity": severity.upper(),
                                "message": f"{name} injected in {area}", "state": "ACTIVE / UNACK"})
        return event

    def advance(self, sim: dict, *, force: bool = False) -> None:
        """Advance exactly one simulation second; ``force`` supports paused single-step."""
        if not sim["running"] and not force:
            return
        sim["tick"] += 1
        rng = random.Random(sim["seed"] + sim["tick"] * 8000)
        expired = [event for event in sim["events"] if sim["tick"] - event.started >= event.duration]
        for event in expired:
            sim["alarms"].insert(0, {"time": sim["tick"], "area": event.area, "severity": "INFO",
                                    "message": f"{event.name} cleared in {event.area}", "state": "CLEARED"})
        live = [event for event in sim["events"] if event not in expired]
        sim["events"] = live
        active_by_area = {area: [event for event in live if event.area in (area, "Whole factory")]
                          for area in self.areas}

        for tag in sim["tags"]:
            # Process-specific models own these values. Keep this broad model
            # responsible only for unmodeled equipment and the site fleet.
            if tag.get("dynamic_model"):
                issues = active_by_area[tag["area"]]
                comms_fault = next((event for event in issues
                                    if self.incidents[event.name] == "comms"), None)
                tag["quality"] = "STALE" if comms_fault else "GOOD"
                tag["last_change"] = comms_fault.name if comms_fault else "Dynamic process model"
                continue
            if tag["sensor"] in ("gps_latitude", "gps_longitude", "gps_speed", "heading", "payload"):
                latitude, longitude, speed, heading, payload, _state = self.fleet_motion(tag["unit"], sim["tick"])
                tag["value"] = round({"gps_latitude": latitude, "gps_longitude": longitude,
                                      "gps_speed": speed, "heading": heading, "payload": payload}[tag["sensor"]], 3)
                tag["quality"] = "GOOD"
                tag["last_change"] = "Moving along simulated route"
                continue

            base = tag["base"]
            span = tag["high"] - tag["low"]
            value = base + span * (0.012 * (rng.random() - 0.5) +
                                   0.018 * math.sin(sim["tick"] / 67 + tag["phase"]))
            issues = active_by_area[tag["area"]]
            for event in issues:
                intensity = {"Small": 0.12, "Medium": 0.34, "Large": 0.72}[event.severity]
                kind = self.incidents[event.name]
                if kind == "measurement" and tag["sensor"] in ("process_temperature", "line_pressure"):
                    value += span * intensity
                elif kind == "mechanical" and tag["sensor"] in ("vibration_rms", "motor_current"):
                    value += span * intensity * 0.7
                elif kind == "process" and tag["sensor"] in ("tank_level", "flow_rate", "process_temperature"):
                    value -= span * intensity * 0.45
                elif kind == "utility" and tag["sensor"] in ("production_rate", "flow_rate", "line_pressure"):
                    value *= 1 - intensity
                elif kind == "jam" and tag["sensor"] in ("production_rate", "motor_current", "vibration_rms"):
                    value += span * intensity * (1 if tag["sensor"] != "production_rate" else -1)
                tag["quality"] = "STALE" if kind == "comms" else "GOOD"
                tag["last_change"] = event.name
            if not issues:
                tag["quality"] = "GOOD"
                tag["last_change"] = "Normal"
            tag["value"] = round(min(tag["high"] * 1.15, max(tag["low"] - span * 0.1, value)), 2)
