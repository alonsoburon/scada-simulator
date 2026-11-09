"""Protocol-specific telemetry fault injection for isolated client testing."""
from __future__ import annotations

from copy import deepcopy


PROTOCOLS = ("all", "Modbus TCP", "OPC UA", "MQTT")
FAULT_MODES = ("stale", "bad", "freeze", "suppress")


class ProtocolFaultInjector:
    """Validate faults, expire them in simulation time, and transform protocol output."""

    def __init__(self):
        self._frozen_values: dict[tuple[str, str], float] = {}

    def add(self, sim: dict, protocol: str, mode: str, selector_type: str,
            target: str, duration_seconds: int = 0) -> dict:
        if protocol not in PROTOCOLS:
            raise ValueError(f"protocol must be one of {', '.join(PROTOCOLS)}")
        if mode not in FAULT_MODES:
            raise ValueError(f"mode must be one of {', '.join(FAULT_MODES)}")
        if selector_type not in {"tag", "area"}:
            raise ValueError("selector_type must be 'tag' or 'area'")
        if duration_seconds < 0 or duration_seconds > 86_400:
            raise ValueError("duration_seconds must be 0 (until cleared) or between 1 and 86,400")
        target_key = target.strip().casefold()
        exists = any(tag["tag"].casefold() == target_key for tag in sim["tags"]) if selector_type == "tag" \
            else any(tag["area"].casefold() == target_key for tag in sim["tags"])
        if not target_key or not exists:
            raise ValueError(f"No {selector_type} matches {target!r}")
        fault_id = f"PF-{sim['simulation_id'][:8]}-{sim['next_fault_id']:03d}"
        sim["next_fault_id"] += 1
        fault = {"id": fault_id, "protocol": protocol, "mode": mode,
                 "selector_type": selector_type, "target": target,
                 "created_at_sim_seconds": sim["tick"],
                 "expires_at_sim_seconds": sim["tick"] + duration_seconds if duration_seconds else None}
        sim["protocol_faults"].append(fault)
        return fault

    def clear(self, sim: dict, fault_id: str = "") -> int:
        before = len(sim["protocol_faults"])
        if fault_id:
            sim["protocol_faults"] = [fault for fault in sim["protocol_faults"] if fault["id"] != fault_id]
        else:
            sim["protocol_faults"] = []
        self._discard_inactive_cache(sim["protocol_faults"])
        return before - len(sim["protocol_faults"])

    def expire(self, sim: dict) -> list[dict]:
        expired = [fault for fault in sim["protocol_faults"]
                   if fault["expires_at_sim_seconds"] is not None
                   and sim["tick"] >= fault["expires_at_sim_seconds"]]
        expired_ids = {fault["id"] for fault in expired}
        sim["protocol_faults"] = [fault for fault in sim["protocol_faults"] if fault["id"] not in expired_ids]
        self._discard_inactive_cache(sim["protocol_faults"])
        return expired

    def apply(self, snapshot: dict, protocol: str) -> dict:
        """Return a protocol view; suppress semantics differ where protocols require stable maps."""
        output = dict(snapshot)
        transformed = []
        for original in snapshot["tags"]:
            tag = dict(original)
            matching = [fault for fault in snapshot.get("protocol_faults", [])
                        if fault["protocol"] in ("all", protocol) and self._matches(fault, tag)]
            suppressed = False
            for fault in matching:
                cache_key = (fault["id"], tag["tag"])
                if fault["mode"] in {"freeze", "suppress"}:
                    self._frozen_values.setdefault(cache_key, tag["value"])
                    tag["value"] = self._frozen_values[cache_key]
                if fault["mode"] == "stale":
                    tag["quality"] = "STALE"
                elif fault["mode"] == "bad":
                    tag["quality"] = "BAD"
                elif fault["mode"] == "suppress":
                    if protocol == "MQTT":
                        suppressed = True
                    else:
                        # Modbus register addresses and OPC UA nodes stay stable.
                        tag["quality"] = "BAD"
            if not suppressed:
                transformed.append(tag)
        output["tags"] = transformed
        return output

    def sync(self, snapshot: dict) -> None:
        """Drop held values after a fault is cleared or expires in simulation time."""
        active_ids = {fault["id"] for fault in snapshot.get("protocol_faults", [])}
        self._frozen_values = {key: value for key, value in self._frozen_values.items()
                               if key[0] in active_ids}

    @staticmethod
    def _matches(fault: dict, tag: dict) -> bool:
        if fault["selector_type"] == "tag":
            return tag["tag"].casefold() == fault["target"].casefold()
        return tag["area"].casefold() == fault["target"].casefold()

    def _discard_inactive_cache(self, faults: list[dict]) -> None:
        active_ids = {fault["id"] for fault in faults}
        self._frozen_values = {key: value for key, value in self._frozen_values.items() if key[0] in active_ids}
