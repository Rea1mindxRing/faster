"""JSON export: full raw records so any aggregate can be recomputed/audited."""

from __future__ import annotations

import dataclasses
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from faster.config import EndpointConfig, WorkloadConfig
from faster.engine.runner import RunResult


@dataclass(frozen=True, slots=True)
class JsonExport:
    """Top-level result document. ``records`` is the auditable raw data."""

    schema_version: int
    created_utc: float
    endpoint: dict[str, Any]
    workload: dict[str, Any]
    summary: dict[str, Any]
    wall_start: float
    wall_end: float
    records: list[dict[str, Any]]
    resource_samples: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    def write(self, path: str | Path) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return out


def export_json(
    result: RunResult,
    endpoint: EndpointConfig,
    workload: WorkloadConfig,
) -> JsonExport:
    return JsonExport(
        schema_version=1,
        created_utc=time.time(),
        endpoint={
            "base_url": endpoint.base_url,
            "model": endpoint.model,
            "timeout_s": endpoint.timeout_s,
            "extra_body": endpoint.extra_body,
        },
        workload=workload.model_dump(mode="json"),
        summary=result.summary.as_dict(),
        wall_start=result.wall_start,
        wall_end=result.wall_end,
        records=[dataclasses.asdict(r) for r in result.records],
        resource_samples=[dataclasses.asdict(s) for s in result.resource_samples],
    )
