"""Deterministic synthetic process behavior, independent of the Streamlit UI."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

try:
    from .instrumentation import correlated_sensor_noise, signal_drift_bias
except ImportError:
    from instrumentation import correlated_sensor_noise, signal_drift_bias


@dataclass
class ProcessEvent:
    name: str
    area: str
    severity: str
    started: int
    duration: int
    event_id: int
    target_asset: str | None = None
    fault_location_pct: float = 90.0
    primary_breaker_failure: bool = False
    target_tag: str | None = None
    measurement_baseline: float | None = None
    measurement_baseline_tick: int | None = None


class FactoryProcessModel:
    """Apply one deterministic simulated second to the plant state."""

    def __init__(self, areas: dict[str, list[str]], incidents: dict[str, str],
                 fleet_motion: Callable[[str, int], tuple]):
        self.areas = areas
        self.incidents = incidents
        self.fleet_motion = fleet_motion

    def inject(self, sim: dict, name: str, area: str, severity: str,
               duration_seconds: int | None = None,
               target_asset: str | None = None, fault_location_pct: float = 90.0,
               primary_breaker_failure: bool = False,
               target_tag: str | None = None) -> ProcessEvent:
        if name not in self.incidents:
            raise ValueError(f"Unknown incident: {name}")
        if severity not in {"Small", "Medium", "Large"}:
            raise ValueError("Severity must be Small, Medium, or Large")
        if area != "Whole factory" and area not in self.areas:
            raise ValueError(f"Unknown target area: {area}")
        valid_assets = (set(asset for assets in self.areas.values() for asset in assets)
                        if area == "Whole factory" else set(self.areas.get(area, [])))
        if target_asset is not None and target_asset not in valid_assets:
            raise ValueError(f"Unknown target asset {target_asset!r} in {area}")
        if name == "Electrical fault" and area != "Grid distribution":
            raise ValueError("Electrical fault must target Grid distribution")
        if name == "Water main leak" and area != "Water distribution":
            raise ValueError("Water main leak must target Water distribution")
        if name == "Water main leak" and target_asset not in {
                "District zone A", "District zone B", "District zone C"}:
            raise ValueError("Water main leak must target one named district zone")
        if name == "Electrical fault" and target_asset not in {
                "Feeder north", "Feeder south", "Feeder east", "Feeder west"}:
            raise ValueError("Electrical fault must target one distribution feeder")
        if name == "Electrical fault" and not 0.0 <= float(fault_location_pct) <= 100.0:
            raise ValueError("Fault location must be between 0 and 100 percent of feeder length")
        if target_tag is not None:
            if name not in {"Signal drift", "Signal freeze"}:
                raise ValueError("target_tag is only valid for Signal drift or Signal freeze")
            matching = next((tag for tag in sim["tags"] if tag["tag"] == target_tag), None)
            if matching is None or (area != "Whole factory" and matching["area"] != area):
                raise ValueError("target_tag must identify a tag in the selected target area")
            if matching.get("units") in {"bool", "enum", "cycles"}:
                raise ValueError("Instrument faults require an analog measurement tag")
        elif name in {"Signal drift", "Signal freeze"}:
            # A real instrument fault affects one transmitter. Select a stable,
            # reproducible analog point so the operator has healthy peers to compare.
            candidates = [tag for tag in sim["tags"]
                          if tag.get("dynamic_model")
                          and tag.get("units") not in {"bool", "enum", "cycles"}
                          and (area == "Whole factory" or tag["area"] == area)]
            if not candidates:
                raise ValueError(f"No analog measurement tags are available in {area}")
            target_tag = candidates[sim["next_event"] % len(candidates)]["tag"]
        duration = duration_seconds or {"Small": 90, "Medium": 240, "Large": 600}[severity]
        if not 1 <= duration <= 86_400:
            raise ValueError("Incident duration must be between 1 and 86,400 simulated seconds")
        event = ProcessEvent(name, area, severity, sim["tick"], duration,
                             sim["next_event"], target_asset, float(fault_location_pct),
                             bool(primary_breaker_failure), target_tag)
        if name == "Signal freeze" and target_tag:
            matching = next(tag for tag in sim["tags"] if tag["tag"] == target_tag)
            event.measurement_baseline = float(matching["value"])
        sim["events"].append(event)
        sim["next_event"] += 1
        description = f"{name} injected in {area}"
        if name == "Electrical fault":
            description += f" on {target_asset} at {fault_location_pct:.0f}% of feeder length"
            if primary_breaker_failure:
                description += "; feeder breaker failure simulated"
        if name == "Water main leak":
            description += f" on {target_asset}"
        if target_tag:
            description += f" on instrument {target_tag}"
        sim["alarms"].insert(0, {"time": sim["tick"], "area": area, "severity": severity.upper(),
                                "message": description, "state": "ACTIVE / UNACK"})
        return event

    @staticmethod
    def apply_instrument_faults(tags: list[dict], events: list[ProcessEvent], tick: int) -> None:
        """Apply reproducible instrument faults after process models refresh tags.

        A frozen level is also supplied to its process controller by the water
        network model; other targets are telemetry-only instrument faults.
        """
        instrument_fault_by_tag = {event.target_tag: event for event in events
                                   if event.name in {"Signal drift", "Signal freeze"}
                                   and event.target_tag}
        for tag in tags:
            event = instrument_fault_by_tag.get(tag["tag"])
            if event is None:
                continue
            if event.name == "Signal freeze":
                tag["value"] = round(float(event.measurement_baseline), 3)
                tag["quality"] = "BAD"
                tag["last_change"] = "Signal frozen at last value"
                continue
            span = max(0.0, float(tag.get("high", 0.0)) - float(tag.get("low", 0.0)))
            if span <= 0.0:
                continue
            bias = signal_drift_bias(tag["tag"], event.severity, event.duration,
                                     tick - event.started, span)
            low, high = float(tag.get("low", 0.0)), float(tag.get("high", 0.0))
            if event.measurement_baseline_tick != tick:
                event.measurement_baseline = float(tag["value"])
                event.measurement_baseline_tick = tick
            baseline = float(event.measurement_baseline)
            tag["value"] = round(min(high, max(low, baseline + bias)), 3)
            tag["last_change"] = f"Signal drift ({bias:+.3f} {tag.get('units', '')})"

    def advance(self, sim: dict, *, force: bool = False) -> None:
        """Advance exactly one simulation second; ``force`` supports paused single-step."""
        if not sim["running"] and not force:
            return
        sim["tick"] += 1
        expired = [event for event in sim["events"] if sim["tick"] - event.started >= event.duration]
        for event in expired:
            description = f"{event.name} cleared in {event.area}"
            if event.target_asset:
                description += f" on {event.target_asset}"
            sim["alarms"].insert(0, {"time": sim["tick"], "area": event.area, "severity": "INFO",
                                    "message": description, "state": "CLEARED"})
        live = [event for event in sim["events"] if event not in expired]
        sim["events"] = live
        active_by_area = {area: [event for event in live if event.area in (area, "Whole factory")]
                          for area in self.areas}

        for tag in sim["tags"]:
            # Process-specific models own these values. Keep this broad model
            # responsible only for unmodeled equipment and the site fleet.
            if tag.get("dynamic_model"):
                issues = [event for event in active_by_area[tag["area"]]
                          if event.target_asset is None or event.target_asset.casefold() == tag["unit"].casefold()]
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
            value = base
            issues = [event for event in active_by_area[tag["area"]]
                      if event.target_asset is None or event.target_asset.casefold() == tag["unit"].casefold()]
            for event in issues:
                intensity = {"Small": 0.12, "Medium": 0.34, "Large": 0.72}[event.severity]
                kind = self.incidents[event.name]
                if (kind == "measurement" and tag["sensor"] in ("process_temperature", "line_pressure")
                        and event.target_tag is None):
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
            if tag.get("units") not in {"bool", "enum", "cycles"}:
                value += correlated_sensor_noise(
                    sim, tag["tag"], sim["seed"], sim["tick"],
                    max(0.0, span) * 0.002)
            tag["value"] = round(min(tag["high"] * 1.15, max(tag["low"] - span * 0.1, value)), 2)
