"""Versioned JSONL dataset loading and split integrity checks."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from opspilot.models import EvaluationCase


class DatasetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    version: str
    schema_version: str
    case_count: int
    splits: dict[str, int]
    categories: list[str]
    frozen_test: bool
    label_policy: str


class Dataset:
    def __init__(self, root: Path, manifest: DatasetManifest, by_split: dict[str, list[EvaluationCase]]) -> None:
        self.root = root
        self.manifest = manifest
        self.by_split = by_split

    def cases(self, split: str) -> list[EvaluationCase]:
        try:
            return list(self.by_split[split])
        except KeyError as exc:
            raise ValueError(f"unknown split: {split}") from exc


def load_dataset(root: str | Path) -> Dataset:
    root = Path(root)
    manifest = DatasetManifest.model_validate_json((root / "manifest.json").read_text(encoding="utf-8"))
    by_split: dict[str, list[EvaluationCase]] = {}
    seen: set[str] = set()
    for split, expected_count in manifest.splits.items():
        path = root / f"{split}.jsonl"
        cases = [
            EvaluationCase.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if len(cases) != expected_count:
            raise ValueError(f"{split} expected {expected_count} cases, found {len(cases)}")
        for case in cases:
            if case.split != split:
                raise ValueError(f"case {case.case_id} declares split {case.split}, expected {split}")
            if case.dataset_version != manifest.version:
                raise ValueError(f"case {case.case_id} has wrong dataset version")
            if case.case_id in seen:
                raise ValueError(f"duplicate case_id across splits: {case.case_id}")
            seen.add(case.case_id)
        by_split[split] = cases
    if len(seen) != manifest.case_count:
        raise ValueError(f"manifest expected {manifest.case_count} total cases, found {len(seen)}")
    return Dataset(root, manifest, by_split)

