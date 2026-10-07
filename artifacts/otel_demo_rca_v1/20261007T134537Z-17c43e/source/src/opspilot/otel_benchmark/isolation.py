"""Remove control-plane disclosures, retain genuine telemetry facts and reject leakage."""

import json
import re

FORBIDDEN_KEYS = {"fault_control", "ground_truth", "expected_root_cause_type", "expected_root_cause",
                  "scenario_id", "scenario", "fault_flag", "fault_metadata"}
CONTROL = re.compile(r"feature.?flag|flagd|scenario active|overload simulation|problempattern|problem pattern", re.IGNORECASE)
PII = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)


class LeakageGuard:
    def __init__(self, flag_names):
        self.flag_names = tuple(flag_names)
        self.dropped = 0

    def mentions_control(self, text):
        return bool(CONTROL.search(text)) or any(flag.lower() in text.lower() for flag in self.flag_names)

    def assert_clean(self, value):
        def visit(item):
            if isinstance(item, dict):
                for key, child in item.items():
                    visit(key)
                    normalized_key = key.lower().replace(".", "_")
                    if normalized_key in FORBIDDEN_KEYS or normalized_key.startswith("feature_flag"):
                        raise ValueError("Ground truth or control metadata reached engine boundary")
                    visit(child)
            elif isinstance(item, list):
                for child in item:
                    visit(child)
            elif isinstance(item, str) and any(flag.lower() in item.lower() for flag in self.flag_names):
                raise ValueError("Feature flag name reached engine boundary")
        visit(value)

    def sanitize(self, value):
        if isinstance(value, list):
            output = []
            for item in value:
                # Drop explicit injection/configuration log entries and feature flag spans.
                text = " ".join(str(item.get(k, "")) for k in ("message", "operation", "name", "service")) if isinstance(item, dict) else ""
                if self.mentions_control(text):
                    self.dropped += 1
                    continue
                output.append(self.sanitize(item))
            return output
        if isinstance(value, dict):
            output = {}
            for key, item in value.items():
                normalized_key = key.lower().replace(".", "_")
                if normalized_key in FORBIDDEN_KEYS or normalized_key.startswith("feature_flag") or self.mentions_control(key):
                    self.dropped += 1
                    continue
                if isinstance(item, str) and self.mentions_control(item):
                    self.dropped += 1
                    continue
                output[key] = self.sanitize(item)
            return output
        if isinstance(value, str):
            return PII.sub("[redacted-email]", value)
        return value

    def recorded_fixture(self, value):
        clean = self.sanitize(value)
        self.assert_clean(clean)
        return json.loads(json.dumps(clean))
