"""Reusable robust comparison of historical and recent observation windows."""

import math
from statistics import median


def temporal_payload(points, timestamp, *, query=None):
    """Keep time context without consulting incident controls or case labels.

    Prefer history ending five minutes before the alert. For short retention or
    a restarted process, use the earliest third of the pre-alert samples. The
    latter is a local reference, not a guaranteed healthy baseline.
    """
    points = sorted((float(t), float(v)) for t, v in points if math.isfinite(float(v)))
    history = [(t, v) for t, v in points if t < timestamp]
    reference = [v for t, v in history if t <= timestamp - 300]
    if len(reference) < 3:
        reference = [v for _, v in history[:len(history) // 3]]
    payload = {"current": points[-1][1], "data_points": [{"timestamp": t, "value": v} for t, v in points],
               "baseline_series": [v for _, v in history], "time_series": [v for _, v in points],
               "reference_series": reference, "recent_series": [v for _, v in points[-4:]],
               "temporal_context": True}
    if history:
        payload["baseline"] = sum(v for _, v in history) / len(history)
    if query is not None:
        payload["query"] = query
    return payload


def change(payload, *, direction="up", minimum_delta=0.0, minimum_ratio=1.5, reset_aware=False):
    """Require a material sustained shift exceeding robust historical noise."""
    if not isinstance(payload, dict):
        return None
    history = payload.get("reference_series", payload.get("baseline_series", []))
    reset_index = 0
    if reset_aware:
        # A large downward discontinuity in a fractional resource gauge can
        # separate process incarnations or recovery regimes. Never compare
        # their combined level variance with the current process's growth.
        for index in range(1, len(history)):
            previous, value = float(history[index - 1]), float(history[index])
            if previous - value > 0.1 and value < previous * 0.8:
                reset_index = index
        history = history[reset_index:]
    if len(history) < 3:
        return None
    baseline = median(float(v) for v in history)
    recent = [float(v) for v in payload.get("recent_series", [payload.get("current", 0)])]
    current = median(recent)
    noise = median(abs(float(v) - baseline) for v in history)
    delta = current - baseline if direction == "up" else baseline - current
    material = max(minimum_delta, abs(baseline) * (minimum_ratio - 1), 6 * noise)
    if delta <= material:
        return None
    return {"baseline": baseline, "recent": current, "delta": delta, "noise": noise,
            "reference_samples": len(history), "discarded_before_discontinuity": reset_index}
