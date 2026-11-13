"""Shared deterministic instrument-fault calculations for process and telemetry models."""
from __future__ import annotations

import math
import random


def stable_tag_id(tag_name: str) -> int:
    """Return a stable ID independent of Python's randomized string hashing."""
    return sum((index + 1) * ord(char) for index, char in enumerate(tag_name))


def correlated_sensor_noise(state: dict, tag_name: str, seed: int, tick: int,
                           standard_deviation: float, tau_seconds: float = 3.0) -> float:
    """Return repeatable first-order-correlated transmitter noise for one scan.

    Noise is measurement-only state, cached by tag and scan so UI refreshes
    cannot advance a sensor twice or alter the physical process state.
    """
    if standard_deviation <= 0.0:
        return 0.0
    cache = state.setdefault("_instrument_noise", {})
    entry = cache.setdefault(tag_name, {"tick": int(tick) - 1, "noise": 0.0})
    tick = int(tick)
    if entry["tick"] == tick:
        return float(entry["noise"])
    if entry["tick"] > tick:
        entry.update(tick=tick - 1, noise=0.0)
    alpha = math.exp(-1.0 / max(1e-6, tau_seconds))
    innovation_scale = math.sqrt(1.0 - alpha * alpha)
    stable_id = stable_tag_id(tag_name)
    noise = float(entry["noise"])
    for sample_tick in range(int(entry["tick"]) + 1, tick + 1):
        rng = random.Random(seed * 1_000_003 + sample_tick * 9_176 + stable_id)
        noise = alpha * noise + innovation_scale * rng.gauss(0.0, standard_deviation)
    entry.update(tick=tick, noise=noise)
    return noise


def signal_drift_bias(tag_name: str, severity: str, duration_s: float,
                      elapsed_s: float, span: float) -> float:
    """Return a reproducible, signed bias ramp for one analog transmitter."""
    magnitude = {"Small": 0.01, "Medium": 0.03, "Large": 0.08}[severity]
    ramp_seconds = max(1.0, duration_s * 0.25)
    ramp = min(1.0, max(0.0, elapsed_s / ramp_seconds))
    sign = 1.0 if stable_tag_id(tag_name) % 2 == 0 else -1.0
    return sign * max(0.0, span) * magnitude * ramp
