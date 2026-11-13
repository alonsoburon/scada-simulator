"""Shared deterministic instrument-fault calculations for process and telemetry models."""
from __future__ import annotations


def stable_tag_id(tag_name: str) -> int:
    """Return a stable ID independent of Python's randomized string hashing."""
    return sum((index + 1) * ord(char) for index, char in enumerate(tag_name))


def signal_drift_bias(tag_name: str, severity: str, duration_s: float,
                      elapsed_s: float, span: float) -> float:
    """Return a reproducible, signed bias ramp for one analog transmitter."""
    magnitude = {"Small": 0.01, "Medium": 0.03, "Large": 0.08}[severity]
    ramp_seconds = max(1.0, duration_s * 0.25)
    ramp = min(1.0, max(0.0, elapsed_s / ramp_seconds))
    sign = 1.0 if stable_tag_id(tag_name) % 2 == 0 else -1.0
    return sign * max(0.0, span) * magnitude * ramp
