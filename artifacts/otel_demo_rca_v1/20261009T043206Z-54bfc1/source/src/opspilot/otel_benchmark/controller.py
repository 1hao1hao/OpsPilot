"""Control flags through verified upstream atomic writer and flagd evaluation RPC."""

import asyncio
import copy
from datetime import UTC, datetime

import httpx


class OpenTelemetryDemoFaultController:
    def __init__(self, save, *, ui_url="http://127.0.0.1:14000", flagd_url="http://127.0.0.1:18013", transport=None):
        self.save = save
        self.ui_url = ui_url
        self.flagd_url = flagd_url
        self.transport = transport
        self.sequence = 0

    def client(self):
        return httpx.AsyncClient(timeout=15, trust_env=False, transport=self.transport)

    async def read_current_flags(self):
        async with self.client() as client:
            response = await client.get(self.ui_url + "/api/read")
            response.raise_for_status()
            return response.json()

    async def _apply(self, before, after):
        self.sequence += 1
        name = f"control/{self.sequence:04d}"
        self.save(name + "_before.json", {"time": datetime.now(UTC).isoformat(), "state": before})
        self.save(name + "_requested.json", {"time": datetime.now(UTC).isoformat(), "state": after})
        async with self.client() as client:
            # Upstream storage.ex writes temporary file then atomically renames on same filesystem.
            response = await client.post(self.ui_url + "/api/write", json={"data": after})
            response.raise_for_status()
            evaluations = {}
            for attempt in range(40):
                current = await self.read_current_flags()
                if current == after:
                    matched = True
                    for flag, config in after["flags"].items():
                        if "off" not in config["variants"]:
                            continue
                        value = config["variants"][config["defaultVariant"]]
                        method = "ResolveBoolean" if isinstance(value, bool) else "ResolveFloat" if isinstance(value, float) else "ResolveInt"
                        response = await client.post(self.flagd_url + "/flagd.evaluation.v1.Service/" + method,
                                                     json={"flagKey": flag, "context": {}},
                                                     headers={"Connect-Protocol-Version": "1"})
                        response.raise_for_status()
                        evaluation = response.json()
                        evaluations[flag] = evaluation
                        matched &= evaluation.get("variant") == config["defaultVariant"]
                    if matched:
                        self.save(name + "_confirmed.json", {"time": datetime.now(UTC).isoformat(),
                                                              "state": current, "evaluations": evaluations})
                        return
                await asyncio.sleep(0.5)
        raise RuntimeError("flagd reload did not match requested variants")

    async def reset_all_faults(self):
        before = await self.read_current_flags()
        after = copy.deepcopy(before)
        for config in after["flags"].values():
            if "off" in config["variants"]:
                config["defaultVariant"] = "off"
                config.pop("targeting", None)
        await self._apply(before, after)

    async def set_fault(self, flag, variant):
        before = await self.read_current_flags()
        if flag not in before["flags"] or variant not in before["flags"][flag]["variants"]:
            raise ValueError("Unknown upstream flag or variant")
        after = copy.deepcopy(before)
        after["flags"][flag]["defaultVariant"] = variant
        after["flags"][flag].pop("targeting", None)
        await self._apply(before, after)
