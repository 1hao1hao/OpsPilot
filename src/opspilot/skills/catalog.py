"""Agent Skills metadata discovery and on-demand instruction loading."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str
    path: Path


@dataclass(frozen=True)
class SkillActivation:
    name: str
    reason: str
    version: str
    loaded_bytes: int
    tool_order: tuple[str, ...]
    instructions: str


class SkillCatalog:
    """Metadata is indexed at startup; full bodies are read only on activation."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(__file__).parent
        self.metadata: dict[str, SkillMetadata] = {}
        self.activations: list[SkillActivation] = []
        for path in sorted(self.root.glob("*/SKILL.md")):
            header = self._header(path)
            name = header.get("name")
            description = header.get("description")
            if name != path.parent.name or not isinstance(description, str) or not description.strip():
                continue
            self.metadata[name] = SkillMetadata(name, description, path)

    @staticmethod
    def _header(path: Path) -> dict:
        with path.open(encoding="utf-8") as stream:
            if stream.readline().strip() != "---":
                return {}
            lines = []
            for line in stream:
                if line.strip() == "---":
                    break
                lines.append(line)
        value = yaml.safe_load("".join(lines))
        return value if isinstance(value, dict) else {}

    def select(self, *, domain: str, signal: str) -> SkillMetadata | None:
        names = {
            "db": "db-bottleneck-triage", "redis": "redis-cache-triage",
            "kafka": "mq-backlog-triage", "rpc": "rpc-timeout-triage",
        }
        name = names.get(domain)
        item = self.metadata.get(name) if name else None
        if item and (domain in item.description.lower() or signal in item.description):
            return item
        return None

    def activate(self, name: str, *, reason: str, allowed_tools: list[str]) -> SkillActivation:
        item = self.metadata[name]
        raw = item.path.read_bytes()
        body = raw.decode("utf-8").split("---", 2)[-1]
        in_tools = False
        requested = []
        for line in body.splitlines():
            if line.startswith("## "):
                in_tools = line.strip() == "## Tool order"
            elif in_tools and line.startswith("- `") and "`" in line[3:]:
                requested.append(line.split("`", 2)[1])
        # Skill text can suggest order but never enlarge the authorized set.
        selected = tuple(tool for tool in requested if tool in allowed_tools)
        activation = SkillActivation(
            name=name, reason=reason, version=hashlib.sha256(raw).hexdigest()[:12],
            loaded_bytes=len(raw), tool_order=selected, instructions=body.strip(),
        )
        self.activations.append(activation)
        return activation
